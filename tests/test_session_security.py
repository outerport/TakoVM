"""Security-contract tests for the long-lived session API.

Unit-level — no Docker daemon required. They pin the invariants the
session sandbox depends on:

- runtime, capability dropping, and seccomp come from *server config*; a
  client can never weaken them via the request;
- the requested image is allowlisted and resources are bounded;
- the workspace is confined to the same-path-mounted allowlist prefix
  (and the *resolved* path is what gets mounted);
- a single ``exec`` is time-bounded *inside the container* and its output
  is size-bounded;
- the concurrent-session cap and idle/age TTL hold.

These mirror, for sessions, what ``op-parseport``'s
``test_tako_security.py`` does for the one-shot ``/execute`` path.
"""

from pathlib import Path

import pytest

from tako_vm import session as session_mod
from tako_vm.config import TakoVMConfig
from tako_vm.constants import DEFAULT_IMAGE
from tako_vm.server import sessions as sessions_mod
from tako_vm.server.sessions import (
    CreateSessionRequest,
    SessionLimitError,
    SessionManager,
    _SessionRecord,
    _validate_image,
    _validate_resources,
    _validate_workspace,
    resolve_scope_workspace,
)
from tako_vm.session import (
    MAX_OUTPUT_CHARS,
    build_session_docker_command,
    run_docker_exec,
)


def _permissive_config(**overrides):
    """A config that won't trigger a real gVisor probe."""
    base = dict(
        security_mode="permissive",
        container_runtime="runc",
        enable_cap_restrictions=True,
        enable_seccomp=False,
    )
    base.update(overrides)
    return TakoVMConfig(**base)


# ---------------------------------------------------------------------------
# build_session_docker_command — isolation flags
# ---------------------------------------------------------------------------


def test_runsc_flag_present_only_for_gvisor():
    runsc = build_session_docker_command(
        "c",
        Path("/ws"),
        DEFAULT_IMAGE,
        "512m",
        1.0,
        False,
        "/workspace",
        True,
        runtime="runsc",
    )
    runc = build_session_docker_command(
        "c",
        Path("/ws"),
        DEFAULT_IMAGE,
        "512m",
        1.0,
        False,
        "/workspace",
        True,
        runtime="runc",
    )
    assert "--runtime=runsc" in runsc
    # runc is the daemon default; we must NOT pass --runtime=runc (some
    # daemons reject it) and must not silently leave gVisor off when asked.
    assert "--runtime=runsc" not in runc
    assert "--runtime=runc" not in runc


def test_caps_dropped_and_no_new_privileges():
    cmd = build_session_docker_command(
        "c", Path("/ws"), DEFAULT_IMAGE, "512m", 1.0, False, "/workspace", True
    )
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt=no-new-privileges" in cmd
    assert "--read-only" in cmd
    assert "--user=1000:1000" in cmd


def test_network_none_by_default_bridge_when_enabled():
    off = build_session_docker_command(
        "c", Path("/ws"), DEFAULT_IMAGE, "512m", 1.0, False, "/workspace", True
    )
    on = build_session_docker_command(
        "c", Path("/ws"), DEFAULT_IMAGE, "512m", 1.0, True, "/workspace", True
    )
    assert "--network=none" in off
    assert "--network=bridge" in on


def test_seccomp_applied_on_native_linux(monkeypatch, tmp_path):
    profile = tmp_path / "seccomp.json"
    profile.write_text("{}")
    monkeypatch.setattr(session_mod, "is_native_linux", lambda: True)
    cmd = build_session_docker_command(
        "c",
        Path("/ws"),
        DEFAULT_IMAGE,
        "512m",
        1.0,
        False,
        "/workspace",
        True,
        enable_seccomp=True,
        seccomp_profile_path=profile,
    )
    assert f"--security-opt=seccomp={profile}" in cmd


