"""Regression tests for email sync state persistence and ingestion orchestration."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock, patch

from baldwin.email import Email, MailboxFolders, PostgresEmailVectorStore
from baldwin.embedding import EmbeddingResult
from baldwin.http_handlers import EmailIngestionService, EnvironmentSettings


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
        )

        lookup_sql = str(cursor.execute.call_args_list[0].args[0])
        insert_sql = str(cursor.execute.call_args_list[1].args[0])
        self.assertIn("SELECT id FROM", lookup_sql)
        self.assertIn("document_sync_runs", insert_sql)
        self.assertEqual(cursor.execute.call_args_list[0].args[1]["document_key"], "doc-123")


class EmailIngestionServiceSyncTests(unittest.TestCase):
    """Behavior tests for wiring sync-state recording into mailbox ingestion."""

    @patch("baldwin.http_handlers.EmailService.fetch_emails")
    def test_ingest_mailbox_records_sync_state_and_bootstraps_once(self, fetch_emails: Mock) -> None:
        """Ingestion should bootstrap the schema once and record sync state for observed folders."""
        fetch_emails.return_value = [
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
            )
        ]
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
            service.ingest_mailbox(1, MailboxFolders.from_values(["INBOX"]))
            service.ingest_mailbox(1, MailboxFolders.from_values(["INBOX"]))

        mock_store.bootstrap.assert_called_once_with()
        self.assertEqual(mock_store.record_document_sync.call_count, 2)
        self.assertEqual(mock_store.upsert_mailbox_sync_state.call_count, 2)


if __name__ == "__main__":
    unittest.main()
    