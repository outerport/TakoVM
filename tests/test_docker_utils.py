"""
Tests for Docker utility functions.

Tests container naming, cleanup, and platform detection.
"""

import subprocess
from unittest.mock import MagicMock, patch

import tako_vm.execution.worker as worker_module
from tako_vm.config import TakoVMConfig
from tako_vm.execution import CodeExecutor
from tako_vm.execution.docker import (
    generate_container_name,
    is_native_linux,
    kill_container,
)


class TestIsNativeLinux:
    """Tests for is_native_linux() function."""

    def test_is_native_linux_on_linux(self):
        """is_native_linux() returns True on Linux."""
        with patch("platform.system", return_value="Linux"):
            assert is_native_linux() is True

    def test_is_native_linux_on_macos(self):
        """is_native_linux() returns False on macOS."""
        with patch("platform.system", return_value="Darwin"):
            assert is_native_linux() is False

    def test_is_native_linux_on_windows(self):
        """is_native_linux() returns False on Windows."""
        with patch("platform.system", return_value="Windows"):
            assert is_native_linux() is False


class TestGenerateContainerName:
    """Tests for generate_container_name() function."""

    def test_generate_container_name_with_job_id(self):
        """Container name includes job_id when provided."""
        name = generate_container_name("tako", job_id="abc123")
        assert name == "tako-abc123"

    def test_generate_container_name_without_job_id(self):
        """Container name uses UUID when job_id not provided."""
        name = generate_container_name("tako-sandbox")

        assert name.startswith("tako-sandbox-")
        # UUID hex part should be 12 characters
        suffix = name.replace("tako-sandbox-", "")
        assert len(suffix) == 12
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_generate_container_name_unique(self):
        """Each call generates unique name."""
        names = [generate_container_name("tako") for _ in range(10)]
        assert len(set(names)) == 10  # All unique

    def test_generate_container_name_custom_prefix(self):
        """Supports custom prefix."""
        name = generate_container_name("my-app", job_id="job1")
        assert name == "my-app-job1"


class TestKillContainer:
    """Tests for kill_container() function."""

    @patch("subprocess.run")
    def test_kill_container_calls_docker(self, mock_run):
        """kill_container calls docker kill command."""
        mock_run.return_value = MagicMock(returncode=0)

        kill_container("tako-test-123")

        mock_run.assert_called_once_with(
            ["docker", "kill", "tako-test-123"],
            capture_output=True,
            timeout=10,
            check=False,
        )

    @patch("subprocess.run")
    def test_kill_container_ignores_errors(self, mock_run):
        """kill_container silently ignores errors."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=10)

        # Should not raise
        kill_container("nonexistent-container")

    @patch("subprocess.run")
    def test_kill_container_ignores_docker_errors(self, mock_run):
        """kill_container ignores docker command failures."""
        mock_run.return_value = MagicMock(returncode=1)  # Container not found

        # Should not raise
        kill_container("already-stopped-container")

    @patch("subprocess.run")
    def test_kill_container_handles_exception(self, mock_run):
        """kill_container handles unexpected exceptions."""
        mock_run.side_effect = Exception("Unexpected error")

        # Should not raise
        kill_container("error-container")


class TestConcurrentIdlessJobNaming:
    """A job's container name is derived from its id, and jobs submitted
    without an id must still get unique containers under concurrency.

    The id-less fallback derives an id from the wall clock, so two jobs
    created close together can resolve the same instant. Each must still
    map to a distinct container name -- otherwise the second ``docker run``
    fails with a name conflict and the job is lost.
    """

    def _executor(self, monkeypatch):
        # Treat gVisor as available so the executor resolves a runtime without
        # a real runsc install; no container is ever launched (see below).
        monkeypatch.setattr(worker_module, "_gvisor_available", True)
        return CodeExecutor(config=TakoVMConfig(container_runtime="runsc", security_mode="strict"))

    def test_idless_jobs_at_same_instant_get_unique_container_names(self, monkeypatch):
        executor = self._executor(monkeypatch)

        # Freeze the wall clock so both jobs resolve to the same instant --
        # the collision the fallback must survive.
        monkeypatch.setattr(worker_module.time, "time", lambda: 1000.5)

        seen_job_ids = []

        def capture(**kwargs):
            seen_job_ids.append(kwargs["job_id"])
            return {"success": True, "stdout": "", "stderr": "", "exit_code": 0}

        monkeypatch.setattr(executor, "_run_container", capture)

        job = {"code": "print('x')", "input_data": {}}
        executor.execute_job(dict(job))
        executor.execute_job(dict(job))

        assert len(seen_job_ids) == 2
        container_names = {generate_container_name("tako", jid) for jid in seen_job_ids}
        assert len(container_names) == 2, container_names

    def test_explicit_job_id_is_used_verbatim(self, monkeypatch):
        """An explicitly supplied id is still honoured unchanged, so callers
        that reuse an id for idempotency/tracking keep that behaviour."""
        executor = self._executor(monkeypatch)

        captured = {}

        def capture(**kwargs):
            captured["job_id"] = kwargs["job_id"]
            return {"success": True, "stdout": "", "stderr": "", "exit_code": 0}

        monkeypatch.setattr(executor, "_run_container", capture)

        executor.execute_job({"id": "explicit-123", "code": "print('x')", "input_data": {}})

        assert captured["job_id"] == "explicit-123"


class TestContainerNameValidation:
    """Tests for container name format validation."""

    def test_container_name_format_with_job_id(self):
        """Container names with job_id follow expected format."""
        name = generate_container_name("tako", job_id="test-job-123")

        # Should be valid Docker container name
        # Docker allows: [a-zA-Z0-9][a-zA-Z0-9_.-]*
        assert name[0].isalnum()
        assert all(c.isalnum() or c in "_.-" for c in name)

    def test_container_name_format_uuid(self):
        """Container names with UUID follow expected format."""
        name = generate_container_name("tako")

        # Should be valid Docker container name
        assert name[0].isalnum()
        assert all(c.isalnum() or c in "_.-" for c in name)

    def test_container_name_length(self):
        """Container names are reasonable length."""
        name = generate_container_name("tako-sandbox", job_id="a" * 64)

        # Docker has a max container name length, but we don't enforce it
        # Just verify it's not empty
        assert len(name) > 0