def test_seccomp_skipped_off_native_linux(monkeypatch, tmp_path):
    profile = tmp_path / "seccomp.json"
    profile.write_text("{}")
    monkeypatch.setattr(session_mod, "is_native_linux", lambda: False)
    cmd = build_session_docker_command(
        "c",
        Path("/ws"),
        DEFAULT_IMAGE,
        "512m",
        1.0,
        False,
        "/workspace",
        True,
        enable_seccomp=True,
        seccomp_profile_path=profile,
    )
    assert not any(part.startswith("--security-opt=seccomp=") for part in cmd)


# ---------------------------------------------------------------------------
# run_docker_exec — time and output bounds
# ---------------------------------------------------------------------------


def test_exec_wraps_command_in_container_timeout(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")

        class R:
            stdout = "ok"
            stderr = ""
            returncode = 0

        return R()

    monkeypatch.setattr(session_mod.subprocess, "run", fake_run)
    run_docker_exec("c", "echo hi", "/workspace", timeout=30)
    # In-container coreutils timeout bounds the process tree, not just the
    # host-side docker-exec client.
    assert "timeout" in captured["cmd"]
    assert "30s" in captured["cmd"]
    # Host-side backstop fires strictly after the in-container limit.
    assert captured["timeout"] > 30


def test_exec_timeout_exit_code_marks_timed_out(monkeypatch):
    def fake_run(cmd, **kwargs):
        class R:
            stdout = ""
            stderr = ""
            returncode = 124  # GNU timeout's "killed" code

        return R()

    monkeypatch.setattr(session_mod.subprocess, "run", fake_run)
    result = run_docker_exec("c", "sleep 999", "/workspace", timeout=1)
    assert result.timed_out is True


def test_exec_output_is_truncated(monkeypatch):
    def fake_run(cmd, **kwargs):
        class R:
            stdout = "a" * (MAX_OUTPUT_CHARS + 5000)
            stderr = ""
            returncode = 0

        return R()

    monkeypatch.setattr(session_mod.subprocess, "run", fake_run)
    result = run_docker_exec("c", "yes", "/workspace", timeout=5)
    assert "[truncated" in result.stdout
    assert len(result.stdout) < MAX_OUTPUT_CHARS + 200


# ---------------------------------------------------------------------------
# Request model + validators — caller can't weaken or overspend
# ---------------------------------------------------------------------------


def test_cpu_limit_ceiling_enforced():
    with pytest.raises(Exception):
        CreateSessionRequest(scope="anonymous", cpu_limit=999.0)


@pytest.mark.parametrize("bad", ["512", "512k", "abc", "0m", "-1g"])
def test_memory_limit_format_rejected(bad):
    with pytest.raises(Exception):
        CreateSessionRequest(scope="anonymous", memory_limit=bad)


def test_memory_limit_above_ceiling_rejected():
    with pytest.raises(Exception):
        CreateSessionRequest(scope="anonymous", memory_limit="64g")


def test_client_cannot_set_capability_field():
    # enable_cap_restrictions is server-policy; an attempt to send it is
    # silently dropped (Pydantic extra='ignore'), never honored.
    req = CreateSessionRequest(scope="anonymous", enable_cap_restrictions=False)
    assert not hasattr(req, "enable_cap_restrictions")


def test_validate_image_rejects_non_allowlisted():
    with pytest.raises(ValueError):
        _validate_image("evil/image:latest", sessions_mod.DEFAULT_SESSION_IMAGE_ALLOWLIST)


def test_validate_image_rejects_injection():
    with pytest.raises(ValueError):
        _validate_image("img;rm -rf /", sessions_mod.DEFAULT_SESSION_IMAGE_ALLOWLIST)


def test_validate_resources_bounds():
    _validate_resources(1.0, "512m")  # ok
    with pytest.raises(ValueError):
        _validate_resources(999.0, "512m")
    with pytest.raises(ValueError):
        _validate_resources(1.0, "64g")


# ---------------------------------------------------------------------------
# Workspace confinement
# ---------------------------------------------------------------------------


def test_validate_workspace_confines_and_resolves():
    resolved = _validate_workspace(Path("/tmp/tako-vm-workspace/abc"))
    # The resolved path (what we actually mount) stays under the prefix.
    assert str(resolved).endswith("tako-vm-workspace/abc")


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "/tmp/tako-vm-workspace/../../etc",
        "/tmp/other",
    ],
)
def test_validate_workspace_rejects_outside(bad):
    with pytest.raises(ValueError):
        _validate_workspace(Path(bad))


