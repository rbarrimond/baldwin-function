"""
Baldwin Mail Assistant - Azure Function App

This module defines HTTP-triggered Azure Functions used to scan IMAP folders,
summarize messages, build digests, and email completed digests. The functions
are designed to be called via a Custom GPT or external automation pipeline.

Endpoints:
- POST /api/scan-mail               - Enqueue async IMAP ingestion (per folder)
- GET  /api/scan-mail               - IMAP ingestion (sync, deprecated)
- GET  /api/scan-mail/status/{job_id} - Poll async scan job status
- POST /api/summarize-email         - Summarizes individual email body
- POST /api/build-digest            - Formats summaries into Markdown digest
- POST /api/send-digest             - SMTP-based email dispatch

Triggers:
- Queue: process_scan_folder        - Processes one folder per queue message
- Timer: cleanup_scan_jobs          - Nightly deletion of expired job records
"""

import json
import os

import azure.functions as func
from azure.functions import HttpRequest, HttpResponse
from baldwin.http_handlers import build_http_handlers
from baldwin.log import get_logger

_logger = get_logger(__name__)

SCAN_MAIL_QUEUE_NAME = os.environ.get("SCAN_MAIL_QUEUE_NAME", "scan-mail-jobs")
SCAN_MAIL_POISON_QUEUE_NAME = os.environ.get(
    "SCAN_MAIL_POISON_QUEUE_NAME",
    f"{SCAN_MAIL_QUEUE_NAME}-poison",
)

app = func.FunctionApp()
HANDLERS = build_http_handlers()

@app.function_name(name="enqueue_scan")
@app.route(route="scan-mail", methods=["POST"])
def enqueue_scan(req: HttpRequest) -> HttpResponse:
    """Enqueue an async per-folder scan job and return 202 with a job ID."""
    return HANDLERS.enqueue_scan(req)

@app.function_name(name="get_scan_status")
@app.route(route="scan-mail/status/{job_id}", methods=["GET"])
def get_scan_status(req: HttpRequest) -> HttpResponse:
    """Return the status of an async scan job."""
    return HANDLERS.get_scan_status(req)

@app.function_name(name="process_scan_folder")
@app.queue_trigger(
    arg_name="msg",
    queue_name=SCAN_MAIL_QUEUE_NAME,
    connection="AzureWebJobsStorage",
)
def process_scan_folder(msg: func.QueueMessage) -> None:
    """Process a single folder ingestion dispatched from the scan queue."""
    _logger.info(
        "Queue trigger fired: id=%s dequeue_count=%d",
        msg.id,
        msg.dequeue_count,
    )
    HANDLERS.process_folder_job(json.loads(msg.get_body().decode("utf-8")))


@app.function_name(name="process_scan_folder_poison")
@app.queue_trigger(
    arg_name="msg",
    queue_name=SCAN_MAIL_POISON_QUEUE_NAME,
    connection="AzureWebJobsStorage",
)
def process_scan_folder_poison(msg: func.QueueMessage) -> None:
    """Record poison queue terminal failures for per-folder scan jobs."""
    _logger.error("Poison queue trigger fired: id=%s", msg.id)
    HANDLERS.process_poison_folder_job(json.loads(msg.get_body().decode("utf-8")))

@app.function_name(name="cleanup_scan_jobs")
@app.timer_trigger(
    schedule="0 0 2 * * *",
    arg_name="_mytimer",
    run_on_startup=False,
    use_monitor=False,
)
def cleanup_scan_jobs(_mytimer: func.TimerRequest) -> None:
    """Delete expired scan job tracking records (nightly at 02:00 UTC)."""
    HANDLERS.cleanup_scan_jobs()

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
