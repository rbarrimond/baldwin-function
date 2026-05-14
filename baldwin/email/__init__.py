"""Public package exports for Baldwin email helpers."""

from baldwin.exceptions import (
    EmailDeliveryError,
    EmailFetchError,
    EmailNormalizationError,
    EmailServiceError,
)

from .email_service import (
    DEFAULT_IMAP_FOLDER,
    Email,
    EmailService,
    MailboxFolders,
    MailboxFolderStatus,
)
from .postgres_store import PostgresEmailVectorStore, StoreResult
from .semantic import (
    SemanticClassification,
    SemanticEnricher,
    SemanticKeywordPolicy,
    SemanticKeywordSuggestion,
    build_semantic_enricher,
)
from .vectorization import EmailNormalizer, HashingVectorizer, NormalizedEmail

__all__ = [
    "DEFAULT_IMAP_FOLDER",
    "Email",
    "EmailDeliveryError",
    "EmailFetchError",
    "EmailNormalizationError",
    "EmailNormalizer",
    "EmailService",
    "EmailServiceError",
    "HashingVectorizer",
    "MailboxFolders",
    "MailboxFolderStatus",
    "NormalizedEmail",
    "PostgresEmailVectorStore",
    "SemanticClassification",
    "SemanticEnricher",
    "SemanticKeywordPolicy",
    "SemanticKeywordSuggestion",
    "StoreResult",
    "build_semantic_enricher",
]
