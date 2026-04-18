"""Unit tests for inbox normalization and vector generation."""

import unittest

from baldwin.email import Email, EmailNormalizer, HashingVectorizer, PostgresEmailVectorStore


class EmailNormalizerTests(unittest.TestCase):
    """Behavioral tests for email normalization."""

    def test_message_id_drives_stable_fingerprint(self) -> None:
        """The message id should be preserved and used as the stable identity input."""
        email_message = Email(
            id="<message-123@example.com>",
            subject="Subject",
            sender="sender@example.com",
            to=["recipient@example.com"],
            cc=None,
            bcc=None,
            reply_to=None,
            date="Fri, 11 Apr 2026 09:15:00 +0000",
            body="First line\n\nSecond line",
            headers={"Message-ID": "<message-123@example.com>"},
        )

        normalized = EmailNormalizer().normalize(email_message)

        self.assertEqual(normalized.source_message_id, "<message-123@example.com>")
        self.assertEqual(normalized.recipients, ["recipient@example.com"])
        self.assertEqual(normalized.folders, [])
        self.assertIn("Subject", normalized.searchable_text)
        self.assertIn("First line Second line", normalized.searchable_text)

    def test_fallback_fingerprint_is_deterministic(self) -> None:
        """Fallback fingerprints should remain stable across identical inputs."""
        email_message = Email(
            id=None,
            subject="Status update",
            sender="sender@example.com",
            to=None,
            cc=None,
            bcc=None,
            reply_to=None,
            date="Fri, 11 Apr 2026 09:15:00 +0000",
            body="Body text",
            headers={},
        )

        normalizer = EmailNormalizer()
        first = normalizer.normalize(email_message)
        second = normalizer.normalize(email_message)

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.content_checksum, second.content_checksum)

    def test_invalid_dates_are_preserved_as_missing_sent_timestamp(self) -> None:
        """Invalid email dates should degrade to a missing sent_at value."""
        email_message = Email(
            id="<message-123@example.com>",
            subject="Subject",
            sender="sender@example.com",
            to=["recipient@example.com"],
            cc=None,
            bcc=None,
            reply_to=None,
            date="not-a-real-email-date",
            body="Body text",
            headers={"Message-ID": "<message-123@example.com>"},
        )

        normalized = EmailNormalizer().normalize(email_message)

        self.assertIsNone(normalized.sent_at)

    def test_merge_duplicates_preserves_folder_provenance(self) -> None:
        """Duplicate normalized emails should collapse into one record with ordered folder provenance."""
        normalizer = EmailNormalizer()
        inbox_email = normalizer.normalize(
            Email(
                id="<message-123@example.com>",
                subject="Subject",
                sender="sender@example.com",
                to=["recipient@example.com"],
                cc=None,
                bcc=None,
                reply_to=None,
                date="Fri, 11 Apr 2026 09:15:00 +0000",
                body="Body text",
                headers={"Message-ID": "<message-123@example.com>"},
                folder="INBOX",
            )
        )
        archive_email = normalizer.normalize(
            Email(
                id="<message-123@example.com>",
                subject="Subject",
                sender="sender@example.com",
                to=["recipient@example.com"],
                cc=None,
                bcc=None,
                reply_to=None,
                date="Fri, 11 Apr 2026 09:15:00 +0000",
                body="Body text",
                headers={"Message-ID": "<message-123@example.com>"},
                folder="Archive",
            )
        )

        merged = normalizer.merge_duplicates([inbox_email, archive_email])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].folders, ["INBOX", "Archive"])


class HashingVectorizerTests(unittest.TestCase):
    """Behavioral tests for deterministic vector generation."""

    def test_vectorizer_is_deterministic_and_normalized(self) -> None:
        """Repeated calls for the same text should produce the same normalized vector."""
        vectorizer = HashingVectorizer(dimensions=32)

        first = vectorizer.vectorize("Alpha beta beta")
        second = vectorizer.vectorize("Alpha beta beta")

        self.assertEqual(len(first), 32)
        self.assertEqual(first, second)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0, places=6)


class PostgresEmailVectorStoreTests(unittest.TestCase):
    """Behavioral tests for adapting normalized emails into generic vector documents."""

    def test_to_document_maps_email_fields_into_generic_payload(self) -> None:
        """The email store should translate a normalized email into a vector document."""
        normalized_email = EmailNormalizer().normalize(
            Email(
                id="<message-123@example.com>",
                subject="Subject",
                sender="sender@example.com",
                to=["recipient@example.com"],
                cc=["cc@example.com"],
                bcc=None,
                reply_to=["reply@example.com"],
                date="Fri, 11 Apr 2026 09:15:00 +0000",
                body="First line\n\nSecond line",
                headers={"Message-ID": "<message-123@example.com>", "X-Test": "1"},
                folder="Archive",
            )
        )

        document = PostgresEmailVectorStore.to_document(normalized_email)

        self.assertEqual(document.document_key, normalized_email.fingerprint)
        self.assertEqual(document.source_type, "email")
        self.assertEqual(document.source_id, "<message-123@example.com>")
        self.assertEqual(document.title, "Subject")
        self.assertEqual(document.body, "First line Second line")
        self.assertEqual(document.searchable_text, normalized_email.searchable_text)
        self.assertEqual(document.content_checksum, normalized_email.content_checksum)
        self.assertEqual(document.metadata["sender"], "sender@example.com")
        self.assertEqual(document.metadata["folder"], "Archive")
        self.assertEqual(document.metadata["folders"], ["Archive"])
        self.assertEqual(
            document.metadata["recipients"],
            ["recipient@example.com", "cc@example.com", "reply@example.com"],
        )
        self.assertEqual(document.metadata["headers"]["X-Test"], "1")


if __name__ == "__main__":
    unittest.main()
