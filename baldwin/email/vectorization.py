"""Utilities for normalizing emails into a persistence-ready shape."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from email.utils import parsedate_to_datetime

from baldwin.embedding import HashingEmbeddingProvider
from baldwin.exceptions import EmailNormalizationError

from .email_service import Email


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_date(raw_date: str) -> str | None:
    if not raw_date:
        return None

    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError, IndexError) as exc:
        raise EmailNormalizationError(
            f"Unable to normalize email sent date from value {raw_date!r}."
        ) from exc

    if parsed.tzinfo is None:
        return parsed.isoformat() + "Z"

    return parsed.isoformat()


@dataclass(frozen=True)
class NormalizedEmail:
    """Canonical email representation used for persistence and vectorization."""

    fingerprint: str
    source_message_id: str | None
    subject: str
    sender: str
    recipients: list[str]
    raw_date: str
    sent_at: str | None
    folders: list[str]
    folder_uids: dict[str, int]
    body: str
    searchable_text: str
    content_checksum: str
    headers: dict[str, str]
    folder_flags: dict[str, list[str]] = field(default_factory=dict)
    folder_keywords: dict[str, list[str]] = field(default_factory=dict)


class EmailNormalizer:
    """Converts mailbox emails into a stable persistence shape."""

    @staticmethod
    def _merge_folders(existing: NormalizedEmail, candidate: NormalizedEmail) -> list[str]:
        folders = existing.folders.copy()
        for folder in candidate.folders:
            if folder not in folders:
                folders.append(folder)
        return folders

    @staticmethod
    def _merge_folder_uids(existing: NormalizedEmail, candidate: NormalizedEmail) -> dict[str, int]:
        folder_uids = existing.folder_uids.copy()
        for folder_name, folder_uid in candidate.folder_uids.items():
            if folder_name in folder_uids and folder_uids[folder_name] != folder_uid:
                raise EmailNormalizationError(
                    "Conflicting normalized emails share fingerprint "
                    f"{candidate.fingerprint} but have different IMAP UIDs for folder {folder_name!r}."
                )
            folder_uids[folder_name] = folder_uid
        return folder_uids

    @staticmethod
    def _merge_folder_metadata(
        existing: dict[str, list[str]],
        candidate: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        merged = {folder_name: values.copy() for folder_name, values in existing.items()}
        for folder_name, values in candidate.items():
            merged[folder_name] = values.copy()
        return merged

    @staticmethod
    def _build_recipients(email_message: Email) -> list[str]:
        recipients: list[str] = []
        recipient_groups = (
            email_message.to,
            email_message.cc,
            email_message.bcc,
            email_message.reply_to,
        )
        for group in recipient_groups:
            if group:
                recipients.extend(address for address in group if address)
        return recipients

    @staticmethod
    def _build_searchable_text(subject: str, body: str) -> str:
        normalized_subject = _normalize_whitespace(subject)
        normalized_body = _normalize_whitespace(body)
        if normalized_subject and normalized_body:
            return f"{normalized_subject}\n\n{normalized_body}"
        return normalized_subject or normalized_body

    @staticmethod
    def _build_fingerprint(email_message: Email, searchable_text: str) -> str:
        message_id = _normalize_whitespace(email_message.id or "")
        if message_id:
            return hashlib.sha256(message_id.encode("utf-8")).hexdigest()

        fallback = "|".join(
            [
                _normalize_whitespace(email_message.sender),
                _normalize_whitespace(email_message.date),
                _normalize_whitespace(email_message.subject),
                searchable_text,
            ]
        )
        return hashlib.sha256(fallback.encode("utf-8")).hexdigest()

    def normalize(self, email_message: Email) -> NormalizedEmail:
        """Normalize a mailbox email into the canonical persistence shape."""
        subject = _normalize_whitespace(email_message.subject)
        body = _normalize_whitespace(email_message.body)
        normalized_folder = _normalize_whitespace(email_message.folder or "")
        searchable_text = self._build_searchable_text(subject, body)
        if not searchable_text:
            raise EmailNormalizationError(
                "Email body or subject is required for vectorization."
            )

        try:
            sent_at = _parse_date(email_message.date)
        except EmailNormalizationError:
            sent_at = None

        checksum = hashlib.sha256(searchable_text.encode("utf-8")).hexdigest()
        return NormalizedEmail(
            fingerprint=self._build_fingerprint(email_message, searchable_text),
            source_message_id=_normalize_whitespace(email_message.id or "") or None,
            subject=subject,
            sender=_normalize_whitespace(email_message.sender),
            recipients=self._build_recipients(email_message),
            raw_date=email_message.date,
            sent_at=sent_at,
            folders=[normalized_folder] if normalized_folder else [],
            folder_uids={normalized_folder: email_message.imap_uid}
            if normalized_folder and email_message.imap_uid is not None
            else {},
            folder_flags={normalized_folder: list(email_message.imap_flags or [])}
            if normalized_folder and email_message.imap_flags
            else {},
            folder_keywords={normalized_folder: list(email_message.imap_keywords or [])}
            if normalized_folder and email_message.imap_keywords
            else {},
            body=body,
            searchable_text=searchable_text,
            content_checksum=checksum,
            headers=email_message.headers,
        )

    @staticmethod
    def merge_duplicates(normalized_emails: list[NormalizedEmail]) -> list[NormalizedEmail]:
        """Collapse duplicate normalized emails while preserving folder provenance."""
        merged: dict[str, NormalizedEmail] = {}
        ordered_fingerprints: list[str] = []

        for normalized_email in normalized_emails:
            existing = merged.get(normalized_email.fingerprint)
            if existing is None:
                merged[normalized_email.fingerprint] = normalized_email
                ordered_fingerprints.append(normalized_email.fingerprint)
                continue

            if existing.content_checksum != normalized_email.content_checksum:
                raise EmailNormalizationError(
                    "Conflicting normalized emails share fingerprint "
                    f"{normalized_email.fingerprint} but have different content checksums."
                )

            merged[normalized_email.fingerprint] = replace(
                existing,
                folders=EmailNormalizer._merge_folders(existing, normalized_email),
                folder_uids=EmailNormalizer._merge_folder_uids(existing, normalized_email),
                folder_flags=EmailNormalizer._merge_folder_metadata(
                    existing.folder_flags,
                    normalized_email.folder_flags,
                ),
                folder_keywords=EmailNormalizer._merge_folder_metadata(
                    existing.folder_keywords,
                    normalized_email.folder_keywords,
                ),
            )

        return [merged[fingerprint] for fingerprint in ordered_fingerprints]


class HashingVectorizer:
    """Compatibility wrapper over the shared hashing embedding provider."""

    def __init__(self, dimensions: int = 256, model_name: str = "hashing-v1"):
        self.provider = HashingEmbeddingProvider(dimensions=dimensions, model_name=model_name)
        self.dimensions = self.provider.dimensions
        self.model_name = self.provider.model_name

    def vectorize(self, text: str) -> list[float]:
        """Convert input text into a deterministic dense vector."""
        return self.provider.embed_texts([text])[0].vector
