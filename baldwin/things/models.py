"""Typed Baldwin models for local Things data."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ThingsArea:
    """An area of responsibility from Things."""

    uuid: str
    title: str


@dataclass(frozen=True)
class ThingsProject:
    """An active Things project."""

    uuid: str
    title: str
    area_uuid: str | None
    notes: str | None
    status: str | None


@dataclass(frozen=True)
class ThingsHeading:
    """A project section or heading from Things."""

    uuid: str
    title: str
    project_uuid: str | None
    project_title: str | None
    notes: str | None
    status: str | None
    start: str | None


@dataclass(frozen=True)
class ThingsChecklistItem:
    """A checklist item embedded within a Things to-do."""

    uuid: str
    title: str
    status: str | None


@dataclass(frozen=True)
class ThingsTodo:
    """An open Things to-do."""

    uuid: str
    title: str
    project_uuid: str | None
    project_title: str | None
    area_uuid: str | None
    heading_uuid: str | None
    heading_title: str | None
    notes: str | None
    status: str | None
    start: str | None
    checklist_items: tuple[ThingsChecklistItem, ...] = ()


@dataclass(frozen=True)
class ThingsNote:
    """A non-empty note attached to an active project or open to-do."""

    item_uuid: str
    item_type: str
    title: str
    content: str
    project_uuid: str | None = None
    area_uuid: str | None = None


@dataclass(frozen=True)
class ThingsSnapshot:
    """Requested local Things slices for Baldwin consumers."""

    areas: tuple[ThingsArea, ...]
    projects: tuple[ThingsProject, ...]
    headings: tuple[ThingsHeading, ...]
    todos: tuple[ThingsTodo, ...]
    notes: tuple[ThingsNote, ...]
