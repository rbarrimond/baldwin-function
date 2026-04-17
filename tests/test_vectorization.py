"""Unit tests for inbox normalization and vector generation."""

import unittest

from BaldwinEmail import Email, EmailNormalizer, HashingVectorizer


class EmailNormalizerTests(unittest.TestCase):
    def test_message_id_drives_stable_fingerprint(self) -> None:
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
        self.assertIn("Subject", normalized.searchable_text)
        self.assertIn("First line Second line", normalized.searchable_text)

    def test_fallback_fingerprint_is_deterministic(self) -> None:
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


class HashingVectorizerTests(unittest.TestCase):
    def test_vectorizer_is_deterministic_and_normalized(self) -> None:
        vectorizer = HashingVectorizer(dimensions=32)

        first = vectorizer.vectorize("Alpha beta beta")
        second = vectorizer.vectorize("Alpha beta beta")

        self.assertEqual(len(first), 32)
        self.assertEqual(first, second)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()