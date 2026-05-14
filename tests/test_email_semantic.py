"""Unit tests for semantic email enrichment policy behavior."""

from __future__ import annotations

import unittest

from baldwin.email.semantic import (
    SemanticClassification,
    SemanticEnricher,
    SemanticKeywordPolicy,
    SemanticKeywordSuggestion,
)
from baldwin.email.vectorization import NormalizedEmail


class _DeterministicClassifier:
    """Classifier stub returning a fixed classification response."""

    def __init__(self, classification: SemanticClassification | None):
        self._classification = classification

    def classify(self, normalized_email: NormalizedEmail) -> SemanticClassification | None:
        """Return the predetermined classification, ignoring the input email."""
        del normalized_email
        return self._classification


class SemanticEnricherTests(unittest.TestCase):
    """Behavioral tests for semantic keyword policy and enrichment flow."""

    def _build_email(self) -> NormalizedEmail:
        return NormalizedEmail(
            fingerprint="abc",
            source_message_id="<id@example.com>",
            subject="Subject",
            sender="sender@example.com",
            recipients=["recipient@example.com"],
            raw_date="Fri, 11 Apr 2026 09:15:00 +0000",
            sent_at="2026-04-11T09:15:00+00:00",
            folders=["INBOX"],
            folder_uids={"INBOX": 42},
            body="Body",
            searchable_text="Subject\n\nBody",
            content_checksum="checksum",
            headers={"Message-ID": "<id@example.com>"},
            folder_flags={},
            folder_keywords={},
        )

    def test_enricher_applies_allowlisted_high_confidence_keywords(self) -> None:
        """Allowed high-confidence keywords should be projected into folder keywords."""
        classifier = _DeterministicClassifier(
            SemanticClassification(
                provider="test-provider",
                model_name="test-model",
                summary="Action required",
                review_required=False,
                suggested_keywords=(
                    SemanticKeywordSuggestion(keyword="$Action", confidence=0.95),
                    SemanticKeywordSuggestion(keyword="$Unapproved", confidence=0.99),
                ),
            )
        )
        policy = SemanticKeywordPolicy(
            allowed_keywords=["$Action"],
            auto_apply_min_confidence=0.9,
        )
        enricher = SemanticEnricher(enabled=True, classifier=classifier, policy=policy)

        enriched = enricher.enrich([self._build_email()])[0]

        self.assertEqual(enriched.folder_keywords["INBOX"], ["$Action"])
        self.assertEqual(enriched.semantic_annotations["approved_keywords"], ["$Action"])
        self.assertEqual(enriched.semantic_annotations["rejected_keywords"], ["$Unapproved"])

    def test_enricher_does_not_apply_keywords_when_review_required(self) -> None:
        """Review-required classifications should persist metadata without IMAP writeback."""
        classifier = _DeterministicClassifier(
            SemanticClassification(
                provider="test-provider",
                model_name="test-model",
                summary="Needs review",
                review_required=True,
                suggested_keywords=(
                    SemanticKeywordSuggestion(keyword="$Action", confidence=0.99),
                ),
            )
        )
        policy = SemanticKeywordPolicy(
            allowed_keywords=["$Action"],
            auto_apply_min_confidence=0.9,
        )
        enricher = SemanticEnricher(enabled=True, classifier=classifier, policy=policy)

        enriched = enricher.enrich([self._build_email()])[0]

        self.assertEqual(enriched.folder_keywords, {})
        self.assertEqual(enriched.semantic_annotations["auto_applied_keywords"], [])
        self.assertTrue(enriched.semantic_annotations["review_required"])


if __name__ == "__main__":
    unittest.main()
