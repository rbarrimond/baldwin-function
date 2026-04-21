# Changelog

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
