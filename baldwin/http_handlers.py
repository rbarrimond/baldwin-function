"""HTTP request handlers for Baldwin mailbox workflows."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import imaplib
import json
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any, Callable, Mapping, Sequence, TypeVar
from uuid import uuid4

from azure.functions import HttpRequest, HttpResponse

from baldwin.email import (
    DEFAULT_IMAP_FOLDER,
    EmailDeliveryError,
    EmailFetchError,
    EmailService,
    MailboxFolderStatus,
    MailboxFolders,
)
from baldwin.email.postgres_store import PostgresEmailVectorStore
from baldwin.email.vectorization import EmailNormalizer
from baldwin.embedding import (
    EmbeddingProviderError,
    build_embedding_provider,
    load_embedding_settings,
)
from baldwin.exceptions import (
    BaldwinConfigurationError,
    BaldwinValidationError,
    ImapReasonCategory,
    VectorStoreError,
)
from baldwin.log import get_logger

_logger = get_logger(__name__)

JSON_MIMETYPE = "application/json"
MARKDOWN_MIMETYPE = "text/markdown"
DEFAULT_SUMMARY_WORD_LIMIT = 48
DEFAULT_SCAN_MAIL_MAX_WORKERS = 4
INTERNAL_SERVER_ERROR_MESSAGE = "Internal server error."
InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class ScanMailboxRequest:
    """Validated request parameters for mailbox ingestion."""

    days: int
    folders: MailboxFolders


@dataclass(frozen=True)
class FolderFetchResult:
    """Threaded fetch result for a single IMAP folder."""

    folder_name: str
    folder_status: MailboxFolderStatus
    sync_mode: str
    emails: list[Any]


@dataclass(frozen=True)
class ScanMailboxProgress:
    """Progress update for a scan-mail ingestion stage."""

    stage: str
    current: int
    total: int


ProgressCallback = Callable[[ScanMailboxProgress], None]


class EnvironmentSettings:
    """Read application settings from the process environment."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = environ or os.environ

    def get_required(self, name: str) -> str:
        """Get a required setting value or raise an error if it is missing."""
        value = self.environ.get(name)
        if not value:
            raise BaldwinConfigurationError(f"App setting '{name}' is required.")
        return value

    def get(self, name: str, default: str | None = None) -> str | None:
        """Get an optional setting value or return the provided default."""
        return self.environ.get(name, default)

    def get_int(self, name: str, default: int) -> int:
        """Get an optional integer setting value or return the provided default."""
        raw_value = self.environ.get(name)
        if not raw_value:
            return default
        try:
            return int(raw_value)
        except ValueError as exc:
            raise BaldwinConfigurationError(
                f"App setting '{name}' must be an integer, got {raw_value!r}."
            ) from exc


class MailboxRequestParser:
    """Convert HTTP request input into typed mailbox requests."""

    def __init__(self, settings: EnvironmentSettings):
        self.settings = settings

    def parse_scan_request(self, req: HttpRequest) -> ScanMailboxRequest:
        """Parse and validate the scan-mail query parameters."""
        raw_days = req.params.get("days", "1")
        try:
            days = int(raw_days)
        except ValueError as exc:
            raise BaldwinValidationError(
                f"The 'days' query parameter must be an integer, got {raw_days!r}."
            ) from exc

        if days < 1:
            raise BaldwinValidationError(
                "The 'days' query parameter must be greater than 0."
            )

        raw_folders = req.params.get("folders")
        default_folders = self.settings.get("IMAP_FOLDERS", DEFAULT_IMAP_FOLDER)
        folders = MailboxFolders.from_values(
            [raw_folders] if raw_folders else None,
            default_values=[default_folders] if default_folders else None,
        )
        return ScanMailboxRequest(days=days, folders=folders)


