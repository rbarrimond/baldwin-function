"""Unit tests for mailbox vectorization script helpers."""

import argparse
import unittest
from unittest.mock import patch

from baldwin.email import Email
from scripts.vectorize_mailbox import _build_progress_label, _format_chunking_status, _load_settings, _normalize_emails


class VectorizeInboxLoggingTests(unittest.TestCase):
    """Coverage for script-level chunking log formatting."""

    def test_format_chunking_status_ignores_non_chunked_metadata(self) -> None:
        """Single-chunk or missing metadata should not emit a chunking log line."""
        self.assertIsNone(_format_chunking_status({}))
        self.assertIsNone(_format_chunking_status({"chunk_count": 1}))

    def test_format_chunking_status_includes_count_and_max_length(self) -> None:
        """Chunked metadata should surface the chunk count and largest chunk size."""
        status = _format_chunking_status({"chunk_count": 3, "chunk_lengths": [120, 80, 95]})

        self.assertEqual(status, "chunked=3 max_chunk_length=120")

    def test_build_progress_label_prefixes_folder_when_present(self) -> None:
        """Progress labels should include folder provenance when available."""
        label = _build_progress_label(
            type("NormalizedEmail", (), {"subject": "Subject", "folders": ["Archive", "Receipts"]})()
        )

        self.assertEqual(label, "[Archive,Receipts] Subject")


class VectorizeInboxSettingsTests(unittest.TestCase):
    """Coverage for script-level mailbox folder configuration."""

    @patch.dict(
        "os.environ",
        {
            "IMAP_USER": "user@example.com",
            "IMAP_PASSWORD": "password",
            "DATABASE_URL": "postgresql://localhost/test",
            "IMAP_FOLDERS": "INBOX,Archive",
        },
        clear=True,
    )
    def test_load_settings_prefers_cli_folder_selection(self) -> None:
        """CLI folder selection should override environment variable configuration."""
        args = argparse.Namespace(
            days=1,
            folders=["Receipts", "Archive"],
            embedding_provider="ollama",
            embedding_base_url="http://127.0.0.1:11434",
            dimensions=256,
            model_name="qllama/bge-small-en-v1.5",
            embedding_timeout_seconds=30.0,
            fallback_provider="hashing",
            dry_run=True,
        )

        settings = _load_settings(args)

        self.assertEqual(settings.imap_folders.folders, ("Receipts", "Archive"))

    def test_normalize_emails_collapses_duplicate_folders(self) -> None:
        """Emails with the same Message-ID but different folders should be normalized into a single record with combined folder provenance."""
        emails = [
            Email(
                id="<message-123@example.com>",
                subject="Subject",
                sender="sender@example.com",
                to=["recipient@example.com"],
                cc=None,
                bcc=None,
                reply_to=None,
                date="Fri, 11 Apr 2026 09:15:00 +0000",
                body="Body",
                headers={"Message-ID": "<message-123@example.com>"},
                folder="INBOX",
            ),
            Email(
                id="<message-123@example.com>",
                subject="Subject",
                sender="sender@example.com",
                to=["recipient@example.com"],
                cc=None,
                bcc=None,
                reply_to=None,
                date="Fri, 11 Apr 2026 09:15:00 +0000",
                body="Body",
                headers={"Message-ID": "<message-123@example.com>"},
                folder="Archive",
            ),
        ]

        normalized = _normalize_emails(emails, __import__("baldwin.email", fromlist=["EmailNormalizer"]).EmailNormalizer())

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].folders, ["INBOX", "Archive"])


if __name__ == "__main__":
    unittest.main()
