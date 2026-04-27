"""Unit tests for the persisted embedding cost report script."""

import argparse
import io
import json
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from scripts import embedding_cost_report


class EmbeddingCostReportScriptTests(unittest.TestCase):
    """Coverage for the persisted embedding cost report CLI."""

    def test_resolve_pricing_context_uses_azure_openai_preset(self) -> None:
        """Azure OpenAI embedding presets should resolve to the documented token price."""
        args = argparse.Namespace(
            azure_openai_model="text-embedding-3-small",
            price_per_1k_tokens=None,
        )

        pricing = embedding_cost_report.resolve_pricing_context(args)

        self.assertIsNotNone(pricing)
        assert pricing is not None
        self.assertEqual(pricing.label, "text-embedding-3-small")
        self.assertEqual(pricing.price_per_1k_tokens_usd, 0.000022)
        self.assertEqual(pricing.reference_date, "2026-04-27")

    @patch("scripts.embedding_cost_report.psycopg.connect")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_main_prints_report_json_with_estimated_cost(self, stdout, connect) -> None:
        """The script should summarize persisted embedding rows and optional hosted-cost estimates."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            (
                "email",
                "ollama",
                "qllama/bge-small-en-v1.5",
                2,
                2,
                800,
                400.0,
                300,
                500,
                datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
                datetime(2026, 4, 21, 12, 0, tzinfo=UTC),
            )
        ]
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        with patch(
            "sys.argv",
            [
                "embedding_cost_report",
                "--database-url",
                "postgresql://localhost/test",
                "--azure-openai-model",
                "text-embedding-3-small",
            ],
        ):
            exit_code = embedding_cost_report.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["summary"]["embedding_rows"], 2)
        self.assertEqual(payload["summary"]["estimated_tokens"], 200)
        self.assertAlmostEqual(payload["summary"]["estimated_cost_usd"], 0.0000044)
        self.assertEqual(payload["estimation_basis"]["chars_per_token"], 4.0)
        self.assertEqual(payload["pricing"]["label"], "text-embedding-3-small")
        self.assertEqual(payload["by_provider_model"][0]["provider"], "ollama")

    @patch("sys.stderr", new_callable=io.StringIO)
    def test_main_requires_database_url(self, stderr) -> None:
        """The script should fail cleanly when no PostgreSQL connection string is available."""
        with patch("sys.argv", ["embedding_cost_report"]), patch.dict("os.environ", {}, clear=True):
            exit_code = embedding_cost_report.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("PostgreSQL database URL is required", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()