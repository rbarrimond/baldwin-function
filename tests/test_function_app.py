"""HTTP contract tests for the Azure Function endpoints."""

import importlib
import imaplib
import json
import os
import smtplib
import sys
import unittest
from unittest.mock import patch

import azure.functions as func

import function_app
from baldwin.email import EmailDeliveryError, EmailFetchError, MailboxFolders


def _json_request(method: str, url: str, payload: dict | None = None, params: dict | None = None) -> func.HttpRequest:
    """Helper to create an HttpRequest with a JSON body and query parameters."""
    return func.HttpRequest(
        method=method,
        url=url,
        headers={"Content-Type": "application/json"},
        params=params or {},
        route_params={},
        body=json.dumps(payload or {}).encode("utf-8"),
    )


class FunctionAppEndpointTests(unittest.TestCase):
    """HTTP-level regression tests for function handlers."""

    def test_summarize_email_returns_summary_payload(self) -> None:
        """The summarize_email endpoint should return a JSON payload with the generated summary."""
        response = function_app.summarize_email(
            _json_request(
                "POST",
                "http://localhost/api/summarize-email",
                {"body": "Agenda for tomorrow. Please review the contract."},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.get_body()), {"summary": "Agenda for tomorrow."})

    def test_build_digest_returns_markdown_response(self) -> None:
        """The build_digest endpoint should return a Markdown-formatted digest in the response body."""
        response = function_app.build_digest(
            _json_request(
                "POST",
                "http://localhost/api/build-digest",
                {"summaries": ["One", {"summary": "Two"}], "audience": "robert"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("## Daily Digest for Robert", response.get_body().decode("utf-8"))
        self.assertIn("- One", response.get_body().decode("utf-8"))
        self.assertEqual(response.mimetype, "text/markdown")

    def test_scan_mail_returns_502_for_imap_failures(self) -> None:
        """The scan_mail endpoint should return a 502 status code with a generic error message
        if the ingestion service raises an EmailFetchError due to IMAP issues."""
        def raise_imap_failure(days: int, folders: MailboxFolders) -> dict:
            del days, folders
            try:
                raise imaplib.IMAP4.error("invalid credentials")
            except imaplib.IMAP4.error as exc:
                raise EmailFetchError("Failed to fetch emails from IMAP folders: INBOX, Archive.") from exc

        with patch.object(function_app.HANDLERS.ingestion_service, "ingest_mailbox", side_effect=raise_imap_failure):
            response = function_app.scan_mail(
                _json_request("GET", "http://localhost/api/scan-mail", params={"days": "1"})
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(response.get_body()), {"error": "Unable to read from the requested IMAP folders."})

    def test_scan_mail_passes_requested_folders_to_service(self) -> None:
        """The scan_mail endpoint should pass the requested IMAP folders to the ingestion service."""
        with patch.object(function_app.HANDLERS.ingestion_service, "ingest_mailbox", return_value={"total_fetched": 0, "total_normalized": 0, "total_deduped": 0, "persisted": []}) as ingest_mailbox:
            response = function_app.scan_mail(
                _json_request("GET", "http://localhost/api/scan-mail", params={"days": "1", "folders": "INBOX,Archive"})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ingest_mailbox.call_args.args[0], 1)
        self.assertEqual(ingest_mailbox.call_args.args[1].folders, ("INBOX", "Archive"))

    def test_function_app_import_does_not_require_database_url(self) -> None:
        """Importing function_app should not require DATABASE_URL before scan-mail is invoked."""
        original_module = sys.modules.pop("function_app", None)
        try:
            with patch.dict(os.environ, {}, clear=True):
                imported_module = importlib.import_module("function_app")
            self.assertTrue(hasattr(imported_module, "HANDLERS"))
        finally:
            sys.modules.pop("function_app", None)
            if original_module is not None:
                sys.modules["function_app"] = original_module

    def test_scan_mail_returns_400_for_invalid_days_parameter(self) -> None:
        """The scan_mail endpoint should translate Baldwin validation errors into a 400 response."""
        response = function_app.scan_mail(
            _json_request("GET", "http://localhost/api/scan-mail", params={"days": "abc"})
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.get_body()),
            {"error": "The 'days' query parameter must be an integer, got 'abc'."},
        )

    def test_send_digest_returns_502_for_smtp_failures(self) -> None:
        """The send_digest endpoint should return a 502 status code with a generic error message
        if the digest delivery service raises an EmailDeliveryError due to SMTP issues."""
        def raise_smtp_failure(to_address: str, subject: str, content: str) -> str:
            del to_address, subject, content
            try:
                raise smtplib.SMTPException("send failed")
            except smtplib.SMTPException as exc:
                raise EmailDeliveryError("Unable to send the digest email.") from exc

        with patch.object(function_app.HANDLERS.digest_delivery_service, "send", side_effect=raise_smtp_failure):
            response = function_app.send_digest(
                _json_request(
                    "POST",
                    "http://localhost/api/send-digest",
                    {"to": "user@example.com", "subject": "Digest", "content": "Body"},
                )
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(response.get_body()), {"error": "Unable to send the digest email."})

    def test_send_digest_returns_400_for_missing_fields(self) -> None:
        """The send_digest endpoint should return a 400 status code with a generic error message
        if the request is missing required fields (to, subject, or content)."""
        response = function_app.send_digest(
            _json_request("POST", "http://localhost/api/send-digest", {"to": "user@example.com"})
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.get_body()),
            {"error": "Recipient, subject, and content are required to send a digest."},
        )


if __name__ == "__main__":
    unittest.main()
