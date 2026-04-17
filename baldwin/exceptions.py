"""Shared Baldwin exception hierarchy."""


class BaldwinError(RuntimeError):
    """Base runtime error for Baldwin domain failures."""


class EmailServiceError(BaldwinError):
    """Base error for mailbox and email-delivery failures."""


class EmailFetchError(EmailServiceError):
    """Raised when inbox reads fail."""


class EmailDeliveryError(EmailServiceError):
    """Raised when digest delivery fails."""


class VectorStoreError(BaldwinError):
    """Raised when vector persistence fails."""