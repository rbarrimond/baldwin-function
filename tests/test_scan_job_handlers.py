"""Unit tests for async scan job handler edge cases."""

import unittest
from typing import Any, cast
from unittest.mock import MagicMock, patch

from azure.core.exceptions import ResourceExistsError
from azure.storage.queue import QueueClient
from baldwin.email import EmailFetchError
from baldwin.exceptions import (
    BaldwinConfigurationError,
    ImapReasonCategory,
    VectorStoreError,
)
from baldwin.jobs import ScanJobStore
from baldwin.http_handlers import (
    DigestBuilder,
    DigestDeliveryService,
    EmailIngestionService,
    EnvironmentSettings,
    FolderIngestionResult,
    MailboxHttpHandlers,
    MailboxRequestParser,
    ResponseFactory,
    SummaryService,
)


class _MailboxHttpHandlersTestCase(unittest.TestCase):
    """Shared helper for MailboxHttpHandlers tests."""

    def _build_handlers(self) -> MailboxHttpHandlers:
        settings = EnvironmentSettings(
            {
                "DATABASE_URL": "postgresql://localhost/test",
                "IMAP_USER": "user@example.com",
                "IMAP_PASSWORD": "password",
            }
        )
        return MailboxHttpHandlers(
            ingestion_service=EmailIngestionService(settings),
            request_parser=MailboxRequestParser(settings),
            summary_service=SummaryService(),
            digest_builder=DigestBuilder(),
            digest_delivery_service=DigestDeliveryService(settings),
            response_factory=ResponseFactory(),
            settings=settings,
        )


class _TestableMailboxHttpHandlers(MailboxHttpHandlers):
    """Expose focused test seams for protected helper coverage."""

    @staticmethod
    def enqueue_folder_messages_for_test(
        *,
        queue_client: Any,
        job_id: str,
        folders: list[str],
        days: int,
        enqueued_folders: list[str],
    ) -> None:
        """Expose the enqueue_folder_messages helper for direct testing."""
        MailboxHttpHandlers._enqueue_folder_messages(
            queue_client=cast(QueueClient, queue_client),
            job_id=job_id,
            folders=folders,
            days=days,
            enqueued_folders=enqueued_folders,
        )

    def record_unsent_folder_failures_for_test(
        self,
        *,
        store: Any,
        job_id: str,
        folders: list[str],
        enqueued_folders: list[str],
    ) -> None:
        """Expose the record_unsent_folder_failures helper for direct testing."""
        self._record_unsent_folder_failures(
            store=cast(ScanJobStore | None, store),
            job_id=job_id,
            folders=folders,
            enqueued_folders=enqueued_folders,
        )


class PoisonScanJobHandlerTests(_MailboxHttpHandlersTestCase):
    """Coverage for poison queue handling in MailboxHttpHandlers."""

    def test_process_poison_folder_job_marks_failed_and_finalizes_when_last(self) -> None:
        """Poison messages should persist terminal failure and finalize the parent job when complete."""
        handlers = self._build_handlers()
        store = MagicMock()
        store.mark_folder_failed.return_value = 0
        with patch.object(handlers, "_get_scan_job_store", return_value=store):
            with patch.object(handlers, "_finalize_folder_job") as finalize_job:
                handlers.process_poison_folder_job(
                    {"job_id": "00000000-0000-0000-0000-000000000001", "folder": "Archive"}
                )

        store.mark_folder_failed.assert_called_once()
        error_info = store.mark_folder_failed.call_args.args[2]
        self.assertEqual(error_info["type"], "QueuePoisonError")
        finalize_job.assert_called_once_with(
            "00000000-0000-0000-0000-000000000001"
        )

    def test_process_poison_folder_job_ignores_malformed_payload(self) -> None:
        """Malformed poison payloads should be ignored without touching the store."""
        handlers = self._build_handlers()
        store = MagicMock()
        with patch.object(handlers, "_get_scan_job_store", return_value=store):
            handlers.process_poison_folder_job({"job_id": "only-job-id"})

        store.mark_folder_failed.assert_not_called()


