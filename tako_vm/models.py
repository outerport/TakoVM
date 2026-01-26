"""
Core data models for Tako VM production runtime.

This module defines the audit-grade ExecutionRecord and related models
used throughout the system.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import datetime
import hashlib
import uuid


class ResourceUsage(BaseModel):
    """Resource consumption metrics from container execution."""

    max_rss_mb: Optional[float] = None
    """Peak resident set size in megabytes."""

    cpu_time_ms: Optional[int] = None
    """Total CPU time (user + system) in milliseconds."""

    wall_time_ms: int = 0
    """Wall clock duration in milliseconds."""


class Artifact(BaseModel):
    """Output artifact metadata."""

    name: str
    """Filename, e.g., 'result.json'."""

    size_bytes: int
    """File size in bytes."""

    sha256: str
    """SHA256 hash of file contents."""

    path: str
    """Container path, e.g., '/output/result.json'."""


class ExecutionError(BaseModel):
    """Sanitized error information."""

    type: str
    """Error type, e.g., 'timeout', 'oom', 'runtime_error'."""

    message: str
    """Sanitized error message (no sensitive paths)."""


class ExecutionRecord(BaseModel):
    """
    Audit-grade execution record.

    This is the primary record returned from all executions, containing
    complete audit trail information for compliance and debugging.
    """

    # Identity
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier for this execution."""

    # Status
    status: Literal["pending", "running", "success", "error", "timeout", "oom", "cancelled"] = "pending"
    """Current execution status."""

    # Job reference
    job_type: str = "default"
    """Job type name, e.g., 'data-processing'."""

    job_version: str = "latest"
    """Job type version, e.g., 'sha256:a1b2c3d4' or 'v1.0.0'."""

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    """When the job was submitted."""

    started_at: Optional[datetime] = None
    """When execution began."""

    ended_at: Optional[datetime] = None
    """When execution completed."""

    duration_ms: Optional[int] = None
    """Total execution duration in milliseconds."""

    # Input fingerprints (not raw data for security)
    code_hash: str = ""
    """SHA256 hash of submitted code."""

    input_hash: str = ""
    """SHA256 hash of input_data JSON."""

    # Results
    exit_code: Optional[int] = None
    """Container exit code."""

    stdout: str = ""
    """Captured stdout (bounded to max_stdout_bytes)."""

    stderr: str = ""
    """Captured stderr (bounded to max_stderr_bytes)."""

    output: Optional[dict] = None
    """Parsed output from /output/result.json."""

    # Resources
    resource_usage: Optional[ResourceUsage] = None
    """Resource consumption metrics."""

    # Artifacts
    artifacts: List[Artifact] = Field(default_factory=list)
    """List of output artifacts with metadata."""

    # Error (sanitized)
    error: Optional[ExecutionError] = None
    """Sanitized error information if failed."""

    # Audit
    api_key_id: Optional[str] = None
    """API key that initiated this execution."""

    client_ip: Optional[str] = None
    """Client IP address."""

    @field_validator('stdout', 'stderr', mode='before')
    @classmethod
    def truncate_output(cls, v: str) -> str:
        """Ensure outputs don't exceed reasonable size in model."""
        if v and len(v) > 65536:
            return v[:65536]
        return v or ""

    @staticmethod
    def hash_content(content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def to_summary(self) -> dict:
        """Return a summary suitable for list endpoints."""
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "job_type": self.job_type,
            "created_at": self.created_at.isoformat(),
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
        }


class APIKey(BaseModel):
    """API key configuration with quotas and permissions."""

    key_id: str
    """Unique identifier for this key."""

    key_hash: str
    """SHA256 hash of the actual key (never store raw key)."""

    name: str
    """Human-readable name for this key."""

    tenant_id: Optional[str] = None
    """Project or tenant identifier."""

    # Quotas
    rate_limit_per_minute: int = 60
    """Maximum requests per minute."""

    rate_limit_per_hour: int = 1000
    """Maximum requests per hour."""

    max_concurrent_jobs: int = 5
    """Maximum concurrent running jobs."""

    max_timeout_seconds: int = 300
    """Maximum allowed timeout for jobs."""

    # Permissions
    allowed_job_types: List[str] = Field(default_factory=lambda: ["*"])
    """List of allowed job types, '*' means all."""

    # Status
    enabled: bool = True
    """Whether this key is active."""

    created_at: datetime = Field(default_factory=datetime.utcnow)
    """When this key was created."""

    expires_at: Optional[datetime] = None
    """When this key expires (None = never)."""

    def is_valid(self) -> bool:
        """Check if key is currently valid."""
        if not self.enabled:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True

    def can_use_job_type(self, job_type: str) -> bool:
        """Check if key can use specified job type."""
        if "*" in self.allowed_job_types:
            return True
        return job_type in self.allowed_job_types


class JobVersion(BaseModel):
    """Immutable job type version record."""

    job_type_name: str
    """Name of the job type."""

    version_tag: Optional[str] = None
    """Semantic version tag, e.g., 'v1.0.0'."""

    digest: str
    """SHA256 digest of job type configuration."""

    # Build metadata
    built_at: datetime = Field(default_factory=datetime.utcnow)
    """When this version was built."""

    built_by: Optional[str] = None
    """API key or 'manual' that triggered the build."""

    dockerfile_hash: str = ""
    """SHA256 of generated Dockerfile."""

    requirements_hash: str = ""
    """SHA256 of requirements list."""

    # Docker image reference
    image_ref: str = ""
    """Full image reference, e.g., 'tako-vm-data-processing@sha256:...'."""

    @property
    def full_ref(self) -> str:
        """Return job_type@digest format."""
        return f"{self.job_type_name}@sha256:{self.digest[:12]}"

    @property
    def short_digest(self) -> str:
        """Return shortened digest for display."""
        return self.digest[:12]
