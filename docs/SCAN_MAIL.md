# Scan-Mail Email Ingestion

Baldwin provides two ingestion entry points:

- **`POST /api/scan-mail`** — Async, queue-backed, per-folder fan-out. Preferred for production.
- **`GET /api/scan-mail`** — Synchronous single-request scan. Deprecated; kept for backwards compatibility.

Async ingestion enqueues one Azure Storage Queue message per folder, returns `202 Accepted` immediately with a job ID, and allows each folder to be processed independently by a queue-triggered worker. This document covers both modes.

## Async Ingestion (POST)

### Enqueue Endpoint

- Method: `POST`
- Route: `/api/scan-mail`
- Content type: `application/json`

#### Request Body

```json
{
  "days": 7,
  "folders": ["INBOX", "Archive"]
}
```

- `days`: Optional integer lookback window. Defaults to `1`. Must be greater than `0`.
- `folders`: Optional list of IMAP folder names. Falls back to `IMAP_FOLDERS`, then `INBOX`.

#### Response — 202 Accepted

```json
{
  "job_id": "3f6b1a2c-...",
  "folder_count": 2,
  "status_url": "/api/scan-mail/status/3f6b1a2c-..."
}
```

### Status Endpoint

- Method: `GET`
- Route: `/api/scan-mail/status/{job_id}`

#### Response — 200 OK

```json
{
  "job_id": "3f6b1a2c-...",
  "status": "in_progress",
  "folder_count": 2,
  "params": { "days": 7, "folders": ["INBOX", "Archive"] },
  "created_at": "2025-06-01T02:00:00Z",
  "completed_at": null,
  "folders": [
    {
      "folder": "INBOX",
      "status": "completed",
      "started_at": "2025-06-01T02:00:01Z",
      "completed_at": "2025-06-01T02:00:12Z",
      "stats": { "total_fetched": 8, "total_deduped": 7, "total_persisted": 7 },
      "error": null
    },
    {
      "folder": "Archive",
      "status": "in_progress",
      "started_at": "2025-06-01T02:00:08Z",
      "completed_at": null,
      "stats": null,
      "error": null
    }
  ]
}
```

Job `status` values: `pending`, `in_progress`, `completed`, `partial` (at least one folder failed).

#### Response — 404 Not Found

```json
{ "error": "Scan job not found: '3f6b1a2c-...'" }
```

### Queue Message Schema

Each message placed on `scan-mail-jobs` has the following JSON body:

```json
{ "job_id": "3f6b1a2c-...", "folder": "INBOX", "days": 7 }
```

Messages are sent as raw UTF-8 JSON (no base64 encoding). The `QueueClient` is created with `message_encode_policy=None`.

If enqueuing fails partway through a folder list, Baldwin records only the unsent folders as failed (`QueueEnqueueError`) so scan status remains accurate and does not leave orphan `pending` rows.

### Async Flow Sequence

1. `POST /api/scan-mail` — parse request, allocate `job_id`, persist job + folder rows in PostgreSQL (`scan_jobs`, `scan_job_folders`), create queue if absent, enqueue one message per folder.
2. `process_scan_folder` (queue trigger) — for each message: marks folder `in_progress`, calls `ingest_folder()`, marks `completed` or `failed` with stats/error, decrements remaining count.
3. If a folder message exceeds `maxDequeueCount`, Azure moves it to `scan-mail-jobs-poison`; `process_scan_folder_poison` records a terminal folder failure (`QueuePoisonError`) and participates in normal job finalization.
4. When `remaining == 0` — `_finalize_folder_job()` runs `delete_documents_without_folders()` then marks the job `completed` (or `partial` if any folder failed).
5. `cleanup_scan_jobs` (timer trigger at 02:00 UTC daily) — deletes job records older than `SCAN_JOB_RETENTION_DAYS`.

### Multi-Folder JSONB Correctness

When multiple folders are scanned in parallel and the same email appears in more than one folder, concurrent upserts must merge — not overwrite — the `folders`, `folder_uids`, `folder_flags`, and `folder_keywords` fields.

`PostgresEmailVectorStore` overrides `_upsert_email_on_connection()` to perform this merge via `ON CONFLICT DO UPDATE` with PostgreSQL JSONB set-union logic:

```sql
metadata = EXCLUDED.metadata || jsonb_build_object(
    'folders', <set-union of existing + incoming arrays>,
    'folder_uids', COALESCE(existing, '{}') || COALESCE(incoming, '{}'),
    ...
)
```

The `delete_documents_without_folders()` cleanup step runs only once, after all folder jobs have completed, to avoid removing documents that will be re-observed by a concurrent folder worker.

---

## Synchronous Ingestion (GET) — Deprecated

The `GET /api/scan-mail` endpoint is deprecated. It remains available for backwards compatibility but returns a `_deprecation_notice` field in its response body. Use `POST /api/scan-mail` for new integrations.

