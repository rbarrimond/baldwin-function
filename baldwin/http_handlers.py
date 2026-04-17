"""HTTP handler classes for the Baldwin Function App."""

from __future__ import annotations

import imaplib
import json
import logging
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Mapping

from azure.core.exceptions import AzureError, ResourceExistsError
from azure.functions import HttpRequest, HttpResponse
from azure.storage.blob import BlobServiceClient

from baldwin.email import DEFAULT_IMAP_FOLDER, EmailDeliveryError, EmailFetchError, EmailService, MailboxFolders

JSON_MIMETYPE = "application/json"
MARKDOWN_MIMETYPE = "text/markdown"
DEFAULT_EMAIL_CONTAINER = "emails"
DEFAULT_SUMMARY_WORD_LIMIT = 48
INTERNAL_SERVER_ERROR_MESSAGE = "Internal server error."


class EnvironmentSettings:
    """Read application settings from the process environment."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = environ or os.environ

    def get_required(self, name: str) -> str:
        """Get a required setting value or raise an error if it's missing."""
        value = self.environ.get(name)
        if not value:
            raise ValueError(f"App setting '{name}' is required.")
        return value

    def get(self, name: str, default: str | None = None) -> str | None:
        """Get an optional setting value or return a default if it's missing."""
        return self.environ.get(name, default)

    def get_int(self, name: str, default: int) -> int:
        """Get an optional integer setting value or return a default if it's missing."""
        raw_value = self.environ.get(name)
        if not raw_value:
            return default
        return int(raw_value)


@dataclass(frozen=True)
class MailScanRequest:
    """Parsed request parameters for an IMAP folder scan."""

    days: int
    folders: MailboxFolders


class MailboxRequestParser:
    """Parse HTTP request inputs for mailbox operations."""

    def __init__(self, settings: EnvironmentSettings):
        self.settings = settings

    @staticmethod
    def _parse_days(req: HttpRequest) -> int:
        raw_days = req.params.get("days", "1")
        days = int(raw_days)
        if days < 1 or days > 30:
            raise ValueError("The 'days' query parameter must be between 1 and 30.")
        return days

    def parse_scan_request(self, req: HttpRequest) -> MailScanRequest:
        """Parse the query parameters from a mailbox scan HTTP request."""
        raw_folders = req.params.get("folders") or req.params.get("folder")
        default_folders = self.settings.get("IMAP_FOLDERS")
        return MailScanRequest(
            days=self._parse_days(req),
            folders=MailboxFolders.from_values(
                [raw_folders] if raw_folders is not None else None,
                default_values=[default_folders] if default_folders else None,
            ),
        )


class ResponseFactory:
    """Create HTTP responses for the function handlers."""

    @staticmethod
    def json(payload: dict | list, status_code: int = 200) -> HttpResponse:
        """Serialize a JSON payload into an HTTP response with the appropriate content type."""
        return HttpResponse(
            json.dumps(payload),
            status_code=status_code,
            mimetype=JSON_MIMETYPE,
        )

    @staticmethod
    def markdown(content: str, status_code: int = 200) -> HttpResponse:
        """Create an HTTP response with Markdown content and the appropriate content type."""
        return HttpResponse(content, status_code=status_code, mimetype=MARKDOWN_MIMETYPE)


class EmailArchiveStore:
    """Persist scanned email payloads to blob storage when configured."""

    def __init__(self, settings: EnvironmentSettings):
        self.settings = settings

    @staticmethod
    def _build_blob_name(email_payload: dict[str, Any]) -> str:
        folder = re.sub(r"[^A-Za-z0-9._/-]+", "_", email_payload.get("folder") or DEFAULT_IMAP_FOLDER)
        subject = re.sub(r"[^A-Za-z0-9._-]+", "_", email_payload.get("subject") or "email")
        timestamp = re.sub(r"[^A-Za-z0-9._-]+", "_", email_payload.get("date") or "unknown-date")
        return f"{folder}/{timestamp}_{subject.strip('_') or 'email'}.json"

    def persist(self, email_payloads: list[dict[str, Any]]) -> None:
        """Persist email payloads to blob storage if configured, otherwise skip with a log message."""
        connection_string = self.settings.get("AzureWebJobsStorage")
        if not connection_string:
            logging.info("Skipping blob persistence because AzureWebJobsStorage is not configured.")
            return

        container_name = self.settings.get("EMAILS_CONTAINER", DEFAULT_EMAIL_CONTAINER) or DEFAULT_EMAIL_CONTAINER
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        container_client = blob_service_client.get_container_client(container_name)

        try:
            container_client.create_container()
        except ResourceExistsError:
            pass

        for email_payload in email_payloads:
            blob_client = container_client.get_blob_client(self._build_blob_name(email_payload))
            blob_client.upload_blob(json.dumps(email_payload), overwrite=True)