def test_resolve_scope_workspace_under_allowlist():
    anon = resolve_scope_workspace("anonymous")
    agent = resolve_scope_workspace("agent-session:abc-123")
    assert str(anon).startswith("/tmp/tako-vm-workspace/")
    assert str(agent).startswith("/tmp/tako-vm-workspace/")


@pytest.mark.parametrize("bad", ["agent-session:../escape", "agent-session:a/b", "bogus"])
def test_resolve_scope_workspace_rejects_bad(bad):
    with pytest.raises(ValueError):
        resolve_scope_workspace(bad)


# ---------------------------------------------------------------------------
# SessionManager lifecycle — count cap, TTL, server-policy isolation
# ---------------------------------------------------------------------------


def _dummy_record(i):
    return _SessionRecord(
        session_id=f"s{i}",
        container_name=f"c{i}",
        workspace=Path("/tmp/tako-vm-workspace/x"),
        workspace_mount="/workspace",
        image=DEFAULT_IMAGE,
        network_enabled=False,
    )


def test_session_count_cap_enforced():
    mgr = SessionManager(config=_permissive_config(), max_sessions=1)
    mgr._sessions["s0"] = _dummy_record(0)
    with pytest.raises(SessionLimitError):
        mgr.create(
            workspace=Path("/tmp/tako-vm-workspace/y"),
            image=DEFAULT_IMAGE,
            memory_limit="512m",
            cpu_limit=1.0,
            network_enabled=False,
            workspace_mount="/workspace",
        )


def test_expired_session_is_reaped_on_get(monkeypatch):
    killed = []
    monkeypatch.setattr(sessions_mod, "kill_container", lambda name: killed.append(name))
    mgr = SessionManager(config=_permissive_config(), idle_seconds=0)
    rec = _dummy_record(0)
    rec.last_used -= 10  # older than idle_seconds=0
    mgr._sessions[rec.session_id] = rec
    assert mgr.get(rec.session_id) is None
    assert killed == [rec.container_name]


def test_network_clamped_when_server_denies(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr(sessions_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(sessions_mod, "resolve_runtime", lambda cfg: "runc")
    monkeypatch.setattr(sessions_mod, "ensure_workspace_writable", lambda w: None)

    # allow_network defaults to False (operator opt-in).
    mgr = SessionManager(config=_permissive_config())
    rec = mgr.create(
        workspace=Path("/tmp/tako-vm-workspace/z"),
        image=DEFAULT_IMAGE,
        memory_limit="512m",
        cpu_limit=1.0,
        network_enabled=True,  # caller asks for network...
        workspace_mount="/workspace",
    )
    # ...but operator policy denies it, so it's forced off.
    assert "--network=none" in captured["cmd"]
    assert rec.network_enabled is False


def test_isolation_flags_come_from_config(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr(sessions_mod.subprocess, "run", fake_run)
    # Pretend the host provides gVisor; the session must pick it up.
    monkeypatch.setattr(sessions_mod, "resolve_runtime", lambda cfg: "runsc")
    monkeypatch.setattr(sessions_mod, "ensure_workspace_writable", lambda w: None)

    mgr = SessionManager(config=_permissive_config(enable_cap_restrictions=True))
    mgr.create(
        workspace=Path("/tmp/tako-vm-workspace/z2"),
        image=DEFAULT_IMAGE,
        memory_limit="512m",
        cpu_limit=1.0,
        network_enabled=False,
        workspace_mount="/workspace",
    )
    assert "--runtime=runsc" in captured["cmd"]
    assert "--cap-drop=ALL" in captured["cmd"]
