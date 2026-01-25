"""
Job Type Registry - Define and manage pre-built container configurations.

Job types allow you to pre-configure containers with specific dependencies,
making execution faster and more predictable.

Example:
    from job_types import JobTypeRegistry, JobType

    registry = JobTypeRegistry()
    registry.register(JobType(
        name="data-processing",
        requirements=["pandas", "numpy"],
    ))
"""

from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
import json


@dataclass
class JobType:
    """Configuration for a job type container."""

    name: str
    """Unique identifier for this job type."""

    requirements: list[str] = field(default_factory=list)
    """Python packages to install (pip install)."""

    python_version: str = "3.11"
    """Python version to use."""

    base_image: Optional[str] = None
    """Custom base image. If None, uses python:{version}-slim."""

    shared_code: list[str] = field(default_factory=list)
    """Paths to Python files/modules to include in container."""

    environment: dict[str, str] = field(default_factory=dict)
    """Environment variables to set in container."""

    memory_limit: str = "512m"
    """Memory limit for container."""

    cpu_limit: float = 1.0
    """CPU limit for container."""

    timeout: int = 30
    """Default timeout in seconds."""

    @property
    def image_name(self) -> str:
        """Docker image name for this job type."""
        return f"tako-vm-{self.name}:latest"

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "requirements": self.requirements,
            "python_version": self.python_version,
            "base_image": self.base_image,
            "shared_code": self.shared_code,
            "environment": self.environment,
            "memory_limit": self.memory_limit,
            "cpu_limit": self.cpu_limit,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "JobType":
        """Create from dictionary."""
        return cls(**data)


class JobTypeRegistry:
    """
    Registry for job type configurations.

    Stores job types in a JSON file for persistence.
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize the registry.

        Args:
            config_path: Path to config file. Defaults to ./job_types.json
        """
        self.config_path = config_path or Path("job_types.json")
        self._job_types: dict[str, JobType] = {}
        self._load()

    def _load(self):
        """Load job types from config file."""
        if self.config_path.exists():
            with open(self.config_path) as f:
                data = json.load(f)
                for item in data.get("job_types", []):
                    jt = JobType.from_dict(item)
                    self._job_types[jt.name] = jt

    def _save(self):
        """Save job types to config file."""
        data = {
            "job_types": [jt.to_dict() for jt in self._job_types.values()]
        }
        with open(self.config_path, "w") as f:
            json.dump(data, f, indent=2)

    def register(self, job_type: JobType) -> None:
        """
        Register a new job type.

        Args:
            job_type: Job type configuration
        """
        self._job_types[job_type.name] = job_type
        self._save()

    def get(self, name: str) -> Optional[JobType]:
        """
        Get a job type by name.

        Args:
            name: Job type name

        Returns:
            JobType or None if not found
        """
        return self._job_types.get(name)

    def list(self) -> list[JobType]:
        """List all registered job types."""
        return list(self._job_types.values())

    def remove(self, name: str) -> bool:
        """
        Remove a job type.

        Args:
            name: Job type name

        Returns:
            True if removed, False if not found
        """
        if name in self._job_types:
            del self._job_types[name]
            self._save()
            return True
        return False


# Default job types
DEFAULT_JOB_TYPES = [
    JobType(
        name="default",
        requirements=[],
        memory_limit="512m",
        cpu_limit=1.0,
        timeout=30,
    ),
    JobType(
        name="data-processing",
        requirements=["pandas", "numpy"],
        memory_limit="1g",
        cpu_limit=2.0,
        timeout=60,
    ),
    JobType(
        name="ml-inference",
        requirements=["numpy", "scikit-learn"],
        memory_limit="2g",
        cpu_limit=2.0,
        timeout=120,
    ),
]


def init_default_job_types(registry: JobTypeRegistry) -> None:
    """Initialize registry with default job types."""
    for jt in DEFAULT_JOB_TYPES:
        if registry.get(jt.name) is None:
            registry.register(jt)
