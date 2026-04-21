"""Email-specific PostgreSQL persistence built on the generic vector store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql

from baldwin.embedding import EmbeddingResult
from baldwin.exceptions import VectorStoreError
from baldwin.vector.postgres_store import (
    PostgresVectorStore,
    VectorDocument,
    VectorStoreResult,
)
from .vectorization import NormalizedEmail

StoreResult = VectorStoreResult


class PostgresEmailVectorStore(PostgresVectorStore):
    """Stores normalized emails using the shared vector persistence layer."""

    def __init__(self, database_url: str):
        super().__init__(
            database_url=database_url,
            document_table="vector_documents",
            embedding_table="vector_embeddings",
        )

    def bootstrap(self) -> None:
        """Create the generic vector schema plus email sync tracking tables."""
        super().bootstrap()

        try:
            with psycopg.connect(self.database_url, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS mailbox_sync_state (
                            id BIGSERIAL PRIMARY KEY,
                            imap_user TEXT NOT NULL,
                            imap_host TEXT NOT NULL,
                            imap_folder TEXT NOT NULL,
                            uidvalidity BIGINT NOT NULL DEFAULT 0,
                            last_synced_uid BIGINT,
                            last_sync_time TIMESTAMPTZ NOT NULL,
                            sync_run_id UUID NOT NULL,
                            total_emails_in_folder BIGINT NOT NULL DEFAULT 0,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            UNIQUE (imap_user, imap_host, imap_folder, uidvalidity)
                        )
                        """
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {sync_runs_table} (
                                document_id BIGINT NOT NULL REFERENCES {document_table}(id) ON DELETE CASCADE,
                                sync_run_id UUID NOT NULL,
                                was_present_in_mailbox BOOLEAN NOT NULL DEFAULT TRUE,
                                folder_names JSONB NOT NULL DEFAULT '[]'::jsonb,
                                folder_uids JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                                last_seen_at TIMESTAMPTZ NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                PRIMARY KEY (document_id, sync_run_id)
                            )
                            """
                        ).format(
                            sync_runs_table=sql.Identifier("document_sync_runs"),
                            document_table=sql.Identifier(self.document_table),
                        )
                    )
                    cursor.execute(
                        "ALTER TABLE document_sync_runs ADD COLUMN IF NOT EXISTS folder_uids JSONB NOT NULL DEFAULT '{}'::jsonb"
                    )
        except psycopg.Error as exc:
            raise VectorStoreError("Failed to bootstrap PostgreSQL email sync state.") from exc

    @staticmethod
    def to_document(normalized_email: NormalizedEmail) -> VectorDocument:
        """Map a normalized email into the generic vector document shape."""
        metadata: dict[str, Any] = {
            "sender": normalized_email.sender,
            "recipients": normalized_email.recipients,
            "raw_date": normalized_email.raw_date,
            "sent_at": normalized_email.sent_at,
            "folder": normalized_email.folders[0] if normalized_email.folders else None,
            "folders": normalized_email.folders,
            "folder_uids": normalized_email.folder_uids,
            "headers": normalized_email.headers,
        }
        return VectorDocument(
            document_key=normalized_email.fingerprint,
            source_type="email",
            source_id=normalized_email.source_message_id,
            title=normalized_email.subject,
            body=normalized_email.body,
            searchable_text=normalized_email.searchable_text,
            content_checksum=normalized_email.content_checksum,
            metadata=metadata,
        )

    def upsert_email(
        self,
        normalized_email: NormalizedEmail,
        embedding: EmbeddingResult,
    ) -> VectorStoreResult:
        """Upsert an email by delegating to the generic document store."""
        return self.upsert_document(self.to_document(normalized_email), embedding)

    def upsert_mailbox_sync_state(
        self,
        *,
        imap_user: str,
        imap_host: str,
        imap_folder: str,
        sync_run_id: str,
        total_emails_in_folder: int,
        uidvalidity: int = 0,
        last_synced_uid: int | None = None,
        synced_at: datetime | None = None,
    ) -> None:
        """Record the latest observed sync state for an IMAP folder."""
        observed_at = synced_at or datetime.now(UTC)

        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO mailbox_sync_state (
                            imap_user,
                            imap_host,
                            imap_folder,
                            uidvalidity,
                            last_synced_uid,
                            last_sync_time,
                            sync_run_id,
                            total_emails_in_folder
                        )
                        VALUES (
                            %(imap_user)s,
                            %(imap_host)s,
                            %(imap_folder)s,
                            %(uidvalidity)s,
                            %(last_synced_uid)s,
                            %(last_sync_time)s,
                            %(sync_run_id)s,
                            %(total_emails_in_folder)s
                        )
                        ON CONFLICT (imap_user, imap_host, imap_folder, uidvalidity) DO UPDATE SET
                            last_synced_uid = EXCLUDED.last_synced_uid,
                            last_sync_time = EXCLUDED.last_sync_time,
                            sync_run_id = EXCLUDED.sync_run_id,
                            total_emails_in_folder = EXCLUDED.total_emails_in_folder,
                            updated_at = NOW()
                        """,
                        {
                            "imap_user": imap_user,
                            "imap_host": imap_host,
                            "imap_folder": imap_folder,
                            "uidvalidity": uidvalidity,
                            "last_synced_uid": last_synced_uid,
                            "last_sync_time": observed_at,
                            "sync_run_id": sync_run_id,
                            "total_emails_in_folder": total_emails_in_folder,
                        },
                    )

                connection.commit()
        except psycopg.Error as exc:
            raise VectorStoreError("Failed to persist mailbox sync state.") from exc

    def record_document_sync(
        self,
        *,
        document_key: str,
        sync_run_id: str,
        folder_names: list[str],
        folder_uids: dict[str, int] | None = None,
        last_seen_at: datetime | None = None,
        was_present_in_mailbox: bool = True,
    ) -> None:
        """Record that a persisted document was observed in a specific sync run."""
        observed_at = last_seen_at or datetime.now(UTC)

        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            "SELECT id FROM {document_table} WHERE document_key = %(document_key)s"
                        ).format(document_table=sql.Identifier(self.document_table)),
                        {"document_key": document_key},
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise VectorStoreError(
                            f"Unable to record sync state for unknown document_key {document_key!r}."
                        )

                    document_id = row[0]
                    cursor.execute(
                        """
                        INSERT INTO document_sync_runs (
                            document_id,
                            sync_run_id,
                            was_present_in_mailbox,
                            folder_names,
                            folder_uids,
                            last_seen_at
                        )
                        VALUES (
                            %(document_id)s,
                            %(sync_run_id)s,
                            %(was_present_in_mailbox)s,
                            %(folder_names)s::jsonb,
                            %(folder_uids)s::jsonb,
                            %(last_seen_at)s
                        )
                        ON CONFLICT (document_id, sync_run_id) DO UPDATE SET
                            was_present_in_mailbox = EXCLUDED.was_present_in_mailbox,
                            folder_names = EXCLUDED.folder_names,
                            folder_uids = EXCLUDED.folder_uids,
                            last_seen_at = EXCLUDED.last_seen_at
                        """,
                        {
                            "document_id": document_id,
                            "sync_run_id": sync_run_id,
                            "was_present_in_mailbox": was_present_in_mailbox,
                            "folder_names": json.dumps(folder_names),
                            "folder_uids": json.dumps(folder_uids or {}),
                            "last_seen_at": observed_at,
                        },
                    )

                connection.commit()
        except psycopg.Error as exc:
            raise VectorStoreError("Failed to record document sync observation.") from exc

    def get_mailbox_sync_state(
        self,
        *,
        imap_user: str,
        imap_host: str,
        imap_folder: str,
    ) -> dict[str, Any] | None:
        """Return the most recent stored sync cursor for an IMAP folder."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT uidvalidity, last_synced_uid, last_sync_time, sync_run_id, total_emails_in_folder
                        FROM mailbox_sync_state
                        WHERE imap_user = %(imap_user)s
                          AND imap_host = %(imap_host)s
                          AND imap_folder = %(imap_folder)s
                        ORDER BY last_sync_time DESC
                        LIMIT 1
                        """,
                        {
                            "imap_user": imap_user,
                            "imap_host": imap_host,
                            "imap_folder": imap_folder,
                        },
                    )
                    row = cursor.fetchone()
        except psycopg.Error as exc:
            raise VectorStoreError("Failed to read mailbox sync state.") from exc

        if row is None:
            return None

        return {
            "uidvalidity": row[0],
            "last_synced_uid": row[1],
            "last_sync_time": row[2],
            "sync_run_id": str(row[3]),
            "total_emails_in_folder": row[4],
        }

    def get_current_folder_uids(self, *, folder_name: str) -> dict[str, int]:
        """Return current persisted document keys to IMAP UIDs for a folder."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT document_key, metadata -> 'folder_uids' ->> %(folder_name)s AS folder_uid
                            FROM {document_table}
                            WHERE (metadata -> 'folder_uids') ? %(folder_name)s
                            """
                        ).format(document_table=sql.Identifier(self.document_table)),
                        {"folder_name": folder_name},
                    )
                    rows = cursor.fetchall()
        except psycopg.Error as exc:
            raise VectorStoreError("Failed to read current folder UID state.") from exc

        result: dict[str, int] = {}
        for document_key, folder_uid in rows:
            if folder_uid is None:
                continue
            try:
                result[str(document_key)] = int(folder_uid)
            except (TypeError, ValueError):
                continue
        return result

    def remove_folder_membership(self, *, document_key: str, folder_name: str) -> None:
        """Remove a folder association from the current persisted email metadata."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {document_table}
                            SET metadata = jsonb_set(
                                    jsonb_set(
                                        metadata,
                                        '{{folders}}',
                                        COALESCE(
                                            (
                                                SELECT jsonb_agg(value)
                                                FROM jsonb_array_elements_text(COALESCE(metadata -> 'folders', '[]'::jsonb)) AS value
                                                WHERE value <> %(folder_name)s
                                            ),
                                            '[]'::jsonb
                                        ),
                                        true
                                    ),
                                    '{{folder_uids}}',
                                    COALESCE((metadata -> 'folder_uids') - %(folder_name)s, '{}'::jsonb),
                                    true
                                )
                            WHERE document_key = %(document_key)s
                            """
                        ).format(document_table=sql.Identifier(self.document_table)),
                        {"document_key": document_key, "folder_name": folder_name},
                    )
                connection.commit()
        except psycopg.Error as exc:
            raise VectorStoreError("Failed to remove reconciled folder membership.") from exc

    def delete_documents_without_folders(self) -> int:
        """Delete email documents that no longer belong to any tracked folder."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            DELETE FROM {document_table}
                            WHERE source_type = 'email'
                              AND jsonb_array_length(COALESCE(metadata -> 'folders', '[]'::jsonb)) = 0
                            """
                        ).format(document_table=sql.Identifier(self.document_table))
                    )
                    deleted_count = cursor.rowcount or 0
                connection.commit()
        except psycopg.Error as exc:
            raise VectorStoreError("Failed to delete stale email documents.") from exc

        return deleted_count
