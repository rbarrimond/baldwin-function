"""Local CLI for reading a Things snapshot through Baldwin."""

import argparse
import json
from dataclasses import asdict

from baldwin.things import ThingsClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read areas, active projects, open todos, and notes from Things.")
    parser.add_argument(
        "--database-path",
        dest="database_path",
        help="Optional explicit path to the Things SQLite database.",
    )
    return parser


def main() -> int:
    """Run the Things snapshot CLI."""
    parser = _build_parser()
    args = parser.parse_args()
    snapshot = ThingsClient(database_path=args.database_path).fetch_snapshot()
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
