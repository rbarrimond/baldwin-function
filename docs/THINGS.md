# Things Integration

This repository includes a local-only `baldwin.things` package that reads data from the Things app through the third-party `things.py` library.

## Scope

This integration is designed for local macOS usage where the Things app and its SQLite database are available on the same machine. It is not part of the deployed Azure Function API surface.

## Returned Data

`ThingsClient.fetch_snapshot()` returns a typed `ThingsSnapshot` with four slices:

- `areas`: all areas of responsibility returned by `things.areas()`
- `projects`: active projects, defined as incomplete and non-trashed projects
- `todos`: open to-dos, defined as incomplete and non-trashed to-dos
- `notes`: non-empty notes attached to the returned projects and to-dos only

The package maps the third-party library's raw dictionaries into Baldwin dataclasses so the rest of the codebase can depend on a stable typed contract.

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

The CLI prints a JSON payload with `areas`, `projects`, `todos`, and `notes` arrays.

## Failure Semantics

- Missing or invalid client configuration raises `ThingsConfigurationError`.
- Runtime read failures from the underlying library are translated to `ThingsServiceError` with the original cause preserved.

## Dependency

This feature depends on `things.py` being installed in the local Python environment.