class MailboxScanService:
    """Fetch mailbox messages and normalize them into HTTP payloads."""

    def __init__(self, settings: EnvironmentSettings, archive_store: EmailArchiveStore):
        self.settings = settings
        self.archive_store = archive_store

    @staticmethod
    def _email_to_dict(email_message: Any) -> dict[str, Any]:
        if hasattr(email_message, "model_dump"):
            return email_message.model_dump()
        return email_message.dict()

    def fetch_recent_emails(self, days: int, folders: MailboxFolders) -> list[dict[str, Any]]:
        """Fetch recent emails from the specified IMAP folders and return them as dictionaries."""
        email_service = EmailService(
            self.settings.get_required("IMAP_USER"),
            self.settings.get_required("IMAP_PASSWORD"),
            imap_host=self.settings.get("IMAP_HOST", "imap.mail.me.com") or "imap.mail.me.com",
            imap_port=self.settings.get_int("IMAP_PORT", 993),
        )
        email_payloads = [
            self._email_to_dict(email_message)
            for email_message in email_service.fetch_emails(days, folders)
        ]
        self.archive_store.persist(email_payloads)
        return email_payloads


class SummaryService:
    """Summarize individual email bodies for the HTTP API."""

    def summarize(self, body: str) -> str:
        """Generate a concise summary of an email body, prioritizing the first sentence or truncating to a word limit."""
        normalized = " ".join(body.split())
        if not normalized:
            raise ValueError("Email body is required for summarization.")

        sentences = re.split(r"(?<=[.!?])\s+", normalized)
        first_sentence = sentences[0].strip()
        if first_sentence and len(first_sentence) <= 240:
            return first_sentence

        words = normalized.split()
        if len(words) <= DEFAULT_SUMMARY_WORD_LIMIT:
            return normalized

        return " ".join(words[:DEFAULT_SUMMARY_WORD_LIMIT]) + "..."


class DigestBuilder:
    """Build Markdown digests from summary items."""

    @staticmethod
    def _normalize_digest_items(summaries: list[Any]) -> list[str]:
        digest_items: list[str] = []
        for item in summaries:
            if isinstance(item, dict):
                summary = str(item.get("summary", "")).strip()
            else:
                summary = str(item).strip()

            if summary:
                digest_items.append(summary)

        if not digest_items:
            raise ValueError("Summaries are required to build a digest.")

        return digest_items

    def build(self, summaries: list[Any], audience: str) -> str:
        """Construct a Markdown digest from a list of summaries and an audience name."""
        digest = f"## Daily Digest for {audience.capitalize()}\n\n"
        for item in self._normalize_digest_items(summaries):
            digest += f"- {item}\n"
        return digest


class DigestDeliveryService:
    """Send digest emails over SMTP."""

    def __init__(self, settings: EnvironmentSettings):
        self.settings = settings

    def send(self, to_address: str, subject: str, content: str) -> str:
        """Send a digest email to the specified recipient with the given subject and content, returning the sender address."""
        smtp_server = self.settings.get_required("SMTP_SERVER")
        smtp_port = int(self.settings.get("SMTP_PORT", "587") or "587")
        smtp_username = self.settings.get("SMTP_USERNAME")
        smtp_password = self.settings.get("SMTP_PASSWORD")
        from_address = self.settings.get("SMTP_FROM", smtp_username or "no-reply@localhost") or "no-reply@localhost"

        message = EmailMessage()
        message["To"] = to_address
        message["From"] = from_address
        message["Subject"] = subject
        message.set_content(content)

        try:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as smtp_client:
                smtp_client.ehlo()
                smtp_client.starttls()
                smtp_client.ehlo()

                if smtp_username and smtp_password:
                    smtp_client.login(smtp_username, smtp_password)

                smtp_client.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailDeliveryError("Unable to send the digest email.") from exc

        return from_address


