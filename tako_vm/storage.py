"""
SQLite storage for Tako VM execution records.

Provides async CRUD operations for ExecutionRecords and JobVersions.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

from .models import ExecutionRecord, ResourceUsage, Artifact, ExecutionError, JobVersion


# SQLite schema
SCHEMA_SQL = """
-- Execution records table
CREATE TABLE IF NOT EXISTS execution_records (
    execution_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    job_type TEXT NOT NULL,
    job_version TEXT NOT NULL,

    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    duration_ms INTEGER,

    code_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,

    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    output_json TEXT,

    max_rss_mb REAL,
    cpu_time_ms INTEGER,
    wall_time_ms INTEGER,

    artifacts_json TEXT,
    error_json TEXT,

    api_key_id TEXT,
    client_ip TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_execution_status ON execution_records(status);
CREATE INDEX IF NOT EXISTS idx_execution_job_type ON execution_records(job_type);
CREATE INDEX IF NOT EXISTS idx_execution_created_at ON execution_records(created_at);
CREATE INDEX IF NOT EXISTS idx_execution_api_key_id ON execution_records(api_key_id);

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

-- API key usage tracking for rate limiting
CREATE TABLE IF NOT EXISTS api_key_usage (
    key_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    request_count INTEGER DEFAULT 0,
    PRIMARY KEY (key_id, window_start)
);
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

    def init(self) -> None:
        """Create database and tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        conn.executescript(SCHEMA_SQL)
        conn.commit()

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
        conn = self._get_connection()

        # Serialize complex fields
        resource_usage = record.resource_usage
        artifacts_json = json.dumps([a.model_dump() for a in record.artifacts])
        error_json = json.dumps(record.error.model_dump()) if record.error else None
        output_json = json.dumps(record.output) if record.output else None

        conn.execute("""
            INSERT OR REPLACE INTO execution_records (
                execution_id, status, job_type, job_version,
                created_at, started_at, ended_at, duration_ms,
                code_hash, input_hash,
                exit_code, stdout, stderr, output_json,
                max_rss_mb, cpu_time_ms, wall_time_ms,
                artifacts_json, error_json,
                api_key_id, client_ip
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.execution_id,
            record.status,
            record.job_type,
            record.job_version,
            record.created_at.isoformat(),
            record.started_at.isoformat() if record.started_at else None,
            record.ended_at.isoformat() if record.ended_at else None,
            record.duration_ms,
            record.code_hash,
            record.input_hash,
            record.exit_code,
            record.stdout,
            record.stderr,
            output_json,
            resource_usage.max_rss_mb if resource_usage else None,
            resource_usage.cpu_time_ms if resource_usage else None,
            resource_usage.wall_time_ms if resource_usage else None,
            artifacts_json,
            error_json,
            record.api_key_id,
            record.client_ip,
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
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM execution_records WHERE execution_id = ?",
            (execution_id,)
        ).fetchone()

        if not row:
            return None

        return self._row_to_record(row)

    def list_records(
        self,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        api_key_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[ExecutionRecord]:
        """
        List execution records with optional filters.

        Args:
            status: Filter by status
            job_type: Filter by job type
            api_key_id: Filter by API key
            limit: Maximum records to return
            offset: Offset for pagination

        Returns:
            List of ExecutionRecords
        """
        conn = self._get_connection()

        query = "SELECT * FROM execution_records WHERE 1=1"
        params: List = []

        if status:
            query += " AND status = ?"
            params.append(status)

        if job_type:
            query += " AND job_type = ?"
            params.append(job_type)

        if api_key_id:
            query += " AND api_key_id = ?"
            params.append(api_key_id)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

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
        conn = self._get_connection()
        cutoff = (datetime.utcnow() - timedelta(days=ttl_days)).isoformat()

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

        # Parse artifacts
        artifacts = []
        if row['artifacts_json']:
            artifacts_data = json.loads(row['artifacts_json'])
            artifacts = [Artifact(**a) for a in artifacts_data]

        # Parse error
        error = None
        if row['error_json']:
            error_data = json.loads(row['error_json'])
            error = ExecutionError(**error_data)

        # Parse output
        output = None
        if row['output_json']:
            output = json.loads(row['output_json'])

        return ExecutionRecord(
            execution_id=row['execution_id'],
            status=row['status'],
            job_type=row['job_type'],
            job_version=row['job_version'],
            created_at=datetime.fromisoformat(row['created_at']),
            started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
            ended_at=datetime.fromisoformat(row['ended_at']) if row['ended_at'] else None,
            duration_ms=row['duration_ms'],
            code_hash=row['code_hash'],
            input_hash=row['input_hash'],
            exit_code=row['exit_code'],
            stdout=row['stdout'] or "",
            stderr=row['stderr'] or "",
            output=output,
            resource_usage=resource_usage,
            artifacts=artifacts,
            error=error,
            api_key_id=row['api_key_id'],
            client_ip=row['client_ip'],
        )

    # Job version methods

    def save_version(self, version: JobVersion) -> None:
        """Save job version record."""
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

    # Rate limiting methods

    def get_request_count(self, key_id: str, window_start: str) -> int:
        """Get request count for rate limiting window."""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT request_count FROM api_key_usage WHERE key_id = ? AND window_start = ?",
            (key_id, window_start)
        ).fetchone()

        return row['request_count'] if row else 0

    def increment_request_count(self, key_id: str, window_start: str) -> int:
        """Increment request count for rate limiting window."""
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO api_key_usage (key_id, window_start, request_count)
            VALUES (?, ?, 1)
            ON CONFLICT(key_id, window_start)
            DO UPDATE SET request_count = request_count + 1
        """, (key_id, window_start))
        conn.commit()

        row = conn.execute(
            "SELECT request_count FROM api_key_usage WHERE key_id = ? AND window_start = ?",
            (key_id, window_start)
        ).fetchone()

        return row['request_count']

    def cleanup_old_usage(self, hours: int = 2) -> int:
        """Clean up old rate limit tracking data."""
        conn = self._get_connection()
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()[:16]

        cursor = conn.execute(
            "DELETE FROM api_key_usage WHERE window_start < ?",
            (cutoff,)
        )
        conn.commit()
        return cursor.rowcount
