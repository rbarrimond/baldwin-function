"""
Baldwin Inbox Assistant - Azure Function App

This module defines HTTP-triggered Azure Functions used to scan, summarize,
digest, and email inbox messages. The functions are designed
to be called via a Custom GPT or external automation pipeline.

Endpoints:
- GET  /api/scan-mail        - IMAP email fetcher
- POST /api/summarize-email - Summarizes individual email body
- POST /api/build-digest    - Formats summaries into Markdown digest
- POST /api/send-digest     - SMTP-based email dispatch
"""

# pylint: disable=line-too-long

import imaplib
import json
import logging
import os
import re
import smtplib
from email.message import EmailMessage

import azure.functions as func
from azure.core.exceptions import AzureError, ResourceExistsError
from azure.functions import HttpRequest, HttpResponse
from azure.storage.blob import BlobServiceClient

from baldwin.email import EmailService

app = func.FunctionApp()

JSON_MIMETYPE = "application/json"
MARKDOWN_MIMETYPE = "text/markdown"
DEFAULT_EMAIL_CONTAINER = "emails"
DEFAULT_SUMMARY_WORD_LIMIT = 48
INTERNAL_SERVER_ERROR_MESSAGE = "Internal server error."


def _json_response(payload: dict | list, status_code: int = 200) -> HttpResponse:
    return HttpResponse(
        json.dumps(payload),
        status_code=status_code,
        mimetype=JSON_MIMETYPE,
    )


def _get_required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"App setting '{name}' is required.")
    return value


def _parse_days(req: HttpRequest) -> int:
    raw_days = req.params.get("days", "1")
    days = int(raw_days)
    if days < 1 or days > 30:
        raise ValueError("The 'days' query parameter must be between 1 and 30.")
    return days


def _email_to_dict(email_message) -> dict:
    if hasattr(email_message, "model_dump"):
        return email_message.model_dump()
    return email_message.dict()


def _build_blob_name(email_payload: dict) -> str:
    subject = re.sub(r"[^A-Za-z0-9._-]+", "_", email_payload.get("subject") or "email")
    timestamp = re.sub(r"[^A-Za-z0-9._-]+", "_", email_payload.get("date") or "unknown-date")
    return f"{timestamp}_{subject.strip('_') or 'email'}.json"


def _persist_emails(email_payloads: list[dict]) -> None:
    connection_string = os.getenv("AzureWebJobsStorage")
    if not connection_string:
        logging.info("Skipping blob persistence because AzureWebJobsStorage is not configured.")
        return

    container_name = os.getenv("EMAILS_CONTAINER", DEFAULT_EMAIL_CONTAINER)
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service_client.get_container_client(container_name)

    try:
        container_client.create_container()
    except ResourceExistsError:
        pass

    for email_payload in email_payloads:
        blob_client = container_client.get_blob_client(_build_blob_name(email_payload))
        blob_client.upload_blob(json.dumps(email_payload), overwrite=True)


def _summarize_text(body: str) -> str:
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


def _normalize_digest_items(summaries: list) -> list[str]:
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


