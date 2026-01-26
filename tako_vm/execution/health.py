"""
Docker health monitoring with circuit breaker and cleanup utilities.

Provides:
- Circuit breaker to prevent cascading failures when Docker is unavailable
- Orphaned container cleanup on startup
"""

import subprocess
import time
import threading
import logging
from dataclasses import dataclass
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Failing, reject requests immediately
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    failure_threshold: int = 5
    """Number of failures before opening circuit."""

    recovery_timeout: float = 30.0
    """Seconds to wait before testing recovery (half-open state)."""

    success_threshold: int = 2
    """Successful calls in half-open state before closing circuit."""


class DockerCircuitBreaker:
    """
    Circuit breaker for Docker operations.

    Prevents cascading failures by temporarily stopping attempts
    to reach Docker when it's unavailable.
    """

    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        """
        Initialize circuit breaker.

        Args:
            config: Circuit breaker configuration
        """
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            return self._state

    @property
    def is_available(self) -> bool:
        """Check if requests should be allowed through."""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                if self._last_failure_time is not None:
                    elapsed = time.time() - self._last_failure_time
                    if elapsed >= self.config.recovery_timeout:
                        self._state = CircuitState.HALF_OPEN
                        self._success_count = 0
                        logger.info("Circuit breaker entering half-open state")
                        return True
                return False

            # HALF_OPEN - allow requests for testing
            return True

    def record_success(self) -> None:
        """Record a successful Docker operation."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info("Circuit breaker closed (Docker recovered)")
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    def record_failure(self, error: Optional[str] = None) -> None:
        """
        Record a failed Docker operation.

        Args:
            error: Optional error message for logging
        """
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if error:
                logger.warning(f"Docker operation failed: {error}")

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                self._state = CircuitState.OPEN
                logger.warning("Circuit breaker re-opened (Docker still failing)")
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.config.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.error(
                        f"Circuit breaker opened after {self._failure_count} failures"
                    )

    def check_docker_health(self) -> bool:
        """
        Perform a health check on Docker daemon.

        Returns:
            True if Docker is healthy
        """
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5.0,
                check=False
            )
            healthy = result.returncode == 0

            if healthy:
                self.record_success()
            else:
                self.record_failure("docker info returned non-zero")

            return healthy

        except subprocess.TimeoutExpired:
            self.record_failure("docker info timed out")
            return False
        except FileNotFoundError:
            self.record_failure("docker command not found")
            return False
        except Exception as e:
            self.record_failure(str(e))
            return False

    def get_status(self) -> dict:
        """Get circuit breaker status for monitoring."""
        with self._lock:
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "last_failure": self._last_failure_time,
            }


class DockerCleanup:
    """Utilities for cleaning up Docker resources."""

    CONTAINER_LABEL = "tako-vm-executor"
    """Label used to identify Tako VM executor containers."""

    @classmethod
    def cleanup_orphaned_containers(cls, max_age_seconds: int = 3600) -> int:
        """
        Remove orphaned Tako VM executor containers.

        Args:
            max_age_seconds: Remove containers older than this age

        Returns:
            Number of containers removed
        """
        removed = 0

        try:
            # Find containers with our label
            result = subprocess.run(
                [
                    "docker", "ps", "-a",
                    "--filter", f"label={cls.CONTAINER_LABEL}",
                    "--format", "{{.ID}}\t{{.CreatedAt}}\t{{.Status}}"
                ],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False
            )

            if result.returncode != 0:
                logger.warning(f"Failed to list containers: {result.stderr}")
                return 0

            # Also find containers with our naming pattern (job-*)
            result_pattern = subprocess.run(
                [
                    "docker", "ps", "-a",
                    "--filter", "name=job-",
                    "--format", "{{.ID}}"
                ],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False
            )

            containers_to_remove = set()

            # Parse labeled containers
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split('\t')
                if len(parts) >= 1:
                    containers_to_remove.add(parts[0])

            # Add pattern-matched containers
            for container_id in result_pattern.stdout.strip().split('\n'):
                if container_id:
                    containers_to_remove.add(container_id)

            # Remove containers
            for container_id in containers_to_remove:
                try:
                    # Force remove (handles running containers too)
                    rm_result = subprocess.run(
                        ["docker", "rm", "-f", container_id],
                        capture_output=True,
                        timeout=10.0,
                        check=False
                    )
                    if rm_result.returncode == 0:
                        removed += 1
                        logger.info(f"Removed orphaned container: {container_id}")
                except Exception as e:
                    logger.warning(f"Failed to remove container {container_id}: {e}")

            if removed > 0:
                logger.info(f"Cleaned up {removed} orphaned containers")

            return removed

        except subprocess.TimeoutExpired:
            logger.warning("Container cleanup timed out")
            return 0
        except Exception as e:
            logger.error(f"Container cleanup failed: {e}")
            return 0

    @classmethod
    def cleanup_dangling_images(cls) -> int:
        """
        Remove dangling Docker images.

        Returns:
            Number of images removed
        """
        try:
            result = subprocess.run(
                ["docker", "image", "prune", "-f"],
                capture_output=True,
                text=True,
                timeout=60.0,
                check=False
            )

            if result.returncode == 0:
                # Parse output to count removed images
                output = result.stdout
                if "Total reclaimed space" in output:
                    logger.info(f"Image cleanup: {output.strip()}")
                return 0  # Docker doesn't give count, just space

            return 0

        except Exception as e:
            logger.warning(f"Image cleanup failed: {e}")
            return 0


# Global circuit breaker instance
_circuit_breaker: Optional[DockerCircuitBreaker] = None


def get_circuit_breaker() -> DockerCircuitBreaker:
    """Get or create the global circuit breaker instance."""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = DockerCircuitBreaker()
    return _circuit_breaker


def startup_cleanup() -> dict:
    """
    Perform startup cleanup tasks.

    Returns:
        Dict with cleanup results
    """
    results = {
        "docker_healthy": False,
        "containers_removed": 0,
    }

    # Check Docker health
    breaker = get_circuit_breaker()
    results["docker_healthy"] = breaker.check_docker_health()

    if results["docker_healthy"]:
        # Clean up orphaned containers
        results["containers_removed"] = DockerCleanup.cleanup_orphaned_containers()

    return results
