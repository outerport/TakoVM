"""Tests that the session path applies the gVisor runtime.

A session is a persistent container an agent's exec/bash tool hangs off of;
it must run under gVisor like the stateless ``/execute`` path. Before the
shared ``resolve_runtime`` resolver, ``build_session_docker_command`` had no
``--runtime`` flag, so session containers silently ran under runc (the host
kernel) regardless of strict mode -- a gVisor isolation gap in the roaming
path. These tests lock the fix and cover the hardening the session feature
shipped with but had no tests for.
"""

from pathlib import Path

import pytest

import tako_vm.execution.worker as worker_module
from tako_vm.config import TakoVMConfig, reset_config
from tako_vm.execution.worker import (
    RuntimeUnavailableError,
    reset_gvisor_check,
    resolve_runtime,
)
from tako_vm.session import LocalTakoSession, build_session_docker_command


@pytest.fixture(autouse=True)
def reset_caches():
    reset_gvisor_check()
    reset_config()
    yield
    reset_gvisor_check()
    reset_config()


def _session_cmd(runtime):
    return build_session_docker_command(
        container_name="tako-session-test",
        workspace=Path("/tmp/tako-vm-workspace/sessions/test"),
        image="code-executor:latest",
        memory_limit="512m",
        cpu_limit=1.0,
        network_enabled=False,
        workspace_mount="/workspace",
        enable_cap_restrictions=True,
        runtime=runtime,
    )


class TestSharedResolverDrivesSessions:
    """The session path and CodeExecutor resolve the runtime through the same
    ``resolve_runtime`` function, so they can't drift on isolation again."""

    def test_strict_with_gvisor_resolves_runsc_and_command_uses_it(self, monkeypatch):
        # The regression guard: strict + gVisor must put --runtime=runsc on the
        # session container. The original bug was exactly its absence.
        monkeypatch.setattr(worker_module, "_gvisor_available", True)
        config = TakoVMConfig(container_runtime="runsc", security_mode="strict")
        runtime = resolve_runtime(config)
        assert runtime == "runsc"
        assert "--runtime=runsc" in _session_cmd(runtime)

    def test_strict_without_gvisor_fails_closed(self, monkeypatch):
        monkeypatch.setattr(worker_module, "_gvisor_available", False)
        config = TakoVMConfig(container_runtime="runsc", security_mode="strict")
        with pytest.raises(RuntimeUnavailableError):
            resolve_runtime(config)

    def test_runc_in_strict_mode_rejected(self, monkeypatch):
        monkeypatch.setattr(worker_module, "_gvisor_available", True)
        config = TakoVMConfig(container_runtime="runc", security_mode="strict")
        with pytest.raises(RuntimeUnavailableError):
            resolve_runtime(config)

    def test_permissive_without_gvisor_falls_back_to_runc(self, monkeypatch):
        monkeypatch.setattr(worker_module, "_gvisor_available", False)
        config = TakoVMConfig(container_runtime="runsc", security_mode="permissive")
        assert resolve_runtime(config) == "runc"


class TestSessionCommandRuntimeFlag:
    """``build_session_docker_command`` applies ``--runtime`` only for runsc,
    mirroring ``CodeExecutor._run_container`` (runc is docker's default and
    some daemons reject ``--runtime=runc``)."""

    def test_runsc_appends_runtime_flag(self):
        assert "--runtime=runsc" in _session_cmd("runsc")

    def test_runc_omits_runtime_flag(self):
        assert not any(arg.startswith("--runtime") for arg in _session_cmd("runc"))

    def test_none_omits_runtime_flag(self):
        assert not any(arg.startswith("--runtime") for arg in _session_cmd(None))

    def test_isolation_flags_present(self):
        cmd = _session_cmd("runsc")
        assert "--read-only" in cmd
        assert "--cap-drop=ALL" in cmd
        assert "--network=none" in cmd


class TestSessionStartAppliesRuntime:
    """End-to-end wiring: LocalTakoSession.start must resolve the runtime from
    config and put --runtime=runsc on the actual ``docker run`` command. The
    original bug was the missing connection, which the pure-function tests
    above wouldn't catch (they pass runtime manually). SessionManager.create
    uses the identical resolve-and-pass pattern."""

    def test_start_in_strict_mode_runs_under_gvisor(self, monkeypatch, tmp_path):
        monkeypatch.setattr(worker_module, "_gvisor_available", True)
        monkeypatch.setenv("TAKO_VM_SECURITY_MODE", "strict")
        monkeypatch.setenv("TAKO_VM_CONTAINER_RUNTIME", "runsc")
        reset_config()

        captured = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd

            class _Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return _Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        LocalTakoSession(workspace=tmp_path).start()
        assert "--runtime=runsc" in captured["cmd"]

    def test_start_strict_without_gvisor_fails_closed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(worker_module, "_gvisor_available", False)
        monkeypatch.setenv("TAKO_VM_SECURITY_MODE", "strict")
        monkeypatch.setenv("TAKO_VM_CONTAINER_RUNTIME", "runsc")
        reset_config()

        started = {"run": False}

        def fake_run(cmd, *args, **kwargs):
            started["run"] = True

            class _Result:
                returncode = 0

            return _Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        with pytest.raises(RuntimeUnavailableError):
            LocalTakoSession(workspace=tmp_path).start()
        assert started["run"] is False  # no container ever started
