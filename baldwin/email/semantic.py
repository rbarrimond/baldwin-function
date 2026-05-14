"""Semantic enrichment contracts for normalized IMAP emails."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Protocol, Sequence, cast

from baldwin.exceptions import BaldwinConfigurationError, BaldwinError
from baldwin.log import get_logger

from .vectorization import NormalizedEmail

_logger = get_logger(__name__)

DEFAULT_AUTO_APPLY_MIN_CONFIDENCE = 0.9


@dataclass(frozen=True)
class SemanticKeywordSuggestion:
    """Classifier suggestion for one semantic IMAP keyword."""

    keyword: str
    confidence: float
    rationale: str | None = None


@dataclass(frozen=True)
class SemanticClassification:
    """Model output for one normalized email."""

    provider: str
    model_name: str
    suggested_keywords: tuple[SemanticKeywordSuggestion, ...]
    summary: str | None = None
    review_required: bool = False


@dataclass(frozen=True)
class SemanticPolicyDecision:
    """Policy evaluation outcome for classifier suggestions."""

    approved_keywords: tuple[str, ...]
    rejected_keywords: tuple[str, ...]
    auto_applied_keywords: tuple[str, ...]
    review_required: bool


class SemanticClassifier(Protocol):
    """Contract for semantic classifiers used in ingestion."""

    def classify(self, normalized_email: NormalizedEmail) -> SemanticClassification | None:
        """Return a semantic classification for one normalized email."""


class NullSemanticClassifier:
    """Default classifier that keeps enrichment as a no-op."""

    def classify(self, normalized_email: NormalizedEmail) -> SemanticClassification | None:
        """Always return None, indicating no classification or enrichment."""
        del normalized_email
        return None


class SemanticKeywordPolicy:
    """Allowlist and confidence policy for semantic IMAP keyword writes."""

    def __init__(self, *, allowed_keywords: Sequence[str], auto_apply_min_confidence: float):
        if auto_apply_min_confidence < 0.0 or auto_apply_min_confidence > 1.0:
            raise BaldwinConfigurationError(
                "App setting 'SEMANTIC_AUTO_APPLY_MIN_CONFIDENCE' must be between 0 and 1."
            )
        self._allowed_keywords = {
            normalized
            for normalized in (_normalize_keyword(keyword) for keyword in allowed_keywords)
            if normalized
        }
        self._auto_apply_min_confidence = auto_apply_min_confidence

    def evaluate(self, classification: SemanticClassification) -> SemanticPolicyDecision:
        """Evaluate classifier output against allowlist and confidence policy."""
        approved_keywords: list[str] = []
        rejected_keywords: list[str] = []
        auto_applied_keywords: list[str] = []

        for suggestion in classification.suggested_keywords:
            normalized_keyword = _normalize_keyword(suggestion.keyword)
            if not normalized_keyword:
                continue
            if self._allowed_keywords and normalized_keyword not in self._allowed_keywords:
                rejected_keywords.append(normalized_keyword)
                continue

            approved_keywords.append(normalized_keyword)
            if (
                not classification.review_required
                and suggestion.confidence >= self._auto_apply_min_confidence
            ):
                auto_applied_keywords.append(normalized_keyword)

        return SemanticPolicyDecision(
            approved_keywords=tuple(_dedupe_preserving_order(approved_keywords)),
            rejected_keywords=tuple(_dedupe_preserving_order(rejected_keywords)),
            auto_applied_keywords=tuple(_dedupe_preserving_order(auto_applied_keywords)),
            review_required=classification.review_required,
        )


class SemanticEnricher:
    """Apply semantic classification and policy to normalized emails."""

    def __init__(
        self,
        *,
        enabled: bool,
        classifier: SemanticClassifier,
        policy: SemanticKeywordPolicy,
    ):
        self._enabled = enabled
        self._classifier = classifier
        self._policy = policy

    def enrich(self, normalized_emails: list[NormalizedEmail]) -> list[NormalizedEmail]:
        """Apply semantic enrichment to a batch of normalized emails."""
        if not self._enabled or not normalized_emails:
            return normalized_emails

        enriched: list[NormalizedEmail] = []
        for normalized_email in normalized_emails:
            enriched.append(self._enrich_single(normalized_email))
        return enriched

    def _enrich_single(self, normalized_email: NormalizedEmail) -> NormalizedEmail:
        try:
            classification = self._classifier.classify(normalized_email)
        except (BaldwinError, ValueError, TypeError):
            _logger.exception(
                "Semantic classification failed; continuing without semantic enrichment: "
                "fingerprint=%r",
                normalized_email.fingerprint,
            )
            return normalized_email

        if classification is None:
            return normalized_email

        decision = self._policy.evaluate(classification)
        folder_keywords = {
            folder_name: values.copy()
            for folder_name, values in normalized_email.folder_keywords.items()
        }

        for folder_name in normalized_email.folders:
            existing_keywords = folder_keywords.get(folder_name, []).copy()
            for keyword in decision.auto_applied_keywords:
                if keyword not in existing_keywords:
                    existing_keywords.append(keyword)
            if existing_keywords:
                folder_keywords[folder_name] = existing_keywords

        semantic_annotations = {
            "provider": classification.provider,
            "model_name": classification.model_name,
            "summary": classification.summary,
            "review_required": decision.review_required,
            "approved_keywords": list(decision.approved_keywords),
            "rejected_keywords": list(decision.rejected_keywords),
            "auto_applied_keywords": list(decision.auto_applied_keywords),
            "suggestions": [
                {
                    "keyword": _normalize_keyword(suggestion.keyword),
                    "confidence": suggestion.confidence,
                    "rationale": suggestion.rationale,
                }
                for suggestion in classification.suggested_keywords
                if _normalize_keyword(suggestion.keyword)
            ],
        }

        return cast(
            NormalizedEmail,
            replace(
                normalized_email,
                folder_keywords=folder_keywords,
                semantic_annotations=semantic_annotations,
            ),
        )


def build_semantic_enricher(environ: Mapping[str, str]) -> SemanticEnricher:
    """Build semantic enrichment wiring from environment configuration."""
    enabled = _parse_bool(environ.get("SEMANTIC_ENRICHMENT_ENABLED"), default=False)

    auto_apply_raw = environ.get("SEMANTIC_AUTO_APPLY_MIN_CONFIDENCE")
    if not auto_apply_raw:
        auto_apply_threshold = DEFAULT_AUTO_APPLY_MIN_CONFIDENCE
    else:
        try:
            auto_apply_threshold = float(auto_apply_raw)
        except ValueError as exc:
            raise BaldwinConfigurationError(
                "App setting 'SEMANTIC_AUTO_APPLY_MIN_CONFIDENCE' must be a number between 0 and 1."
            ) from exc

    allowed_keywords = _parse_csv(environ.get("SEMANTIC_ALLOWED_KEYWORDS"))
    return SemanticEnricher(
        enabled=enabled,
        classifier=NullSemanticClassifier(),
        policy=SemanticKeywordPolicy(
            allowed_keywords=allowed_keywords,
            auto_apply_min_confidence=auto_apply_threshold,
        ),
    )


def _normalize_keyword(value: str) -> str:
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return ""
    if not normalized.startswith("$"):
        return f"${normalized}"
    return normalized


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []

    values: list[str] = []
    for raw in value.split(","):
        candidate = raw.strip()
        if candidate:
            values.append(candidate)
    return values


def _dedupe_preserving_order(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped
