# Scan-Mail Email Ingestion

The `GET /api/scan-mail` endpoint is Baldwin's mailbox ingestion entrypoint. It reads one or more IMAP folders, normalizes the fetched messages, generates embeddings for the normalized text, and persists the results into PostgreSQL through the shared vector-document store.

This document describes the HTTP contract, runtime behavior, environment requirements, and operational failure modes for the ingestion flow exposed by `function_app.py` and implemented in `baldwin/http_handlers.py`.

## Endpoint Contract

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
3. Open an IMAP session with `EmailService`.
4. Inspect each requested folder for current `UIDVALIDITY`, `UIDNEXT`, and server UID membership.
5. Resume from the stored UID cursor when possible; otherwise fall back to the requested lookback window.
6. Normalize each message using `EmailNormalizer`.
7. Merge duplicates while preserving folder provenance and current folder UID mappings.
8. Preserve per-folder IMAP flags and keywords for each observed folder membership.
9. Generate embeddings from each normalized `searchable_text` value.
10. Persist metadata and embeddings through `PostgresEmailVectorStore`.
11. Record a sync run observation for each persisted document.
12. Reconcile previously tracked folder memberships that disappeared from the IMAP server.
13. Update mailbox-level sync state for each scanned IMAP folder.
14. Delete email documents that no longer belong to any tracked folder.
15. Return a JSON summary of the ingestion run.

The implementation intentionally builds the embedding provider and vector store lazily inside the ingestion path. This keeps `function_app.py` import-safe for local development and tests when `DATABASE_URL` is not configured, while still enforcing the requirement when `/api/scan-mail` is invoked.

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
- `DATABASE_URL`: Required PostgreSQL connection string for vector persistence.
- `EMBEDDING_PROVIDER`: Optional embedding provider identifier.
- `EMBEDDING_BASE_URL`: Optional provider base URL.
- `EMBEDDING_MODEL`: Optional embedding model identifier.
- `EMBEDDING_TIMEOUT_SECONDS`: Optional provider timeout.
- `EMBEDDING_ENABLE_FALLBACK`: Optional fallback toggle.
- `EMBEDDING_FALLBACK_PROVIDER`: Optional fallback provider name.
- `EMBEDDING_HASH_DIMENSIONS`: Optional hashing vector dimension count.
- `EMAIL_VECTOR_DIMENSIONS`: Compatibility alias for hashing dimensions.
- `EMAIL_VECTOR_MODEL`: Compatibility alias for embedding model name.

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
  "error": "Unable to read from the requested IMAP folders."
}
```

### `500 Internal Server Error`

Returned when configuration is missing, embedding generation fails, or PostgreSQL persistence fails.

Example payload:

```json
{
  "error": "Internal server error."
}
```

The implementation deliberately avoids leaking raw infrastructure errors through the public HTTP surface.

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

For regression coverage of the public contract, see `tests/test_function_app.py`.
