"""Public package exports for Baldwin email helpers."""

from baldwin.exceptions import EmailDeliveryError, EmailFetchError, EmailServiceError

from .email_service import Email, EmailService
from .postgres_store import PostgresEmailVectorStore, StoreResult
from .vectorization import EmailNormalizer, HashingVectorizer, NormalizedEmail

__all__ = [
    "Email",
    "EmailDeliveryError",
    "EmailFetchError",
    "EmailNormalizer",
    "EmailService",
    "EmailServiceError",
    "HashingVectorizer",
    "NormalizedEmail",
    "PostgresEmailVectorStore",
    "StoreResult",
]
