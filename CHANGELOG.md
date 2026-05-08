# Changelog

## 0.8.0 - 2026-06-01

- Added async, queue-backed scan-mail ingestion: `POST /api/scan-mail` now enqueues one Azure Storage Queue message per IMAP folder and returns `202 Accepted` with a job ID, decoupling response latency from mailbox size.
- Added `GET /api/scan-mail/status/{job_id}` to poll per-job and per-folder ingestion status from PostgreSQL.
- Added `process_scan_folder` queue trigger (`scan-mail-jobs`) to process individual folder jobs with Azure Functions built-in retry semantics (`maxDequeueCount=5`).
- Added `process_scan_folder_poison` queue trigger (`scan-mail-jobs-poison`) so terminal dequeue failures are persisted as folder failures and still finalize their parent scan jobs.
- Added `cleanup_scan_jobs` timer trigger (daily at 02:00 UTC) to delete expired job tracking records, configurable via `SCAN_JOB_RETENTION_DAYS` (default: 30 days).
- Added `baldwin/jobs.py` — `ScanJobStore` class backed by two new PostgreSQL tables (`scan_jobs`, `scan_job_folders`) for job-level and folder-level status tracking.
- Hardened async folder/job status transitions: retries now preserve initial folder start timestamps, successful retries clear stale `error_json`, completed folders are protected from late retry-failure overwrites, and finalization now no-ops until all folders are terminal.
- Fixed multi-folder JSONB merge correctness: `PostgresEmailVectorStore` now overrides `_upsert_email_on_connection()` with an `ON CONFLICT DO UPDATE` clause that performs a set-union merge of `folders`, `folder_uids`, `folder_flags`, and `folder_keywords` instead of overwriting them. This prevents a concurrent folder job from erasing folder membership recorded by an earlier job.
- Moved `delete_documents_without_folders()` out of per-folder ingestion into a single post-completion step in `_finalize_folder_job()`, ensuring stale cleanup runs only once all parallel folder jobs have finished.
- Deprecated `GET /api/scan-mail`; the synchronous endpoint now returns a `_deprecation_notice` field in its response and remains available for backwards compatibility.
- Added `azure-storage-queue>=12.0.0,<13.0.0` dependency.
- Added `SCAN_MAIL_QUEUE_NAME` and `SCAN_JOB_RETENTION_DAYS` to the Terraform Baldwin module `app_settings` and provisioned the `scan-mail-jobs` Azure Storage Queue via `azurerm_storage_queue`.

## 0.7.0 - 2026-05-03