- Method: `GET`
- Route: `/api/scan-mail`
- Content type: `application/json`

### Query Parameters

- `days`: Optional integer lookback window. Defaults to `1`. Must be greater than `0`.
- `folders`: Optional comma-separated IMAP folder list. If omitted, the runtime falls back to `IMAP_FOLDERS`, then to `INBOX`.

### Example Request

```bash
curl "http://localhost:7071/api/scan-mail?days=1&folders=INBOX,Archive"
```

### Success Response

Status: `200 OK`

```json
{
  "_deprecation_notice": "GET /api/scan-mail is deprecated. Use POST /api/scan-mail for async execution.",
  "total_fetched": 12,
  "total_normalized": 12,
  "total_deduped": 9,
  "folders": {
    "INBOX": {
      "sync_mode": "incremental",
      "message_count": 42,
      "uidvalidity": 999,
      "uidnext": 143
    }
  },
  "reconciled_missing": 1,
  "deleted_stale_documents": 1,
  "persisted": [
    {
      "fingerprint": "f4c46f0d...",
      "subject": "Quarterly update",
      "inserted": true,
      "embedding_updated": true
    }
  ]
}
```

Response fields:

- `total_fetched`: Raw email count returned from IMAP across all requested folders.
- `total_normalized`: Number of emails successfully normalized into the canonical persistence shape.
- `total_deduped`: Count after duplicate-collapse by fingerprint.
- `folders`: Per-folder sync summary including whether the run used a full scan or resumed incrementally from a stored UID cursor.
- `reconciled_missing`: Count of previously tracked folder memberships that were no longer present on the IMAP server during this run.
- `deleted_stale_documents`: Count of email documents removed because they no longer belonged to any tracked folder after reconciliation.
- `persisted`: Per-document persistence results from the PostgreSQL vector store.

## Runtime Flow

The endpoint performs the following steps:

1. Parse and validate the HTTP query parameters.
2. Resolve the effective IMAP folder list.
3. Resolve a bounded worker pool size for the threaded fetch, normalization, and embedding stages.
4. Inspect each requested folder for current `UIDVALIDITY`, `UIDNEXT`, and server UID membership.
5. Resume from the stored UID cursor when possible; otherwise fall back to the requested lookback window.
6. Fetch each requested folder on its own IMAP service instance while preserving the requested folder order in the aggregated result.
7. Normalize each message using `EmailNormalizer`.
8. Merge duplicates while preserving folder provenance and current folder UID mappings.
9. Preserve per-folder IMAP flags and keywords for each observed folder membership.
10. Generate embeddings from each normalized `searchable_text` value.
11. Persist metadata and embeddings through `PostgresEmailVectorStore`.
12. Record a sync run observation for each persisted document.
13. Reconcile previously tracked folder memberships that disappeared from the IMAP server.
14. Update mailbox-level sync state for each scanned IMAP folder.
15. Delete email documents that no longer belong to any tracked folder.
16. Return a JSON summary of the ingestion run.

The implementation intentionally builds the embedding provider and vector store lazily inside the ingestion path. This keeps `function_app.py` import-safe for local development and tests when `DATABASE_URL` is not configured, while still enforcing the requirement when `/api/scan-mail` is invoked.

The threaded stages are intentionally bounded and ordered. Folder fetches, normalization, and embedding generation can run across worker threads, but PostgreSQL persistence, folder-membership reconciliation, and stale-document deletion remain serialized so the sync-state invariants stay unchanged.

The email store now also records mailbox sync-state tables in PostgreSQL:

- `mailbox_sync_state`: one row per observed IMAP folder and sync run frontier.
- `document_sync_runs`: one row per persisted document observed in a specific sync run, including folder UID details.

This now powers cursor-based incremental sync, folder-membership reconciliation, and stale email cleanup for the HTTP ingestion path.

## Environment Requirements

The scan-mail endpoint depends on the following environment variables:

