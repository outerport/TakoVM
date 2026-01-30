"""
Pytest configuration for Tako VM tests.
"""

import os
import subprocess
import tempfile
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


def is_running_in_vm() -> bool:
    """
    Detect if tests are running in an environment where host mounts don't work.

    This includes VM environments (e.g., Lima on macOS) and CI environments
    where temp directory mounts may not work correctly with Docker.
    """
    # Check for CI environment (GitHub Actions, GitLab CI, etc.)
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return True

    # Check for Lima environment
    if os.path.exists("/Users") and os.path.exists("/home"):
        # We're in Lima (macOS paths mounted + Linux home exists)
        return True

    # Check if temp dir is accessible from Docker
    # This catches cases where Docker is remote or in a VM
    try:
        temp_dir = tempfile.gettempdir()
        result = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{temp_dir}:/test:ro", "alpine", "ls", "/test"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        # If mount fails or is empty when it shouldn't be, we're in a VM
        if result.returncode != 0:
            return True
    except Exception:
        pass

    return False


def is_gvisor_available() -> bool:
    """Check if gVisor runtime is available."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0 and "runsc" in result.stdout
    except Exception:
        return False


# Check Docker availability once at module load
DOCKER_AVAILABLE = is_docker_available()
EXECUTOR_IMAGE_AVAILABLE = is_executor_image_available()
RUNNING_IN_VM = is_running_in_vm() if DOCKER_AVAILABLE else False
GVISOR_AVAILABLE = is_gvisor_available() if DOCKER_AVAILABLE else False


# Skip markers for tests that require Docker
requires_docker = pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker is not available")

requires_executor_image = pytest.mark.skipif(
    not EXECUTOR_IMAGE_AVAILABLE, reason="Docker executor image (code-executor:latest) not built"
)


def pytest_configure(config):
    """Add custom markers."""
    config.addinivalue_line("markers", "requires_docker: mark test as requiring Docker")
    config.addinivalue_line(
        "markers", "requires_executor_image: mark test as requiring the executor image"
    )
    config.addinivalue_line(
        "markers", "requires_host_mounts: mark test as requiring host path mounts (skip in VM)"
    )
    config.addinivalue_line("markers", "requires_gvisor: mark test as requiring gVisor runtime")


def pytest_collection_modifyitems(config, items):
    """Auto-apply skip markers based on Docker availability."""
    for item in items:
        # Auto-skip sandbox tests if Docker/executor image not available
        if "test_sandbox" in item.nodeid:
            if not DOCKER_AVAILABLE:
                item.add_marker(pytest.mark.skip(reason="Docker not available"))
            elif not EXECUTOR_IMAGE_AVAILABLE:
                item.add_marker(
                    pytest.mark.skip(
                        reason="Executor image not built. Run: docker build -t code-executor:latest -f docker/Dockerfile.executor ."
                    )
                )

        # Auto-skip API tests if Docker not available
        if "test_api" in item.nodeid and "TestHealthEndpoint" not in item.nodeid:
            if not DOCKER_AVAILABLE:
                item.add_marker(pytest.mark.skip(reason="Docker not available"))

        # Skip tests that require host mounts when running in a VM
        if item.get_closest_marker("requires_host_mounts"):
            if RUNNING_IN_VM:
                item.add_marker(
                    pytest.mark.skip(
                        reason="Test requires host path mounts which don't work in VM environments"
                    )
                )

        # Skip tests that require gVisor when it's not available
        if item.get_closest_marker("requires_gvisor"):
            if not GVISOR_AVAILABLE:
                item.add_marker(pytest.mark.skip(reason="gVisor runtime not available"))


@pytest.fixture(scope="session")
def docker_available():
    """Fixture to check if Docker is available."""
    return DOCKER_AVAILABLE


@pytest.fixture(scope="session")
def executor_image_available():
    """Fixture to check if executor image is available."""
    return EXECUTOR_IMAGE_AVAILABLE


@pytest.fixture(scope="session")
def gvisor_available():
    """Fixture to check if gVisor is available."""
    return GVISOR_AVAILABLE


@pytest.fixture(scope="session")
def running_in_vm():
    """Fixture to check if running in a VM environment."""
    return RUNNING_IN_VM


@pytest.fixture
def temp_data_dir():
    """
    Create a temporary data directory for isolated tests.

    Resets config and sets environment variables to use a temp directory,
    ensuring test isolation from user data.
    """
    from pathlib import Path

    from tako_vm.config import reset_config

    with tempfile.TemporaryDirectory() as tmpdir:
        original_data_dir = os.environ.get("TAKO_VM_DATA_DIR")
        original_db_file = os.environ.get("TAKO_VM_DATABASE_FILE")

        os.environ["TAKO_VM_DATA_DIR"] = tmpdir
        os.environ["TAKO_VM_DATABASE_FILE"] = str(Path(tmpdir) / "test.db")

        reset_config()

        yield tmpdir

        reset_config()
        if original_data_dir:
            os.environ["TAKO_VM_DATA_DIR"] = original_data_dir
        else:
            os.environ.pop("TAKO_VM_DATA_DIR", None)
        if original_db_file:
            os.environ["TAKO_VM_DATABASE_FILE"] = original_db_file
        else:
            os.environ.pop("TAKO_VM_DATABASE_FILE", None)


@pytest.fixture
def test_storage(temp_data_dir):
    """
    Create an ExecutionStorage instance for testing.

    Uses the temp_data_dir fixture for isolation.
    """
    from pathlib import Path

    from tako_vm.storage import ExecutionStorage

    storage = ExecutionStorage(Path(temp_data_dir) / "test.db")
    storage.init()
    yield storage
    storage.close()