class MailboxHttpHandlers:
    """Translate HTTP requests into Baldwin mailbox operations."""

    def __init__(
        self,
        *,
        mailbox_scan_service: MailboxScanService,
        request_parser: MailboxRequestParser,
        summary_service: SummaryService,
        digest_builder: DigestBuilder,
        digest_delivery_service: DigestDeliveryService,
        response_factory: ResponseFactory,
    ):
        self.mailbox_scan_service = mailbox_scan_service
        self.request_parser = request_parser
        self.summary_service = summary_service
        self.digest_builder = digest_builder
        self.digest_delivery_service = digest_delivery_service
        self.response_factory = response_factory

    @staticmethod
    def _is_caused_by(exc: BaseException, expected_type: type[BaseException]) -> bool:
        return isinstance(exc.__cause__, expected_type)

    def scan_mail(self, req: HttpRequest) -> HttpResponse:
        """Handle an HTTP request to scan mailbox folders and return recent emails as JSON."""
        try:
            scan_request = self.request_parser.parse_scan_request(req)
            email_payloads = self.mailbox_scan_service.fetch_recent_emails(
                scan_request.days,
                scan_request.folders,
            )
            return self.response_factory.json(email_payloads)
        except ValueError as exc:
            logging.warning("Invalid request for scan_mail: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)
        except EmailFetchError as exc:
            if self._is_caused_by(exc, imaplib.IMAP4.error):
                logging.warning("IMAP request failed for scan_mail: %s", exc)
                return self.response_factory.json({"error": "Unable to read from the requested IMAP folders."}, status_code=502)

            logging.exception("Unexpected email fetch error in scan_mail")
            return self.response_factory.json({"error": INTERNAL_SERVER_ERROR_MESSAGE}, status_code=500)
        except AzureError:
            logging.exception("Unexpected error in scan_mail")
            return self.response_factory.json({"error": INTERNAL_SERVER_ERROR_MESSAGE}, status_code=500)

    def summarize_email(self, req: HttpRequest) -> HttpResponse:
        """Handle an HTTP request to summarize an email body and return the summary as JSON."""
        try:
            data = req.get_json()
            summary = self.summary_service.summarize(str(data.get("body", "")))
            return self.response_factory.json({"summary": summary})
        except ValueError as exc:
            logging.warning("Invalid request for summarize_email: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)

    def build_digest(self, req: HttpRequest) -> HttpResponse:
        """Handle an HTTP request to build a digest from email summaries and return it as Markdown."""
        try:
            data = req.get_json()
            digest = self.digest_builder.build(data.get("summaries", []), data.get("audience", "robert"))
            return self.response_factory.markdown(digest)
        except ValueError as exc:
            logging.warning("Invalid request for build_digest: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)

    def send_digest(self, req: HttpRequest) -> HttpResponse:
        """Handle an HTTP request to send a digest email and return the sender address as JSON."""
        try:
            data = req.get_json()
            to_address = data.get("to")
            subject = data.get("subject")
            content = data.get("content")
            if not all([to_address, subject, content]):
                raise ValueError("Recipient, subject, and content are required to send a digest.")

            from_address = self.digest_delivery_service.send(to_address, subject, content)
            return self.response_factory.json({"status": "sent", "from": from_address})
        except ValueError as exc:
            logging.warning("Invalid request for send_digest: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)
        except EmailDeliveryError as exc:
            if self._is_caused_by(exc, smtplib.SMTPException):
                logging.warning("SMTP request failed for send_digest: %s", exc)
                return self.response_factory.json({"error": "Unable to send the digest email."}, status_code=502)

            logging.exception("Unexpected email delivery error in send_digest")
            return self.response_factory.json({"error": INTERNAL_SERVER_ERROR_MESSAGE}, status_code=500)


def build_http_handlers() -> MailboxHttpHandlers:
    """Create the production HTTP handler graph for Azure Functions."""
    settings = EnvironmentSettings()
    return MailboxHttpHandlers(
        mailbox_scan_service=MailboxScanService(settings, EmailArchiveStore(settings)),
        request_parser=MailboxRequestParser(settings),
        summary_service=SummaryService(),
        digest_builder=DigestBuilder(),
        digest_delivery_service=DigestDeliveryService(settings),
        response_factory=ResponseFactory(),
    )
