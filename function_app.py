"""
Baldwin Mail Assistant - Azure Function App

This module defines HTTP-triggered Azure Functions used to scan IMAP folders,
summarize messages, build digests, and email completed digests. The functions
are designed to be called via a Custom GPT or external automation pipeline.

Endpoints:
- GET  /api/scan-mail        - IMAP mailbox ingestion and vector persistence summary
- POST /api/summarize-email - Summarizes individual email body
- POST /api/build-digest    - Formats summaries into Markdown digest
- POST /api/send-digest     - SMTP-based email dispatch
"""

import azure.functions as func
from azure.functions import HttpRequest, HttpResponse
from baldwin.http_handlers import build_http_handlers

app = func.FunctionApp()
HANDLERS = build_http_handlers()

@app.function_name(name="scan_mail")
@app.route(route="scan-mail", methods=["GET"])
def scan_mail(req: HttpRequest) -> HttpResponse:
    """Ingest recent IMAP emails from one or more folders."""
    return HANDLERS.scan_mail(req)

@app.function_name(name="summarize_email")
@app.route(route="summarize-email", methods=["POST"])
def summarize_email(req: HttpRequest) -> HttpResponse:
    """Generate a summary for a request body."""
    return HANDLERS.summarize_email(req)

@app.function_name(name="build_digest")
@app.route(route="build-digest", methods=["POST"])
def build_digest(req: HttpRequest) -> HttpResponse:
    """Build a Markdown digest from summaries."""
    return HANDLERS.build_digest(req)

@app.function_name(name="send_digest")
@app.route(route="send-digest", methods=["POST"])
def send_digest(req: HttpRequest) -> HttpResponse:
    """Send a prepared digest email."""
    return HANDLERS.send_digest(req)
