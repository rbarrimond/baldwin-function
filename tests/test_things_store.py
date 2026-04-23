"""Unit tests for Things snapshot PostgreSQL persistence."""

import unittest
from unittest.mock import MagicMock, Mock, patch

import psycopg

from baldwin.exceptions import ThingsStoreError
from baldwin.things import (
    PostgresThingsStore,
    ThingsArea,
    ThingsChecklistItem,
    ThingsHeading,
    ThingsNote,
    ThingsProject,
    ThingsSnapshot,
    ThingsTodo,
)


class PostgresThingsStoreTests(unittest.TestCase):
    """Regression tests for Things snapshot persistence."""

    def _build_snapshot(self) -> ThingsSnapshot:
        return ThingsSnapshot(
            areas=(ThingsArea(uuid="area-1", title="Family"),),
            projects=(
                ThingsProject(
                    uuid="project-1",
                    title="Quarterly Review",
                    area_uuid="area-1",
                    notes="Bring last quarter metrics.",
                    status="incomplete",
                ),
            ),
            headings=(
                ThingsHeading(
                    uuid="heading-1",
                    title="Errands",
                    project_uuid="project-1",
                    project_title="Quarterly Review",
                    notes="",
                    status="incomplete",
                    start="Anytime",
                ),
            ),
            todos=(
                ThingsTodo(
                    uuid="todo-1",
                    title="Book dentist",
                    project_uuid="project-1",
                    project_title="Quarterly Review",
                    area_uuid="area-1",
                    heading_uuid="heading-1",
                    heading_title="Errands",
                    notes="Ask about whitening.",
                    status="incomplete",
                    start="Anytime",
                    checklist_items=(
                        ThingsChecklistItem(uuid="check-1", title="Call provider", status="incomplete"),
                    ),
                ),
            ),
            notes=(
                ThingsNote(
                    item_uuid="todo-1",
                    item_type="to-do",
                    title="Book dentist",
                    content="Ask about whitening.",
                    project_uuid="project-1",
                    area_uuid="area-1",
                ),
            ),
        )

    @patch("baldwin.things.postgres_store.psycopg.connect")
    def test_bootstrap_creates_things_snapshot_tables(self, connect: Mock) -> None:
        """Bootstrap should emit schema creation for each Things table."""
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = PostgresThingsStore(database_url="postgresql://localhost/test")

        store.bootstrap()

        self.assertEqual(cursor.execute.call_count, 6)
        statements = [str(call.args[0]) for call in cursor.execute.call_args_list]
        self.assertTrue(any("things_areas" in statement for statement in statements))
        self.assertTrue(any("things_projects" in statement for statement in statements))
        self.assertTrue(any("things_headings" in statement for statement in statements))
        self.assertTrue(any("things_todos" in statement for statement in statements))
        self.assertTrue(any("things_checklist_items" in statement for statement in statements))
        self.assertTrue(any("things_notes" in statement for statement in statements))

    @patch("baldwin.things.postgres_store.psycopg.connect")
    def test_replace_snapshot_clears_and_inserts_all_snapshot_slices(self, connect: Mock) -> None:
        """Replacing a snapshot should clear old rows and persist all normalized Things models."""
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.cursor.return_value.__exit__.return_value = None
        connect.return_value.__enter__.return_value = connection
        connect.return_value.__exit__.return_value = None

        store = PostgresThingsStore(database_url="postgresql://localhost/test")

        store.replace_snapshot(self._build_snapshot())

        self.assertEqual(cursor.execute.call_count, 6)
        delete_statements = [str(call.args[0]) for call in cursor.execute.call_args_list]
        self.assertIn("DELETE FROM", delete_statements[0])
        self.assertIn("things_checklist_items", delete_statements[0])
        self.assertIn("DELETE FROM", delete_statements[-1])
        self.assertIn("things_areas", delete_statements[-1])
        self.assertEqual(cursor.executemany.call_count, 6)
        checklist_rows = cursor.executemany.call_args_list[4].args[1]
        self.assertEqual(
            checklist_rows,
            [{"uuid": "check-1", "todo_uuid": "todo-1", "title": "Call provider", "status": "incomplete"}],
        )
        note_rows = cursor.executemany.call_args_list[5].args[1]
        self.assertEqual(note_rows[0]["item_type"], "to-do")
        connection.commit.assert_called_once_with()

    @patch("baldwin.things.postgres_store.psycopg.connect")
    def test_bootstrap_wraps_database_errors_with_causality(self, connect: Mock) -> None:
        """Bootstrap failures should surface as ThingsStoreError with the original cause preserved."""
        connect.side_effect = psycopg.OperationalError("db unavailable")
        store = PostgresThingsStore(database_url="postgresql://localhost/test")

        with self.assertRaises(ThingsStoreError) as captured:
            store.bootstrap()

        self.assertIsInstance(captured.exception.__cause__, psycopg.OperationalError)

    @patch("baldwin.things.postgres_store.psycopg.connect")
    def test_replace_snapshot_wraps_database_errors_with_causality(self, connect: Mock) -> None:
        """Persistence failures should surface as ThingsStoreError with the original cause preserved."""
        connect.side_effect = psycopg.OperationalError("db unavailable")
        store = PostgresThingsStore(database_url="postgresql://localhost/test")

        with self.assertRaises(ThingsStoreError) as captured:
            store.replace_snapshot(self._build_snapshot())

        self.assertIsInstance(captured.exception.__cause__, psycopg.OperationalError)


if __name__ == "__main__":
    unittest.main()
