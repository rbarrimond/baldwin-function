"""HTTP contract tests for the Azure Function endpoints."""

import importlib
import imaplib
import json
import os
import smtplib
import sys
import unittest
from unittest.mock import MagicMock, patch

import azure.functions as func
from azure.core.exceptions import AzureError

import function_app
from baldwin.email import EmailDeliveryError, EmailFetchError, MailboxFolders
from baldwin.exceptions import ImapErrorCode, ImapReasonCategory


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

    def test_queue_name_defaults_are_consistent(self) -> None:
        """Queue and poison queue names should derive from the same base default."""
        self.assertEqual(function_app.SCAN_MAIL_QUEUE_NAME, "scan-mail-jobs")
        self.assertEqual(function_app.SCAN_MAIL_POISON_QUEUE_NAME, "scan-mail-jobs-poison")

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
                raise EmailFetchError(
                    "Failed to fetch emails from IMAP folders: INBOX, Archive.",
                    error_code=ImapErrorCode.IMAP_LOGIN_FAILED,
                    reason_category=ImapReasonCategory.AUTH,
                    folders=("INBOX", "Archive"),
                ) from exc

        with patch.object(function_app.HANDLERS.ingestion_service, "ingest_mailbox", side_effect=raise_imap_failure):
            response = function_app.scan_mail(
                _json_request("GET", "http://localhost/api/scan-mail", params={"days": "1"})
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            json.loads(response.get_body()),
            {
                "error": "Unable to process one or more requested IMAP folders.",
                "error_code": "IMAP_LOGIN_FAILED",
                "reason_category": "auth",
                "folders": ["INBOX", "Archive"],
            },
        )

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

    def test_process_scan_folder_poison_forwards_payload_to_handlers(self) -> None:
        """Poison queue trigger should forward decoded payload to the poison handler."""

        class _Message:
            id = "msg-1"

            @staticmethod
            def get_body() -> bytes:
                """Simulate a poison message payload with job and folder details."""
                return b'{"job_id":"job-1","folder":"Archive","days":30}'

        with patch.object(function_app.HANDLERS, "process_poison_folder_job") as poison_handler:
            function_app.process_scan_folder_poison(_Message())

        poison_handler.assert_called_once_with(
            {"job_id": "job-1", "folder": "Archive", "days": 30}
        )

    def test_process_scan_folder_forwards_payload_to_handlers(self) -> None:
        """Primary queue trigger should forward decoded payload to the folder-job handler."""

        class _Message:
            id = "msg-2"
            dequeue_count = 3

            @staticmethod
            def get_body() -> bytes:
                """Simulate a queue message payload with job and folder details."""
                return b'{"job_id":"job-2","folder":"Inbox","days":14}'

        with patch.object(function_app.HANDLERS, "process_folder_job") as folder_handler:
            function_app.process_scan_folder(_Message())

        folder_handler.assert_called_once_with(
            {"job_id": "job-2", "folder": "Inbox", "days": 14}
        )

    def test_cleanup_scan_jobs_forwards_to_handlers(self) -> None:
        """Timer trigger wrapper should delegate cleanup work to handlers."""
        with patch.object(function_app.HANDLERS, "cleanup_scan_jobs") as cleanup_handler:
            function_app.cleanup_scan_jobs(MagicMock())

        cleanup_handler.assert_called_once_with()

    def test_enqueue_scan_marks_unsent_folders_failed_when_queue_send_breaks(self) -> None:
        """Queue send failures should mark only unsent folders failed for status correctness."""

        class _QueueClient:
            def __init__(self) -> None:
                self._calls = 0

            def create_queue(self) -> None:
                """Simulate successful queue creation."""
                return None

            def send_message(self, _payload: str) -> None:
                """Simulate a transient queue send failure after the first message."""
                self._calls += 1
                if self._calls > 1:
                    raise AzureError("queue send failed")

        request = _json_request(
            "POST",
            "http://localhost/api/scan-mail",
            params={"days": "1", "folders": "INBOX,Archive,Bulk"},
        )

        store = MagicMock()
        store.mark_folders_failed.return_value = 2

        with patch.dict(os.environ, {"AzureWebJobsStorage": "UseDevelopmentStorage=true"}, clear=False):
            with patch.object(function_app.HANDLERS, "_get_scan_job_store", return_value=store):
                with patch("baldwin.http_handlers.QueueClient.from_connection_string", return_value=_QueueClient()):
                    response = function_app.enqueue_scan(request)

        self.assertEqual(response.status_code, 500)
        store.mark_folders_failed.assert_called_once()
        unsent = store.mark_folders_failed.call_args.args[1]
        self.assertEqual(unsent, ["Archive", "Bulk"])

    def test_enqueue_scan_finalizes_when_all_folders_unsent(self) -> None:
        """If no folder messages are sent, enqueue failure should finalize the job immediately."""

        class _QueueClient:
            @staticmethod
            def create_queue() -> None:
                """Simulate successful queue creation."""
                return None

            @staticmethod
            def send_message(_payload: str) -> None:
                """Simulate a queue send failure for all folders."""
                raise AzureError("queue unavailable")

        request = _json_request(
            "POST",
            "http://localhost/api/scan-mail",
            params={"days": "1", "folders": "INBOX,Archive"},
        )

        store = MagicMock()
        store.mark_folders_failed.return_value = 0

        with patch.dict(os.environ, {"AzureWebJobsStorage": "UseDevelopmentStorage=true"}, clear=False):
            with patch.object(function_app.HANDLERS, "_get_scan_job_store", return_value=store):
                with patch.object(function_app.HANDLERS, "_finalize_folder_job") as finalize_job:
                    with patch("baldwin.http_handlers.QueueClient.from_connection_string", return_value=_QueueClient()):
                        response = function_app.enqueue_scan(request)

        self.assertEqual(response.status_code, 500)
        finalize_job.assert_called_once()

    def test_enqueue_scan_returns_202_when_all_folder_messages_sent(self) -> None:
        """Successful enqueue should return 202 and avoid failure bookkeeping."""

        class _QueueClient:
            def __init__(self) -> None:
                self.sent_payloads: list[str] = []

            @staticmethod
            def create_queue() -> None:
                """Simulate successful queue creation."""
                return None

            def send_message(self, payload: str) -> None:
                """Simulate successful sends for all folders."""
                self.sent_payloads.append(payload)

        queue_client = _QueueClient()
        request = _json_request(
            "POST",
            "http://localhost/api/scan-mail",
            params={"days": "1", "folders": "INBOX,Archive"},
        )

        store = MagicMock()

        with patch.dict(os.environ, {"AzureWebJobsStorage": "UseDevelopmentStorage=true"}, clear=False):
            with patch.object(function_app.HANDLERS, "_get_scan_job_store", return_value=store):
                with patch("baldwin.http_handlers.QueueClient.from_connection_string", return_value=queue_client):
                    response = function_app.enqueue_scan(request)

        self.assertEqual(response.status_code, 202)
        payload = json.loads(response.get_body())
        self.assertEqual(payload["folder_count"], 2)
        self.assertIn("job_id", payload)
        self.assertEqual(len(queue_client.sent_payloads), 2)
        store.mark_folders_failed.assert_not_called()

    def test_get_scan_status_returns_400_for_invalid_job_id(self) -> None:
        """Invalid UUID route params should be rejected as client errors before DB lookup."""
        request = func.HttpRequest(
            method="GET",
            url="http://localhost/api/scan-mail/status/not-a-uuid",
            headers={},
            params={},
            route_params={"job_id": "261bff6-10c7-43df-808c-585fe3f3771d"},
            body=b"",
        )

        with patch.object(function_app.HANDLERS, "_get_scan_job_store") as get_store:
            response = function_app.get_scan_status(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.get_body()),
            {"error": "job_id must be a valid UUID."},
        )
        get_store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
