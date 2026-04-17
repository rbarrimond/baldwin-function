# Mailbox Folder Vectorization

This repository can fetch messages from one or more IMAP folders, normalize them into a stable persistence shape, generate embeddings through a shared provider layer, and store both metadata and vectors in PostgreSQL.

The persistence layer now uses a generic vector-document store with an email-specific adapter layered on top. The email adapter maps normalized emails into generic vector documents before delegating to PostgreSQL storage.

## Scope

- The first implementation is a manual script.
- The default local embedding provider is Ollama over HTTP.
- Deterministic hashing remains available as a fallback and test baseline.
- When Ollama rejects a long input for context length, the runtime now splits the normalized email text into smaller chunks and stores a single length-weighted aggregate embedding for the document instead of immediately falling back.
- PostgreSQL is expected to have the `pgvector` extension available.
- Azure PostgreSQL is not provisioned in this repository yet.

## Configuration

The script accepts the following settings:

- `DATABASE_URL`: PostgreSQL connection string.
- `IMAP_USER` or `MAIL_USERNAME`: IMAP username.
- `IMAP_PASSWORD` or `MAIL_APP_PASSWORD`: IMAP password.
- `IMAP_HOST` (optional): IMAP hostname, default `imap.mail.me.com`.
- `IMAP_PORT` (optional): IMAP port, default `993`.
- `IMAP_FOLDERS` (optional): comma-separated default IMAP folder list, default `INBOX`.
- `EMBEDDING_PROVIDER` (optional): provider identifier, default `ollama`.
- `EMBEDDING_BASE_URL` (optional): provider base URL, default `http://127.0.0.1:11434`.
- `EMBEDDING_MODEL` (optional): model identifier, default `qllama/bge-small-en-v1.5`.
- `EMBEDDING_TIMEOUT_SECONDS` (optional): HTTP timeout, default `30`.
- `EMBEDDING_ENABLE_FALLBACK` (optional): whether fallback is enabled, default `true`.
- `EMBEDDING_FALLBACK_PROVIDER` (optional): fallback provider identifier, default `hashing`.
- `EMBEDDING_HASH_DIMENSIONS` (optional): hashing vector dimension count, default `256`.
- `EMAIL_VECTOR_DIMENSIONS` and `EMAIL_VECTOR_MODEL` remain accepted as compatibility aliases.

## Schema

### `vector_documents`

- `id BIGSERIAL PRIMARY KEY`
- `document_key TEXT UNIQUE NOT NULL`: deterministic idempotency key.
- `source_type TEXT NOT NULL`: source classifier such as `email`.
- `source_id TEXT NULL`: upstream source identifier, such as `Message-ID`.
- `title TEXT NOT NULL`
- `body TEXT NOT NULL`
- `searchable_text TEXT NOT NULL`: normalized text used for vector generation.
- `metadata JSONB NOT NULL`: email-specific fields such as sender, recipients, raw date, parsed sent timestamp, primary source folder, folder provenance list, and headers.
- `content_checksum TEXT NOT NULL`: checksum used to detect embedding refreshes.
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`

### `vector_embeddings`

- `document_id BIGINT NOT NULL REFERENCES vector_documents(id) ON DELETE CASCADE`
- `provider TEXT NOT NULL`: embedding provider identifier such as `ollama` or `hashing`
- `model_name TEXT NOT NULL`
- `dimensions INTEGER NOT NULL`
- `embedding VECTOR NOT NULL`
- `content_checksum TEXT NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `PRIMARY KEY (document_id, provider, model_name)`

## Deduplication

The email adapter prefers `Message-ID` when it is present. If the upstream message does not provide one, the fallback fingerprint is computed from sender, date, subject, and normalized text content. Re-running the script against the same mailbox-folder window is expected to be idempotent within the same provider-model space, while still allowing additional embeddings to be stored for other providers or models.

When the same message appears in multiple scanned folders, the mailbox vectorization runtime collapses those duplicates into one persisted document and stores folder provenance in `metadata.folders`, while `metadata.folder` preserves the first folder as a compatibility alias.

## Long Email Embeddings

For Ollama-backed embeddings, long normalized emails are first attempted as a single input. If Ollama returns a context-length error, the runtime recursively splits the text on paragraph or whitespace boundaries, embeds the smaller chunks, and stores one normalized length-weighted aggregate vector for the original document. This keeps one embedding row per document/provider/model while reducing unnecessary fallback to hashing.

## Local Run

```bash
python scripts/vectorize_mailbox.py --days 3 --folder INBOX --folder Archive
```

To validate connectivity without writes:

```bash
python scripts/vectorize_mailbox.py --days 1 --folder INBOX --folder Archive --dry-run
```

The legacy `scripts/vectorize_inbox.py` entrypoint remains available as a compatibility shim.

## Azure Promotion Path

When this feature moves beyond local development:

1. Provision Azure PostgreSQL Flexible Server in `azure-infra`.
2. Confirm `pgvector` availability in the target service tier.
3. Store the connection string in Key Vault.
4. Resolve the connection string through the existing managed identity and app settings pattern.
5. Replace bootstrap DDL with explicit migrations before production rollout.
6. Ensure retrieval selects a single provider/model space rather than mixing embeddings across models.
