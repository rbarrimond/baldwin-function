"""Unit tests for async scan job state persistence."""

import unittest
from unittest.mock import MagicMock, Mock, patch

from baldwin.jobs import ScanJobStore


class ScanJobStoreTests(unittest.TestCase):
    """Regression tests for scan job folder/job lifecycle transitions."""

    @patch("baldwin.jobs.psycopg.connect")
    def test_mark_folder_done_clears_previous_error_payload(self, connect: Mock) -> None:
        """A successful retry should not retain prior error_json on the folder row."""
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = ScanJobStore(database_url="postgresql://localhost/test")

        remaining = store.mark_folder_done(
            job_id="00000000-0000-0000-0000-000000000001",
            folder="Archive",
            stats={"total_fetched": 1},
        )

        self.assertEqual(remaining, 0)
        update_sql = str(cursor.execute.call_args_list[0].args[0])
        self.assertIn("error_json = NULL", update_sql)

    @patch("baldwin.jobs.psycopg.connect")
    def test_mark_folder_started_preserves_original_start_time(self, connect: Mock) -> None:
        """Retries should keep the first observed started_at timestamp."""
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = ScanJobStore(database_url="postgresql://localhost/test")

        store.mark_folder_started(
            job_id="00000000-0000-0000-0000-000000000001",
            folder="Archive",
        )

        update_sql = str(cursor.execute.call_args_list[0].args[0])
        self.assertIn("started_at = COALESCE(started_at, NOW())", update_sql)

    @patch("baldwin.jobs.psycopg.connect")
    def test_finalize_job_skips_when_any_folder_is_still_active(self, connect: Mock) -> None:
        """Finalization should be deferred until all folders are terminal."""
        cursor = MagicMock()
        cursor.fetchone.return_value = (1, 0, 1)  # remaining_count, failed_count, completed_count
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = ScanJobStore(database_url="postgresql://localhost/test")

        store.finalize_job(job_id="00000000-0000-0000-0000-000000000001")

        self.assertEqual(cursor.execute.call_count, 1)

    @patch("baldwin.jobs.psycopg.connect")
    def test_finalize_job_updates_only_pending_or_in_progress_rows(self, connect: Mock) -> None:
        """Finalization updates should be idempotent for already terminal job rows."""
        cursor = MagicMock()
        cursor.fetchone.return_value = (0, 1, 7)  # remaining_count, failed_count, completed_count
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = ScanJobStore(database_url="postgresql://localhost/test")

        store.finalize_job(job_id="00000000-0000-0000-0000-000000000001")

        self.assertEqual(cursor.execute.call_count, 2)
        update_sql = str(cursor.execute.call_args_list[1].args[0])
        self.assertIn("status IN ('pending', 'in_progress')", update_sql)

    @patch("baldwin.jobs.psycopg.connect")
    def test_mark_folder_failed_does_not_overwrite_completed_rows(self, connect: Mock) -> None:
        """Late failures should not flip a folder that already completed successfully."""
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = ScanJobStore(database_url="postgresql://localhost/test")

        store.mark_folder_failed(
            job_id="00000000-0000-0000-0000-000000000001",
            folder="Archive",
            error={"type": "EmailFetchError", "message": "boom"},
        )

        update_sql = str(cursor.execute.call_args_list[0].args[0])
        self.assertIn("status <> 'completed'", update_sql)

    @patch("baldwin.jobs.psycopg.connect")
    def test_mark_folders_failed_updates_multiple_rows(self, connect: Mock) -> None:
        """Bulk folder failure updates should support enqueue partial-failure recovery."""
        cursor = MagicMock()
        cursor.fetchone.return_value = (2,)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = ScanJobStore(database_url="postgresql://localhost/test")

        remaining = store.mark_folders_failed(
            job_id="00000000-0000-0000-0000-000000000001",
            folders=["Archive", "Bulk"],
            error={"type": "QueueEnqueueError", "message": "unsent"},
        )

        self.assertEqual(remaining, 2)
        update_sql = str(cursor.execute.call_args_list[0].args[0])
        self.assertIn("folder = ANY", update_sql)

    @patch("baldwin.jobs.psycopg.connect")
    def test_mark_folders_failed_with_empty_list_returns_zero_and_skips_db(self, connect: Mock) -> None:
        """Empty bulk failure updates should return early without opening a DB connection."""
        store = ScanJobStore(database_url="postgresql://localhost/test")

        remaining = store.mark_folders_failed(
            job_id="00000000-0000-0000-0000-000000000001",
            folders=[],
            error={"type": "QueueEnqueueError", "message": "unsent"},
        )

        self.assertEqual(remaining, 0)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