class ResponseFactory:
    """Create consistent HTTP responses for the Azure Functions surface."""

    def json(self, payload: Any, status_code: int = 200) -> HttpResponse:
        """Serialize a JSON response payload."""
        return HttpResponse(
            body=json.dumps(payload),
            status_code=status_code,
            mimetype=JSON_MIMETYPE,
        )

    def markdown(self, content: str, status_code: int = 200) -> HttpResponse:
        """Return Markdown content with the expected content type."""
        return HttpResponse(
            body=content,
            status_code=status_code,
            mimetype=MARKDOWN_MIMETYPE,
        )


class EmailIngestionService:
    """Fetch, normalize, embed, and persist mailbox content."""

    def __init__(self, settings: EnvironmentSettings):
        self.settings = settings
        self.normalizer = EmailNormalizer()
        self._schema_ready = False

    def _build_vector_store(self) -> PostgresEmailVectorStore:
        """Create the vector store from the current environment settings."""
        return PostgresEmailVectorStore(self.settings.get_required("DATABASE_URL"))

    def _build_email_service(self) -> EmailService:
        """Create an IMAP service from the current environment settings."""
        return EmailService(
            self.settings.get_required("IMAP_USER"),
            self.settings.get_required("IMAP_PASSWORD"),
            imap_host=self.settings.get("IMAP_HOST", "imap.mail.me.com")
            or "imap.mail.me.com",
            imap_port=self.settings.get_int("IMAP_PORT", 993),
        )

    def _ensure_store_schema(self, vector_store: PostgresEmailVectorStore) -> None:
        """Create required persistence schema once per process lifecycle."""
        if self._schema_ready:
            return
        vector_store.bootstrap()
        self._schema_ready = True

    @staticmethod
    def _build_embedding_provider() -> Any:
        """Create the configured embedding provider on demand."""
        return build_embedding_provider(load_embedding_settings())

    def _scan_mail_max_workers(self) -> int:
        """Return the configured upper bound for threaded scan-mail stages."""
        max_workers = self.settings.get_int(
            "SCAN_MAIL_MAX_WORKERS",
            DEFAULT_SCAN_MAIL_MAX_WORKERS,
        )
        if max_workers < 1:
            raise BaldwinConfigurationError(
                "App setting 'SCAN_MAIL_MAX_WORKERS' must be greater than 0."
            )
        return max_workers

    def _resolve_stage_workers(self, item_count: int) -> int:
        """Resolve a bounded worker count for a specific ingestion stage."""
        if item_count < 1:
            return 1
        return min(item_count, self._scan_mail_max_workers())

    @staticmethod
    def _map_ordered(
        items: Sequence[InputT],
        *,
        worker_count: int,
        func: Callable[[InputT], ResultT],
        progress_stage: str,
        progress_callback: ProgressCallback | None = None,
    ) -> list[ResultT]:
        """Apply work in parallel while preserving the input ordering."""
        if not items:
            return []
        if worker_count <= 1 or len(items) == 1:
            results: list[ResultT] = []
            for index, item in enumerate(items, start=1):
                results.append(func(item))
                if progress_callback is not None:
                    progress_callback(ScanMailboxProgress(progress_stage, index, len(items)))
            return results

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_index = {
                executor.submit(func, item): index
                for index, item in enumerate(items)
            }
            ordered_results: list[ResultT | None] = [None] * len(items)
            completed = 0
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                ordered_results[index] = future.result()
                completed += 1
                if progress_callback is not None:
                    progress_callback(ScanMailboxProgress(progress_stage, completed, len(items)))

            return [result for result in ordered_results if result is not None]

    def _incremental_sync_enabled(self) -> bool:
        return (
            str(self.settings.get("IMAP_INCREMENTAL_SYNC", "true")).strip().lower()
            not in {"0", "false", "no", "off"}
        )

    def _fetch_single_folder_payload(
        self,
        *,
        folder_name: str,
        days: int,
        vector_store: PostgresEmailVectorStore,
        incremental_sync_enabled: bool,
    ) -> FolderFetchResult:
        """Fetch the payload for one folder using its own IMAP service instance."""
        email_service = self._build_email_service()
        folder_status = email_service.get_folder_status(folder_name)
        stored_state = vector_store.get_mailbox_sync_state(
            imap_user=email_service.imap_user,
            imap_host=email_service.imap_host,
            imap_folder=folder_name,
        )
        last_synced_uid = (
            int(stored_state["last_synced_uid"])
            if stored_state is not None and stored_state.get("last_synced_uid") is not None
            else None
        )
        has_valid_cursor = (
            incremental_sync_enabled
            and stored_state is not None
            and stored_state.get("uidvalidity") == folder_status.uidvalidity
            and last_synced_uid is not None
        )

        if has_valid_cursor:
            assert last_synced_uid is not None
            emails: list[Any] = []
            if folder_status.uidnext is not None and last_synced_uid + 1 < folder_status.uidnext:
                emails = email_service.fetch_emails_by_uid_range(
                    folder_name,
                    start_uid=last_synced_uid + 1,
                    end_uid=folder_status.uidnext - 1,
                )
            return FolderFetchResult(
                folder_name=folder_name,
                folder_status=folder_status,
                sync_mode="incremental",
                emails=emails,
            )

        return FolderFetchResult(
            folder_name=folder_name,
            folder_status=folder_status,
            sync_mode="full",
            emails=email_service.fetch_emails(days, MailboxFolders((folder_name,))),
        )

    def _fetch_folder_payloads(
        self,
        *,
        vector_store: PostgresEmailVectorStore,
        folders: MailboxFolders,
        days: int,
        incremental_sync_enabled: bool,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[Any], dict[str, MailboxFolderStatus], dict[str, str]]:
        """Fetch folder payloads in folder order with bounded concurrency."""
        folder_names = list(folders.folders)
        worker_count = self._resolve_stage_workers(len(folder_names))

        def fetch_folder(folder_name: str) -> FolderFetchResult:
            return self._fetch_single_folder_payload(
                folder_name=folder_name,
                days=days,
                vector_store=vector_store,
                incremental_sync_enabled=incremental_sync_enabled,
            )

        folder_results = self._map_ordered(
            folder_names,
            worker_count=worker_count,
            func=fetch_folder,
            progress_stage="fetch",
            progress_callback=progress_callback,
        )

        emails: list[Any] = []
        folder_statuses: dict[str, MailboxFolderStatus] = {}
        sync_modes: dict[str, str] = {}
        for folder_result in folder_results:
            folder_statuses[folder_result.folder_name] = folder_result.folder_status
            sync_modes[folder_result.folder_name] = folder_result.sync_mode
            emails.extend(folder_result.emails)

        return emails, folder_statuses, sync_modes

    def _normalize_emails(
        self,
        emails: Sequence[Any],
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> list[Any]:
        """Normalize fetched emails with bounded concurrency while preserving order."""
        return self._map_ordered(
            list(emails),
            worker_count=self._resolve_stage_workers(len(emails)),
            func=self.normalizer.normalize,
            progress_stage="normalize",
            progress_callback=progress_callback,
        )

    def _embed_searchable_texts(
        self,
        searchable_texts: Sequence[str],
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> list[Any]:
        """Generate embeddings for normalized text with bounded concurrency."""
        texts = list(searchable_texts)
        if not texts:
            return []

        embedding_provider = self._build_embedding_provider()
        if self._resolve_stage_workers(len(texts)) == 1:
            return self._map_ordered(
                texts,
                worker_count=1,
                func=lambda text: embedding_provider.embed_texts([text])[0],
                progress_stage="embed",
                progress_callback=progress_callback,
            )

        def embed_single_text(text: str) -> Any:
            return embedding_provider.embed_texts([text])[0]

        return self._map_ordered(
            texts,
            worker_count=self._resolve_stage_workers(len(texts)),
            func=embed_single_text,
            progress_stage="embed",
            progress_callback=progress_callback,
        )

    @staticmethod
    def _reconcile_folder_membership(
        *,
        vector_store: PostgresEmailVectorStore,
        folders: MailboxFolders,
        folder_statuses: dict[str, Any],
        sync_modes: dict[str, str],
        sync_run_id: str,
        observed_at: datetime,
    ) -> int:
        reconciled_missing = 0
        for folder_name in folders.folders:
            folder_status = folder_statuses[folder_name]
            if sync_modes[folder_name] != "incremental" and folder_status.uidvalidity == 0:
                continue

            previous_folder_uids = vector_store.get_current_folder_uids(folder_name=folder_name)
            current_folder_uids = set(folder_status.uids)
            for document_key, persisted_uid in previous_folder_uids.items():
                if persisted_uid in current_folder_uids:
                    continue
                vector_store.record_document_sync(
                    document_key=document_key,
                    sync_run_id=sync_run_id,
                    folder_names=[folder_name],
                    folder_uids={folder_name: persisted_uid},
                    last_seen_at=observed_at,
                    was_present_in_mailbox=False,
                )
                vector_store.remove_folder_membership(
                    document_key=document_key,
                    folder_name=folder_name,
                )
                reconciled_missing += 1

        return reconciled_missing

    def ingest_mailbox(
        self,
        days: int,
        folders: MailboxFolders,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Fetch, normalize, deduplicate, embed, and persist mailbox messages."""
        sync_run_id = str(uuid4())
        observed_at = datetime.now(UTC)
        email_service = self._build_email_service()
        vector_store = self._build_vector_store()
        self._ensure_store_schema(vector_store)
        emails, folder_statuses, sync_modes = self._fetch_folder_payloads(
            vector_store=vector_store,
            folders=folders,
            days=days,
            incremental_sync_enabled=self._incremental_sync_enabled(),
            progress_callback=progress_callback,
        )

        normalized = self._normalize_emails(emails, progress_callback=progress_callback)
        deduped = self.normalizer.merge_duplicates(normalized)
        embeddings = self._embed_searchable_texts(
            [email_message.searchable_text for email_message in deduped],
            progress_callback=progress_callback,
        )

        persisted: list[dict[str, Any]] = []
        for index, (normalized_email, embedding) in enumerate(zip(deduped, embeddings), start=1):
            store_result = vector_store.upsert_email(normalized_email, embedding)
            vector_store.record_document_sync(
                document_key=normalized_email.fingerprint,
                sync_run_id=sync_run_id,
                folder_names=normalized_email.folders,
                folder_uids=normalized_email.folder_uids,
                last_seen_at=observed_at,
            )
            persisted.append(
                {
                    "fingerprint": normalized_email.fingerprint,
                    "subject": normalized_email.subject,
                    "inserted": store_result.inserted,
                    "embedding_updated": store_result.embedding_updated,
                }
            )
            if progress_callback is not None:
                progress_callback(ScanMailboxProgress("persist", index, len(deduped)))

        reconciled_missing = self._reconcile_folder_membership(
            vector_store=vector_store,
            folders=folders,
            folder_statuses=folder_statuses,
            sync_modes=sync_modes,
            sync_run_id=sync_run_id,
            observed_at=observed_at,
        )

        for folder_name in folders.folders:
            folder_status = folder_statuses[folder_name]
            vector_store.upsert_mailbox_sync_state(
                imap_user=email_service.imap_user,
                imap_host=email_service.imap_host,
                imap_folder=folder_name,
                sync_run_id=sync_run_id,
                total_emails_in_folder=folder_status.message_count,
                uidvalidity=folder_status.uidvalidity,
                last_synced_uid=max(folder_status.uids) if folder_status.uids else None,
                synced_at=observed_at,
            )

        deleted_stale_documents = vector_store.delete_documents_without_folders()

        return {
            "total_fetched": len(emails),
            "total_normalized": len(normalized),
            "total_deduped": len(deduped),
            "folders": {
                folder_name: {
                    "sync_mode": sync_modes[folder_name],
                    "message_count": folder_statuses[folder_name].message_count,
                    "uidvalidity": folder_statuses[folder_name].uidvalidity,
                    "uidnext": folder_statuses[folder_name].uidnext,
                }
                for folder_name in folders.folders
            },
            "reconciled_missing": reconciled_missing,
            "deleted_stale_documents": deleted_stale_documents,
            "persisted": persisted,
        }


class SummaryService:
    """Summarize individual email bodies for the HTTP API."""

    def summarize(self, body: str) -> str:
        """Generate a concise summary of an email body."""
        normalized = " ".join(body.split())
        if not normalized:
            raise BaldwinValidationError("Email body is required for summarization.")

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
            summary = str(item.get("summary", "")).strip() if isinstance(item, dict) else str(item).strip()
            if summary:
                digest_items.append(summary)

        if not digest_items:
            raise BaldwinValidationError("Summaries are required to build a digest.")
        return digest_items

    def build(self, summaries: list[Any], audience: str) -> str:
        """Construct a Markdown digest from a list of summaries."""
        normalized_audience = str(audience or "robert").strip() or "robert"
        lines = [f"## Daily Digest for {normalized_audience.capitalize()}", ""]
        lines.extend(f"- {item}" for item in self._normalize_digest_items(summaries))
        return "\n".join(lines)


class DigestDeliveryService:
    """Send digest emails over SMTP."""

    def __init__(self, settings: EnvironmentSettings):
        self.settings = settings

    def send(self, to_address: str, subject: str, content: str) -> str:
        """Send a digest email and return the sender address used."""
        smtp_server = self.settings.get_required("SMTP_SERVER")
        smtp_port = self.settings.get_int("SMTP_PORT", 587)
        smtp_username = self.settings.get("SMTP_USERNAME")
        smtp_password = self.settings.get("SMTP_PASSWORD")
        from_address = (
            self.settings.get("SMTP_FROM", smtp_username or "no-reply@localhost")
            or "no-reply@localhost"
        )

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
        ingestion_service: EmailIngestionService,
        request_parser: MailboxRequestParser,
        summary_service: SummaryService,
        digest_builder: DigestBuilder,
        digest_delivery_service: DigestDeliveryService,
        response_factory: ResponseFactory,
    ):
        self.ingestion_service = ingestion_service
        self.request_parser = request_parser
        self.summary_service = summary_service
        self.digest_builder = digest_builder
        self.digest_delivery_service = digest_delivery_service
        self.response_factory = response_factory

    @staticmethod
    def _is_caused_by(
        exc: BaseException,
        expected_type: type[BaseException],
    ) -> bool:
        return isinstance(exc.__cause__, expected_type)

    @staticmethod
    def _is_imap_fetch_failure(exc: EmailFetchError) -> bool:
        return bool(exc.folders) or exc.reason_category != ImapReasonCategory.UNKNOWN or isinstance(
            exc.__cause__, imaplib.IMAP4.error
        )

    @staticmethod
    def _imap_fetch_failure_payload(exc: EmailFetchError) -> dict[str, object]:
        payload: dict[str, object] = {
            "error": "Unable to process one or more requested IMAP folders.",
            "error_code": exc.error_code.value,
            "reason_category": exc.reason_category.value,
            "folders": list(exc.folders),
        }
        return payload

    def scan_mail(self, req: HttpRequest) -> HttpResponse:
        """Handle a request to scan mailbox folders and persist email content."""
        try:
            scan_request = self.request_parser.parse_scan_request(req)
            summary = self.ingestion_service.ingest_mailbox(
                scan_request.days,
                scan_request.folders,
            )
            return self.response_factory.json(summary)
        except BaldwinConfigurationError:
            _logger.exception("Configuration error in scan_mail")
            return self.response_factory.json(
                {"error": INTERNAL_SERVER_ERROR_MESSAGE},
                status_code=500,
            )
        except BaldwinValidationError as exc:
            _logger.warning("Invalid request for scan_mail: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)
        except EmailFetchError as exc:
            if self._is_imap_fetch_failure(exc):
                _logger.warning("IMAP request failed for scan_mail: %s", exc)
                return self.response_factory.json(
                    self._imap_fetch_failure_payload(exc),
                    status_code=502,
                )
            _logger.exception("Unexpected email fetch error in scan_mail")
            return self.response_factory.json(
                {"error": INTERNAL_SERVER_ERROR_MESSAGE},
                status_code=500,
            )
        except (EmbeddingProviderError, VectorStoreError) as exc:
            _logger.exception("Persistence or embedding error in scan_mail: %s", exc)
            return self.response_factory.json(
                {"error": INTERNAL_SERVER_ERROR_MESSAGE},
                status_code=500,
            )

    def summarize_email(self, req: HttpRequest) -> HttpResponse:
        """Handle a request to summarize an email body."""
        try:
            data = req.get_json()
            summary = self.summary_service.summarize(str(data.get("body", "")))
            return self.response_factory.json({"summary": summary})
        except BaldwinValidationError as exc:
            _logger.warning("Invalid request for summarize_email: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)
        except ValueError as exc:
            _logger.warning("Invalid request for summarize_email: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)

    def build_digest(self, req: HttpRequest) -> HttpResponse:
        """Handle a request to build a Markdown digest from summaries."""
        try:
            data = req.get_json()
            digest = self.digest_builder.build(
                data.get("summaries", []),
                data.get("audience", "robert"),
            )
            return self.response_factory.markdown(digest)
        except BaldwinValidationError as exc:
            _logger.warning("Invalid request for build_digest: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)
        except ValueError as exc:
            _logger.warning("Invalid request for build_digest: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)

    def send_digest(self, req: HttpRequest) -> HttpResponse:
        """Handle a request to send a digest email."""
        try:
            data = req.get_json()
            to_address = data.get("to")
            subject = data.get("subject")
            content = data.get("content")
            if not all([to_address, subject, content]):
                raise BaldwinValidationError(
                    "Recipient, subject, and content are required to send a digest."
                )

            from_address = self.digest_delivery_service.send(to_address, subject, content)
            return self.response_factory.json({"status": "sent", "from": from_address})
        except BaldwinConfigurationError:
            _logger.exception("Configuration error in send_digest")
            return self.response_factory.json(
                {"error": INTERNAL_SERVER_ERROR_MESSAGE},
                status_code=500,
            )
        except BaldwinValidationError as exc:
            _logger.warning("Invalid request for send_digest: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)
        except ValueError as exc:
            _logger.warning("Invalid request for send_digest: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)
        except EmailDeliveryError as exc:
            if self._is_caused_by(exc, smtplib.SMTPException):
                _logger.warning("SMTP request failed for send_digest: %s", exc)
                return self.response_factory.json(
                    {"error": "Unable to send the digest email."},
                    status_code=502,
                )
            _logger.exception("Unexpected email delivery error in send_digest")
            return self.response_factory.json(
                {"error": INTERNAL_SERVER_ERROR_MESSAGE},
                status_code=500,
            )


def build_http_handlers() -> MailboxHttpHandlers:
    """Create the production handler graph for Azure Functions."""
    settings = EnvironmentSettings()
    return MailboxHttpHandlers(
        ingestion_service=EmailIngestionService(settings),
        request_parser=MailboxRequestParser(settings),
        summary_service=SummaryService(),
        digest_builder=DigestBuilder(),
        digest_delivery_service=DigestDeliveryService(settings),
        response_factory=ResponseFactory(),
    )
