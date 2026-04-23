# Things Integration

This repository includes a local-only `baldwin.things` package that reads data from the Things app through the third-party `things.py` library.

## Scope

This integration is designed for local macOS usage where the Things app and its SQLite database are available on the same machine. It is not part of the deployed Azure Function API surface.

## Returned Data

`ThingsClient.fetch_snapshot()` returns a typed `ThingsSnapshot` with five top-level slices:

- `areas`: all areas of responsibility returned by `things.areas()`
- `projects`: active projects, defined as incomplete and non-trashed projects
- `headings`: incomplete, non-trashed Things headings or sections returned through `things.tasks(type="heading", ...)`
- `todos`: open to-dos, defined as incomplete and non-trashed to-dos
- `notes`: non-empty notes attached to the returned projects and to-dos only

The package maps the third-party library's raw dictionaries into Baldwin dataclasses so the rest of the codebase can depend on a stable typed contract.

To-dos also carry explicit nested checklist modeling when checklist items exist. Summary to-do rows in `things.py` may expose checklist presence only as a boolean flag, so the client performs a detailed to-do read to hydrate `checklist_items` when needed.

## Usage

```python
from baldwin.things import ThingsClient

snapshot = ThingsClient().fetch_snapshot()

for project in snapshot.projects:
    print(project.title)
```

To use an explicit database file instead of the library default:

```python
snapshot = ThingsClient(database_path="/path/to/ThingsData-ABCD1234.sqlite").fetch_snapshot()
```

## CLI

The repository also provides a local CLI entrypoint:

```bash
things-snapshot
```

Optional explicit database path:

```bash
things-snapshot --database-path /path/to/ThingsData-ABCD1234.sqlite
```

The CLI prints a JSON payload with `areas`, `projects`, `headings`, `todos`, and `notes` arrays. Each to-do object may also contain a `checklist_items` array plus optional `project_title`, `heading_uuid`, and `heading_title` context when that data is present in the Things database.

## Failure Semantics

- Missing or invalid client configuration raises `ThingsConfigurationError`.
- Runtime read failures from the underlying library are translated to `ThingsServiceError` with the original cause preserved.

## Dependency

This feature depends on `things.py` being installed in the local Python environment.
