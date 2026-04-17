"""Utilities for normalizing emails and generating local vectors."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Iterable

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


def _tokenize(value: str) -> Iterable[str]:
    return re.findall(r"[A-Za-z0-9']+", value.lower())


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
    body: str
    searchable_text: str
    content_checksum: str
    headers: dict[str, str]


class EmailNormalizer:
    """Converts inbox emails into a stable persistence shape."""

    @staticmethod
    def _build_recipients(email_message: Email) -> list[str]:
        recipients: list[str] = []
        for group in (email_message.to, email_message.cc, email_message.bcc, email_message.reply_to):
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
            body=body,
            searchable_text=searchable_text,
            content_checksum=checksum,
            headers=email_message.headers,
        )


class HashingVectorizer:
    """A deterministic local vectorizer that produces dense vectors for pgvector storage."""

    def __init__(self, dimensions: int = 256, model_name: str = "hashing-v1"):
        if dimensions < 8:
            raise ValueError("dimensions must be at least 8")

        self.dimensions = dimensions
        self.model_name = model_name

    def vectorize(self, text: str) -> list[float]:
        normalized_text = _normalize_whitespace(text)
        if not normalized_text:
            raise ValueError("Text is required for vectorization.")

        vector = [0.0] * self.dimensions
        for token in _tokenize(normalized_text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            return vector

        return [value / magnitude for value in vector]