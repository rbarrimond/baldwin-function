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
from threading import Lock
from typing import Any, Callable, Mapping, Sequence, TypeVar
from uuid import UUID, uuid4

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
from baldwin.email.semantic import SemanticEnricher, build_semantic_enricher
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
from azure.core.exceptions import AzureError, ResourceExistsError
from azure.storage.queue import QueueClient
from baldwin.jobs import ScanJobStore
from baldwin.log import get_logger, set_trace_id

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


@dataclass(frozen=True)
class FolderIngestionResult:
    """Summary of a single async folder ingestion job."""

    folder_name: str
    sync_mode: str
    sync_run_id: str
    total_fetched: int
    total_normalized: int
    total_deduped: int
    total_persisted: int
    reconciled_missing: int


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
        self.semantic_enricher: SemanticEnricher = build_semantic_enricher(self.settings.environ)
        self._schema_ready = False
        self._schema_lock = Lock()

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
        with self._schema_lock:
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
        _logger.debug("Fetching folder payload: folder_name=%r days=%d", folder_name, days)
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

    def _enrich_semantics(self, normalized_emails: Sequence[Any]) -> list[Any]:
        """Run semantic enrichment in an additive, non-fatal post-dedup stage."""
        return self.semantic_enricher.enrich(list(normalized_emails))

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
        _logger.info(
            "Starting mailbox ingestion: sync_run_id=%r folders=%r days=%d",
            sync_run_id, list(folders.folders), days,
        )
        emails, folder_statuses, sync_modes = self._fetch_folder_payloads(
            vector_store=vector_store,
            folders=folders,
            days=days,
            incremental_sync_enabled=self._incremental_sync_enabled(),
            progress_callback=progress_callback,
        )

        _logger.info("Fetch complete: sync_run_id=%r fetched=%d", sync_run_id, len(emails))
        normalized = self._normalize_emails(emails, progress_callback=progress_callback)
        _logger.info("Normalize complete: sync_run_id=%r normalized=%d", sync_run_id, len(normalized))
        deduped = self.normalizer.merge_duplicates(normalized)
        _logger.info("Dedup complete: sync_run_id=%r deduped=%d", sync_run_id, len(deduped))
        deduped = self._enrich_semantics(deduped)
        _logger.info("Semantic enrichment complete: sync_run_id=%r deduped=%d", sync_run_id, len(deduped))
        embeddings = self._embed_searchable_texts(
            [email_message.searchable_text for email_message in deduped],
            progress_callback=progress_callback,
        )
        _logger.info("Embed complete: sync_run_id=%r embedded=%d", sync_run_id, len(embeddings))

        persisted: list[dict[str, Any]] = []
        batch_results = vector_store.upsert_emails_batch(deduped, embeddings)
        sync_records: list[dict[str, Any]] = []
        for normalized_email, (store_result, document_id) in zip(deduped, batch_results):
            persisted.append(
                {
                    "fingerprint": normalized_email.fingerprint,
                    "subject": normalized_email.subject,
                    "inserted": store_result.inserted,
                    "embedding_updated": store_result.embedding_updated,
                }
            )
            sync_records.append(
                {
                    "document_key": normalized_email.fingerprint,
                    "document_id": document_id,
                    "folder_names": normalized_email.folders,
                    "folder_uids": normalized_email.folder_uids,
                }
            )

        vector_store.record_document_syncs_batch(
            sync_records,
            sync_run_id=sync_run_id,
            last_seen_at=observed_at,
        )

        if progress_callback is not None:
            progress_callback(ScanMailboxProgress("persist", len(persisted), len(deduped)))

        _logger.info("Persist complete: sync_run_id=%r persisted=%d", sync_run_id, len(persisted))
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

        _logger.info(
            "Reconciliation complete: sync_run_id=%r reconciled_missing=%d",
            sync_run_id, reconciled_missing,
        )
        _logger.info(
            "Mailbox ingestion complete: sync_run_id=%r total_fetched=%d total_deduped=%d deleted_stale=%d",
            sync_run_id, len(emails), len(deduped), deleted_stale_documents,
        )
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

    def ingest_folder(
        self,
        folder_name: str,
        days: int,
    ) -> FolderIngestionResult:
        """Fetch, normalize, embed, and persist emails for a single IMAP folder.

        The caller is responsible for running delete_documents_without_folders()
        after all folder jobs for a scan have completed.
        """
        sync_run_id = str(uuid4())
        observed_at = datetime.now(UTC)
        email_service = self._build_email_service()
        vector_store = self._build_vector_store()
        self._ensure_store_schema(vector_store)
        folders = MailboxFolders((folder_name,))

        _logger.info(
            "Starting folder ingestion: sync_run_id=%r folder=%r days=%d",
            sync_run_id, folder_name, days,
        )
        emails, folder_statuses, sync_modes = self._fetch_folder_payloads(
            vector_store=vector_store,
            folders=folders,
            days=days,
            incremental_sync_enabled=self._incremental_sync_enabled(),
        )

        normalized = self._normalize_emails(emails)
        deduped = self.normalizer.merge_duplicates(normalized)
        deduped = self._enrich_semantics(deduped)
        embeddings = self._embed_searchable_texts(
            [email_message.searchable_text for email_message in deduped],
        )
        batch_results = vector_store.upsert_emails_batch(deduped, embeddings)
        sync_records: list[dict[str, Any]] = []
        for normalized_email, (_, document_id) in zip(deduped, batch_results):
            sync_records.append(
                {
                    "document_key": normalized_email.fingerprint,
                    "document_id": document_id,
                    "folder_names": normalized_email.folders,
                    "folder_uids": normalized_email.folder_uids,
                }
            )

        vector_store.record_document_syncs_batch(
            sync_records,
            sync_run_id=sync_run_id,
            last_seen_at=observed_at,
        )
        reconciled_missing = self._reconcile_folder_membership(
            vector_store=vector_store,
            folders=folders,
            folder_statuses=folder_statuses,
            sync_modes=sync_modes,
            sync_run_id=sync_run_id,
            observed_at=observed_at,
        )

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

        _logger.info(
            "Folder ingestion complete: sync_run_id=%r folder=%r fetched=%d deduped=%d",
            sync_run_id, folder_name, len(emails), len(deduped),
        )
        return FolderIngestionResult(
            folder_name=folder_name,
            sync_mode=sync_modes[folder_name],
            sync_run_id=sync_run_id,
            total_fetched=len(emails),
            total_normalized=len(normalized),
            total_deduped=len(deduped),
            total_persisted=len(batch_results),
            reconciled_missing=reconciled_missing,
        )

    def delete_stale_documents(self) -> int:
        """Delete documents no longer associated with any folder."""
        return self._build_vector_store().delete_documents_without_folders()


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

        _logger.info(
            "Digest email sent: to=%r from=%r subject=%r",
            to_address, from_address, subject,
        )
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
        settings: EnvironmentSettings,
    ):
        self.ingestion_service = ingestion_service
        self.request_parser = request_parser
        self.summary_service = summary_service
        self.digest_builder = digest_builder
        self.digest_delivery_service = digest_delivery_service
        self.response_factory = response_factory
        self.settings = settings
        self._scan_job_store: ScanJobStore | None = None
        self._scan_job_store_ready: bool = False

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

    def _get_scan_job_store(self) -> ScanJobStore:
        """Return the ScanJobStore, bootstrapping the schema on first access."""
        if self._scan_job_store is None:
            self._scan_job_store = ScanJobStore(self.settings.get_required("DATABASE_URL"))
        if not self._scan_job_store_ready:
            self._scan_job_store.bootstrap()
            self._scan_job_store_ready = True
        return self._scan_job_store

    @staticmethod
    def _resolve_trace_id(req: HttpRequest) -> str:
        """Extract a trace ID from the W3C traceparent header or generate a fresh UUID.

        The traceparent format is ``00-{trace-id}-{parent-id}-{flags}`` where
        the trace ID is a 32-character hex string.  Falls back to a new UUID
        when the header is absent or malformed.
        """
        traceparent = req.headers.get("traceparent", "")
        parts = traceparent.split("-")
        if len(parts) == 4 and len(parts[1]) == 32:
            return parts[1]
        return str(uuid4())

    def enqueue_scan(self, req: HttpRequest) -> HttpResponse:
        """Enqueue an async scan job per folder and return 202 with a job ID."""
        set_trace_id(self._resolve_trace_id(req))
        job_id: str | None = None
        folders: list[str] = []
        enqueued_folders: list[str] = []
        store: ScanJobStore | None = None
        try:
            scan_request = self.request_parser.parse_scan_request(req)
            folders = list(scan_request.folders.folders)
            job_id = str(uuid4())

            store = self._get_scan_job_store()
            store.create_job(
                job_id,
                params={"days": scan_request.days, "folders": folders},
                folder_count=len(folders),
            )
            store.create_folder_records(job_id, folders)

            connection_string = self.settings.get_required("AzureWebJobsStorage")
            queue_name = self.settings.get("SCAN_MAIL_QUEUE_NAME") or "scan-mail-jobs"
            queue_api_version = self.settings.get("QUEUE_API_VERSION") or None
            queue_client = QueueClient.from_connection_string(
                connection_string,
                queue_name,
                message_encode_policy=None,
                message_decode_policy=None,
                api_version=queue_api_version,
            )
            self._enqueue_folder_messages(
                queue_client=queue_client,
                job_id=job_id,
                folders=folders,
                days=scan_request.days,
                enqueued_folders=enqueued_folders,
            )

            _logger.info(
                "Scan job enqueued: job_id=%r folder_count=%d days=%d",
                job_id, len(folders), scan_request.days,
            )
            return self.response_factory.json(
                {
                    "job_id": job_id,
                    "folder_count": len(folders),
                    "status_url": f"/api/scan-mail/status/{job_id}",
                },
                status_code=202,
            )
        except BaldwinConfigurationError:
            _logger.exception("Configuration error in enqueue_scan")
            return self.response_factory.json(
                {"error": INTERNAL_SERVER_ERROR_MESSAGE}, status_code=500
            )
        except BaldwinValidationError as exc:
            _logger.warning("Invalid request for enqueue_scan: %s", exc)
            return self.response_factory.json({"error": str(exc)}, status_code=400)
        except (VectorStoreError, AzureError) as exc:
            self._record_unsent_folder_failures(
                store=store,
                job_id=job_id,
                folders=folders,
                enqueued_folders=enqueued_folders,
            )
            _logger.exception("Error enqueuing scan job: %s", exc)
            return self.response_factory.json(
                {"error": INTERNAL_SERVER_ERROR_MESSAGE}, status_code=500
            )

    @staticmethod
    def _enqueue_folder_messages(
        *,
        queue_client: QueueClient,
        job_id: str,
        folders: list[str],
        days: int,
        enqueued_folders: list[str],
    ) -> None:
        """Create queue if needed and enqueue one message per requested folder."""
        try:
            queue_client.create_queue()
        except ResourceExistsError:
            pass

        for folder in folders:
            queue_client.send_message(
                json.dumps({"job_id": job_id, "folder": folder, "days": days})
            )
            enqueued_folders.append(folder)

    def _record_unsent_folder_failures(
        self,
        *,
        store: ScanJobStore | None,
        job_id: str | None,
        folders: list[str],
        enqueued_folders: list[str],
    ) -> None:
        """Persist enqueue failures for folders that were never queued."""
        if store is None or job_id is None or not folders:
            return

        unsent_folders = [folder for folder in folders if folder not in enqueued_folders]
        if not unsent_folders:
            return

        try:
            remaining = store.mark_folders_failed(
                job_id,
                unsent_folders,
                {
                    "type": "QueueEnqueueError",
                    "message": "Folder job was not enqueued due to queue failure.",
                },
            )
            if remaining == 0:
                self._finalize_folder_job(job_id)
        except VectorStoreError:
            _logger.exception(
                "Failed to persist unsent folder failures: job_id=%r unsent=%d",
                job_id,
                len(unsent_folders),
            )

    def process_folder_job(self, msg_body: dict[str, Any]) -> None:
        """Execute a single folder ingestion dispatched from the scan queue."""
        job_id = msg_body["job_id"]
        folder = msg_body["folder"]
        days = int(msg_body["days"])

        _logger.info(
            "Processing folder job: job_id=%r folder=%r days=%d",
            job_id, folder, days,
        )
        store: ScanJobStore | None = None
        remaining: int | None = None

        try:
            store = self._get_scan_job_store()
            store.mark_folder_started(job_id, folder)
            result = self.ingestion_service.ingest_folder(folder, days)
            stats = {
                "total_fetched": result.total_fetched,
                "total_normalized": result.total_normalized,
                "total_deduped": result.total_deduped,
                "total_persisted": result.total_persisted,
                "reconciled_missing": result.reconciled_missing,
                "sync_mode": result.sync_mode,
                "sync_run_id": result.sync_run_id,
            }
            remaining = store.mark_folder_done(job_id, folder, stats)
        except EmailFetchError as exc:
            _logger.exception(
                "Folder job failed: job_id=%r folder=%r error=%s", job_id, folder, exc
            )
            self._record_folder_job_failure(store, job_id, folder, exc)
            if exc.reason_category == ImapReasonCategory.FOLDER:
                _logger.info(
                    "Not retrying non-retriable folder error: job_id=%r folder=%r",
                    job_id,
                    folder,
                )
                return
            raise
        except (EmbeddingProviderError, VectorStoreError) as exc:
            _logger.exception(
                "Folder job failed: job_id=%r folder=%r error=%s", job_id, folder, exc
            )
            self._record_folder_job_failure(store, job_id, folder, exc)
            raise
        except BaldwinConfigurationError as exc:
            _logger.exception(
                "Configuration error in folder job (non-retriable): job_id=%r folder=%r",
                job_id,
                folder,
            )
            self._record_folder_job_failure(store, job_id, folder, exc)
            # Do not re-raise — config errors are non-retriable; retrying won't help.
        except Exception as exc:
            _logger.exception(
                "Unexpected error in folder job: job_id=%r folder=%r",
                job_id,
                folder,
            )
            try:
                self._record_folder_job_failure(store, job_id, folder, exc)
            except VectorStoreError:
                _logger.exception(
                    "Failed to record unexpected folder error in store: job_id=%r", job_id
                )
            raise

        if remaining == 0:
            self._finalize_folder_job(job_id)

    def _record_folder_job_failure(
        self,
        store: ScanJobStore | None,
        job_id: str,
        folder: str,
        exc: BaseException,
    ) -> None:
        """Persist folder failure details and finalize job when terminal."""
        if store is None:
            return

        error_info: dict[str, Any] = {"type": type(exc).__name__, "message": str(exc)}
        remaining = store.mark_folder_failed(job_id, folder, error_info)
        if remaining == 0:
            self._finalize_folder_job(job_id)

    def process_poison_folder_job(self, msg_body: dict[str, Any]) -> None:
        """Record terminal failure for a folder job moved to the poison queue."""
        job_id = msg_body.get("job_id")
        folder = msg_body.get("folder")
        if not job_id or not folder:
            _logger.error(
                "Poison folder job missing required fields: payload=%r",
                msg_body,
            )
            return

        _logger.error(
            "Processing poison folder job: job_id=%r folder=%r",
            job_id,
            folder,
        )
        error_info: dict[str, Any] = {
            "type": "QueuePoisonError",
            "message": (
                "Folder job exceeded max dequeue count and moved to poison queue."
            ),
        }

        store = self._get_scan_job_store()
        remaining = store.mark_folder_failed(job_id, folder, error_info)
        if remaining == 0:
            self._finalize_folder_job(job_id)

    def _finalize_folder_job(self, job_id: str) -> None:
        """Delete stale documents and mark the scan job as completed or partial."""
        _logger.info("Finalizing scan job: job_id=%r", job_id)
        try:
            deleted = self.ingestion_service.delete_stale_documents()
            _logger.info(
                "Stale document cleanup complete: job_id=%r deleted=%d", job_id, deleted
            )
        except VectorStoreError:
            _logger.exception(
                "Error during stale document cleanup: job_id=%r", job_id
            )
        finally:
            self._get_scan_job_store().finalize_job(job_id)

    def get_scan_status(self, req: HttpRequest) -> HttpResponse:
        """Return the current status of an async scan job."""
        job_id = req.route_params.get("job_id", "").strip()
        if not job_id:
            return self.response_factory.json({"error": "job_id is required."}, status_code=400)
        try:
            UUID(job_id)
        except ValueError:
            _logger.warning("Invalid job_id format for scan status: job_id=%r", job_id)
            return self.response_factory.json(
                {"error": "job_id must be a valid UUID."},
                status_code=400,
            )
        try:
            status = self._get_scan_job_store().get_job_status(job_id)
            if status is None:
                return self.response_factory.json(
                    {"error": f"Scan job not found: {job_id!r}"},
                    status_code=404,
                )
            return self.response_factory.json(status)
        except VectorStoreError:
            _logger.exception(
                "Database error fetching scan job status: job_id=%r", job_id
            )
            return self.response_factory.json(
                {"error": INTERNAL_SERVER_ERROR_MESSAGE}, status_code=500
            )

    def cleanup_scan_jobs(self) -> None:
        """Delete expired scan job records; invoked by a nightly timer trigger."""
        retention_days = self.settings.get_int("SCAN_JOB_RETENTION_DAYS", 30)
        _logger.info("Starting scan job cleanup: retention_days=%d", retention_days)
        deleted = self._get_scan_job_store().delete_expired_jobs(retention_days)
        _logger.info("Scan job cleanup complete: deleted=%d", deleted)

    def scan_mail(self, req: HttpRequest) -> HttpResponse:
        """Handle a request to scan mailbox folders and persist email content."""
        set_trace_id(self._resolve_trace_id(req))
        try:
            scan_request = self.request_parser.parse_scan_request(req)
            summary = self.ingestion_service.ingest_mailbox(
                scan_request.days,
                scan_request.folders,
            )
            summary["_deprecation_notice"] = (
                "GET /api/scan-mail is deprecated. Use POST /api/scan-mail for async execution."
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
        settings=settings,
    )
