"""Unit tests for generic vector-store behavior."""

import unittest
from unittest.mock import MagicMock, Mock, patch

from baldwin.vector import PostgresVectorStore


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
            dimensions=256,
            model_name="hashing-v1",
        )

        store.bootstrap()

        self.assertEqual(cursor.execute.call_count, 4)


if __name__ == "__main__":
    unittest.main()
    