"""Email-specific PostgreSQL persistence built on the generic vector store."""

from __future__ import annotations

from typing import Any

from baldwin.vector.postgres_store import (
    PostgresVectorStore,
    VectorDocument,
    VectorStoreResult,
)
from .vectorization import NormalizedEmail

StoreResult = VectorStoreResult


class PostgresEmailVectorStore(PostgresVectorStore):
    """Stores normalized emails using the shared vector persistence layer."""

    def __init__(self, database_url: str, dimensions: int, model_name: str):
        super().__init__(
            database_url=database_url,
            dimensions=dimensions,
            model_name=model_name,
            document_table="vector_documents",
            embedding_table="vector_embeddings",
        )

    @staticmethod
    def to_document(normalized_email: NormalizedEmail) -> VectorDocument:
        """Map a normalized email into the generic vector document shape."""
        metadata: dict[str, Any] = {
            "sender": normalized_email.sender,
            "recipients": normalized_email.recipients,
            "raw_date": normalized_email.raw_date,
            "sent_at": normalized_email.sent_at,
            "headers": normalized_email.headers,
        }
        return VectorDocument(
            document_key=normalized_email.fingerprint,
            source_type="email",
            source_id=normalized_email.source_message_id,
            title=normalized_email.subject,
            body=normalized_email.body,
            searchable_text=normalized_email.searchable_text,
            content_checksum=normalized_email.content_checksum,
            metadata=metadata,
        )

    def upsert_email(
        self,
        normalized_email: NormalizedEmail,
        vector: list[float],
    ) -> VectorStoreResult:
        """Upsert an email by delegating to the generic document store."""
        return self.upsert_document(self.to_document(normalized_email), vector)
    