"""HTTP contract tests for the Azure Function endpoints."""

import imaplib
import json
import smtplib
import unittest
from unittest.mock import patch

import azure.functions as func

import function_app
from baldwin.email import EmailDeliveryError, EmailFetchError


def _json_request(method: str, url: str, payload: dict | None = None, params: dict | None = None) -> func.HttpRequest:
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
        def raise_imap_failure(days: int) -> list[dict]:
            del days
            try:
                raise imaplib.IMAP4.error("invalid credentials")
            except imaplib.IMAP4.error as exc:
                raise EmailFetchError("Failed to fetch emails from the IMAP inbox.") from exc

        with patch.object(function_app.HANDLERS.mailbox_scan_service, "fetch_recent_emails", side_effect=raise_imap_failure):
            response = function_app.scan_mail(
                _json_request("GET", "http://localhost/api/scan-mail", params={"days": "1"})
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(response.get_body()), {"error": "Unable to read from the IMAP inbox."})

    def test_send_digest_returns_502_for_smtp_failures(self) -> None:
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