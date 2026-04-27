"""Regression tests for the scan-mail flow CLI."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.scan_mail_flow import DEFAULT_SCAN_MAIL_FLOW_MAX_WORKERS, main


class ScanMailFlowCliTests(unittest.TestCase):
    """Coverage for the local scan-mail flow runner."""

    def test_cli_defaults_to_eight_workers(self) -> None:
        """The scan-mail flow CLI should default to eight workers for local runs."""
        with patch("scripts.scan_mail_flow._load_runtime_environ", return_value={}):
            with patch("scripts.scan_mail_flow.EmailIngestionService") as ingestion_service:
                ingestion_service.return_value.ingest_mailbox.return_value = {"status": "ok"}
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main([])

        self.assertEqual(exit_code, 0)
        settings = ingestion_service.call_args.args[0]
        self.assertEqual(settings.get("SCAN_MAIL_MAX_WORKERS"), str(DEFAULT_SCAN_MAIL_FLOW_MAX_WORKERS))

    def test_cli_passes_requested_inputs_to_ingestion_service(self) -> None:
        """The CLI should pass explicit days and folder input into the scan-mail service."""
        runtime_environ = {
            "IMAP_USER": "user@example.com",
            "IMAP_PASSWORD": "password",
            "DATABASE_URL": "postgresql://localhost/test",
            "IMAP_FOLDERS": "INBOX",
        }
        summary = {"total_fetched": 1, "persisted": []}

        with patch("scripts.scan_mail_flow._load_runtime_environ", return_value=runtime_environ):
            with patch("scripts.scan_mail_flow.EmailIngestionService") as ingestion_service:
                ingestion_service.return_value.ingest_mailbox.return_value = summary
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main(["--days", "2", "--folder", "Archive,Receipts", "--max-workers", "8"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            ingestion_service.return_value.ingest_mailbox.call_args.args[0],
            2,
        )
        self.assertEqual(
            ingestion_service.return_value.ingest_mailbox.call_args.args[1].folders,
            ("Archive", "Receipts"),
        )
        self.assertEqual(json.loads(stdout.getvalue()), summary)

    def test_cli_rejects_non_positive_worker_counts(self) -> None:
        """The CLI should reject invalid worker counts before invoking ingestion."""
        exit_code = main(["--max-workers", "0"])

        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
