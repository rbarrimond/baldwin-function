# Changelog

## 0.2.0 - 2026-04-16

- Refactored vector persistence into a generic `baldwin.vector` PostgreSQL store.
- Converted email persistence into an email-specific adapter over the generic vector-document store.
- Updated the documented PostgreSQL schema from email-specific tables to generic vector document tables.