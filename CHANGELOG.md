# Changelog

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
