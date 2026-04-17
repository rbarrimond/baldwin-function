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
from baldwin.email import EmailNormalizer, EmailService, PostgresEmailVectorStore
from baldwin.embedding import build_embedding_service, load_embedding_settings


def _status(message: str) -> None:
    print(f"[vectorize-inbox] {message}", file=sys.stderr)


def _display_label(value: str, fallback: str = "(no subject)") -> str:
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return fallback
    if len(normalized) <= 80:
        return normalized
    return normalized[:77] + "..."


def _format_chunking_status(metadata: dict[str, object]) -> str | None:
    chunk_count = metadata.get("chunk_count")
    if not isinstance(chunk_count, int) or chunk_count <= 1:
        return None

    chunk_lengths = metadata.get("chunk_lengths")
    if isinstance(chunk_lengths, list) and all(isinstance(length, int) for length in chunk_lengths):
        max_chunk_length = max(chunk_lengths) if chunk_lengths else 0
        return f"chunked={chunk_count} max_chunk_length={max_chunk_length}"

    return f"chunked={chunk_count}"


def _render_progress(
    current: int,
    total: int,
    label: str,
    *,
    inserted: int,
    updated: int,
    skipped: int,
) -> None:
    if total <= 0:
        return

    width = 24
    filled = int(width * current / total)
    progress_bar = "#" * filled + "-" * (width - filled)
    message = (
        f"\r[vectorize-inbox] Processing [{progress_bar}] {current}/{total} "
        f"inserted={inserted} updated={updated} skipped={skipped} "
        f"subject={label}"
    )
    print(message, end="", file=sys.stderr, flush=True)


def _finish_progress(total: int) -> None:
    if total > 0:
        print(file=sys.stderr, flush=True)


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
    embedding_provider: str
    embedding_model: str
    embedding_base_url: str
    embedding_timeout_seconds: float
    hashing_dimensions: int
    fallback_provider: str | None
    enable_fallback: bool


@dataclass(frozen=True)
class ProcessingCounters:
    """Running totals for the vectorization progress bar."""

    inserted: int
    updated: int
    skipped: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=1, help="Inbox lookback window in days.")
    parser.add_argument(
        "--embedding-provider",
        default=_get_setting("EMBEDDING_PROVIDER", default="ollama"),
        help="Embedding provider identifier.",
    )
    parser.add_argument(
        "--embedding-base-url",
        default=_get_setting("EMBEDDING_BASE_URL", default="http://127.0.0.1:11434"),
        help="Base URL for the HTTP embedding provider.",
    )
    parser.add_argument(
        "--dimensions",
        type=int,
        default=int(
            _get_setting("EMBEDDING_HASH_DIMENSIONS", "EMAIL_VECTOR_DIMENSIONS", default="256")
            or "256"
        ),
        help="Vector dimension size for hashing fallback embeddings.",
    )
    parser.add_argument(
        "--model-name",
        default=_get_setting("EMBEDDING_MODEL", "EMAIL_VECTOR_MODEL", default="qllama/bge-small-en-v1.5"),
        help="Identifier stored with the generated vectors.",
    )
    parser.add_argument(
        "--embedding-timeout-seconds",
        type=float,
        default=float(_get_setting("EMBEDDING_TIMEOUT_SECONDS", default="30") or "30"),
        help="Timeout in seconds for HTTP embedding calls.",
    )
    parser.add_argument(
        "--fallback-provider",
        default=_get_setting("EMBEDDING_FALLBACK_PROVIDER", default="hashing"),
        help="Fallback provider identifier to use when the primary provider fails.",
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
        embedding_provider=args.embedding_provider,
        embedding_model=args.model_name,
        embedding_base_url=args.embedding_base_url,
        embedding_timeout_seconds=args.embedding_timeout_seconds,
        hashing_dimensions=args.dimensions,
        fallback_provider=args.fallback_provider,
        enable_fallback=(_get_setting("EMBEDDING_ENABLE_FALLBACK", default="true") or "true").lower()
        not in {"0", "false", "no", "off"},
    )


def _process_email(
    *,
    email_message,
    args: argparse.Namespace,
    normalizer: EmailNormalizer,
    embedding_service,
    store: PostgresEmailVectorStore,
) -> tuple[bool, bool, bool]:
    normalized_email = normalizer.normalize(email_message)
    embedding_result = embedding_service.embed_text(normalized_email.searchable_text)
    chunking_status = _format_chunking_status(embedding_result.embedding.metadata)
    if embedding_result.used_fallback:
        _status(
            "Primary embedding provider failed; using fallback provider "
            f"{embedding_result.embedding.provider}. reason={embedding_result.fallback_reason}"
        )
    elif chunking_status:
        _status(
            "Primary embedding provider chunked long input. "
            f"provider={embedding_result.embedding.provider} {chunking_status}"
        )

    if args.dry_run:
        return False, False, True

    result = store.upsert_email(normalized_email, embedding_result.embedding)
    return result.inserted, result.embedding_updated, False


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
        f"days={args.days} dry_run={args.dry_run} provider={settings.embedding_provider} "
        f"model={settings.embedding_model} fallback={settings.fallback_provider} "
        f"imap_host={settings.imap_host}:{settings.imap_port}"
    )
    email_service = EmailService(
        settings.imap_user,
        settings.imap_password,
        imap_host=settings.imap_host,
        imap_port=settings.imap_port,
    )
    normalizer = EmailNormalizer()
    embedding_service = build_embedding_service(
        load_embedding_settings(
            {
                "provider_name": settings.embedding_provider,
                "model_name": settings.embedding_model,
                "base_url": settings.embedding_base_url,
                "timeout_seconds": settings.embedding_timeout_seconds,
                "hashing_dimensions": settings.hashing_dimensions,
                "fallback_provider_name": settings.fallback_provider,
                "enable_fallback": settings.enable_fallback,
            }
        )
    )
    store = PostgresEmailVectorStore(
        database_url=settings.database_url,
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
        try:
            inserted, updated, skipped = _process_email(
                email_message=email_message,
                args=args,
                normalizer=normalizer,
                embedding_service=embedding_service,
                store=store,
            )
            if inserted:
                inserted_count += 1
            if updated:
                updated_count += 1
            if skipped:
                skipped_count += 1
            _render_progress(
                index,
                fetched_count,
                subject_label,
                inserted=inserted_count,
                updated=updated_count,
                skipped=skipped_count,
            )
        except ValueError as exc:
            skipped_count += 1
            _render_progress(
                index,
                fetched_count,
                subject_label,
                inserted=inserted_count,
                updated=updated_count,
                skipped=skipped_count,
            )
            print(f"Skipping email: {exc}", file=sys.stderr)

    _finish_progress(fetched_count)

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