def _send_digest_email(to_address: str, subject: str, content: str) -> str:
    smtp_server = _get_required_setting("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_address = os.getenv("SMTP_FROM", smtp_username or "no-reply@localhost")

    message = EmailMessage()
    message["To"] = to_address
    message["From"] = from_address
    message["Subject"] = subject
    message.set_content(content)

    with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as smtp_client:
        smtp_client.ehlo()
        smtp_client.starttls()
        smtp_client.ehlo()

        if smtp_username and smtp_password:
            smtp_client.login(smtp_username, smtp_password)

        smtp_client.send_message(message)

    return from_address

@app.function_name(name="scan_mail")
@app.route(route="scan-mail", methods=["GET"])
def scan_mail(req: HttpRequest) -> HttpResponse:
    """
    Fetches recent IMAP emails.

    Query Parameters:
        - days (int, optional): Number of past days to fetch emails from.

    Returns:
        JSON response containing a list of email-like objects.
    """
    try:
        days = _parse_days(req)
        imap_user = _get_required_setting("IMAP_USER")
        imap_pass = _get_required_setting("IMAP_PASSWORD")

        email_service = EmailService(imap_user, imap_pass)
        emails = email_service.fetch_emails(days)
        email_payloads = [_email_to_dict(email_message) for email_message in emails]
        _persist_emails(email_payloads)
        return _json_response(email_payloads)
    except ValueError as exc:
        logging.warning("Invalid request for scan_mail: %s", exc)
        return _json_response({"error": str(exc)}, status_code=400)
    except imaplib.IMAP4.error as exc:
        logging.warning("IMAP request failed for scan_mail: %s", exc)
        return _json_response({"error": "Unable to read from the IMAP inbox."}, status_code=502)
    except (AzureError, RuntimeError, OSError):
        logging.exception("Unexpected error in scan_mail")
        return _json_response({"error": INTERNAL_SERVER_ERROR_MESSAGE}, status_code=500)

@app.function_name(name="summarize_email")
@app.route(route="summarize-email", methods=["POST"])
def summarize_email(req: HttpRequest) -> HttpResponse:
    """
    Generates a summary for a given email body.

    Request Body:
        - body (str): The content of the email to summarize.

    Returns:
        JSON response containing a summary string.
    """
    try:
        data = req.get_json()
        summary = _summarize_text(str(data.get("body", "")))
        return _json_response({"summary": summary})
    except ValueError as exc:
        logging.warning("Invalid request for summarize_email: %s", exc)
        return _json_response({"error": str(exc)}, status_code=400)

@app.function_name(name="build_digest")
@app.route(route="build-digest", methods=["POST"])
def build_digest(req: HttpRequest) -> HttpResponse:
    """
    Builds a Markdown-formatted digest from email summaries.

    Request Body:
        - summaries (list): List of email summaries.
        - audience (str): Identifier for the digest recipient (e.g., 'robert', 'lisa').

    Returns:
        Markdown response string representing the digest.
    """
    try:
        data = req.get_json()
        summaries = _normalize_digest_items(data.get("summaries", []))
        audience = data.get("audience", "robert")
        digest = f"## Daily Digest for {audience.capitalize()}\n\n"
        for item in summaries:
            digest += f"- {item}\n"
        return HttpResponse(digest, mimetype=MARKDOWN_MIMETYPE)
    except ValueError as exc:
        logging.warning("Invalid request for build_digest: %s", exc)
        return _json_response({"error": str(exc)}, status_code=400)

@app.function_name(name="send_digest")
@app.route(route="send-digest", methods=["POST"])
def send_digest(req: HttpRequest) -> HttpResponse:
    """
    Sends a prepared digest email to a specified recipient.

    Request Body:
        - to (str): Recipient email address.
        - subject (str): Email subject line.
        - content (str): Email body content.

    Returns:
        JSON response confirming dispatch.
    """
    try:
        data = req.get_json()
        to = data.get("to")
        subject = data.get("subject")
        content = data.get("content")
        if not all([to, subject, content]):
            raise ValueError("Recipient, subject, and content are required to send a digest.")
        from_address = _send_digest_email(to, subject, content)
        return _json_response({"status": "sent", "from": from_address})
    except ValueError as exc:
        logging.warning("Invalid request for send_digest: %s", exc)
        return _json_response({"error": str(exc)}, status_code=400)
    except smtplib.SMTPException as exc:
        logging.warning("SMTP request failed for send_digest: %s", exc)
        return _json_response({"error": "Unable to send the digest email."}, status_code=502)
    except OSError:
        logging.exception("Unexpected error in send_digest")
        return _json_response({"error": INTERNAL_SERVER_ERROR_MESSAGE}, status_code=500)
