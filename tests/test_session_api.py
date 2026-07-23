"""Tests for the unauthenticated HTTP session plane.

The tako-vm server has no auth, so ``_validate_workspace`` / the scope charset
guard / the isolation-policy enforcement are the only things standing between a
network client and an arbitrary host bind-mount or a privileged container. These
lock that behaviour; ``SessionManager.create`` is mocked so no docker runs.
"""

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tako_vm.config import get_config, reset_config
from tako_vm.server.sessions import (
    CreateSessionRequest,
    SessionLimitExceededError,
    SessionManager,
    _clamp_memory,
    _session_isolation,
    _SessionRecord,
    _validate_workspace,
    create_sessions_router,
    resolve_scope_workspace,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _record(**over):
    base = dict(
        session_id="sid",
        container_name="tako-session-x",
        workspace=Path("/tmp/tako-vm-workspace/sessions/a"),
        workspace_mount="/workspace",
        image="code-executor:latest",
        network_enabled=False,
        created_at=time.time(),
        last_used=time.time(),
    )
    base.update(over)
    return _SessionRecord(**base)


def _client(manager):
    app = FastAPI()
    app.include_router(create_sessions_router(manager))
    return TestClient(app)


class TestWorkspaceValidator:
    def test_shared_root_rejected(self):
        # The cross-session-theft escape: passing the parent of all workspaces.
        with pytest.raises(ValueError):
            _validate_workspace(Path("/tmp/tako-vm-workspace/sessions"))

    def test_allowlist_root_rejected(self):
        with pytest.raises(ValueError):
            _validate_workspace(Path("/tmp/tako-vm-workspace"))

    def test_outside_allowlist_rejected(self):
        with pytest.raises(ValueError):
            _validate_workspace(Path("/etc"))

    def test_leaf_accepted_and_resolved(self):
        out = _validate_workspace(Path("/tmp/tako-vm-workspace/sessions/abc"))
        assert out == Path("/tmp/tako-vm-workspace/sessions/abc")


class TestScopeResolver:
    def test_traversal_chars_rejected(self):
        with pytest.raises(ValueError):
            resolve_scope_workspace("agent-session:../../etc")

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError):
            resolve_scope_workspace("agent-session:")

    def test_unknown_scope_rejected(self):
        with pytest.raises(ValueError):
            resolve_scope_workspace("root:0")

    def test_anonymous_is_fresh_each_call(self):
        a = resolve_scope_workspace("anonymous")
        b = resolve_scope_workspace("anonymous")
        assert a != b


class TestIsolationPolicy:
    def test_non_privileged_non_allowlisted_image_rejected(self):
        req = CreateSessionRequest(
            scope="anonymous",
            image="alpine:latest",
            network_enabled=True,
            enable_cap_restrictions=False,
        )
        with pytest.raises(ValueError):
            _session_isolation(req, get_config())

    def test_non_privileged_default_image_forced_hardened(self):
        req = CreateSessionRequest(
            scope="anonymous", network_enabled=True, enable_cap_restrictions=False
        )
        image, network, caps = _session_isolation(req, get_config())
        assert network is False and caps is True
        assert image == "code-executor:latest"

    def test_clamp_memory(self):
        assert _clamp_memory("64g", 4096) == "4096m"
        assert _clamp_memory("256m", 4096) == "256m"
        with pytest.raises(ValueError):
            _clamp_memory("not-a-size", 4096)


class TestSessionRoutes:
    def test_both_scope_and_workspace_rejected(self):
        client = _client(SessionManager())
        r = client.post("/sessions", json={"scope": "anonymous", "workspace": "/x"})
        assert r.status_code == 400

    def test_neither_scope_nor_workspace_rejected(self):
        client = _client(SessionManager())
        r = client.post("/sessions", json={})
        assert r.status_code == 400

    def test_path_workspace_requires_privileged(self):
        client = _client(SessionManager())
        r = client.post("/sessions", json={"workspace": "/tmp/tako-vm-workspace/sessions/a"})
        assert r.status_code == 400
        assert "allow_privileged" in r.json()["detail"]

    def test_scope_traversal_rejected(self):
        client = _client(SessionManager())
        r = client.post("/sessions", json={"scope": "agent-session:../../etc"})
        assert r.status_code == 400

    def test_non_allowlisted_image_rejected_before_create(self, monkeypatch):
        manager = SessionManager()
        monkeypatch.setattr(manager, "create", lambda **k: _record())
        client = _client(manager)
        r = client.post("/sessions", json={"scope": "anonymous", "image": "alpine:latest"})
        assert r.status_code == 400

    def test_create_forwards_hardened_values(self, monkeypatch):
        manager = SessionManager()
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return _record()

        monkeypatch.setattr(manager, "create", fake_create)
        client = _client(manager)
        r = client.post(
            "/sessions",
            json={"scope": "anonymous", "network_enabled": True, "enable_cap_restrictions": False},
        )
        assert r.status_code == 201
        assert r.json()["workspace_mount"] == "/workspace"
        assert captured["network_enabled"] is False
        assert captured["enable_cap_restrictions"] is True
        assert captured["ephemeral"] is True

    def test_session_limit_returns_429(self, monkeypatch):
        manager = SessionManager()

        def full(**kwargs):
            raise SessionLimitExceededError("full")

        monkeypatch.setattr(manager, "create", full)
        client = _client(manager)
        r = client.post("/sessions", json={"scope": "anonymous"})
        assert r.status_code == 429

    def test_exec_unknown_session_404(self):
        client = _client(SessionManager())
        r = client.post("/sessions/nope/exec", json={"command": "echo hi"})
        assert r.status_code == 404

    def test_get_unknown_session_404(self):
        client = _client(SessionManager())
        assert client.get("/sessions/nope").status_code == 404

    def test_delete_unknown_session_404(self):
        client = _client(SessionManager())
        assert client.delete("/sessions/nope").status_code == 404
