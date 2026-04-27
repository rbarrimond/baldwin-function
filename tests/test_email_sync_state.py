"""Regression tests for email sync state persistence and ingestion orchestration."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, Mock, patch

from baldwin.email import Email, MailboxFolderStatus, MailboxFolders, PostgresEmailVectorStore
from baldwin.embedding import EmbeddingResult
from baldwin.exceptions import BaldwinConfigurationError
from baldwin.http_handlers import EmailIngestionService, EnvironmentSettings, FolderFetchResult


class TestableEmailIngestionService(EmailIngestionService):
    """Expose protected helper methods for focused unit tests."""

    def scan_mail_max_workers(self) -> int:
        """Expose the max worker count parsing logic for direct testing."""
        return self._scan_mail_max_workers()

    def fetch_folder_payloads(
        self,
        *,
        vector_store: Mock,
        folders: MailboxFolders,
        days: int,
        incremental_sync_enabled: bool,
    ) -> tuple[list[Any], dict[str, MailboxFolderStatus], dict[str, str]]:
        """Expose the folder payload fetching logic for direct testing."""
        return self._fetch_folder_payloads(
            vector_store=vector_store,
            folders=folders,
            days=days,
            incremental_sync_enabled=incremental_sync_enabled,
        )

    def embed_searchable_texts(self, searchable_texts: list[str]) -> list[EmbeddingResult]:
        """Expose the embedding logic for direct testing."""
        return self._embed_searchable_texts(searchable_texts)


class PostgresEmailVectorStoreSyncTests(unittest.TestCase):
    """SQL generation tests for additive email sync state tables."""

    @patch("baldwin.email.postgres_store.psycopg.connect")
    def test_bootstrap_adds_email_sync_state_tables(self, connect: Mock) -> None:
        """Email store bootstrap should extend the generic schema with sync-state tables."""
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = PostgresEmailVectorStore(database_url="postgresql://localhost/test")

        store.bootstrap()

        statements = [str(call.args[0]) for call in cursor.execute.call_args_list]
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS mailbox_sync_state" in statement for statement in statements))
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS" in statement and "document_sync_runs" in statement for statement in statements))
        self.assertTrue(any("ADD COLUMN IF NOT EXISTS folder_uids" in statement for statement in statements))

    @patch("baldwin.email.postgres_store.psycopg.connect")
    def test_record_document_sync_targets_document_by_key(self, connect: Mock) -> None:
        """Document sync observations should resolve the persisted document id before insert."""
        cursor = MagicMock()
        cursor.fetchone.return_value = (123,)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = PostgresEmailVectorStore(database_url="postgresql://localhost/test")

        store.record_document_sync(
            document_key="doc-123",
            sync_run_id="run-1",
            folder_names=["INBOX", "Archive"],
            folder_uids={"INBOX": 101, "Archive": 202},
        )

        lookup_sql = str(cursor.execute.call_args_list[0].args[0])
        insert_sql = str(cursor.execute.call_args_list[1].args[0])
        self.assertIn("SELECT id FROM", lookup_sql)
        self.assertIn("document_sync_runs", insert_sql)
        self.assertIn("folder_uids", insert_sql)
        self.assertEqual(cursor.execute.call_args_list[0].args[1]["document_key"], "doc-123")

    @patch("baldwin.email.postgres_store.psycopg.connect")
    def test_remove_folder_membership_cleans_up_folder_flags_and_keywords(self, connect: Mock) -> None:
        """Folder reconciliation should remove folder-scoped flags and keywords with the folder membership."""
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = PostgresEmailVectorStore(database_url="postgresql://localhost/test")

        store.remove_folder_membership(document_key="doc-123", folder_name="Archive")

        update_sql = str(cursor.execute.call_args_list[0].args[0])
        self.assertIn("folder_uids", update_sql)
        self.assertIn("folder_flags", update_sql)
        self.assertIn("folder_keywords", update_sql)

    @patch("baldwin.email.postgres_store.psycopg.connect")
    def test_get_mailbox_sync_state_returns_latest_cursor(self, connect: Mock) -> None:
        """Mailbox sync state lookups should surface the latest stored UID cursor."""
        cursor = MagicMock()
        cursor.fetchone.return_value = (999, 101, "2026-04-11T10:00:00Z", "run-1", 25)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = PostgresEmailVectorStore(database_url="postgresql://localhost/test")

        result = store.get_mailbox_sync_state(
            imap_user="user@example.com",
            imap_host="imap.example.com",
            imap_folder="INBOX",
        )

        if result is None:
            self.fail("Expected a persisted mailbox sync state result.")
        self.assertEqual(result["uidvalidity"], 999)
        self.assertEqual(result["last_synced_uid"], 101)
        self.assertEqual(result["sync_run_id"], "run-1")


class EmailIngestionServiceSyncTests(unittest.TestCase):
    """Behavior tests for wiring sync-state recording into mailbox ingestion."""

    def test_scan_mail_max_workers_must_be_positive(self) -> None:
        """Threaded scan-mail stages should reject non-positive worker counts."""
        service = TestableEmailIngestionService(
            EnvironmentSettings(
                {
                    "DATABASE_URL": "postgresql://localhost/test",
                    "IMAP_USER": "user@example.com",
                    "IMAP_PASSWORD": "password",
                    "SCAN_MAIL_MAX_WORKERS": "0",
                }
            )
        )

        with self.assertRaises(BaldwinConfigurationError):
            service.scan_mail_max_workers()

    def test_fetch_folder_payloads_preserves_folder_order_when_threaded(self) -> None:
        """Threaded folder fetches should aggregate results in requested folder order."""
        service = TestableEmailIngestionService(
            EnvironmentSettings(
                {
                    "DATABASE_URL": "postgresql://localhost/test",
                    "IMAP_USER": "user@example.com",
                    "IMAP_PASSWORD": "password",
                    "SCAN_MAIL_MAX_WORKERS": "4",
                }
            )
        )
        folder_status = Mock(message_count=1, uidvalidity=999, uidnext=2, uids=(1,))

        def build_folder_result(*, folder_name: str, days: int, vector_store: Mock, incremental_sync_enabled: bool) -> FolderFetchResult:
            del days, vector_store, incremental_sync_enabled
            return FolderFetchResult(
                folder_name=folder_name,
                folder_status=folder_status,
                sync_mode="full",
                emails=[f"email-from-{folder_name}"],
            )

        with patch.object(service, "_fetch_single_folder_payload", side_effect=build_folder_result):
            emails, folder_statuses, sync_modes = service.fetch_folder_payloads(
                vector_store=MagicMock(),
                folders=MailboxFolders.from_values(["INBOX", "Archive"]),
                days=1,
                incremental_sync_enabled=True,
            )

        self.assertEqual(emails, ["email-from-INBOX", "email-from-Archive"])
        self.assertEqual(list(folder_statuses.keys()), ["INBOX", "Archive"])
        self.assertEqual(sync_modes, {"INBOX": "full", "Archive": "full"})

    def test_embed_searchable_texts_preserves_input_order_when_threaded(self) -> None:
        """Threaded embedding work should preserve the deduped input ordering."""
        service = TestableEmailIngestionService(
            EnvironmentSettings(
                {
                    "DATABASE_URL": "postgresql://localhost/test",
                    "IMAP_USER": "user@example.com",
                    "IMAP_PASSWORD": "password",
                    "SCAN_MAIL_MAX_WORKERS": "4",
                }
            )
        )

        class DeterministicProvider:
            """A mock embedding provider that returns the input text as metadata for deterministic testing."""

            def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
                """Return an embedding result with the input text in metadata for each input text."""
                return [
                    EmbeddingResult(
                        vector=[float(len(text))],
                        provider="hashing",
                        model_name="hashing-v1",
                        dimensions=1,
                        metadata={"text": text},
                    )
                    for text in texts
                ]

        with patch.object(service, "_build_embedding_provider", return_value=DeterministicProvider()):
            embeddings = service.embed_searchable_texts(["bbb", "a", "cc"])

        self.assertEqual([embedding.metadata["text"] for embedding in embeddings], ["bbb", "a", "cc"])

    @patch("baldwin.http_handlers.EmailService.get_folder_status")
    @patch("baldwin.http_handlers.EmailService.fetch_emails_by_uid_range")
    @patch("baldwin.http_handlers.EmailService.fetch_emails")
    def test_ingest_mailbox_records_sync_state_and_bootstraps_once(
        self,
        fetch_emails: Mock,
        fetch_emails_by_uid_range: Mock,
        get_folder_status: Mock,
    ) -> None:
        """Ingestion should bootstrap once, use stored cursors, and reconcile removed folder state."""
        fetch_emails.return_value = []
        fetch_emails_by_uid_range.return_value = [
            Email(
                id="<message-1@example.com>",
                subject="Subject",
                sender="sender@example.com",
                to=["recipient@example.com"],
                cc=None,
                bcc=None,
                reply_to=None,
                date="Fri, 11 Apr 2026 09:15:00 +0000",
                body="Body",
                headers={"Message-ID": "<message-1@example.com>"},
                folder="INBOX",
                imap_uid=102,
            )
        ]
        get_folder_status.return_value = Mock(
            message_count=2,
            uidvalidity=999,
            uidnext=103,
            uids=(101, 102),
        )
        mock_provider = MagicMock()
        mock_provider.embed_texts.return_value = [
            EmbeddingResult(
                vector=[0.1, 0.2, 0.3],
                provider="hashing",
                model_name="hashing-v1",
                dimensions=3,
                metadata={},
            )
        ]
        mock_store = MagicMock()
        mock_store.upsert_email.return_value = MagicMock(inserted=True, embedding_updated=True)
        mock_store.get_mailbox_sync_state.return_value = {
            "uidvalidity": 999,
            "last_synced_uid": 101,
            "last_sync_time": "2026-04-11T10:00:00Z",
            "sync_run_id": "run-previous",
            "total_emails_in_folder": 1,
        }
        mock_store.get_current_folder_uids.return_value = {
            "stale-doc": 100,
            "current-doc": 101,
        }
        mock_store.delete_documents_without_folders.return_value = 1

        service = EmailIngestionService(
            EnvironmentSettings(
                {
                    "DATABASE_URL": "postgresql://localhost/test",
                    "IMAP_USER": "user@example.com",
                    "IMAP_PASSWORD": "password",
                }
            )
        )

        with patch.object(service, "_build_vector_store", return_value=mock_store), patch.object(
            service,
            "_build_embedding_provider",
            return_value=mock_provider,
        ):
            first_result = service.ingest_mailbox(1, MailboxFolders.from_values(["INBOX"]))
            second_result = service.ingest_mailbox(1, MailboxFolders.from_values(["INBOX"]))

        mock_store.bootstrap.assert_called_once_with()
        self.assertEqual(fetch_emails.call_count, 0)
        self.assertEqual(fetch_emails_by_uid_range.call_count, 2)
        self.assertEqual(mock_store.record_document_sync.call_count, 4)
        self.assertEqual(mock_store.remove_folder_membership.call_count, 2)
        self.assertEqual(mock_store.upsert_mailbox_sync_state.call_count, 2)
        self.assertEqual(first_result["reconciled_missing"], 1)
        self.assertEqual(second_result["deleted_stale_documents"], 1)


if __name__ == "__main__":
    unittest.main()
