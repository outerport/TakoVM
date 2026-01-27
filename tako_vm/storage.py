"""
SQLite storage for Tako VM execution records.

Provides async CRUD operations for ExecutionRecords and JobVersions.
"""

import json
import logging
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from .models import (
    ExecutionRecord, ResourceUsage, Artifact, ExecutionError,
    JobVersion, DeadLetterEntry, InputArtifact
)

logger = logging.getLogger(__name__)


# SQLite schema
SCHEMA_SQL = """
-- Execution records table (v2 with SVG/artifact support)
CREATE TABLE IF NOT EXISTS execution_records (
    execution_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    job_type TEXT NOT NULL,
    job_ref TEXT NOT NULL DEFAULT 'default@latest',

    created_at TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    dequeued_at TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_ms INTEGER,

    attempt INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 1,
    worker_id TEXT,
    idempotency_key TEXT,
    idempotency_fingerprint TEXT,

    code_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    params_hash TEXT,
    input_artifacts_hash TEXT,

    input_artifacts_json TEXT,

    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    stdout_truncated INTEGER DEFAULT 0,
    stderr_truncated INTEGER DEFAULT 0,
    result_json TEXT,

    max_rss_mb REAL,
    cpu_time_ms INTEGER,
    wall_time_ms INTEGER,

    artifacts_json TEXT,
    error_json TEXT,

    client_ip TEXT,
    parent_execution_id TEXT,
    relationship TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_execution_status ON execution_records(status);
CREATE INDEX IF NOT EXISTS idx_execution_job_type ON execution_records(job_type);
CREATE INDEX IF NOT EXISTS idx_execution_created_at ON execution_records(created_at);
CREATE INDEX IF NOT EXISTS idx_execution_idempotency ON execution_records(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_execution_parent ON execution_records(parent_execution_id);

-- Composite indexes for filtered pagination queries (scalability fix)
CREATE INDEX IF NOT EXISTS idx_execution_status_created ON execution_records(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_job_type_created ON execution_records(job_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_status_job_type_created ON execution_records(status, job_type, created_at DESC);

-- Job versions table
CREATE TABLE IF NOT EXISTS job_versions (
    digest TEXT PRIMARY KEY,
    job_type_name TEXT NOT NULL,
    version_tag TEXT,

    built_at TEXT NOT NULL,
    built_by TEXT,
    dockerfile_hash TEXT NOT NULL,
    requirements_hash TEXT NOT NULL,
    image_ref TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_version_job_type ON job_versions(job_type_name);
CREATE INDEX IF NOT EXISTS idx_version_tag ON job_versions(job_type_name, version_tag);

-- Dead letter queue for failed jobs
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    job_data_json TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    client_ip TEXT,
    correlation_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_dlq_created_at ON dead_letter_queue(created_at);
CREATE INDEX IF NOT EXISTS idx_dlq_error_type ON dead_letter_queue(error_type);
CREATE INDEX IF NOT EXISTS idx_dlq_job_id ON dead_letter_queue(job_id);
"""


