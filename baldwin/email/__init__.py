"""Public package exports for Baldwin email helpers."""

from .email_service import Email, EmailService
from .postgres_store import PostgresEmailVectorStore, StoreResult
from .vectorization import EmailNormalizer, HashingVectorizer, NormalizedEmail

__all__ = [
    "Email",
    "EmailNormalizer",
    "EmailService",
    "HashingVectorizer",
    "NormalizedEmail",
    "PostgresEmailVectorStore",
    "StoreResult",
]
