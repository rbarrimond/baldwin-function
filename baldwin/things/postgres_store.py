"""PostgreSQL persistence for Baldwin Things snapshots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TypeAlias

import psycopg
from psycopg import sql

from baldwin.exceptions import ThingsStoreError

from .models import (
    ThingsArea,
    ThingsChecklistItem,
    ThingsHeading,
    ThingsNote,
    ThingsProject,
    ThingsSnapshot,
    ThingsTodo,
)

Statement: TypeAlias = sql.SQL | sql.Composed


class PostgresThingsStore:
    """Persists a full Things snapshot into normalized PostgreSQL tables."""

    def __init__(
        self,
        database_url: str,
        *,
        areas_table: str = "things_areas",
        projects_table: str = "things_projects",
        headings_table: str = "things_headings",
        todos_table: str = "things_todos",
        checklist_items_table: str = "things_checklist_items",
        notes_table: str = "things_notes",
    ):
        if not database_url:
            raise ValueError("database_url is required")

        self.database_url = database_url
        self.areas_table = areas_table
        self.projects_table = projects_table
        self.headings_table = headings_table
        self.todos_table = todos_table
        self.checklist_items_table = checklist_items_table
        self.notes_table = notes_table

    def bootstrap(self) -> None:
        """Create the Things snapshot tables when they do not yet exist."""
        try:
            with psycopg.connect(self.database_url, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {areas_table} (
                                uuid TEXT PRIMARY KEY,
                                title TEXT NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                            )
                            """
                        ).format(areas_table=sql.Identifier(self.areas_table))
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {projects_table} (
                                uuid TEXT PRIMARY KEY,
                                title TEXT NOT NULL,
                                area_uuid TEXT,
                                notes TEXT,
                                status TEXT,
                                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                            )
                            """
                        ).format(projects_table=sql.Identifier(self.projects_table))
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {headings_table} (
                                uuid TEXT PRIMARY KEY,
                                title TEXT NOT NULL,
                                project_uuid TEXT,
                                project_title TEXT,
                                notes TEXT,
                                status TEXT,
                                start_value TEXT,
                                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                            )
                            """
                        ).format(headings_table=sql.Identifier(self.headings_table))
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {todos_table} (
                                uuid TEXT PRIMARY KEY,
                                title TEXT NOT NULL,
                                project_uuid TEXT,
                                project_title TEXT,
                                area_uuid TEXT,
                                heading_uuid TEXT,
                                heading_title TEXT,
                                notes TEXT,
                                status TEXT,
                                start_value TEXT,
                                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                            )
                            """
                        ).format(todos_table=sql.Identifier(self.todos_table))
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {checklist_items_table} (
                                uuid TEXT PRIMARY KEY,
                                todo_uuid TEXT NOT NULL,
                                title TEXT NOT NULL,
                                status TEXT,
                                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                            )
                            """
                        ).format(checklist_items_table=sql.Identifier(self.checklist_items_table))
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {notes_table} (
                                item_uuid TEXT NOT NULL,
                                item_type TEXT NOT NULL,
                                title TEXT NOT NULL,
                                content TEXT NOT NULL,
                                project_uuid TEXT,
                                area_uuid TEXT,
                                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                PRIMARY KEY (item_uuid, item_type)
                            )
                            """
                        ).format(notes_table=sql.Identifier(self.notes_table))
                    )
        except psycopg.Error as exc:
            raise ThingsStoreError("Failed to bootstrap PostgreSQL Things storage.") from exc

    def replace_snapshot(self, snapshot: ThingsSnapshot) -> None:
        """Replace the stored Things snapshot with the latest in-memory snapshot."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._delete_all(cursor)
                    self._insert_areas(cursor, snapshot.areas)
                    self._insert_projects(cursor, snapshot.projects)
                    self._insert_headings(cursor, snapshot.headings)
                    self._insert_todos(cursor, snapshot.todos)
                    self._insert_checklist_items(cursor, snapshot.todos)
                    self._insert_notes(cursor, snapshot.notes)
                connection.commit()
        except psycopg.Error as exc:
            raise ThingsStoreError("Failed to persist Things snapshot.") from exc

    def _delete_all(self, cursor: psycopg.Cursor) -> None:
        for table_name in (
            self.checklist_items_table,
            self.notes_table,
            self.todos_table,
            self.headings_table,
            self.projects_table,
            self.areas_table,
        ):
            cursor.execute(sql.SQL("DELETE FROM {table_name}").format(table_name=sql.Identifier(table_name)))

    def _insert_areas(self, cursor: psycopg.Cursor, areas: tuple[ThingsArea, ...]) -> None:
        self._executemany(
            cursor,
            sql.SQL(
                """
                INSERT INTO {areas_table} (uuid, title)
                VALUES (%(uuid)s, %(title)s)
                """
            ).format(areas_table=sql.Identifier(self.areas_table)),
            ({"uuid": area.uuid, "title": area.title} for area in areas),
        )

    def _insert_projects(self, cursor: psycopg.Cursor, projects: tuple[ThingsProject, ...]) -> None:
        self._executemany(
            cursor,
            sql.SQL(
                """
                INSERT INTO {projects_table} (uuid, title, area_uuid, notes, status)
                VALUES (%(uuid)s, %(title)s, %(area_uuid)s, %(notes)s, %(status)s)
                """
            ).format(projects_table=sql.Identifier(self.projects_table)),
            (
                {
                    "uuid": project.uuid,
                    "title": project.title,
                    "area_uuid": project.area_uuid,
                    "notes": project.notes,
                    "status": project.status,
                }
                for project in projects
            ),
        )

    def _insert_headings(self, cursor: psycopg.Cursor, headings: tuple[ThingsHeading, ...]) -> None:
        self._executemany(
            cursor,
            sql.SQL(
                """
                INSERT INTO {headings_table} (
                    uuid,
                    title,
                    project_uuid,
                    project_title,
                    notes,
                    status,
                    start_value
                )
                VALUES (
                    %(uuid)s,
                    %(title)s,
                    %(project_uuid)s,
                    %(project_title)s,
                    %(notes)s,
                    %(status)s,
                    %(start)s
                )
                """
            ).format(headings_table=sql.Identifier(self.headings_table)),
            (
                {
                    "uuid": heading.uuid,
                    "title": heading.title,
                    "project_uuid": heading.project_uuid,
                    "project_title": heading.project_title,
                    "notes": heading.notes,
                    "status": heading.status,
                    "start": heading.start,
                }
                for heading in headings
            ),
        )

    def _insert_todos(self, cursor: psycopg.Cursor, todos: tuple[ThingsTodo, ...]) -> None:
        self._executemany(
            cursor,
            sql.SQL(
                """
                INSERT INTO {todos_table} (
                    uuid,
                    title,
                    project_uuid,
                    project_title,
                    area_uuid,
                    heading_uuid,
                    heading_title,
                    notes,
                    status,
                    start_value
                )
                VALUES (
                    %(uuid)s,
                    %(title)s,
                    %(project_uuid)s,
                    %(project_title)s,
                    %(area_uuid)s,
                    %(heading_uuid)s,
                    %(heading_title)s,
                    %(notes)s,
                    %(status)s,
                    %(start)s
                )
                """
            ).format(todos_table=sql.Identifier(self.todos_table)),
            (
                {
                    "uuid": todo.uuid,
                    "title": todo.title,
                    "project_uuid": todo.project_uuid,
                    "project_title": todo.project_title,
                    "area_uuid": todo.area_uuid,
                    "heading_uuid": todo.heading_uuid,
                    "heading_title": todo.heading_title,
                    "notes": todo.notes,
                    "status": todo.status,
                    "start": todo.start,
                }
                for todo in todos
            ),
        )

    def _insert_checklist_items(self, cursor: psycopg.Cursor, todos: tuple[ThingsTodo, ...]) -> None:
        self._executemany(
            cursor,
            sql.SQL(
                """
                INSERT INTO {checklist_items_table} (uuid, todo_uuid, title, status)
                VALUES (%(uuid)s, %(todo_uuid)s, %(title)s, %(status)s)
                """
            ).format(checklist_items_table=sql.Identifier(self.checklist_items_table)),
            self._checklist_rows(todos),
        )

    def _insert_notes(self, cursor: psycopg.Cursor, notes: tuple[ThingsNote, ...]) -> None:
        self._executemany(
            cursor,
            sql.SQL(
                """
                INSERT INTO {notes_table} (item_uuid, item_type, title, content, project_uuid, area_uuid)
                VALUES (%(item_uuid)s, %(item_type)s, %(title)s, %(content)s, %(project_uuid)s, %(area_uuid)s)
                """
            ).format(notes_table=sql.Identifier(self.notes_table)),
            (
                {
                    "item_uuid": note.item_uuid,
                    "item_type": note.item_type,
                    "title": note.title,
                    "content": note.content,
                    "project_uuid": note.project_uuid,
                    "area_uuid": note.area_uuid,
                }
                for note in notes
            ),
        )

    @staticmethod
    def _checklist_rows(todos: tuple[ThingsTodo, ...]) -> Iterable[dict[str, str | None]]:
        for todo in todos:
            for checklist_item in todo.checklist_items:
                yield PostgresThingsStore._checklist_row(todo.uuid, checklist_item)

    @staticmethod
    def _checklist_row(todo_uuid: str, checklist_item: ThingsChecklistItem) -> dict[str, str | None]:
        return {
            "uuid": checklist_item.uuid,
            "todo_uuid": todo_uuid,
            "title": checklist_item.title,
            "status": checklist_item.status,
        }

    @staticmethod
    def _executemany(
        cursor: psycopg.Cursor,
        statement: Statement,
        rows: Iterable[Mapping[str, object]],
    ) -> None:
        batch = list(rows)
        if not batch:
            return
        cursor.executemany(statement, batch)
