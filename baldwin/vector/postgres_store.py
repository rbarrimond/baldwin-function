"""Generic PostgreSQL persistence for vectorized documents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from baldwin.embedding import EmbeddingResult
import psycopg
from psycopg import sql


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


@dataclass(frozen=True)
class VectorDocument:
    """Persistence-ready document payload for vector storage."""

    document_key: str
    source_type: str
    source_id: str | None
    title: str
    body: str
    searchable_text: str
    content_checksum: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VectorStoreResult:
    """Summary of a single vector document persistence operation."""

    inserted: bool
    embedding_updated: bool


class PostgresVectorStore:
    """Stores generic vectorized documents in PostgreSQL with pgvector."""

    def __init__(
        self,
        database_url: str,
        document_table: str = "vector_documents",
        embedding_table: str = "vector_embeddings",
    ):
        if not database_url:
            raise ValueError("database_url is required")
        if not document_table:
            raise ValueError("document_table is required")
        if not embedding_table:
            raise ValueError("embedding_table is required")

        self.database_url = database_url
        self.document_table = document_table
        self.embedding_table = embedding_table

    def bootstrap(self) -> None:
        """Create the pgvector extension and required tables when absent."""
        document_table = sql.Identifier(self.document_table)
        embedding_table = sql.Identifier(self.embedding_table)

        with psycopg.connect(self.database_url, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {document_table} (
                            id BIGSERIAL PRIMARY KEY,
                            document_key TEXT NOT NULL UNIQUE,
                            source_type TEXT NOT NULL,
                            source_id TEXT,
                            title TEXT NOT NULL,
                            body TEXT NOT NULL,
                            searchable_text TEXT NOT NULL,
                            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            content_checksum TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(document_table=document_table)
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {embedding_table} (
                            document_id BIGINT NOT NULL REFERENCES {document_table}(id) ON DELETE CASCADE,
                            provider TEXT NOT NULL DEFAULT 'legacy',
                            model_name TEXT NOT NULL,
                            dimensions INTEGER NOT NULL,
                            embedding VECTOR NOT NULL,
                            content_checksum TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        embedding_table=embedding_table,
                        document_table=document_table,
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {embedding_table} ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'legacy'"
                    ).format(
                        embedding_table=embedding_table,
                    )
                )
                cursor.execute(
                    sql.SQL("ALTER TABLE {embedding_table} DROP CONSTRAINT IF EXISTS {constraint_name}").format(
                        embedding_table=embedding_table,
                        constraint_name=sql.Identifier(f"{self.embedding_table}_pkey"),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {embedding_table} ADD CONSTRAINT {constraint_name} "
                        "PRIMARY KEY (document_id, provider, model_name)"
                    ).format(
                        embedding_table=embedding_table,
                        constraint_name=sql.Identifier(f"{self.embedding_table}_pkey"),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index_name} ON {embedding_table}(provider, model_name)"
                    ).format(
                        index_name=sql.Identifier(f"idx_{self.embedding_table}_provider_model_name"),
                        embedding_table=embedding_table,
                    )
                )

    def upsert_document(
        self,
        document: VectorDocument,
        embedding: EmbeddingResult,
    ) -> VectorStoreResult:
        """Upsert a document row and refresh its embedding when the content changes."""
        document_table = sql.Identifier(self.document_table)
        embedding_table = sql.Identifier(self.embedding_table)
        vector_value = _vector_literal(embedding.vector)

        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {document_table} (
                            document_key,
                            source_type,
                            source_id,
                            title,
                            body,
                            searchable_text,
                            metadata,
                            content_checksum
                        )
                        VALUES (
                            %(document_key)s,
                            %(source_type)s,
                            %(source_id)s,
                            %(title)s,
                            %(body)s,
                            %(searchable_text)s,
                            %(metadata)s::jsonb,
                            %(content_checksum)s
                        )
                        ON CONFLICT (document_key) DO UPDATE SET
                            source_type = EXCLUDED.source_type,
                            source_id = EXCLUDED.source_id,
                            title = EXCLUDED.title,
                            body = EXCLUDED.body,
                            searchable_text = EXCLUDED.searchable_text,
                            metadata = EXCLUDED.metadata,
                            content_checksum = EXCLUDED.content_checksum,
                            updated_at = NOW()
                        RETURNING id, (xmax = 0) AS inserted
                        """
                    ).format(document_table=document_table),
                    {
                        "document_key": document.document_key,
                        "source_type": document.source_type,
                        "source_id": document.source_id,
                        "title": document.title,
                        "body": document.body,
                        "searchable_text": document.searchable_text,
                        "metadata": json.dumps(document.metadata),
                        "content_checksum": document.content_checksum,
                    },
                )
                document_row = cursor.fetchone()
                if document_row is None:
                    raise RuntimeError("Failed to upsert vector document metadata.")

                document_id, inserted = document_row
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {embedding_table} (
                            document_id,
                            provider,
                            model_name,
                            dimensions,
                            embedding,
                            content_checksum
                        )
                        VALUES (
                            %(document_id)s,
                            %(provider)s,
                            %(model_name)s,
                            %(dimensions)s,
                            %(embedding)s::vector,
                            %(content_checksum)s
                        )
                        ON CONFLICT (document_id, provider, model_name) DO UPDATE SET
                            dimensions = EXCLUDED.dimensions,
                            embedding = EXCLUDED.embedding,
                            content_checksum = EXCLUDED.content_checksum,
                            updated_at = NOW()
                        WHERE {embedding_table}.content_checksum IS DISTINCT FROM EXCLUDED.content_checksum
                           OR {embedding_table}.dimensions IS DISTINCT FROM EXCLUDED.dimensions
                        RETURNING TRUE
                        """
                    ).format(embedding_table=embedding_table),
                    {
                        "document_id": document_id,
                        "provider": embedding.provider,
                        "model_name": embedding.model_name,
                        "dimensions": embedding.dimensions,
                        "embedding": vector_value,
                        "content_checksum": document.content_checksum,
                    },
                )
                embedding_updated = cursor.fetchone() is not None

            connection.commit()

        return VectorStoreResult(inserted=bool(inserted), embedding_updated=embedding_updated)
