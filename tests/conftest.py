"""
Pytest configuration for Tako VM tests.
"""

import os
import subprocess
import warnings

import pytest

# Suppress urllib3 LibreSSL warning on macOS (harmless)
warnings.filterwarnings("ignore", category=DeprecationWarning, module="urllib3")
try:
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except ImportError:
    pass


def is_docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_executor_image_available() -> bool:
    """Check if the executor Docker image exists."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "code-executor:latest"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# Check Docker availability once at module load
DOCKER_AVAILABLE = is_docker_available()
EXECUTOR_IMAGE_AVAILABLE = is_executor_image_available()


# Skip markers for tests that require Docker
requires_docker = pytest.mark.skipif(
    not DOCKER_AVAILABLE,
    reason="Docker is not available"
)

requires_executor_image = pytest.mark.skipif(
    not EXECUTOR_IMAGE_AVAILABLE,
    reason="Docker executor image (code-executor:latest) not built"
)


def pytest_configure(config):
    """Add custom markers."""
    config.addinivalue_line(
        "markers", "requires_docker: mark test as requiring Docker"
    )
    config.addinivalue_line(
        "markers", "requires_executor_image: mark test as requiring the executor image"
    )


def pytest_collection_modifyitems(config, items):
    """Auto-apply skip markers based on Docker availability."""
    for item in items:
        # Auto-skip sandbox tests if Docker/executor image not available
        if "test_sandbox" in item.nodeid:
            if not DOCKER_AVAILABLE:
                item.add_marker(pytest.mark.skip(reason="Docker not available"))
            elif not EXECUTOR_IMAGE_AVAILABLE:
                item.add_marker(pytest.mark.skip(
                    reason="Executor image not built. Run: docker build -t code-executor:latest -f docker/Dockerfile.executor ."
                ))

        # Auto-skip API tests if Docker not available
        if "test_api" in item.nodeid and "TestHealthEndpoint" not in item.nodeid:
            if not DOCKER_AVAILABLE:
                item.add_marker(pytest.mark.skip(reason="Docker not available"))


@pytest.fixture(scope="session")
def docker_available():
    """Fixture to check if Docker is available."""
    return DOCKER_AVAILABLE


@pytest.fixture(scope="session")
def executor_image_available():
    """Fixture to check if executor image is available."""
    return EXECUTOR_IMAGE_AVAILABLE
