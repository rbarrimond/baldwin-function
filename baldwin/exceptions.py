"""Shared Baldwin exception hierarchy."""

from __future__ import annotations

from enum import Enum
from typing import Sequence


class ImapErrorCode(str, Enum):
    """Stable IMAP failure codes exposed to callers."""

    IMAP_REQUEST_FAILED = "IMAP_REQUEST_FAILED"
    IMAP_LOGIN_FAILED = "IMAP_LOGIN_FAILED"
    IMAP_FOLDER_STATUS_FAILED = "IMAP_FOLDER_STATUS_FAILED"
    IMAP_SELECT_FAILED = "IMAP_SELECT_FAILED"
    IMAP_UID_ENUM_FAILED = "IMAP_UID_ENUM_FAILED"
    IMAP_SEARCH_FAILED = "IMAP_SEARCH_FAILED"


class ImapReasonCategory(str, Enum):
    """Low-cardinality IMAP failure categories for client guidance."""

    AUTH = "auth"
    NETWORK = "network"
    PERMISSIONS = "permissions"
    FOLDER = "folder"
    UNKNOWN = "unknown"


def classify_imap_reason(message: str | None) -> ImapReasonCategory:
    """Classify raw IMAP cause text into a stable reason category."""
    normalized = (message or "").strip().lower()
    if not normalized:
        return ImapReasonCategory.UNKNOWN

    if any(token in normalized for token in ("auth", "login", "credential", "password")):
        return ImapReasonCategory.AUTH

    if any(token in normalized for token in ("permission", "denied", "not permitted", "readonly")):
        return ImapReasonCategory.PERMISSIONS

    if any(token in normalized for token in ("timeout", "timed out", "connection", "unreachable", "reset")):
        return ImapReasonCategory.NETWORK

    if any(token in normalized for token in ("no such mailbox", "nonexistent", "parse error", "invalid mailbox", "does not exist")):
        return ImapReasonCategory.FOLDER

    return ImapReasonCategory.UNKNOWN


def normalize_imap_folders(folders: Sequence[str] | None) -> tuple[str, ...]:
    """Return ordered unique folder names for error payloads."""
    if not folders:
        return ()

    normalized: list[str] = []
    for folder in folders:
        candidate = folder.strip()
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


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

    def __init__(
        self,
        message: str,
        *,
        error_code: ImapErrorCode = ImapErrorCode.IMAP_REQUEST_FAILED,
        reason_category: ImapReasonCategory = ImapReasonCategory.UNKNOWN,
        folders: Sequence[str] | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.reason_category = reason_category
        self.folders = normalize_imap_folders(folders)


class EmailDeliveryError(EmailServiceError):
    """Raised when digest delivery fails."""


class VectorStoreError(BaldwinError):
    """Raised when vector persistence fails."""


class ThingsServiceError(BaldwinError):
    """Base error for local Things database access failures."""


class ThingsConfigurationError(BaldwinConfigurationError):
    """Raised when the Things integration configuration is missing or invalid."""


class ThingsStoreError(BaldwinError):
    """Raised when Things snapshot persistence fails."""
