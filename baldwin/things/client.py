"""Local Things database client backed by things.py."""

from collections.abc import Mapping
import importlib
from typing import Any, cast

from baldwin.exceptions import ThingsConfigurationError, ThingsServiceError

from .models import ThingsArea, ThingsNote, ThingsProject, ThingsSnapshot, ThingsTodo

_THINGS_IMPORT_ERROR_MESSAGE = (
    "The things.py dependency is not installed. Install runtime dependencies before using baldwin.things."
)
_UNTITLED_TODO_TITLE = "(untitled to-do)"


class ThingsClient:
    """Read local Things data through the third-party things.py library."""

    def __init__(self, *, database_path: str | None = None):
        """Create a client with an optional explicit Things database path."""
        if database_path is not None and not database_path.strip():
            raise ThingsConfigurationError("Things database path cannot be blank.")
        self._database_path = database_path

    def fetch_snapshot(self) -> ThingsSnapshot:
        """Return areas, active projects, open todos, and related notes."""
        things_module = self._load_things_module()
        query_kwargs = self._build_query_kwargs()

        try:
            raw_areas = self._read_collection(things_module.areas(**query_kwargs), entity_name="areas")
            raw_projects = self._read_collection(
                things_module.projects(status="incomplete", trashed=False, **query_kwargs),
                entity_name="projects",
            )
            raw_todos = self._read_collection(
                things_module.todos(status="incomplete", trashed=False, **query_kwargs),
                entity_name="todos",
            )
        except ThingsServiceError:
            raise
        except Exception as exc:  # pragma: no cover - defensive wrapper for unexpected library failures
            raise ThingsServiceError("Failed to read Things data.") from exc

        areas = tuple(self._map_area(entry) for entry in raw_areas)
        projects = tuple(self._map_project(entry) for entry in raw_projects)
        todos = tuple(self._map_todo(entry) for entry in raw_todos)
        notes = self._collect_notes(projects=projects, todos=todos)
        return ThingsSnapshot(areas=areas, projects=projects, todos=todos, notes=notes)

    def _build_query_kwargs(self) -> dict[str, str]:
        if self._database_path is None:
            return {}
        return {"filepath": self._database_path}

    @staticmethod
    def _load_things_module() -> Any:
        try:
            things = importlib.import_module("things")
        except ImportError as exc:
            raise ThingsConfigurationError(_THINGS_IMPORT_ERROR_MESSAGE) from exc
        return things

    @staticmethod
    def _read_collection(raw_value: Any, *, entity_name: str) -> list[Mapping[str, Any]]:
        if not isinstance(raw_value, list):
            raise ThingsServiceError(f"Things returned an invalid {entity_name} payload.")

        normalized: list[Mapping[str, Any]] = []
        for entry in raw_value:
            if not isinstance(entry, Mapping):
                raise ThingsServiceError(f"Things returned an invalid {entity_name} item.")
            normalized.append(cast(Mapping[str, Any], entry))
        return normalized

    @staticmethod
    def _require_string(entry: Mapping[str, Any], field_name: str, *, entity_name: str) -> str:
        value = entry.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ThingsServiceError(f"Things {entity_name} is missing required field '{field_name}'.")
        return value

    @staticmethod
    def _optional_string(entry: Mapping[str, Any], field_name: str) -> str | None:
        value = entry.get(field_name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ThingsServiceError(f"Things field '{field_name}' must be a string when present.")
        return value

    @classmethod
    def _todo_title(cls, entry: Mapping[str, Any]) -> str:
        title = cls._optional_string(entry, "title")
        if title is None:
            raise ThingsServiceError("Things to-do is missing required field 'title'.")
        if title.strip():
            return title
        return _UNTITLED_TODO_TITLE

    @classmethod
    def _map_area(cls, entry: Mapping[str, Any]) -> ThingsArea:
        return ThingsArea(
            uuid=cls._require_string(entry, "uuid", entity_name="area"),
            title=cls._require_string(entry, "title", entity_name="area"),
        )

    @classmethod
    def _map_project(cls, entry: Mapping[str, Any]) -> ThingsProject:
        return ThingsProject(
            uuid=cls._require_string(entry, "uuid", entity_name="project"),
            title=cls._require_string(entry, "title", entity_name="project"),
            area_uuid=cls._optional_string(entry, "area"),
            notes=cls._optional_string(entry, "notes"),
            status=cls._optional_string(entry, "status"),
        )

    @classmethod
    def _map_todo(cls, entry: Mapping[str, Any]) -> ThingsTodo:
        return ThingsTodo(
            uuid=cls._require_string(entry, "uuid", entity_name="to-do"),
            title=cls._todo_title(entry),
            project_uuid=cls._optional_string(entry, "project"),
            area_uuid=cls._optional_string(entry, "area"),
            notes=cls._optional_string(entry, "notes"),
            status=cls._optional_string(entry, "status"),
            start=cls._optional_string(entry, "start"),
        )

    @staticmethod
    def _collect_notes(
        *,
        projects: tuple[ThingsProject, ...],
        todos: tuple[ThingsTodo, ...],
    ) -> tuple[ThingsNote, ...]:
        notes: list[ThingsNote] = []

        for project in projects:
            if project.notes and project.notes.strip():
                notes.append(
                    ThingsNote(
                        item_uuid=project.uuid,
                        item_type="project",
                        title=project.title,
                        content=project.notes,
                        area_uuid=project.area_uuid,
                    )
                )

        for todo in todos:
            if todo.notes and todo.notes.strip():
                notes.append(
                    ThingsNote(
                        item_uuid=todo.uuid,
                        item_type="to-do",
                        title=todo.title,
                        content=todo.notes,
                        project_uuid=todo.project_uuid,
                        area_uuid=todo.area_uuid,
                    )
                )

        return tuple(notes)
