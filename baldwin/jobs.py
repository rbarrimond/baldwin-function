"""Scan job tracking for async queue-based mailbox ingestion."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from baldwin.exceptions import VectorStoreError
from baldwin.log import get_logger

_logger = get_logger(__name__)

_FOLDER_STATUS_ERROR = "Failed to update folder job status."


class ScanJobStore:
    """Persist and query scan job state for async mailbox ingestion."""

    def __init__(self, database_url: str):
        if not database_url:
            raise ValueError("database_url is required")
        self.database_url = database_url

    def bootstrap(self) -> None:
        """Create scan job tracking tables when absent."""
        try:
            with psycopg.connect(self.database_url, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS scan_jobs (
                            job_id UUID PRIMARY KEY,
                            status TEXT NOT NULL DEFAULT 'pending',
                            params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                            folder_count INT NOT NULL DEFAULT 0,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            completed_at TIMESTAMPTZ
                        )
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS scan_job_folders (
                            job_id UUID NOT NULL REFERENCES scan_jobs(job_id) ON DELETE CASCADE,
                            folder TEXT NOT NULL,
                            status TEXT NOT NULL DEFAULT 'pending',
                            started_at TIMESTAMPTZ,
                            completed_at TIMESTAMPTZ,
                            stats_json JSONB,
                            error_json JSONB,
                            PRIMARY KEY (job_id, folder)
                        )
                        """
                    )
        except psycopg.Error as exc:
            _logger.exception("Database error during scan job schema bootstrap")
            raise VectorStoreError("Failed to bootstrap scan job tables.") from exc

    def create_job(
        self,
        job_id: str,
        params: dict[str, Any],
        folder_count: int,
    ) -> None:
        """Insert a new scan job in pending status."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO scan_jobs (job_id, status, params_json, folder_count, created_at)
                        VALUES (%(job_id)s::uuid, 'pending', %(params_json)s::jsonb, %(folder_count)s, NOW())
                        """,
                        {
                            "job_id": job_id,
                            "params_json": json.dumps(params),
                            "folder_count": folder_count,
                        },
                    )
                connection.commit()
        except psycopg.Error as exc:
            _logger.exception("Database error creating scan job: job_id=%r", job_id)
            raise VectorStoreError("Failed to create scan job.") from exc

    def create_folder_records(self, job_id: str, folders: list[str]) -> None:
        """Insert one pending folder record per folder for a scan job."""
        if not folders:
            return
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    for folder in folders:
                        cursor.execute(
                            """
                            INSERT INTO scan_job_folders (job_id, folder, status)
                            VALUES (%(job_id)s::uuid, %(folder)s, 'pending')
                            """,
                            {"job_id": job_id, "folder": folder},
                        )
                connection.commit()
        except psycopg.Error as exc:
            _logger.exception(
                "Database error creating folder records: job_id=%r folder_count=%d",
                job_id, len(folders),
            )
            raise VectorStoreError("Failed to create scan job folder records.") from exc

    def mark_folder_started(self, job_id: str, folder: str) -> None:
        """Transition a folder record to in_progress."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE scan_job_folders
                        SET status = 'in_progress', started_at = NOW()
                        WHERE job_id = %(job_id)s::uuid AND folder = %(folder)s
                        """,
                        {"job_id": job_id, "folder": folder},
                    )
                    cursor.execute(
                        """
                        UPDATE scan_jobs SET status = 'in_progress'
                        WHERE job_id = %(job_id)s::uuid AND status = 'pending'
                        """,
                        {"job_id": job_id},
                    )
                connection.commit()
        except psycopg.Error as exc:
            _logger.exception(
                "Database error marking folder started: job_id=%r folder=%r",
                job_id, folder,
            )
            raise VectorStoreError(_FOLDER_STATUS_ERROR) from exc

    def mark_folder_done(
        self,
        job_id: str,
        folder: str,
        stats: dict[str, Any],
    ) -> int:
        """Mark a folder completed and return the count of still-active folders."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE scan_job_folders
                        SET status = 'completed', completed_at = NOW(),
                            stats_json = %(stats_json)s::jsonb
                        WHERE job_id = %(job_id)s::uuid AND folder = %(folder)s
                        """,
                        {
                            "job_id": job_id,
                            "folder": folder,
                            "stats_json": json.dumps(stats),
                        },
                    )
                    cursor.execute(
                        """
                        SELECT COUNT(*) FROM scan_job_folders
                        WHERE job_id = %(job_id)s::uuid
                          AND status NOT IN ('completed', 'failed')
                        """,
                        {"job_id": job_id},
                    )
                    row = cursor.fetchone()
                    remaining = int(row[0]) if row else 0
                connection.commit()
            return remaining
        except psycopg.Error as exc:
            _logger.exception(
                "Database error marking folder done: job_id=%r folder=%r",
                job_id, folder,
            )
            raise VectorStoreError(_FOLDER_STATUS_ERROR) from exc

    def mark_folder_failed(
        self,
        job_id: str,
        folder: str,
        error: dict[str, Any],
    ) -> int:
        """Mark a folder failed and return the count of still-active folders."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE scan_job_folders
                        SET status = 'failed', completed_at = NOW(),
                            error_json = %(error_json)s::jsonb
                        WHERE job_id = %(job_id)s::uuid AND folder = %(folder)s
                        """,
                        {
                            "job_id": job_id,
                            "folder": folder,
                            "error_json": json.dumps(error),
                        },
                    )
                    cursor.execute(
                        """
                        SELECT COUNT(*) FROM scan_job_folders
                        WHERE job_id = %(job_id)s::uuid
                          AND status NOT IN ('completed', 'failed')
                        """,
                        {"job_id": job_id},
                    )
                    row = cursor.fetchone()
                    remaining = int(row[0]) if row else 0
                connection.commit()
            return remaining
        except psycopg.Error as exc:
            _logger.exception(
                "Database error marking folder failed: job_id=%r folder=%r",
                job_id, folder,
            )
            raise VectorStoreError(_FOLDER_STATUS_ERROR) from exc

    def finalize_job(self, job_id: str) -> None:
        """Mark a scan job as completed or partial based on folder outcomes."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE status = 'failed') AS failed_count,
                            COUNT(*) FILTER (WHERE status = 'completed') AS completed_count
                        FROM scan_job_folders
                        WHERE job_id = %(job_id)s::uuid
                        """,
                        {"job_id": job_id},
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return
                    failed_count = row[0]
                    final_status = "partial" if failed_count > 0 else "completed"
                    cursor.execute(
                        """
                        UPDATE scan_jobs
                        SET status = %(status)s, completed_at = NOW()
                        WHERE job_id = %(job_id)s::uuid
                        """,
                        {"job_id": job_id, "status": final_status},
                    )
                connection.commit()
        except psycopg.Error as exc:
            _logger.exception("Database error finalizing scan job: job_id=%r", job_id)
            raise VectorStoreError("Failed to finalize scan job.") from exc

    def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Return the full status of a scan job including per-folder detail."""
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT job_id, status, params_json, folder_count,
                               created_at, completed_at
                        FROM scan_jobs
                        WHERE job_id = %(job_id)s::uuid
                        """,
                        {"job_id": job_id},
                    )
                    job_row = cursor.fetchone()
                    if job_row is None:
                        return None
                    job_id_val, status, params_json, folder_count, created_at, completed_at = job_row
                    cursor.execute(
                        """
                        SELECT folder, status, started_at, completed_at,
                               stats_json, error_json
                        FROM scan_job_folders
                        WHERE job_id = %(job_id)s::uuid
                        ORDER BY folder
                        """,
                        {"job_id": job_id},
                    )
                    folder_rows = cursor.fetchall()

            folders: dict[str, Any] = {}
            for folder, f_status, started_at, f_completed_at, stats_json, error_json in folder_rows:
                entry: dict[str, Any] = {"status": f_status}
                if started_at is not None:
                    entry["started_at"] = started_at.isoformat()
                if f_completed_at is not None:
                    entry["completed_at"] = f_completed_at.isoformat()
                if stats_json is not None:
                    entry["stats"] = stats_json
                if error_json is not None:
                    entry["error"] = error_json
                folders[folder] = entry

            return {
                "job_id": str(job_id_val),
                "status": status,
                "params": params_json,
                "folder_count": folder_count,
                "created_at": created_at.isoformat(),
                "completed_at": completed_at.isoformat() if completed_at else None,
                "folders": folders,
            }
        except psycopg.Error as exc:
            _logger.exception(
                "Database error fetching scan job status: job_id=%r", job_id
            )
            raise VectorStoreError("Failed to retrieve scan job status.") from exc

    def delete_expired_jobs(self, retention_days: int = 30) -> int:
        """Delete scan jobs older than retention_days. Returns count deleted."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        try:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        DELETE FROM scan_jobs
                        WHERE created_at < %(cutoff)s
                        """,
                        {"cutoff": cutoff},
                    )
                    deleted = cursor.rowcount
                connection.commit()
            _logger.info(
                "Expired scan jobs deleted: count=%d retention_days=%d",
                deleted, retention_days,
            )
            return deleted
        except psycopg.Error as exc:
            _logger.exception("Database error deleting expired scan jobs")
            raise VectorStoreError("Failed to delete expired scan jobs.") from exc
