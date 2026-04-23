"""Unit tests for the Baldwin Things client."""

from collections.abc import Callable
from types import SimpleNamespace
import unittest
from typing import Any, Protocol, cast
from unittest.mock import patch

from baldwin.exceptions import ThingsConfigurationError, ThingsServiceError
from baldwin.things import ThingsChecklistItem, ThingsClient


class _ThingsModule(Protocol):
    """Typed surface of the third-party module used by the client tests."""

    areas: Callable[..., list[dict[str, Any]]]
    projects: Callable[..., list[dict[str, Any]]]
    tasks: Callable[..., list[dict[str, Any]]]
    todos: Callable[..., Any]


class ThingsClientTests(unittest.TestCase):
    """Coverage for local Things snapshot mapping and failures."""

    @staticmethod
    def _build_things_module(
        *,
        areas: Callable[..., list[dict[str, Any]]],
        projects: Callable[..., list[dict[str, Any]]],
        tasks: Callable[..., list[dict[str, Any]]],
        todos: Callable[..., Any],
    ) -> _ThingsModule:
        return cast(
            _ThingsModule,
            SimpleNamespace(
                areas=areas,
                projects=projects,
                tasks=tasks,
                todos=todos,
            ),
        )

    def test_fetch_snapshot_maps_areas_projects_todos_and_notes(self) -> None:
        """The client should expose the requested Things slices as typed Baldwin models."""
        things_module = self._build_things_module(
            areas=lambda **_: [
                {"uuid": "area-1", "title": "Family"},
                {"uuid": "area-2", "title": "Health"},
            ],
            projects=lambda **_: [
                {
                    "uuid": "project-1",
                    "title": "Quarterly Review",
                    "area": "area-2",
                    "notes": "Bring last quarter metrics.",
                    "status": "incomplete",
                },
                {
                    "uuid": "project-2",
                    "title": "Home Repairs",
                    "area": "area-1",
                    "notes": "   ",
                    "status": "incomplete",
                },
            ],
            tasks=lambda **_: [
                {
                    "uuid": "heading-1",
                    "title": "Errands",
                    "project": "project-1",
                    "project_title": "Quarterly Review",
                    "notes": "",
                    "status": "incomplete",
                    "start": "Anytime",
                }
            ],
            todos=lambda **_: [
                {
                    "uuid": "todo-1",
                    "title": "Book dentist",
                    "project": "project-1",
                    "project_title": "Quarterly Review",
                    "area": "area-2",
                    "heading": "heading-1",
                    "heading_title": "Errands",
                    "notes": "Ask about whitening.",
                    "status": "incomplete",
                    "start": "Anytime",
                    "checklist": [
                        {
                            "uuid": "check-1",
                            "title": "Call provider",
                            "status": "incomplete",
                        }
                    ],
                },
                {
                    "uuid": "todo-2",
                    "title": "Buy paint",
                    "project": "project-2",
                    "area": "area-1",
                    "notes": "",
                    "status": "incomplete",
                    "start": "Someday",
                },
            ],
        )

        with patch.object(ThingsClient, "_load_things_module", return_value=things_module):
            snapshot = ThingsClient().fetch_snapshot()

        self.assertEqual([area.title for area in snapshot.areas], ["Family", "Health"])
        self.assertEqual([project.title for project in snapshot.projects], ["Quarterly Review", "Home Repairs"])
        self.assertEqual([heading.title for heading in snapshot.headings], ["Errands"])
        self.assertEqual([todo.title for todo in snapshot.todos], ["Book dentist", "Buy paint"])
        self.assertEqual(snapshot.todos[0].project_title, "Quarterly Review")
        self.assertEqual(snapshot.todos[0].heading_title, "Errands")
        self.assertEqual(
            snapshot.todos[0].checklist_items,
            (ThingsChecklistItem(uuid="check-1", title="Call provider", status="incomplete"),),
        )
        self.assertEqual(len(snapshot.notes), 2)
        self.assertEqual(snapshot.notes[0].item_type, "project")
        self.assertEqual(snapshot.notes[0].content, "Bring last quarter metrics.")
        self.assertEqual(snapshot.notes[1].item_type, "to-do")
        self.assertEqual(snapshot.notes[1].project_uuid, "project-1")

    def test_fetch_snapshot_passes_explicit_database_path_to_things_calls(self) -> None:
        """The optional database filepath should be forwarded to the library calls."""
        call_kwargs: dict[str, dict[str, str | bool]] = {}

        def _areas(**kwargs):
            call_kwargs["areas"] = kwargs
            return []

        def _projects(**kwargs):
            call_kwargs["projects"] = kwargs
            return []

        def _tasks(**kwargs):
            call_kwargs["tasks"] = kwargs
            return []

        def _todos(**kwargs):
            call_kwargs["todos"] = kwargs
            return []

        things_module = self._build_things_module(areas=_areas, projects=_projects, tasks=_tasks, todos=_todos)

        with patch.object(ThingsClient, "_load_things_module", return_value=things_module):
            ThingsClient(database_path="/tmp/things.sqlite").fetch_snapshot()

        self.assertEqual(call_kwargs["areas"], {"filepath": "/tmp/things.sqlite"})
        self.assertEqual(
            call_kwargs["projects"],
            {"filepath": "/tmp/things.sqlite", "status": "incomplete", "trashed": False},
        )
        self.assertEqual(
            call_kwargs["tasks"],
            {"filepath": "/tmp/things.sqlite", "type": "heading", "status": "incomplete", "trashed": False},
        )
        self.assertEqual(
            call_kwargs["todos"],
            {"filepath": "/tmp/things.sqlite", "status": "incomplete", "trashed": False},
        )

    def test_fetch_snapshot_wraps_library_failures_with_causality(self) -> None:
        """Unexpected things.py runtime failures should become semantic Baldwin errors."""
        def _projects(**_kwargs):
            raise RuntimeError("database unavailable")

        things_module = self._build_things_module(
            areas=lambda **_: [],
            projects=_projects,
            tasks=lambda **_: [],
            todos=lambda **_: [],
        )

        with patch.object(ThingsClient, "_load_things_module", return_value=things_module):
            with self.assertRaises(ThingsServiceError) as captured:
                ThingsClient().fetch_snapshot()

        self.assertEqual(str(captured.exception), "Failed to read Things data.")
        self.assertIsInstance(captured.exception.__cause__, RuntimeError)

    def test_fetch_snapshot_replaces_blank_todo_titles_with_stable_fallback(self) -> None:
        """Blank todo titles from Things should not break snapshot creation."""
        things_module = self._build_things_module(
            areas=lambda **_: [],
            projects=lambda **_: [],
            tasks=lambda **_: [],
            todos=lambda **_: [
                {
                    "uuid": "todo-blank",
                    "title": "",
                    "notes": "",
                    "status": "incomplete",
                    "start": "Anytime",
                }
            ],
        )

        with patch.object(ThingsClient, "_load_things_module", return_value=things_module):
            snapshot = ThingsClient().fetch_snapshot()

        self.assertEqual(snapshot.todos[0].title, "(untitled to-do)")

    def test_fetch_snapshot_rejects_invalid_checklist_shape(self) -> None:
        """Checklist values must remain structured lists of checklist-item mappings."""
        things_module = self._build_things_module(
            areas=lambda **_: [],
            projects=lambda **_: [],
            tasks=lambda **_: [],
            todos=lambda **_: [
                {
                    "uuid": "todo-1",
                    "title": "Book dentist",
                    "checklist": "not-a-list",
                }
            ],
        )

        with patch.object(ThingsClient, "_load_things_module", return_value=things_module):
            with self.assertRaises(ThingsServiceError) as captured:
                ThingsClient().fetch_snapshot()

        self.assertEqual(str(captured.exception), "Things to-do field 'checklist' must be a list when present.")

    def test_fetch_snapshot_hydrates_checklist_items_from_detailed_todo_when_summary_only_has_flag(self) -> None:
        """Boolean checklist flags on summary rows should trigger a detailed to-do read."""
        call_log: list[tuple[Any, dict[str, Any]]] = []

        def _todos(*args, **kwargs):
            call_log.append((args, kwargs))
            if args:
                return {
                    "uuid": "todo-1",
                    "title": "Update Quicken Accounts",
                    "checklist": [
                        {
                            "uuid": "check-1",
                            "title": "Capital One",
                            "status": "incomplete",
                        }
                    ],
                }
            return [
                {
                    "uuid": "todo-1",
                    "title": "Update Quicken Accounts",
                    "checklist": True,
                    "status": "incomplete",
                    "start": "Anytime",
                }
            ]

        things_module = self._build_things_module(
            areas=lambda **_: [],
            projects=lambda **_: [],
            tasks=lambda **_: [],
            todos=_todos,
        )

        with patch.object(ThingsClient, "_load_things_module", return_value=things_module):
            snapshot = ThingsClient(database_path="/tmp/things.sqlite").fetch_snapshot()

        self.assertEqual(
            snapshot.todos[0].checklist_items,
            (ThingsChecklistItem(uuid="check-1", title="Capital One", status="incomplete"),),
        )
        self.assertEqual(call_log[1], (("todo-1",), {"filepath": "/tmp/things.sqlite"}))

    @patch("baldwin.things.client.importlib.import_module")
    def test_fetch_snapshot_raises_configuration_error_when_dependency_missing(self, import_module_mock) -> None:
        """Missing things.py should raise a configuration error with preserved cause."""
        import_module_mock.side_effect = ImportError("No module named 'things'")

        with self.assertRaises(ThingsConfigurationError) as captured:
            ThingsClient().fetch_snapshot()

        self.assertIn("things.py dependency is not installed", str(captured.exception))
        self.assertIsInstance(captured.exception.__cause__, ImportError)

    def test_blank_database_path_raises_configuration_error(self) -> None:
        """Blank database paths should be rejected before any library call."""
        with self.assertRaises(ThingsConfigurationError):
            ThingsClient(database_path="   ")


if __name__ == "__main__":
    unittest.main()
