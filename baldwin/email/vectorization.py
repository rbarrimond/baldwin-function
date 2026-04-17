"""Utilities for normalizing emails into a persistence-ready shape."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from baldwin.embedding import HashingEmbeddingProvider

from .email_service import Email


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_date(raw_date: str) -> str | None:
    if not raw_date:
        return None

    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError, IndexError):
        return None

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
    folder: str | None
    body: str
    searchable_text: str
    content_checksum: str
    headers: dict[str, str]


class EmailNormalizer:
    """Converts mailbox emails into a stable persistence shape."""

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
        searchable_text = self._build_searchable_text(subject, body)
        if not searchable_text:
            raise ValueError("Email body or subject is required for vectorization.")

        checksum = hashlib.sha256(searchable_text.encode("utf-8")).hexdigest()
        return NormalizedEmail(
            fingerprint=self._build_fingerprint(email_message, searchable_text),
            source_message_id=_normalize_whitespace(email_message.id or "") or None,
            subject=subject,
            sender=_normalize_whitespace(email_message.sender),
            recipients=self._build_recipients(email_message),
            raw_date=email_message.date,
            sent_at=_parse_date(email_message.date),
            folder=_normalize_whitespace(email_message.folder or "") or None,
            body=body,
            searchable_text=searchable_text,
            content_checksum=checksum,
            headers=email_message.headers,
        )


class HashingVectorizer:
    """Compatibility wrapper over the shared hashing embedding provider."""

    def __init__(self, dimensions: int = 256, model_name: str = "hashing-v1"):
        self.provider = HashingEmbeddingProvider(dimensions=dimensions, model_name=model_name)
        self.dimensions = self.provider.dimensions
        self.model_name = self.provider.model_name

    def vectorize(self, text: str) -> list[float]:
        """Convert input text into a deterministic dense vector."""
        return self.provider.embed_texts([text])[0].vector
