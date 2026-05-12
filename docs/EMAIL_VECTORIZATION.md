# Mailbox Folder Vectorization

This repository can fetch messages from one or more IMAP folders, normalize them into a stable persistence shape, generate embeddings through a shared provider layer, and store both metadata and vectors in PostgreSQL.

The persistence layer now uses a generic vector-document store with an email-specific adapter layered on top. The email adapter maps normalized emails into generic vector documents before delegating to PostgreSQL storage.

The email adapter now also supports mailbox cursor state and reconciliation through `mailbox_sync_state` and `document_sync_runs`, while the core vector document and embedding tables remain the content source of truth.

The `scan-mail` HTTP path now uses bounded multithreading for per-folder fetch, normalization, and embedding stages, while keeping persistence, sync-state writes, and folder-membership reconciliation serialized to preserve the documented mailbox invariants.

## Resilience

Per-message IMAP fetch operations are resilient to permanent message-specific failures:

- A message fetch that receives `NO` status with reason text matching `"no such message"`, `"expunged"`, or `"invalid messageset"` is logged at WARNING and skipped.
- The folder job continues ingesting remaining messages in the batch.
- Transient service issues (e.g., iCloud `[UNAVAILABLE]`) are retried locally with bounded exponential backoff before the single message is skipped.
- Folder-level operations (SELECT, SEARCH, UID enumeration) and all other IMAP protocol errors remain hard failures that propagate as `EmailFetchError`.
- This allows mailbox ingestion to continue in the face of concurrent mailbox churn (permanent deletions/expunges between SEARCH and FETCH) and transient backend throttling/outages without failing an entire folder job.

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
- `SCAN_MAIL_MAX_WORKERS` (optional): upper bound for threaded `scan-mail` fetch, normalization, and embedding stages, default `4`.
- `EMBEDDING_PROVIDER` (optional): provider identifier, default `ollama`. Accepts `ollama`, `hashing`, or `azure-openai`.
- `EMBEDDING_BASE_URL` (optional): provider base URL for Ollama, default `http://127.0.0.1:11434`.
- `EMBEDDING_MODEL` (optional): model identifier, default `qllama/bge-small-en-v1.5`. When `azure-openai`, defaults to `text-embedding-3-small`.
- `EMBEDDING_TIMEOUT_SECONDS` (optional): HTTP timeout, default `30`.
- `EMBEDDING_ENABLE_FALLBACK` (optional): whether fallback is enabled, default `true`.
- `EMBEDDING_FALLBACK_PROVIDER` (optional): fallback provider identifier, default `hashing`.
- `EMBEDDING_HASH_DIMENSIONS` (optional): hashing vector dimension count, default `256`.
- `EMAIL_VECTOR_DIMENSIONS` and `EMAIL_VECTOR_MODEL` remain accepted as compatibility aliases.
- `AZURE_OPENAI_ENDPOINT` (required when `azure-openai`): base endpoint URL of the Azure OpenAI resource.
- `AZURE_OPENAI_API_KEY` (required when `azure-openai`): Azure OpenAI API key.
- `AZURE_OPENAI_API_VERSION` (optional when `azure-openai`): REST API version, default `2024-02-01`.
- `BALDWIN_LOG_LEVEL` (optional): effective log level for all `baldwin.*` loggers. Accepts `DEBUG`, `INFO`, `WARNING`, `ERROR`. Defaults to `WARNING`.

## Schema

### `vector_documents`

- `id BIGSERIAL PRIMARY KEY`
- `document_key TEXT UNIQUE NOT NULL`: deterministic idempotency key.
- `source_type TEXT NOT NULL`: source classifier such as `email`.
- `source_id TEXT NULL`: upstream source identifier, such as `Message-ID`.
- `title TEXT NOT NULL`
- `body TEXT NOT NULL`
- `searchable_text TEXT NOT NULL`: normalized text used for vector generation.
- `metadata JSONB NOT NULL`: email-specific fields such as sender, recipients, raw date, parsed sent timestamp, primary source folder, folder provenance list, current `folder_uids` mapping, per-folder IMAP `folder_flags`, per-folder IMAP `folder_keywords`, and headers.
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

### `mailbox_sync_state`

- `id BIGSERIAL PRIMARY KEY`
- `imap_user TEXT NOT NULL`
- `imap_host TEXT NOT NULL`
- `imap_folder TEXT NOT NULL`
- `uidvalidity BIGINT NOT NULL DEFAULT 0`: current IMAP folder `UIDVALIDITY` frontier.
- `last_synced_uid BIGINT NULL`: last successfully observed UID frontier for the folder.
- `last_sync_time TIMESTAMPTZ NOT NULL`
- `sync_run_id UUID NOT NULL`
- `total_emails_in_folder BIGINT NOT NULL DEFAULT 0`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `UNIQUE (imap_user, imap_host, imap_folder, uidvalidity)`

