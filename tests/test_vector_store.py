"""Unit tests for generic vector-store behavior."""

import unittest
from unittest.mock import MagicMock, Mock, patch

import psycopg
from baldwin.embedding import EmbeddingResult
from baldwin.vector import PostgresVectorStore, VectorStoreError
from baldwin.vector.postgres_store import VectorDocument


class PostgresVectorStoreTests(unittest.TestCase):
    """Regression tests for generic vector-store SQL generation."""

    @patch("baldwin.vector.postgres_store.psycopg.connect")
    def test_bootstrap_executes_schema_creation_without_format_errors(self, connect: Mock) -> None:
        """Bootstrap should execute all schema statements without raising SQL format errors."""
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = PostgresVectorStore(
            database_url="postgresql://localhost/test",
        )

        store.bootstrap()

        self.assertEqual(cursor.execute.call_count, 7)
        statements = [str(call.args[0]) for call in cursor.execute.call_args_list]
        self.assertTrue(any("provider TEXT NOT NULL" in statement for statement in statements))
        self.assertTrue(any("PRIMARY KEY (document_id, provider, model_name)" in statement for statement in statements))

    @patch("baldwin.vector.postgres_store.psycopg.connect")
    def test_upsert_document_targets_provider_and_model_identity(self, connect: Mock) -> None:
        """Upserts should target a single provider/model space per document."""
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(123, True), (True,)]
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = PostgresVectorStore(database_url="postgresql://localhost/test")

        result = store.upsert_document(
            VectorDocument(
                document_key="doc-1",
                source_type="email",
                source_id="message-1",
                title="Subject",
                body="Body",
                searchable_text="Subject\n\nBody",
                content_checksum="checksum-1",
                metadata={"sender": "sender@example.com"},
            ),
            EmbeddingResult(
                vector=[0.1, 0.2, 0.3],
                provider="ollama",
                model_name="qllama/bge-small-en-v1.5",
                dimensions=3,
                metadata={},
            ),
        )

        self.assertTrue(result.inserted)
        self.assertTrue(result.embedding_updated)

        embedding_sql = str(cursor.execute.call_args_list[1].args[0])
        embedding_params = cursor.execute.call_args_list[1].args[1]
        self.assertIn("ON CONFLICT (document_id, provider, model_name)", embedding_sql)
        self.assertIn("dimensions IS DISTINCT FROM EXCLUDED.dimensions", embedding_sql)
        self.assertEqual(embedding_params["provider"], "ollama")
        self.assertEqual(embedding_params["model_name"], "qllama/bge-small-en-v1.5")

    @patch("baldwin.vector.postgres_store.psycopg.connect")
    def test_bootstrap_wraps_database_errors_with_causality(self, connect: Mock) -> None:
        """Bootstrap should translate psycopg failures into the shared vector-store error."""
        connect.side_effect = psycopg.OperationalError("db unavailable")
        store = PostgresVectorStore(database_url="postgresql://localhost/test")

        with self.assertRaises(VectorStoreError) as captured:
            store.bootstrap()

        self.assertIsInstance(captured.exception.__cause__, psycopg.OperationalError)

    @patch("baldwin.vector.postgres_store.psycopg.connect")
    def test_upsert_document_uses_domain_error_for_missing_metadata_row(self, connect: Mock) -> None:
        """Unexpected missing metadata rows should raise the shared vector-store error."""
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = PostgresVectorStore(database_url="postgresql://localhost/test")

        with self.assertRaises(VectorStoreError) as captured:
            store.upsert_document(
                VectorDocument(
                    document_key="doc-1",
                    source_type="email",
                    source_id="message-1",
                    title="Subject",
                    body="Body",
                    searchable_text="Subject\n\nBody",
                    content_checksum="checksum-1",
                    metadata={"sender": "sender@example.com"},
                ),
                EmbeddingResult(
                    vector=[0.1, 0.2, 0.3],
                    provider="ollama",
                    model_name="qllama/bge-small-en-v1.5",
                    dimensions=3,
                    metadata={},
                ),
            )

        self.assertEqual(str(captured.exception), "Failed to upsert vector document metadata.")
        self.assertIsNone(captured.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
