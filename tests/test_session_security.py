"""Security-property tests for the long-lived session plane.

These lock the hardening applied to the fork-only session feature: the
``ensure_workspace_writable`` permission model, the ``--mount`` CSV-injection
guard (a host-root-escape), the exec output cap / env validation / in-container
timeout, and the workspace/scope path validators that are the only guards on an
unauthenticated server.
"""

import os
import stat
from pathlib import Path

import pytest

from tako_vm.config import TakoVMConfig
from tako_vm.constants import SANDBOX_GID
from tako_vm.session import (
    WORKSPACE_DIR_MODE,
    WORKSPACE_FILE_MODE,
    build_session_docker_command,
    ensure_workspace_writable,
    run_docker_exec,
)


def _cmd(workspace, workspace_mount):
    return build_session_docker_command(
        container_name="tako-session-test",
        workspace=Path(workspace),
        image="code-executor:latest",
        memory_limit="512m",
        cpu_limit=1.0,
        network_enabled=False,
        workspace_mount=workspace_mount,
        enable_cap_restrictions=True,
        config=TakoVMConfig(),
    )


class TestMountInjection:
    """workspace_mount / workspace are interpolated into a ``--mount`` CSV; a
    second ``source=`` re-points the bind mount (e.g. at ``/``). The builder
    must reject any value that could inject one."""

    def test_workspace_mount_csv_injection_rejected(self):
        # The exact host-root escape: a second source= in the target value.
        with pytest.raises(ValueError):
            _cmd("/tmp/tako-vm-workspace/sessions/a", "/workspace,source=/")

    def test_workspace_mount_equals_rejected(self):
        with pytest.raises(ValueError):
            _cmd("/tmp/tako-vm-workspace/sessions/a", "/workspace,ro=true")

    def test_workspace_mount_must_be_absolute(self):
        with pytest.raises(ValueError):
            _cmd("/tmp/tako-vm-workspace/sessions/a", "relative/path")

    def test_comma_in_source_path_rejected(self):
        with pytest.raises(ValueError):
            _cmd("/tmp/tako-vm-workspace/sessions/a,source=/", "/workspace")

    def test_clean_values_accepted(self):
        cmd = _cmd("/tmp/tako-vm-workspace/sessions/a", "/workspace")
        assert "--mount=type=bind,source=/tmp/tako-vm-workspace/sessions/a,target=/workspace" in cmd


class TestEnsureWorkspaceWritable:
    """The PR's headline change. Verify the actual resulting modes rather than
    just that it runs (the shipped version had zero assertions)."""

    def test_dir_is_setgid_group_writable_not_world(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        ensure_workspace_writable(ws)
        mode = os.stat(ws).st_mode
        assert mode & stat.S_ISGID, "setgid bit must be set"
        assert stat.S_IMODE(mode) == WORKSPACE_DIR_MODE  # 0o2770
        assert not (mode & stat.S_IWOTH), "workspace must not be world-writable"

    def test_covers_nested_subdirs_and_files(self, tmp_path):
        # The docstring's "covers the whole tree" claim: a single leaf chmod did
        # NOT; the recursive walk must reach host-created subdirs/files.
        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        f = ws / "src" / "main.py"
        f.write_text("x")
        os.chmod(ws / "src", 0o755)
        os.chmod(f, 0o644)
        ensure_workspace_writable(ws)
        assert stat.S_IMODE(os.stat(ws / "src").st_mode) == WORKSPACE_DIR_MODE
        assert stat.S_IMODE(os.stat(f).st_mode) == WORKSPACE_FILE_MODE

    def test_group_is_sandbox_gid_when_permitted(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        ensure_workspace_writable(ws)
        if os.getgid() == SANDBOX_GID or os.getuid() == 0 or SANDBOX_GID in os.getgroups():
            assert os.stat(ws).st_gid == SANDBOX_GID
        else:
            pytest.skip("runner cannot chgrp to SANDBOX_GID")

    def test_symlink_target_not_followed(self, tmp_path):
        # A symlink planted in the workspace must not redirect chmod onto its
        # target (the TOCTOU/symlink-follow hazard).
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        os.chmod(outside, 0o600)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "link").symlink_to(outside)
        ensure_workspace_writable(ws)
        assert stat.S_IMODE(os.stat(outside).st_mode) == 0o600, "symlink target must be untouched"

    def test_workspace_that_is_symlink_rejected(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        with pytest.raises(ValueError):
            ensure_workspace_writable(link)


class TestRunDockerExecHardening:
    def _capture(self, monkeypatch, stdout="", returncode=0):
        seen = {}

        class _Result:
            def __init__(self):
                self.stdout = stdout
                self.stderr = ""
                self.returncode = returncode

        def fake_run(cmd, *args, **kwargs):
            seen["cmd"] = cmd
            return _Result()

        monkeypatch.setattr("tako_vm.session.subprocess.run", fake_run)
        return seen

    def test_output_is_capped(self, monkeypatch):
        # A flood must not reach the caller uncapped (it would grow the host
        # process's read buffer, not the container cgroup).
        self._capture(monkeypatch, stdout="A" * 10_000)
        res = run_docker_exec("c1", "cmd", "/workspace", timeout=5, max_output_bytes=100)
        assert len(res.stdout.encode()) <= 200
        assert "TRUNCATED" in res.stdout