- `IMAP_USER`: Required IMAP username.
- `IMAP_PASSWORD`: Required IMAP password.
- `IMAP_HOST`: Optional IMAP hostname. Defaults to `imap.mail.me.com`.
- `IMAP_PORT`: Optional IMAP port. Defaults to `993`.
- `IMAP_FOLDERS`: Optional default comma-separated IMAP folder list.
- `IMAP_INCREMENTAL_SYNC`: Optional toggle for UID-based incremental sync. Defaults to `true`.
- `SCAN_MAIL_MAX_WORKERS`: Optional upper bound for threaded scan-mail fetch, normalization, and embedding stages. Defaults to `4`.
- `SCAN_MAIL_QUEUE_NAME`: Optional queue name used by async folder ingestion producer and consumer. Defaults to `scan-mail-jobs`.
- `SCAN_MAIL_POISON_QUEUE_NAME`: Optional poison queue override for terminal dequeue failures. Defaults to `${SCAN_MAIL_QUEUE_NAME}-poison`.
- `DATABASE_URL`: Required PostgreSQL connection string for vector persistence.
- `EMBEDDING_PROVIDER`: Optional embedding provider identifier. Accepts `ollama`, `hashing`, or `azure-openai`.
- `EMBEDDING_BASE_URL`: Optional provider base URL.
- `EMBEDDING_MODEL`: Optional embedding model identifier.
- `EMBEDDING_TIMEOUT_SECONDS`: Optional provider timeout.
- `EMBEDDING_ENABLE_FALLBACK`: Optional fallback toggle.
- `EMBEDDING_FALLBACK_PROVIDER`: Optional fallback provider name.
- `EMBEDDING_HASH_DIMENSIONS`: Optional hashing vector dimension count.
- `EMAIL_VECTOR_DIMENSIONS`: Compatibility alias for hashing dimensions.
- `EMAIL_VECTOR_MODEL`: Compatibility alias for embedding model name.
- `AZURE_OPENAI_ENDPOINT`: Required when `EMBEDDING_PROVIDER=azure-openai`. Base endpoint URL of the Azure OpenAI resource.
- `AZURE_OPENAI_API_KEY`: Required when `EMBEDDING_PROVIDER=azure-openai`.
- `AZURE_OPENAI_API_VERSION`: Optional when `azure-openai`. Defaults to `2024-02-01`.
- `BALDWIN_LOG_LEVEL`: Optional. Controls log verbosity for all `baldwin.*` loggers. Defaults to `WARNING`.

## Error Semantics

The endpoint translates internal failures into stable HTTP responses.

### `400 Bad Request`

Returned when request input is invalid.

Examples:

- `days=abc`
- `days=0`

Example payload:

```json
{
  "error": "The 'days' query parameter must be an integer, got 'abc'."
}
```

### `502 Bad Gateway`

Returned when the underlying IMAP request fails with `imaplib.IMAP4.error`.

Example payload:

```json
{
  "error": "Unable to process one or more requested IMAP folders.",
  "error_code": "IMAP_LOGIN_FAILED",
  "reason_category": "auth",
  "folders": ["INBOX", "Archive"]
}
```

Response field semantics:

- `error_code`: Stable machine-readable IMAP failure code (`IMAP_*`) for client retry/routing logic.
- `reason_category`: Low-cardinality remediation category (`auth`, `network`, `permissions`, `folder`, `unknown`).
- `folders`: Requested IMAP folders associated with the failed operation.

### `500 Internal Server Error`

Returned when configuration is missing, embedding generation fails, or PostgreSQL persistence fails.

Example payload:

```json
{
  "error": "Internal server error."
}
```

The implementation deliberately avoids leaking raw infrastructure errors through the public HTTP surface. Raw provider error text, hostnames, and stack traces are kept in server logs only.

## Persistence Notes

The scan-mail endpoint persists to the same generic vector schema documented in [docs/EMAIL_VECTORIZATION.md](docs/EMAIL_VECTORIZATION.md), plus additive sync-state tables for mailbox observation tracking.

Important invariants:

- Duplicate messages across multiple folders collapse into one persisted document.
- `metadata.folders` preserves all observed folders in order.
- `metadata.folder` remains the compatibility alias for the first observed folder.
- `metadata.folder_uids` stores the current IMAP UID for each observed folder when the server provides one.
- `metadata.folder_flags` stores the current IMAP flags for each observed folder membership.
- `metadata.folder_keywords` stores the current user-defined IMAP keywords for each observed folder membership.
- Starred mail is represented by the IMAP `\Flagged` system flag in the relevant `metadata.folder_flags` entry.
- Re-running the same mailbox window is intended to be idempotent within the same provider-model space.
- Each ingestion run records document observations and mailbox-level sync timestamps in PostgreSQL.
- Folder memberships that disappear from IMAP are removed from persisted metadata during reconciliation, including the matching per-folder UID, flags, and keywords.
- Email documents with no remaining tracked folders are deleted along with their embeddings.

## Local Verification

Start the Functions host:

```bash
func start
```

Then call the endpoint:

```bash
curl "http://localhost:7071/api/scan-mail?days=1&folders=INBOX,Archive"
```

To run the same ingestion flow locally without starting the Functions host, use the dedicated CLI:

```bash
python scripts/scan_mail_flow.py --days 1 --folder INBOX --folder Archive --verbose
```

The CLI sets `SCAN_MAIL_MAX_WORKERS=8` by default for that run, accepts `--max-workers` to override it explicitly, and emits status lines plus stage progress bars to stderr only when `--verbose` is provided.

For regression coverage of the public contract, see `tests/test_function_app.py`.