- Added Azure OpenAI embedding provider (`EMBEDDING_PROVIDER=azure-openai`) backed by `openai>=1.0.0`. Requires `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, and optionally `AZURE_OPENAI_API_VERSION` (defaults to `2024-02-01`) and `EMBEDDING_MODEL` (defaults to `text-embedding-3-small`). The provider returns token-usage metadata and participates in the standard fallback chain.
- Added content-based fingerprinting as a collision guard: when two messages share a `Message-ID` but differ in normalized content, the runtime now re-keys the later message to a content-derived SHA-256 fingerprint instead of silently overwriting the earlier document.
- Added class-level connection pooling to `PostgresVectorStore` so concurrent `scan-mail` worker threads share a single `psycopg_pool.ConnectionPool` per database URL instead of opening a new connection per document.
- Added batch upsert methods to `PostgresEmailVectorStore` and `PostgresVectorStore` to reduce round-trips during threaded ingestion.
- Introduced structured JSON logging via a dedicated `_JsonFormatter` and `_LevelRoutingHandler` in `baldwin.log`. `DEBUG`/`INFO` records go to stdout; `WARNING`+  records go to stderr to prevent the Azure Functions local host from flattening severity.
- Added `BALDWIN_LOG_LEVEL` environment variable to control the effective log level for all `baldwin.*` loggers (defaults to `WARNING`).
- Added trace ID propagation via `set_trace_id()` in `baldwin.log`; the bound value is included in every JSON log record emitted within the same context, and Python's context-variable copying lets it propagate automatically into `ThreadPoolExecutor` worker threads.
- Exported `AzureOpenAIEmbeddingProvider` from `baldwin.embedding` to complete the provider surface alongside `OllamaEmbeddingProvider` and `HashingEmbeddingProvider`.

## 0.6.0 - 2026-05-01

- Replaced the generic `scan-mail` IMAP `502` response body with structured, client-actionable context: `error_code`, `reason_category`, and `folders`.
- Added shared IMAP failure classification metadata on `EmailFetchError` so HTTP and script surfaces expose consistent, sanitized diagnostics.
- Updated scan-mail and mailbox-vectorization script messaging plus regression coverage/documentation to align with the new IMAP error contract.

## 0.5.8 - 2026-04-27

- Added a dedicated `scan-mail-flow` CLI that runs the real `scan-mail` ingestion path with explicit `days`, folder, and worker-count inputs instead of the older mailbox-vectorization flow.
- Defaulted the new CLI to `8` scan-mail workers per run while leaving the HTTP endpoint's environment default unchanged.
- Documented the new local scan-mail CLI surface.

## 0.5.7 - 2026-04-27

- Fixed duplicate email merging so a later observation of the same folder membership can refresh `metadata.folder_uids` instead of raising a normalization conflict.
- Kept per-folder IMAP flags and keywords aligned with the latest observed mailbox state when that same-folder UID refresh occurs.

## 0.5.6 - 2026-04-23

- Added `PostgresThingsStore` so `ThingsSnapshot` models can be bootstrapped and persisted into normalized PostgreSQL tables.
- Extended the `things-snapshot` CLI with an optional `--persist` mode and explicit PostgreSQL connection-string support.
- Added regression coverage for Things snapshot persistence and updated the Things documentation to describe the new PostgreSQL storage capability.

## 0.5.5 - 2026-04-22

- Added a new local-only `baldwin.things` package that reads Things areas, active projects, open to-dos, and attached notes through the `things.py` library.
- Added the `things-snapshot` CLI entrypoint for inspecting the typed Things snapshot from a local workstation.
- Documented the local Things integration contract and added regression coverage for mapping, note filtering, configuration errors, and the CLI surface.

## 0.5.4 - 2026-04-21

- Added IMAP flag and keyword support to mailbox ingestion and persisted the results as per-folder metadata in `vector_documents`.
- Represented starred mail via the IMAP `\Flagged` system flag while keeping document fingerprints and embedding refresh checks based only on message identity and normalized content.
- Extended folder-membership reconciliation so stale folders also remove their associated per-folder flags and keywords, and added regression coverage for IMAP flag parsing and metadata cleanup.

## 0.5.3 - 2026-04-21

- Added IMAP folder-state inspection and UID-range fetching so `scan-mail` can resume from persisted mailbox cursors instead of re-reading the same folder window on every run.
- Persisted current folder-to-UID state in email metadata, reconciled missing folder memberships after each sync, and deleted email documents that no longer belong to any tracked folder.
- Expanded regression coverage for IMAP folder status, UID-based fetch, reconciliation, and the new email metadata shape.

## 0.5.2 - 2026-04-20

- Added additive PostgreSQL sync-state tables for `scan-mail` so each ingestion run now records per-folder mailbox state and per-document observation rows.
- Wired `EmailIngestionService` to bootstrap the email persistence schema lazily and record sync observations without changing the public HTTP response contract.
- Updated vectorization and scan-mail docs and added regression coverage for the new email sync instrumentation.

## 0.5.1 - 2026-04-20

- Rebuilt the HTTP handler module around typed request parsing and consistent response factories after the route layer had drifted into a broken state.
- Delayed `scan-mail` ingestion dependency construction so the Azure Function app can import without requiring `DATABASE_URL` until mailbox ingestion is actually invoked.
- Updated endpoint documentation and regression coverage to match the current mailbox-ingestion contract.

## 0.5.0 - 2026-04-17

- Renamed the mailbox vectorization entrypoint to `scripts/vectorize_mailbox.py` and kept `scripts/vectorize_inbox.py` as a compatibility shim.
- Collapsed duplicate messages seen across multiple scanned folders into one persisted document per fingerprint while preserving ordered folder provenance in `metadata.folders`.
- Bumped the package version to reflect the mailbox ingestion and persisted metadata change.

## 0.4.2 - 2026-04-17

- Broadened IMAP scanning from a hard-coded inbox flow to one-or-more configured or requested IMAP folders per HTTP request and CLI run.
- Added normalized folder selection support in the IMAP service, HTTP handler layer, and vectorization CLI.
- Persisted source-folder provenance in email metadata and updated docs/tests to reflect mailbox-folder scanning rather than inbox-only behavior.

## 0.4.1 - 2026-04-17

- Slimmed the Azure Function entrypoints so `function_app.py` only wires routes to dedicated HTTP handler classes.
- Introduced cohesive request-handling classes for mailbox scanning, digest building, digest delivery, response creation, and environment-backed configuration.
- Added HTTP contract tests for the function endpoints while preserving the existing response semantics.

## 0.4.0 - 2026-04-17

- Added adaptive chunking for long Ollama embedding inputs so oversized normalized emails are split and recombined into a single stored document embedding instead of immediately falling back to hashing.
- Updated the vectorization documentation and regression tests to cover the new long-email embedding behavior.

## 0.3.1 - 2026-04-17

- Changed the default Ollama embedding model from `bge-large` to `qllama/bge-small-en-v1.5` after verifying the namespaced model exists in the Ollama registry, installs locally, and serves embeddings successfully.
- Updated the vectorization CLI, runtime configuration documentation, and regression tests to align with the new default embedding model.

## 0.3.0 - 2026-04-17

- Changed the default Ollama embedding model from `bge-small-en-v1.5` to `bge-large` because `bge-large` is available from the Ollama registry and can be installed locally without additional model packaging.
- Updated the vectorization CLI, runtime configuration documentation, and regression tests to align with the new default embedding model.

## 0.2.0 - 2026-04-16

- Refactored vector persistence into a generic `baldwin.vector` PostgreSQL store.
- Converted email persistence into an email-specific adapter over the generic vector-document store.
- Updated the documented PostgreSQL schema from email-specific tables to generic vector document tables.
