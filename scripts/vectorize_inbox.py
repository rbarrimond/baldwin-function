"""Fetch inbox messages, vectorize them, and store them in PostgreSQL."""

from __future__ import annotations

import argparse
import json
import imaplib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# pylint: disable=wrong-import-position
from baldwin.email import (EmailNormalizer, EmailService, HashingVectorizer,
                           PostgresEmailVectorStore)


def _status(message: str) -> None:
    print(f"[vectorize-inbox] {message}", file=sys.stderr)


def _display_label(value: str, fallback: str = "(no subject)") -> str:
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return fallback
    if len(normalized) <= 80:
        return normalized
    return normalized[:77] + "..."


def _load_local_settings() -> None:
    settings_path = Path(__file__).resolve().parents[1] / "local.settings.json"
    if not settings_path.exists():
        _status("No local.settings.json found; using process environment only.")
        return

    with settings_path.open("r", encoding="utf-8") as settings_file:
        settings_data = json.load(settings_file)

    values = settings_data.get("Values")
    if not isinstance(values, dict):
        _status("local.settings.json does not contain a Values object; skipping load.")
        return

    loaded_names: list[str] = []
    for name, value in values.items():
        if isinstance(value, str) and name not in os.environ:
            os.environ[name] = value
            loaded_names.append(name)

    _status(
        "Loaded local settings for "
        + ", ".join(sorted(loaded_names))
        if loaded_names
        else "local.settings.json found; existing environment variables took precedence."
    )


def _get_setting(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _get_required_setting(*names: str) -> str:
    value = _get_setting(*names)
    if not value:
        raise ValueError(f"One of the settings {names} is required.")
    return value


@dataclass(frozen=True)
class ScriptSettings:
    """Resolved runtime settings for inbox vectorization."""

    imap_user: str
    imap_password: str
    imap_host: str
    imap_port: int
    database_url: str
    dimensions: int
    model_name: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=1, help="Inbox lookback window in days.")
    parser.add_argument(
        "--dimensions",
        type=int,
        default=int(_get_setting("EMAIL_VECTOR_DIMENSIONS", default="256") or "256"),
        help="Vector dimension size for local hashing vectors.",
    )
    parser.add_argument(
        "--model-name",
        default=_get_setting("EMAIL_VECTOR_MODEL", default="hashing-v1"),
        help="Identifier stored with the generated vectors.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and vectorize emails without writing to PostgreSQL.",
    )
    return parser.parse_args()


def _load_settings(args: argparse.Namespace) -> ScriptSettings:
    return ScriptSettings(
        imap_user=_get_required_setting("IMAP_USER", "MAIL_USERNAME"),
        imap_password=_get_required_setting("IMAP_PASSWORD", "MAIL_APP_PASSWORD"),
        imap_host=_get_setting("IMAP_HOST", default="imap.mail.me.com") or "imap.mail.me.com",
        imap_port=int(_get_setting("IMAP_PORT", default="993") or "993"),
        database_url=_get_required_setting("DATABASE_URL"),
        dimensions=args.dimensions,
        model_name=args.model_name,
    )


def main() -> int:
    """Run the inbox fetch, vectorization, and persistence workflow."""
    _status("Starting inbox vectorization run.")
    _load_local_settings()
    args = _parse_args()
    if args.days < 1 or args.days > 365:
        raise ValueError("--days must be between 1 and 365.")

    settings = _load_settings(args)
    _status(
        "Configuration resolved: "
        f"days={args.days} dry_run={args.dry_run} model={settings.model_name} "
        f"dimensions={settings.dimensions} imap_host={settings.imap_host}:{settings.imap_port}"
    )
    email_service = EmailService(
        settings.imap_user,
        settings.imap_password,
        imap_host=settings.imap_host,
        imap_port=settings.imap_port,
    )
    normalizer = EmailNormalizer()
    vectorizer = HashingVectorizer(dimensions=settings.dimensions, model_name=settings.model_name)
    store = PostgresEmailVectorStore(
        database_url=settings.database_url,
        dimensions=settings.dimensions,
        model_name=settings.model_name,
    )

    _status("Fetching emails from IMAP inbox.")
    emails = email_service.fetch_emails(args.days)
    _status(f"Fetched {len(emails)} email(s) from the inbox.")
    if not args.dry_run:
        _status("Bootstrapping PostgreSQL vector schema.")
        store.bootstrap()
        _status("PostgreSQL vector schema is ready.")
    else:
        _status("Dry-run mode enabled; PostgreSQL writes will be skipped.")

    fetched_count = len(emails)
    inserted_count = 0
    updated_count = 0
    skipped_count = 0

    for index, email_message in enumerate(emails, start=1):
        subject_label = _display_label(email_message.subject)
        _status(f"Processing email {index}/{fetched_count}: {subject_label}")
        try:
            normalized_email = normalizer.normalize(email_message)
            vector = vectorizer.vectorize(normalized_email.searchable_text)
            if args.dry_run:
                skipped_count += 1
                _status(f"Dry-run: generated vector for email {index}/{fetched_count}.")
                continue

            result = store.upsert_email(normalized_email, vector)
            if result.inserted:
                inserted_count += 1
            if result.embedding_updated:
                updated_count += 1
            _status(
                f"Stored email {index}/{fetched_count}: inserted={result.inserted} "
                f"embedding_updated={result.embedding_updated}"
            )
        except ValueError as exc:
            skipped_count += 1
            print(f"Skipping email: {exc}", file=sys.stderr)

    print(
        "Vectorization run complete: "
        f"fetched={fetched_count} inserted={inserted_count} "
        f"embedding_updates={updated_count} skipped={skipped_count}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except imaplib.IMAP4.error as exc:
        print(
            "IMAP authentication failed. Check IMAP_USER or MAIL_USERNAME and "
            "IMAP_PASSWORD or MAIL_APP_PASSWORD in local.settings.json or your environment.",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    except (OSError, RuntimeError) as exc:
        print(f"Runtime error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
