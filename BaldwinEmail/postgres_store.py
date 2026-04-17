"""PostgreSQL persistence for normalized inbox emails and vectors."""

from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg

from .vectorization import NormalizedEmail


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


@dataclass(frozen=True)
class StoreResult:
    """Summary of a single email persistence operation."""

    inserted: bool
    embedding_updated: bool


class PostgresEmailVectorStore:
    """Stores email metadata and vectors in PostgreSQL with pgvector."""

    def __init__(self, database_url: str, dimensions: int, model_name: str):
        if not database_url:
            raise ValueError("database_url is required")
        if dimensions < 8:
            raise ValueError("dimensions must be at least 8")
        if not model_name:
            raise ValueError("model_name is required")

        self.database_url = database_url
        self.dimensions = dimensions
        self.model_name = model_name

    def bootstrap(self) -> None:
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS emails (
                        id BIGSERIAL PRIMARY KEY,
                        fingerprint TEXT NOT NULL UNIQUE,
                        source_message_id TEXT,
                        subject TEXT NOT NULL,
                        sender TEXT NOT NULL,
                        recipients JSONB NOT NULL DEFAULT '[]'::jsonb,
                        raw_date TEXT NOT NULL,
                        sent_at TIMESTAMPTZ,
                        body TEXT NOT NULL,
                        searchable_text TEXT NOT NULL,
                        headers JSONB NOT NULL DEFAULT '{}'::jsonb,
                        content_checksum TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS email_embeddings (
                        email_id BIGINT PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
                        model_name TEXT NOT NULL,
                        dimensions INTEGER NOT NULL,
                        embedding VECTOR({self.dimensions}) NOT NULL,
                        content_checksum TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_email_embeddings_model_name ON email_embeddings(model_name)"
                )

    def upsert_email(self, normalized_email: NormalizedEmail, vector: list[float]) -> StoreResult:
        vector_value = _vector_literal(vector)
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO emails (
                        fingerprint,
                        source_message_id,
                        subject,
                        sender,
                        recipients,
                        raw_date,
                        sent_at,
                        body,
                        searchable_text,
                        headers,
                        content_checksum
                    )
                    VALUES (
                        %(fingerprint)s,
                        %(source_message_id)s,
                        %(subject)s,
                        %(sender)s,
                        %(recipients)s::jsonb,
                        %(raw_date)s,
                        %(sent_at)s,
                        %(body)s,
                        %(searchable_text)s,
                        %(headers)s::jsonb,
                        %(content_checksum)s
                    )
                    ON CONFLICT (fingerprint) DO UPDATE SET
                        source_message_id = EXCLUDED.source_message_id,
                        subject = EXCLUDED.subject,
                        sender = EXCLUDED.sender,
                        recipients = EXCLUDED.recipients,
                        raw_date = EXCLUDED.raw_date,
                        sent_at = EXCLUDED.sent_at,
                        body = EXCLUDED.body,
                        searchable_text = EXCLUDED.searchable_text,
                        headers = EXCLUDED.headers,
                        content_checksum = EXCLUDED.content_checksum,
                        updated_at = NOW()
                    RETURNING id, (xmax = 0) AS inserted
                    """,
                    {
                        "fingerprint": normalized_email.fingerprint,
                        "source_message_id": normalized_email.source_message_id,
                        "subject": normalized_email.subject,
                        "sender": normalized_email.sender,
                        "recipients": json.dumps(normalized_email.recipients),
                        "raw_date": normalized_email.raw_date,
                        "sent_at": normalized_email.sent_at,
                        "body": normalized_email.body,
                        "searchable_text": normalized_email.searchable_text,
                        "headers": json.dumps(normalized_email.headers),
                        "content_checksum": normalized_email.content_checksum,
                    },
                )
                email_id, inserted = cursor.fetchone()
                cursor.execute(
                    """
                    INSERT INTO email_embeddings (
                        email_id,
                        model_name,
                        dimensions,
                        embedding,
                        content_checksum
                    )
                    VALUES (
                        %(email_id)s,
                        %(model_name)s,
                        %(dimensions)s,
                        %(embedding)s::vector,
                        %(content_checksum)s
                    )
                    ON CONFLICT (email_id) DO UPDATE SET
                        model_name = EXCLUDED.model_name,
                        dimensions = EXCLUDED.dimensions,
                        embedding = EXCLUDED.embedding,
                        content_checksum = EXCLUDED.content_checksum,
                        updated_at = NOW()
                    WHERE email_embeddings.content_checksum IS DISTINCT FROM EXCLUDED.content_checksum
                    RETURNING TRUE
                    """,
                    {
                        "email_id": email_id,
                        "model_name": self.model_name,
                        "dimensions": self.dimensions,
                        "embedding": vector_value,
                        "content_checksum": normalized_email.content_checksum,
                    },
                )
                embedding_updated = cursor.fetchone() is not None

            connection.commit()

        return StoreResult(inserted=bool(inserted), embedding_updated=embedding_updated)