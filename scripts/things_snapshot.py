"""Local CLI for reading a Things snapshot through Baldwin."""

import argparse
import json
import os
import sys
from dataclasses import asdict

from baldwin.things import PostgresThingsStore, ThingsClient, ThingsConfigurationError, ThingsServiceError, ThingsStoreError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read areas, active projects, open todos, and notes from Things.")
    parser.add_argument(
        "--database-path",
        dest="database_path",
        help="Optional explicit path to the Things SQLite database.",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist the fetched snapshot to PostgreSQL.",
    )
    parser.add_argument(
        "--postgres-database-url",
        dest="postgres_database_url",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL connection string used with --persist. Defaults to DATABASE_URL.",
    )
    return parser


def _persist_snapshot(snapshot, database_url: str | None) -> None:
    if not database_url:
        raise ThingsConfigurationError("A PostgreSQL database URL is required when --persist is used.")

    store = PostgresThingsStore(database_url)
    store.bootstrap()
    store.replace_snapshot(snapshot)


def main() -> int:
    """Run the Things snapshot CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    try:
        snapshot = ThingsClient(database_path=args.database_path).fetch_snapshot()
        if args.persist:
            _persist_snapshot(snapshot, args.postgres_database_url)
    except (ThingsConfigurationError, ThingsServiceError, ThingsStoreError) as exc:
        print(f"[things-snapshot] {exc}", file=sys.stderr)
        return 1

    payload = {
        "areas": [asdict(area) for area in snapshot.areas],
        "projects": [asdict(project) for project in snapshot.projects],
        "headings": [asdict(heading) for heading in snapshot.headings],
        "todos": [asdict(todo) for todo in snapshot.todos],
        "notes": [asdict(note) for note in snapshot.notes],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
