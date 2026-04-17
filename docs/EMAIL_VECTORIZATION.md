# Inbox Vectorization

This repository can fetch inbox messages from IMAP, normalize them into a stable persistence shape, generate deterministic local vectors, and store both metadata and vectors in PostgreSQL.

The persistence layer now uses a generic vector-document store with an email-specific adapter layered on top. The email adapter maps normalized emails into generic vector documents before delegating to PostgreSQL storage.

## Scope

- The first implementation is a manual script.
- The current vectorizer is local and deterministic rather than semantic.
- PostgreSQL is expected to have the `pgvector` extension available.
- Azure PostgreSQL is not provisioned in this repository yet.

## Configuration

The script accepts the following settings:

- `DATABASE_URL`: PostgreSQL connection string.
- `IMAP_USER` or `MAIL_USERNAME`: IMAP username.
- `IMAP_PASSWORD` or `MAIL_APP_PASSWORD`: IMAP password.
- `IMAP_HOST` (optional): IMAP hostname, default `imap.mail.me.com`.
- `IMAP_PORT` (optional): IMAP port, default `993`.
- `EMAIL_VECTOR_DIMENSIONS` (optional): vector dimension count, default `256`.
- `EMAIL_VECTOR_MODEL` (optional): stored model identifier, default `hashing-v1`.

## Schema

### `vector_documents`

- `id BIGSERIAL PRIMARY KEY`
- `document_key TEXT UNIQUE NOT NULL`: deterministic idempotency key.
- `source_type TEXT NOT NULL`: source classifier such as `email`.
- `source_id TEXT NULL`: upstream source identifier, such as `Message-ID`.
- `title TEXT NOT NULL`
- `body TEXT NOT NULL`
- `searchable_text TEXT NOT NULL`: normalized text used for vector generation.
- `metadata JSONB NOT NULL`: email-specific fields such as sender, recipients, raw date, parsed sent timestamp, and headers.
- `content_checksum TEXT NOT NULL`: checksum used to detect embedding refreshes.
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`

### `vector_embeddings`

- `document_id BIGINT PRIMARY KEY REFERENCES vector_documents(id) ON DELETE CASCADE`
- `model_name TEXT NOT NULL`
- `dimensions INTEGER NOT NULL`
- `embedding VECTOR NOT NULL`
- `content_checksum TEXT NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`

## Deduplication

The email adapter prefers `Message-ID` when it is present. If the upstream message does not provide one, the fallback fingerprint is computed from sender, date, subject, and normalized text content. Re-running the script against the same mailbox window is expected to be idempotent.

## Local Run

```bash
python scripts/vectorize_inbox.py --days 3
```

To validate connectivity without writes:

```bash
python scripts/vectorize_inbox.py --days 1 --dry-run
```

## Azure Promotion Path

When this feature moves beyond local development:

1. Provision Azure PostgreSQL Flexible Server in `azure-infra`.
2. Confirm `pgvector` availability in the target service tier.
3. Store the connection string in Key Vault.
4. Resolve the connection string through the existing managed identity and app settings pattern.
5. Replace bootstrap DDL with explicit migrations before production rollout.
