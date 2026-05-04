"""Generic PostgreSQL persistence for vectorized documents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, ClassVar
import threading

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from baldwin.embedding import EmbeddingResult
from baldwin.exceptions import VectorStoreError
from baldwin.log import get_logger

_logger = get_logger(__name__)


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

    _pool: ClassVar[ConnectionPool | None] = None
    _pool_lock: ClassVar[threading.Lock] = threading.Lock()

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
        self._ensure_pool()

    def _ensure_pool(self) -> None:
        """Ensure process-global connection pool is initialized (thread-safe)."""
        if PostgresVectorStore._pool is not None:
            return

        with PostgresVectorStore._pool_lock:
            if PostgresVectorStore._pool is not None:
                return

            _logger.debug("Initializing connection pool for %r", self.database_url)
            try:
                PostgresVectorStore._pool = ConnectionPool(
                    self.database_url,
                    min_size=2,
                    max_size=20,
                    timeout=30,
                )
            except Exception as exc:
                _logger.exception("Failed to initialize connection pool")
                raise VectorStoreError("Failed to create database connection pool.") from exc

    @staticmethod
    def _get_connection() -> Any:
        """Get a connection from the pool."""
        if PostgresVectorStore._pool is None:
            raise VectorStoreError("Connection pool not initialized.")
        return PostgresVectorStore._pool.getconn()

    @staticmethod
    def _return_connection(connection: Any) -> None:
        """Return a connection to the pool."""
        if PostgresVectorStore._pool is not None:
            PostgresVectorStore._pool.putconn(connection)

    def bootstrap(self) -> None:
        """Create the pgvector extension and required tables when absent."""
        _logger.debug("Bootstrapping vector store schema")
        document_table = sql.Identifier(self.document_table)
        embedding_table = sql.Identifier(self.embedding_table)

        try:
            with psycopg.connect(self.database_url, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_lock(hashtext(%s))",
                        (f"baldwin_vector_bootstrap_{self.embedding_table}",),
                    )
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
                        """
                        SELECT con.conname
                        FROM pg_constraint con
                        JOIN pg_class rel ON rel.oid = con.conrelid
                        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                        WHERE con.contype = 'p'
                          AND rel.relname = %s
                          AND nsp.nspname = CURRENT_SCHEMA()
                        """,
                        (self.embedding_table,),
                    )
                    existing_pk_constraints = [row[0] for row in cursor.fetchall()]
                    for constraint_name in existing_pk_constraints:
                        cursor.execute(
                            sql.SQL(
                                "ALTER TABLE {embedding_table} DROP CONSTRAINT IF EXISTS {constraint_name}"
                            ).format(
                                embedding_table=embedding_table,
                                constraint_name=sql.Identifier(constraint_name),
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
        except psycopg.Error as exc:
            _logger.exception("Failed to bootstrap vector store schema")
            raise VectorStoreError("Failed to bootstrap PostgreSQL vector storage.") from exc
        _logger.info("Vector store schema bootstrap complete")

    def _upsert_on_connection(
        self,
        connection: Any,
        document: VectorDocument,
        embedding: EmbeddingResult,
    ) -> tuple[VectorStoreResult, int]:
        """Execute a single document upsert on an existing connection without committing."""
        document_table = sql.Identifier(self.document_table)
        embedding_table = sql.Identifier(self.embedding_table)
        vector_value = _vector_literal(embedding.vector)

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
                raise VectorStoreError("Failed to upsert vector document metadata.")

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

        return VectorStoreResult(inserted=bool(inserted), embedding_updated=embedding_updated), document_id

    def upsert_document(
        self,
        document: VectorDocument,
        embedding: EmbeddingResult,
    ) -> tuple[VectorStoreResult, int]:
        """Upsert a document row and refresh its embedding when the content changes.

        Returns:
            Tuple of (VectorStoreResult, document_id) where document_id can be used
            for batch sync recording operations.
        """
        _logger.debug(
            "Upserting vector document: document_key=%r source_type=%r",
            document.document_key, document.source_type,
        )
        connection = self._get_connection()
        try:
            result, document_id = self._upsert_on_connection(connection, document, embedding)
            connection.commit()
        except psycopg.Error as exc:
            _logger.exception("Failed to persist vector document: document_key=%r", document.document_key)
            raise VectorStoreError("Failed to persist vector document data.") from exc
        finally:
            self._return_connection(connection)

        _logger.debug(
            "Vector document upserted: document_key=%r inserted=%s embedding_updated=%s document_id=%d",
            document.document_key, bool(result.inserted), result.embedding_updated, document_id,
        )
        return result, document_id

    def upsert_documents_batch(
        self,
        documents: list[VectorDocument],
        embeddings: list[EmbeddingResult],
    ) -> list[tuple[VectorStoreResult, int]]:
        """Upsert multiple documents using a single pooled connection.

        Each document is committed individually to preserve upsert invariants.
        Reusing one connection avoids repeated pool acquisition overhead.

        Returns:
            List of (VectorStoreResult, document_id) in the same order as inputs.
        """
        if len(documents) != len(embeddings):
            raise ValueError(
                f"documents and embeddings must have equal length: {len(documents)} != {len(embeddings)}"
            )
        if not documents:
            return []

        _logger.debug("Batch-upserting %d vector documents", len(documents))
        results: list[tuple[VectorStoreResult, int]] = []
        connection = self._get_connection()
        try:
            for document, embedding in zip(documents, embeddings):
                result = self._upsert_on_connection(connection, document, embedding)
                connection.commit()
                results.append(result)
        except psycopg.Error as exc:
            _logger.exception("Failed to persist vector document batch")
            raise VectorStoreError("Failed to persist vector document data.") from exc
        finally:
            self._return_connection(connection)

        _logger.debug("Batch upsert complete: %d documents persisted", len(results))
        return results
