"""Fetch inbox messages, vectorize them, and store them in PostgreSQL."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baldwin.email import (
    EmailNormalizer,
    EmailService,
    HashingVectorizer,
    PostgresEmailVectorStore,
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
        database_url=_get_required_setting("DATABASE_URL"),
        dimensions=args.dimensions,
        model_name=args.model_name,
    )


def main() -> int:
    """Run the inbox fetch, vectorization, and persistence workflow."""
    args = _parse_args()
    if args.days < 1 or args.days > 365:
        raise ValueError("--days must be between 1 and 365.")

    settings = _load_settings(args)
    email_service = EmailService(settings.imap_user, settings.imap_password)
    normalizer = EmailNormalizer()
    vectorizer = HashingVectorizer(dimensions=settings.dimensions, model_name=settings.model_name)
    store = PostgresEmailVectorStore(
        database_url=settings.database_url,
        dimensions=settings.dimensions,
        model_name=settings.model_name,
    )

    emails = email_service.fetch_emails(args.days)
    if not args.dry_run:
        store.bootstrap()

    fetched_count = len(emails)
    inserted_count = 0
    updated_count = 0
    skipped_count = 0

    for email_message in emails:
        try:
            normalized_email = normalizer.normalize(email_message)
            vector = vectorizer.vectorize(normalized_email.searchable_text)
            if args.dry_run:
                skipped_count += 1
                continue

            result = store.upsert_email(normalized_email, vector)
            if result.inserted:
                inserted_count += 1
            if result.embedding_updated:
                updated_count += 1
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
    raise SystemExit(main())
