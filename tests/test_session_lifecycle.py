"""Lifecycle tests for SessionManager: the concurrency cap, the reaper, ephemeral
workspace eviction, and start-timeout cleanup. docker/fs are stubbed."""

import subprocess
import time
from pathlib import Path

import pytest

import tako_vm.server.sessions as sessions_mod
from tako_vm.config import SessionConfig, TakoVMConfig
from tako_vm.server.sessions import SessionLimitExceededError, SessionManager


@pytest.fixture
def patched(monkeypatch):
    """Stub docker + fs so create()/reap()/delete() run without a daemon."""
    cfg = TakoVMConfig(
        session=SessionConfig(max_sessions=2, max_age_seconds=60, idle_timeout_seconds=60)
    )
    monkeypatch.setattr(sessions_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(sessions_mod, "resolve_runtime", lambda c: "runc")
    monkeypatch.setattr(sessions_mod, "ensure_workspace_writable", lambda p: None)
    monkeypatch.setattr(sessions_mod, "_validate_workspace", lambda p: Path(p))

    killed = []
    evicted = []
    monkeypatch.setattr(sessions_mod, "kill_container", lambda name: killed.append(name))
    monkeypatch.setattr(sessions_mod, "_evict_workspace", lambda p: evicted.append(p))

    class _OK:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(sessions_mod.subprocess, "run", lambda *a, **k: _OK())
    return cfg, killed, evicted


def _create(manager, ident, ephemeral=False):
    return manager.create(
        workspace=Path(f"/tmp/tako-vm-workspace/sessions/{ident}"),
        image="code-executor:latest",
        memory_limit="256m",
        cpu_limit=1.0,
        network_enabled=False,
        enable_cap_restrictions=True,
        ephemeral=ephemeral,
    )


class TestConcurrencyCap:
    def test_cap_rejects_beyond_max(self, patched):
        manager = SessionManager()
        _create(manager, "a")
        _create(manager, "b")
        with pytest.raises(SessionLimitExceededError):
            _create(manager, "c")
        assert len(manager.list_ids()) == 2


class TestReaper:
    def test_reaps_by_age(self, patched):
        _cfg, killed, evicted = patched
        manager = SessionManager()
        rec = _create(manager, "old", ephemeral=True)
        rec.created_at = time.time() - 10_000  # older than max_age
        assert manager.reap_expired() == 1
        assert manager.list_ids() == []
        assert rec.container_name in killed
        assert rec.workspace in evicted

    def test_reaps_by_idle(self, patched):
        manager = SessionManager()
        rec = _create(manager, "idle")
        rec.last_used = time.time() - 10_000
        assert manager.reap_expired() == 1

    def test_active_session_survives(self, patched):
        manager = SessionManager()
        _create(manager, "fresh")
        assert manager.reap_expired() == 0
        assert len(manager.list_ids()) == 1


class TestEviction:
    def test_delete_evicts_ephemeral_only(self, patched):
        _cfg, _killed, evicted = patched
        manager = SessionManager()
        eph = _create(manager, "eph", ephemeral=True)
        named = _create(manager, "named", ephemeral=False)
        manager.delete(eph.session_id)
        manager.delete(named.session_id)
        assert eph.workspace in evicted
        assert named.workspace not in evicted


class TestCreateFailureCleanup:
    def test_docker_error_releases_reserved_slot(self, monkeypatch, patched):
        """A docker failure during create must release the reserved slot so it
        doesn't count against the concurrency cap or leave a phantom entry."""
        manager = SessionManager()

        def boom(*a, **k):
            raise subprocess.SubprocessError("docker run blew up")

        monkeypatch.setattr(sessions_mod.subprocess, "run", boom)
        with pytest.raises(subprocess.SubprocessError):
            _create(manager, "slow")
        assert manager.list_ids() == []
