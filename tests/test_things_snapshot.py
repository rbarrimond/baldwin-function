"""Unit tests for the local Things snapshot script."""

import io
import json
import unittest
from unittest.mock import patch

from baldwin.things import (
    ThingsArea,
    ThingsChecklistItem,
    ThingsHeading,
    ThingsNote,
    ThingsProject,
    ThingsSnapshot,
    ThingsTodo,
)
from scripts import things_snapshot


class ThingsSnapshotScriptTests(unittest.TestCase):
    """Coverage for the local CLI surface."""

    @patch("scripts.things_snapshot.ThingsClient")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_main_prints_snapshot_json(self, stdout, things_client) -> None:
        """The script should serialize the Baldwin snapshot to JSON."""
        things_client.return_value.fetch_snapshot.return_value = ThingsSnapshot(
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

        with patch("sys.argv", ["things_snapshot"]):
            exit_code = things_snapshot.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["areas"][0]["title"], "Family")
        self.assertEqual(payload["projects"][0]["uuid"], "project-1")
        self.assertEqual(payload["headings"][0]["title"], "Errands")
        self.assertEqual(payload["todos"][0]["checklist_items"][0]["title"], "Call provider")
        self.assertEqual(payload["notes"][0]["item_type"], "to-do")
        things_client.assert_called_once_with(database_path=None)

    @patch("scripts.things_snapshot.ThingsClient")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_main_passes_database_path_argument(self, _stdout, things_client) -> None:
        """The script should forward the optional database path to the client."""
        things_client.return_value.fetch_snapshot.return_value = ThingsSnapshot(
            areas=(),
            projects=(),
            headings=(),
            todos=(),
            notes=(),
        )

        with patch("sys.argv", ["things_snapshot", "--database-path", "/tmp/things.sqlite"]):
            exit_code = things_snapshot.main()

        self.assertEqual(exit_code, 0)
        things_client.assert_called_once_with(database_path="/tmp/things.sqlite")


if __name__ == "__main__":
    unittest.main()
