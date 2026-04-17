"""Public package exports for Baldwin email helpers."""

from baldwin.exceptions import EmailDeliveryError, EmailFetchError, EmailServiceError

from .email_service import DEFAULT_IMAP_FOLDER, Email, EmailService, MailboxFolders
from .postgres_store import PostgresEmailVectorStore, StoreResult
from .vectorization import EmailNormalizer, HashingVectorizer, NormalizedEmail

__all__ = [
    "DEFAULT_IMAP_FOLDER",
    "Email",
    "EmailDeliveryError",
    "EmailFetchError",
    "EmailNormalizer",
    "EmailService",
    "EmailServiceError",
    "HashingVectorizer",
    "MailboxFolders",
    "NormalizedEmail",
    "PostgresEmailVectorStore",
    "StoreResult",
]