class ProcessFolderJobHandlerTests(_MailboxHttpHandlersTestCase):
    """Coverage for async folder worker branches in MailboxHttpHandlers."""

    def test_process_folder_job_success_finalizes_when_last(self) -> None:
        """A successful final folder should trigger post-job finalization."""
        handlers = self._build_handlers()
        store = MagicMock()
        store.mark_folder_done.return_value = 0
        handlers.ingestion_service.ingest_folder = MagicMock(
            return_value=FolderIngestionResult(
                folder_name="Archive",
                sync_mode="incremental",
                sync_run_id="run-1",
                total_fetched=1,
                total_normalized=1,
                total_deduped=1,
                total_persisted=1,
                reconciled_missing=0,
            )
        )

        with patch.object(handlers, "_get_scan_job_store", return_value=store):
            with patch.object(handlers, "_finalize_folder_job") as finalize_job:
                handlers.process_folder_job(
                    {"job_id": "00000000-0000-0000-0000-000000000001", "folder": "Archive", "days": 7}
                )

        finalize_job.assert_called_once_with("00000000-0000-0000-0000-000000000001")

    def test_process_folder_job_non_retriable_email_error_marks_failed_and_returns(self) -> None:
        """Folder-category IMAP errors should be persisted and not retried."""
        handlers = self._build_handlers()
        store = MagicMock()
        store.mark_folder_failed.return_value = 1
        handlers.ingestion_service.ingest_folder = MagicMock(
            side_effect=EmailFetchError(
                "missing folder",
                reason_category=ImapReasonCategory.FOLDER,
                folders=("Archive",),
            )
        )

        with patch.object(handlers, "_get_scan_job_store", return_value=store):
            with patch.object(handlers, "_finalize_folder_job") as finalize_job:
                handlers.process_folder_job(
                    {"job_id": "00000000-0000-0000-0000-000000000001", "folder": "Archive", "days": 7}
                )

        store.mark_folder_started.assert_called_once_with(
            "00000000-0000-0000-0000-000000000001", "Archive"
        )
        store.mark_folder_failed.assert_called_once()
        finalize_job.assert_not_called()

    def test_process_folder_job_configuration_error_marks_failed_without_reraise(self) -> None:
        """Configuration failures should be terminal but non-retriable."""
        handlers = self._build_handlers()
        store = MagicMock()
        store.mark_folder_failed.return_value = 1
        handlers.ingestion_service.ingest_folder = MagicMock(
            side_effect=BaldwinConfigurationError("bad settings")
        )

        with patch.object(handlers, "_get_scan_job_store", return_value=store):
            handlers.process_folder_job(
                {"job_id": "00000000-0000-0000-0000-000000000001", "folder": "Archive", "days": 7}
            )

        store.mark_folder_failed.assert_called_once()

    def test_process_folder_job_retriable_store_error_after_ingest_reraises(self) -> None:
        """Failures while recording completion should be retried after persisting failed state."""
        handlers = self._build_handlers()
        store = MagicMock()
        store.mark_folder_done.side_effect = VectorStoreError("mark done failed")
        store.mark_folder_failed.return_value = 1
        handlers.ingestion_service.ingest_folder = MagicMock(
            return_value=FolderIngestionResult(
                folder_name="Archive",
                sync_mode="incremental",
                sync_run_id="run-1",
                total_fetched=1,
                total_normalized=1,
                total_deduped=1,
                total_persisted=1,
                reconciled_missing=0,
            )
        )

        with patch.object(handlers, "_get_scan_job_store", return_value=store):
            with self.assertRaises(VectorStoreError):
                handlers.process_folder_job(
                    {"job_id": "00000000-0000-0000-0000-000000000001", "folder": "Archive", "days": 7}
                )

        store.mark_folder_done.assert_called_once()
        store.mark_folder_failed.assert_called_once()

    def test_process_folder_job_store_start_failure_reraises(self) -> None:
        """Store transition failures before ingestion should propagate for retry."""
        handlers = self._build_handlers()
        store = MagicMock()
        store.mark_folder_started.side_effect = VectorStoreError("start failed")

        with patch.object(handlers, "_get_scan_job_store", return_value=store):
            with self.assertRaises(VectorStoreError):
                handlers.process_folder_job(
                    {"job_id": "00000000-0000-0000-0000-000000000001", "folder": "Archive", "days": 7}
                )

        store.mark_folder_failed.assert_called_once()


class EnqueueHelperTests(_MailboxHttpHandlersTestCase):
    """Coverage for enqueue helper branches in MailboxHttpHandlers."""

    def test_enqueue_folder_messages_tolerates_existing_queue(self) -> None:
        """Queue creation should ignore ResourceExistsError and still send all messages."""

        class _QueueClient:
            def __init__(self) -> None:
                self.sent_payloads: list[str] = []

            @staticmethod
            def create_queue() -> None:
                """Simulate queue creation failure due to existing queue."""
                raise ResourceExistsError("exists")

            def send_message(self, payload: str) -> None:
                """Record sent messages for assertion."""
                self.sent_payloads.append(payload)

        queue_client = _QueueClient()
        enqueued_folders: list[str] = []

        _TestableMailboxHttpHandlers.enqueue_folder_messages_for_test(
            queue_client=queue_client,
            job_id="job-1",
            folders=["INBOX", "Archive"],
            days=7,
            enqueued_folders=enqueued_folders,
        )

        self.assertEqual(enqueued_folders, ["INBOX", "Archive"])
        self.assertEqual(len(queue_client.sent_payloads), 2)

    def test_record_unsent_folder_failures_skips_when_everything_sent(self) -> None:
        """No-op when all folders were already enqueued successfully."""
        handlers = self._build_handlers()
        testable_handlers = _TestableMailboxHttpHandlers(
            ingestion_service=handlers.ingestion_service,
            request_parser=handlers.request_parser,
            summary_service=handlers.summary_service,
            digest_builder=handlers.digest_builder,
            digest_delivery_service=handlers.digest_delivery_service,
            response_factory=handlers.response_factory,
            settings=handlers.settings,
        )
        store = MagicMock()

        testable_handlers.record_unsent_folder_failures_for_test(
            store=store,
            job_id="job-1",
            folders=["INBOX", "Archive"],
            enqueued_folders=["INBOX", "Archive"],
        )

        store.mark_folders_failed.assert_not_called()

    def test_record_unsent_folder_failures_swallows_store_errors(self) -> None:
        """Failure bookkeeping should not raise a second error during enqueue failure handling."""
        handlers = self._build_handlers()
        testable_handlers = _TestableMailboxHttpHandlers(
            ingestion_service=handlers.ingestion_service,
            request_parser=handlers.request_parser,
            summary_service=handlers.summary_service,
            digest_builder=handlers.digest_builder,
            digest_delivery_service=handlers.digest_delivery_service,
            response_factory=handlers.response_factory,
            settings=handlers.settings,
        )
        store = MagicMock()
        store.mark_folders_failed.side_effect = VectorStoreError("db failed")

        testable_handlers.record_unsent_folder_failures_for_test(
            store=store,
            job_id="job-1",
            folders=["INBOX", "Archive"],
            enqueued_folders=["INBOX"],
        )

        store.mark_folders_failed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