class ExecutionStorage:
    """
    SQLite storage for execution records.

    Provides synchronous operations (use run_in_executor for async).
    """

    def __init__(self, db_path: Path):
        """
        Initialize storage.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()  # Thread safety for SQLite access

    def init(self) -> None:
        """Create database and tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = None
        try:
            conn = self._get_connection()
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        except Exception:
            # Close connection on error to prevent leaks
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
                self._conn = None
            raise

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def save_record(self, record: ExecutionRecord) -> None:
        """
        Insert or update execution record.

        Args:
            record: ExecutionRecord to save
        """
        # Serialize complex fields outside the lock
        resource_usage = record.resource_usage
        artifacts_json = json.dumps([a.model_dump() for a in record.artifacts])
        input_artifacts_json = json.dumps([a.model_dump() for a in record.input_artifacts])
        error_json = json.dumps(record.error.model_dump()) if record.error else None
        result_json = json.dumps(record.result_json) if record.result_json else None

        with self._lock:
            conn = self._get_connection()
            conn.execute("""
            INSERT OR REPLACE INTO execution_records (
                execution_id, status, job_type, job_ref,
                created_at, queued_at, dequeued_at, started_at, ended_at, duration_ms,
                attempt, max_attempts, worker_id, idempotency_key, idempotency_fingerprint,
                code_hash, input_hash, params_hash, input_artifacts_hash,
                input_artifacts_json,
                exit_code, stdout, stderr, stdout_truncated, stderr_truncated, result_json,
                max_rss_mb, cpu_time_ms, wall_time_ms,
                artifacts_json, error_json,
                client_ip, parent_execution_id, relationship
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.execution_id,
            record.status,
            record.job_type,
            record.job_ref,
            record.created_at.isoformat(),
            record.queued_at.isoformat(),
            record.dequeued_at.isoformat() if record.dequeued_at else None,
            record.started_at.isoformat() if record.started_at else None,
            record.ended_at.isoformat() if record.ended_at else None,
            record.duration_ms,
            record.attempt,
            record.max_attempts,
            record.worker_id,
            record.idempotency_key,
            record.idempotency_fingerprint,
            record.code_hash,
            record.input_hash,
            record.params_hash,
            record.input_artifacts_hash,
            input_artifacts_json,
            record.exit_code,
            record.stdout,
            record.stderr,
            1 if record.stdout_truncated else 0,
            1 if record.stderr_truncated else 0,
            result_json,
            resource_usage.max_rss_mb if resource_usage else None,
            resource_usage.cpu_time_ms if resource_usage else None,
            resource_usage.wall_time_ms if resource_usage else None,
            artifacts_json,
            error_json,
            record.client_ip,
            record.parent_execution_id,
            record.relationship,
            ))
            conn.commit()

    def get_record(self, execution_id: str) -> Optional[ExecutionRecord]:
        """
        Retrieve execution record by ID.

        Args:
            execution_id: Execution ID to look up

        Returns:
            ExecutionRecord or None if not found
        """
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM execution_records WHERE execution_id = ?",
                (execution_id,)
            ).fetchone()

        if not row:
            return None

        return self._row_to_record(row)

    def get_by_idempotency_key(self, key: str) -> Optional[ExecutionRecord]:
        """
        Retrieve execution record by idempotency key.

        Args:
            key: Idempotency key

        Returns:
            ExecutionRecord or None if not found
        """
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM execution_records WHERE idempotency_key = ?",
                (key,)
            ).fetchone()

        if not row:
            return None

        return self._row_to_record(row)

    def list_records(
        self,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[ExecutionRecord]:
        """
        List execution records with optional filters.

        Args:
            status: Filter by status
            job_type: Filter by job type
            limit: Maximum records to return
            offset: Offset for pagination

        Returns:
            List of ExecutionRecords
        """
        query = "SELECT * FROM execution_records WHERE 1=1"
        params: List = []

        if status:
            query += " AND status = ?"
            params.append(status)

        if job_type:
            query += " AND job_type = ?"
            params.append(job_type)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_record(row) for row in rows]

    def cleanup_old_records(self, ttl_days: int) -> int:
        """
        Delete records older than TTL.

        Args:
            ttl_days: Days to retain records

        Returns:
            Number of records deleted
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()

        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "DELETE FROM execution_records WHERE created_at < ?",
                (cutoff,)
            )
            conn.commit()
            return cursor.rowcount

    def _row_to_record(self, row: sqlite3.Row) -> ExecutionRecord:
        """Convert database row to ExecutionRecord."""
        # Parse resource usage
        resource_usage = None
        if row['wall_time_ms'] is not None:
            resource_usage = ResourceUsage(
                max_rss_mb=row['max_rss_mb'],
                cpu_time_ms=row['cpu_time_ms'],
                wall_time_ms=row['wall_time_ms']
            )

        # Parse input artifacts with error handling
        input_artifacts = []
        input_artifacts_json = row['input_artifacts_json'] if 'input_artifacts_json' in row.keys() else None
        if input_artifacts_json:
            try:
                input_artifacts_data = json.loads(input_artifacts_json)
                input_artifacts = [InputArtifact(**a) for a in input_artifacts_data]
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(
                    "Failed to parse input_artifacts_json for %s: %s",
                    row['execution_id'], e
                )

        # Parse output artifacts with error handling
        artifacts = []
        if row['artifacts_json']:
            try:
                artifacts_data = json.loads(row['artifacts_json'])
                artifacts = [Artifact(**a) for a in artifacts_data]
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(
                    "Failed to parse artifacts_json for %s: %s",
                    row['execution_id'], e
                )

        # Parse error with error handling
        error = None
        if row['error_json']:
            try:
                error_data = json.loads(row['error_json'])
                error = ExecutionError(**error_data)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(
                    "Failed to parse error_json for %s: %s",
                    row['execution_id'], e
                )

        # Parse result_json with error handling
        result_json = None
        result_json_col = row['result_json'] if 'result_json' in row.keys() else row.get('output_json')
        if result_json_col:
            try:
                result_json = json.loads(result_json_col)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    "Failed to parse result_json for %s: %s",
                    row['execution_id'], e
                )

        # Handle optional new columns with defaults
        job_ref = row['job_ref'] if 'job_ref' in row.keys() else f"{row['job_type']}@latest"
        queued_at_str = row['queued_at'] if 'queued_at' in row.keys() else row['created_at']
        dequeued_at_str = row['dequeued_at'] if 'dequeued_at' in row.keys() else None

        return ExecutionRecord(
            execution_id=row['execution_id'],
            status=row['status'],
            job_type=row['job_type'],
            job_ref=job_ref,
            created_at=datetime.fromisoformat(row['created_at']),
            queued_at=datetime.fromisoformat(queued_at_str),
            dequeued_at=datetime.fromisoformat(dequeued_at_str) if dequeued_at_str else None,
            started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
            ended_at=datetime.fromisoformat(row['ended_at']) if row['ended_at'] else None,
            duration_ms=row['duration_ms'],
            attempt=row['attempt'] if 'attempt' in row.keys() else 0,
            max_attempts=row['max_attempts'] if 'max_attempts' in row.keys() else 1,
            worker_id=row['worker_id'] if 'worker_id' in row.keys() else None,
            idempotency_key=row['idempotency_key'] if 'idempotency_key' in row.keys() else None,
            idempotency_fingerprint=row['idempotency_fingerprint'] if 'idempotency_fingerprint' in row.keys() else None,
            code_hash=row['code_hash'],
            input_hash=row['input_hash'],
            params_hash=row['params_hash'] if 'params_hash' in row.keys() else "",
            input_artifacts_hash=row['input_artifacts_hash'] if 'input_artifacts_hash' in row.keys() else "",
            input_artifacts=input_artifacts,
            exit_code=row['exit_code'],
            stdout=row['stdout'] or "",
            stderr=row['stderr'] or "",
            stdout_truncated=bool(row['stdout_truncated']) if 'stdout_truncated' in row.keys() else False,
            stderr_truncated=bool(row['stderr_truncated']) if 'stderr_truncated' in row.keys() else False,
            result_json=result_json,
            resource_usage=resource_usage,
            artifacts=artifacts,
            error=error,
            client_ip=row['client_ip'],
            parent_execution_id=row['parent_execution_id'] if 'parent_execution_id' in row.keys() else None,
            relationship=row['relationship'] if 'relationship' in row.keys() else None,
        )

    # Job version methods

    def save_version(self, version: JobVersion) -> None:
        """Save job version record."""
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
                INSERT OR REPLACE INTO job_versions (
                    digest, job_type_name, version_tag,
                    built_at, built_by, dockerfile_hash,
                    requirements_hash, image_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                version.digest,
                version.job_type_name,
                version.version_tag,
                version.built_at.isoformat(),
                version.built_by,
                version.dockerfile_hash,
                version.requirements_hash,
                version.image_ref,
            ))
            conn.commit()

    def get_version_by_digest(
        self,
        job_type_name: str,
        digest: str
    ) -> Optional[JobVersion]:
        """Get version by digest."""
        with self._lock:
            conn = self._get_connection()

            # Handle short digests
            if len(digest) < 64:
                row = conn.execute(
                    "SELECT * FROM job_versions WHERE job_type_name = ? AND digest LIKE ?",
                    (job_type_name, digest + '%')
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM job_versions WHERE digest = ?",
                    (digest,)
                ).fetchone()

        if not row:
            return None

        return self._row_to_version(row)

    def get_version_by_tag(
        self,
        job_type_name: str,
        version_tag: str
    ) -> Optional[JobVersion]:
        """Get version by tag."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM job_versions WHERE job_type_name = ? AND version_tag = ?",
                (job_type_name, version_tag)
            ).fetchone()

        if not row:
            return None

        return self._row_to_version(row)

    def get_latest_version(self, job_type_name: str) -> Optional[JobVersion]:
        """Get most recent version for job type."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM job_versions WHERE job_type_name = ? ORDER BY built_at DESC LIMIT 1",
                (job_type_name,)
            ).fetchone()

        if not row:
            return None

        return self._row_to_version(row)

    def list_versions(self, job_type_name: str) -> List[JobVersion]:
        """List all versions for job type."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT * FROM job_versions WHERE job_type_name = ? ORDER BY built_at DESC",
                (job_type_name,)
            ).fetchall()

        return [self._row_to_version(row) for row in rows]

    def _row_to_version(self, row: sqlite3.Row) -> JobVersion:
        """Convert database row to JobVersion."""
        return JobVersion(
            digest=row['digest'],
            job_type_name=row['job_type_name'],
            version_tag=row['version_tag'],
            built_at=datetime.fromisoformat(row['built_at']),
            built_by=row['built_by'],
            dockerfile_hash=row['dockerfile_hash'],
            requirements_hash=row['requirements_hash'],
            image_ref=row['image_ref'],
        )

    # Dead letter queue methods

    def add_to_dlq(self, entry: DeadLetterEntry) -> int:
        """
        Add a failed job to the dead letter queue.

        Args:
            entry: DeadLetterEntry with job details

        Returns:
            ID of the inserted entry
        """
        job_data_json = json.dumps(entry.job_data)
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                INSERT INTO dead_letter_queue (
                    job_id, job_data_json, error_type, error_message,
                    retry_count, created_at, client_ip, correlation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.job_id,
                job_data_json,
                entry.error_type,
                entry.error_message,
                entry.retry_count,
                entry.created_at.isoformat(),
                entry.client_ip,
                entry.correlation_id,
            ))
            conn.commit()
            return cursor.lastrowid or 0

    def get_dlq_entry(self, entry_id: int) -> Optional[DeadLetterEntry]:
        """Get a DLQ entry by ID."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM dead_letter_queue WHERE id = ?",
                (entry_id,)
            ).fetchone()

        if not row:
            return None

        return self._row_to_dlq_entry(row)

    def list_dlq_entries(
        self,
        error_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[DeadLetterEntry]:
        """
        List dead letter queue entries.

        Args:
            error_type: Filter by error type
            limit: Maximum entries to return
            offset: Offset for pagination

        Returns:
            List of DeadLetterEntry objects
        """
        query = "SELECT * FROM dead_letter_queue WHERE 1=1"
        params: List = []

        if error_type:
            query += " AND error_type = ?"
            params.append(error_type)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_dlq_entry(row) for row in rows]

    def remove_from_dlq(self, entry_id: int) -> bool:
        """
        Remove an entry from the dead letter queue.

        Args:
            entry_id: ID of entry to remove

        Returns:
            True if entry was removed
        """
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "DELETE FROM dead_letter_queue WHERE id = ?",
                (entry_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_dlq_stats(self) -> dict:
        """
        Get statistics about the dead letter queue.

        Returns:
            Dict with count, error_type breakdown, etc.
        """
        with self._lock:
            conn = self._get_connection()

            total = conn.execute(
                "SELECT COUNT(*) as count FROM dead_letter_queue"
            ).fetchone()['count']

            by_error = conn.execute("""
                SELECT error_type, COUNT(*) as count
                FROM dead_letter_queue
                GROUP BY error_type
                ORDER BY count DESC
            """).fetchall()

        return {
            "total": total,
            "by_error_type": {row['error_type']: row['count'] for row in by_error}
        }

    def cleanup_old_dlq_entries(self, ttl_days: int) -> int:
        """
        Delete DLQ entries older than TTL.

        Args:
            ttl_days: Days to retain entries

        Returns:
            Number of entries deleted
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()

        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "DELETE FROM dead_letter_queue WHERE created_at < ?",
                (cutoff,)
            )
            conn.commit()
            return cursor.rowcount

    def _row_to_dlq_entry(self, row: sqlite3.Row) -> DeadLetterEntry:
        """Convert database row to DeadLetterEntry."""
        # Parse job_data with error handling
        job_data = {}
        try:
            job_data = json.loads(row['job_data_json'])
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                "Failed to parse job_data_json for DLQ entry %s: %s",
                row['id'], e
            )

        return DeadLetterEntry(
            id=row['id'],
            job_id=row['job_id'],
            job_data=job_data,
            error_type=row['error_type'],
            error_message=row['error_message'],
            retry_count=row['retry_count'],
            created_at=datetime.fromisoformat(row['created_at']),
            client_ip=row['client_ip'],
            correlation_id=row['correlation_id'],
        )
