"""Shared vector persistence primitives for Baldwin packages."""

from baldwin.exceptions import VectorStoreError

from .postgres_store import PostgresVectorStore, VectorDocument, VectorStoreResult

__all__ = ["PostgresVectorStore", "VectorDocument", "VectorStoreError", "VectorStoreResult"]
