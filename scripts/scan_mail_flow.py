"""Run the scan-mail ingestion flow from the command line."""

from __future__ import annotations

import argparse
import json
import imaplib
import os
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# pylint: disable=wrong-import-position
from baldwin.email import DEFAULT_IMAP_FOLDER, EmailFetchError, MailboxFolders
from baldwin.embedding import EmbeddingProviderError
from baldwin.exceptions import (
    BaldwinConfigurationError,
    BaldwinValidationError,
    VectorStoreError,
)
from baldwin.http_handlers import EmailIngestionService, EnvironmentSettings

DEFAULT_SCAN_MAIL_FLOW_MAX_WORKERS = 8


def _status(message: str) -> None:
    print(f"[scan-mail-flow] {message}", file=sys.stderr)


def _is_caused_by(exc: BaseException, expected_type: type[BaseException]) -> bool:
    return isinstance(exc.__cause__, expected_type)


def _load_runtime_environ() -> dict[str, str]:
    runtime_environ = dict(os.environ)
    settings_path = Path(__file__).resolve().parents[1] / "local.settings.json"
    if not settings_path.exists():
        _status("No local.settings.json found; using process environment only.")
        return runtime_environ

    with settings_path.open("r", encoding="utf-8") as settings_file:
        settings_data = json.load(settings_file)

    values = settings_data.get("Values")
    if not isinstance(values, dict):
        _status("local.settings.json does not contain a Values object; skipping load.")
        return runtime_environ

    loaded_names: list[str] = []
    for name, value in values.items():
        if isinstance(value, str) and name not in runtime_environ:
            runtime_environ[name] = value
            loaded_names.append(name)

    _status(
        "Loaded local settings for " + ", ".join(sorted(loaded_names))
        if loaded_names
        else "local.settings.json found; existing environment variables took precedence."
    )
    return runtime_environ


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=1, help="IMAP folder lookback window in days.")
    parser.add_argument(
        "--folder",
        action="append",
        dest="folders",
        help="IMAP folder to scan. Repeat or use comma-separated values for multiple folders.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_SCAN_MAIL_FLOW_MAX_WORKERS,
        help="Upper bound for threaded scan-mail fetch, normalization, and embedding stages.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the scan-mail ingestion flow with explicit CLI inputs."""
    try:
        args = _parse_args(argv)
        if args.days < 1 or args.days > 365:
            raise ValueError("--days must be between 1 and 365.")
        if args.max_workers < 1:
            raise ValueError("--max-workers must be greater than 0.")

        runtime_environ = _load_runtime_environ()
        runtime_environ["SCAN_MAIL_MAX_WORKERS"] = str(args.max_workers)
        settings = EnvironmentSettings(runtime_environ)
        configured_folders = settings.get("IMAP_FOLDERS", DEFAULT_IMAP_FOLDER)
        folders = MailboxFolders.from_values(
            args.folders,
            default_values=[configured_folders] if configured_folders else None,
        )

        _status(
            "Running scan-mail flow: "
            f"days={args.days} workers={args.max_workers} folders={folders}"
        )
        summary = EmailIngestionService(settings).ingest_mailbox(args.days, folders)
        print(json.dumps(summary, indent=2))
        return 0
    except (ValueError, BaldwinConfigurationError, BaldwinValidationError) as exc:
        _status(str(exc))
        return 2
    except EmailFetchError as exc:
        if _is_caused_by(exc, imaplib.IMAP4.error):
            _status("Unable to read from the requested IMAP folders.")
            return 3
        _status(f"Unexpected email fetch error: {exc}")
        return 4
    except (EmbeddingProviderError, VectorStoreError) as exc:
        _status(f"Scan-mail flow failed: {exc}")
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