### `document_sync_runs`

- `document_id BIGINT NOT NULL REFERENCES vector_documents(id) ON DELETE CASCADE`
- `sync_run_id UUID NOT NULL`
- `was_present_in_mailbox BOOLEAN NOT NULL DEFAULT TRUE`
- `folder_names JSONB NOT NULL DEFAULT '[]'::jsonb`
- `folder_uids JSONB NOT NULL DEFAULT '{}'::jsonb`
- `last_seen_at TIMESTAMPTZ NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `PRIMARY KEY (document_id, sync_run_id)`

## IMAP Flags And Keywords

The email adapter persists IMAP mailbox metadata separately from message content:

- `metadata.folder_flags`: map of folder name to the ordered IMAP flags reported for that folder membership.
- `metadata.folder_keywords`: map of folder name to the ordered user-defined IMAP keywords reported for that folder membership.
- Starred mail is represented by the IMAP system flag `\Flagged` inside the relevant `folder_flags` entry.

These fields are additive metadata only. They do not participate in fingerprint computation, duplicate collapse, or embedding refresh detection.

## Deduplication

The email adapter prefers `Message-ID` when it is present. If the upstream message does not provide one, the fallback fingerprint is computed from sender, date, subject, and normalized text content. Re-running the script against the same mailbox-folder window is expected to be idempotent within the same provider-model space, while still allowing additional embeddings to be stored for other providers or models.

When the same message appears in multiple scanned folders, the mailbox vectorization runtime collapses those duplicates into one persisted document and stores folder provenance in `metadata.folders`, while `metadata.folder` preserves the first folder as a compatibility alias. When the IMAP server provides UIDs, the runtime also tracks the current UID per folder in `metadata.folder_uids`. IMAP flags and keywords are likewise persisted per folder in `metadata.folder_flags` and `metadata.folder_keywords` because the same logical message can have different mailbox state across folders.

**Message-ID collision guard:** If two distinct messages share the same `Message-ID` (a known real-world occurrence with some mail clients), the runtime detects the content divergence via `content_checksum` comparison and re-keys the later message using a content-derived SHA-256 fingerprint instead of silently overwriting the earlier document. The re-keyed document is treated as a new insert.

Each `scan-mail` ingestion run also records which persisted documents were observed, the current folder UID frontier, and whether previously tracked folder memberships disappeared from the server. If a folder membership disappears, the runtime removes that folder entry from `metadata.folders`, `metadata.folder_uids`, `metadata.folder_flags`, and `metadata.folder_keywords`. If a document no longer belongs to any tracked folder after reconciliation, the email document and its embeddings are deleted.

## Long Email Embeddings

For Ollama-backed embeddings, long normalized emails are first attempted as a single input. If Ollama returns a context-length error, the runtime recursively splits the text on paragraph or whitespace boundaries, embeds the smaller chunks, and stores one normalized length-weighted aggregate vector for the original document. This keeps one embedding row per document/provider/model while reducing unnecessary fallback to hashing.

## Logging

All `baldwin.*` modules use structured JSON logging via `baldwin.log`. Each record is a single-line JSON object with the keys `timestamp`, `level`, `logger`, and `message`. When a trace ID is bound in the current context via `set_trace_id()`, a `trace_id` key is also included.

`DEBUG` and `INFO` records are written to stdout. `WARNING` and above are written to stderr. This routing prevents the Azure Functions local host from flattening all log output to `INFO` colour due to raw-text inspection.

The effective log level is controlled by `BALDWIN_LOG_LEVEL` (defaults to `WARNING`). Set `BALDWIN_LOG_LEVEL=DEBUG` locally to see per-email normalization, embedding, and persistence traces.

## Embedding Cost

The default Baldwin embedding path is local Ollama over HTTP. That means the current runtime does not incur a direct per-request vendor bill in the way a token-priced hosted API would. The immediate cost surface today is local compute time, memory pressure, model load time, and the total amount of text the runtime sends to the embedding provider.

Both the manual mailbox CLI and the `scan-mail` HTTP path use the same shared embedding runtime, so cost analysis should be based on the normalized `searchable_text` inputs described in this document rather than on route-specific request shapes.

### Current Observable Inputs

The runtime already exposes a small set of useful cost-adjacent signals in embedding metadata:

- `input_text_length`: normalized character count for the source text sent to the provider.
- `chunk_count`: number of chunked embedding requests used after a context-length overflow.
- `chunk_lengths`: normalized character counts for the chunked requests.
- `total_duration`: provider-reported total request duration when available.
- `load_duration`: provider-reported model load duration when available.

These values are sufficient for a bounded operational report, but they are not token accounting. Any estimate expressed in tokens or hosted-provider cost must be labeled as a derived approximation unless the runtime is later extended with tokenizer-aware measurement.

### Current Cost Truth

- Default provider cost: local Ollama, so no direct vendor charge per embedding call.
- Fallback cost: deterministic hashing fallback, also with no direct vendor charge.
- Persisted cost unit: one stored embedding row per document, provider, and model.
- Chunking effect: long emails can trigger multiple provider calls before producing the single stored embedding row for the original document.

The important operational distinction is that storage remains one row per document-provider-model space, while compute cost can increase when a long email is split into multiple chunk embeddings.

### Proxy Report Model

Until token accounting exists, the recommended report should separate raw observables from derived estimates.

Recommended raw observables per run:

- total normalized emails embedded
- total normalized characters embedded
- total chunked emails
- total chunk requests generated by chunking
- fallback count
- aggregate provider duration

Recommended derived estimates per run:

- average characters per normalized email = total normalized characters / total normalized emails
- average chunk requests per chunked email = total chunk requests / total chunked emails
- average provider time per normalized email = aggregate provider duration / total normalized emails

When comparing against a hosted embedding provider, use the same normalized `searchable_text` values and the same chunking behavior, then apply the target provider's tokenization and pricing model to those inputs. That comparison should document the provider name, model name, pricing date, tokenizer assumption, and whether chunk retries are counted as additional billable input.

### Manual Reporting Workflow

Use the existing CLI workflow for a one-off operational report:

1. Run a dry run to verify mailbox access and confirm the folder scope.
2. Run a representative non-dry mailbox vectorization pass over the target folders and time window.
3. Record the provider, model, folder scope, and sample window used for the run.
4. Collect the normalized character counts, chunking metadata, fallback count, and provider timing values for the sampled emails.
5. Publish the report with both raw observables and any derived hosted-cost comparison assumptions.

If a future phase requires repeatable cost dashboards rather than a one-off report, add structured aggregation in the embedding runtime before introducing token-priced precision claims.

### Azure OpenAI Comparison

For a concrete hosted comparison target, use Azure OpenAI embedding pricing and compare it against the same persisted `searchable_text` values stored in Baldwin.

As of `2026-04-27`, the Azure OpenAI pricing page lists the following embedding model rates:

- `text-embedding-3-small`: `$0.000022` per 1K tokens
- `text-embedding-3-large`: `$0.000143` per 1K tokens
- `ada`: `$0.00011` per 1K tokens

Pricing source: `https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/`

Use the following estimate when Baldwin does not have tokenizer-aware accounting yet:

$$
estimated\_tokens = \left\lceil \frac{total\_input\_characters}{chars\_per\_token} \right\rceil
$$

$$
estimated\_cost\_usd = \frac{estimated\_tokens}{1000} \times price\_per\_1k\_tokens
$$

The recommended first-pass heuristic is `4.0` characters per token. This is only a proxy. Re-verify the Azure pricing page, chosen deployment region, and tokenizer behavior before using the result for budgeting.

### Persisted Cost Report CLI

This repository now includes a small reporting CLI that reads persisted `vector_documents` and `vector_embeddings` rows and generates a repeatable JSON summary.

Example usage:

```bash
embedding-cost-report --database-url "$DATABASE_URL"
embedding-cost-report --database-url "$DATABASE_URL" --provider ollama --model-name qllama/bge-small-en-v1.5
embedding-cost-report --database-url "$DATABASE_URL" --azure-openai-model text-embedding-3-small
```

The report reads persisted `searchable_text` values and estimates token volume from character counts. It does not recover runtime-only metadata such as request durations, `chunk_count`, or `chunk_lengths`, because those fields are not currently stored in PostgreSQL.

## Local Run

```bash
python scripts/vectorize_mailbox.py --days 3 --folder INBOX --folder Archive
```

To validate connectivity without writes:

```bash
python scripts/vectorize_mailbox.py --days 1 --folder INBOX --folder Archive --dry-run
```

The legacy `scripts/vectorize_inbox.py` entrypoint remains available as a compatibility shim.

The standalone CLI script still performs mailbox ingestion as a manual vectorization workflow. The mailbox cursor state and reconciliation logic described above currently runs through the `scan-mail` HTTP path.

## Azure Promotion Path

When this feature moves beyond local development:

1. Provision Azure PostgreSQL Flexible Server in `azure-infra`.
2. Confirm `pgvector` availability in the target service tier.
3. Store the connection string in Key Vault.
4. Resolve the connection string through the existing managed identity and app settings pattern.
5. Replace bootstrap DDL with explicit migrations before production rollout.
6. Ensure retrieval selects a single provider/model space rather than mixing embeddings across models.
