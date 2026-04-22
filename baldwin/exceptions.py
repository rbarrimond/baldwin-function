"""Shared Baldwin exception hierarchy."""


class BaldwinError(RuntimeError):
    """Base runtime error for Baldwin domain failures."""


class BaldwinValidationError(BaldwinError):
    """Raised when Baldwin receives invalid caller input."""


class BaldwinConfigurationError(BaldwinValidationError):
    """Raised when Baldwin configuration is missing or invalid."""


class EmailServiceError(BaldwinError):
    """Base error for mailbox and email-delivery failures."""


class EmailNormalizationError(BaldwinValidationError):
    """Raised when Baldwin cannot normalize mailbox content safely."""


class EmailFetchError(EmailServiceError):
    """Raised when inbox reads fail."""


class EmailDeliveryError(EmailServiceError):
    """Raised when digest delivery fails."""


class VectorStoreError(BaldwinError):
    """Raised when vector persistence fails."""


class ThingsServiceError(BaldwinError):
    """Base error for local Things database access failures."""


class ThingsConfigurationError(BaldwinConfigurationError):
    """Raised when the Things integration configuration is missing or invalid."""
