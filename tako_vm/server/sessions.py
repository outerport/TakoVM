"""Server-side session management for long-lived sandbox containers.

Holds a dict of ``session_id → container_name`` in process memory and
exposes four endpoints:

- ``POST /sessions`` — start a container, return a session_id.
- ``POST /sessions/{session_id}/exec`` — run a shell command in that
  container and return stdout/stderr/exit_code.
- ``DELETE /sessions/{session_id}`` — kill and remove the container.
- ``GET /sessions/{session_id}`` — introspect a session (for debugging).

Workspaces are the server's responsibility. Clients supply either an
explicit ``workspace`` path (legacy / dev path) or an opaque ``scope``
string like ``agent-session:<uuid>`` and let the server derive the path
itself. The server creates the directory, sets permissions, and
bind-mounts it into the sandbox. The client never has to touch the
filesystem on its side; it just gets the resolved ``workspace`` back
in the response for any bookkeeping it wants to do.

Both shapes currently require that the chosen path lives under one of
the allowlisted prefixes that docker-compose mounts shared
(host ↔ tako-server container) — see ``WORKSPACE_PREFIX_ALLOWLIST``.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tako_vm.config import get_config
from tako_vm.constants import DEFAULT_IMAGE
from tako_vm.execution.docker import generate_container_name, kill_container
from tako_vm.execution.worker import resolve_runtime
from tako_vm.session import (
    DEFAULT_SESSION_TIMEOUT,
    DEFAULT_WORKSPACE_MOUNT,
    build_session_docker_command,
    ensure_workspace_writable,
    run_docker_exec,
)

logger = logging.getLogger(__name__)

WORKSPACE_PREFIX_ALLOWLIST = ["/tmp/tako-vm-workspace", "/tmp/tako-vm-sessions"]

# Scope → workspace path mapping. A scope is a short string the client
# hands the server instead of a host path; the server picks where on
# its own disk that scope's workspace lives. Format: "<type>:<id>".
# Recognised scopes today:
#  - ``agent-session:<uuid>`` — per-session workspace (one dir per
#    AgentSession), so two conversations against the same skill don't
#    trample each other's files.
#  - ``anonymous`` — throwaway workspace for unowned sessions.
AGENT_SESSION_WORKSPACE_ROOT = Path("/tmp/tako-vm-workspace/sessions")
ANONYMOUS_WORKSPACE_ROOT = Path("/tmp/tako-vm-sessions/anonymous")


def resolve_scope_workspace(scope: str) -> Path:
    """Turn a scope string into the host path the server will bind-mount.

    ``agent-session:<uuid>`` — per-session workspace. One dir per
    AgentSession; isolated from other sessions even when they share a
    skill.

    ``anonymous`` — throwaway workspace for unowned sessions. Server
    allocates a fresh dir per call.
    """
    if scope == "anonymous":
        return ANONYMOUS_WORKSPACE_ROOT / uuid.uuid4().hex
    if scope.startswith("agent-session:"):
        session_id = scope.split(":", 1)[1]
        if not session_id:
            raise ValueError("agent-session scope requires a non-empty id")
        # Very conservative charset — no path traversal via the id.
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        if safe != session_id:
            raise ValueError(
                f"agent-session id has disallowed characters: {session_id!r}"
            )
        return AGENT_SESSION_WORKSPACE_ROOT / session_id
    raise ValueError(f"Unknown scope: {scope!r}")


@dataclass
class _SessionRecord:
    session_id: str
    container_name: str
    workspace: Path
    workspace_mount: str
    image: str
    network_enabled: bool
    info_extra: Dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """In-memory registry of live sandbox sessions on this server."""

    def __init__(self) -> None:
        self._sessions: Dict[str, _SessionRecord] = {}
        self._lock = threading.Lock()

    def create(
        self,
        workspace: Path,
        image: str,
        memory_limit: str,
        cpu_limit: float,
        network_enabled: bool,
        workspace_mount: str,
        enable_cap_restrictions: bool,
    ) -> _SessionRecord:
        _validate_workspace(workspace)
        ensure_workspace_writable(workspace)

        session_id = uuid.uuid4().hex
        container_name = generate_container_name("tako-session")
        runtime = resolve_runtime(get_config())
        cmd = build_session_docker_command(
            container_name=container_name,
            workspace=workspace,
            image=image,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            network_enabled=network_enabled,
            workspace_mount=workspace_mount,
            enable_cap_restrictions=enable_cap_restrictions,
            runtime=runtime,
        )
        logger.info(
            "Starting session %s (container=%s, workspace=%s)",
            session_id,
            container_name,
            workspace,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker run failed: {result.stderr.strip()}"
            )

        record = _SessionRecord(
            session_id=session_id,
            container_name=container_name,
            workspace=workspace,
            workspace_mount=workspace_mount,
            image=image,
            network_enabled=network_enabled,
        )
        with self._lock:
            self._sessions[session_id] = record
        return record

    def get(self, session_id: str) -> Optional[_SessionRecord]:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            record = self._sessions.pop(session_id, None)
        if record is None:
            return False
        kill_container(record.container_name)
        return True

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    def shutdown(self) -> None:
        """Kill every live session. Called on server shutdown."""
        with self._lock:
            records = list(self._sessions.values())
            self._sessions.clear()
        for r in records:
            try:
                kill_container(r.container_name)
            except Exception:
                logger.exception(
                    "Error killing session container %s", r.container_name
                )


def _validate_workspace(workspace: Path) -> None:
    resolved = workspace.resolve()
    for allowed in WORKSPACE_PREFIX_ALLOWLIST:
        allowed_path = Path(allowed).resolve()
        try:
            resolved.relative_to(allowed_path)
            return
        except ValueError:
            continue
    raise ValueError(
        f"workspace must be under one of {WORKSPACE_PREFIX_ALLOWLIST}, got {resolved}"
    )


# ---------------------------------------------------------------------------
# FastAPI request / response schemas
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    scope: Optional[str] = Field(
        default=None,
        description=(
            "Server-resolved workspace identifier (e.g. "
            "``agent-session:<uuid>`` or ``anonymous``). When set, the "
            "server picks the workspace path itself and returns it in "
            "the response. Mutually exclusive with ``workspace``."
        ),
    )
    workspace: Optional[str] = Field(
        default=None,
        description=(
            "Explicit absolute host path to use as the workspace, under "
            "/tmp/tako-vm-workspace/ so the server can bind-mount it. "
            "Legacy / dev path; prefer ``scope`` for new callers."
        ),
    )
    image: str = Field(default=DEFAULT_IMAGE)
    memory_limit: str = Field(default="512m")
    cpu_limit: float = Field(default=1.0, gt=0)
    network_enabled: bool = Field(default=False)
    workspace_mount: str = Field(default=DEFAULT_WORKSPACE_MOUNT)
    enable_cap_restrictions: bool = Field(default=True)


class CreateSessionResponse(BaseModel):
    session_id: str
    container_name: str
    workspace: str
    workspace_mount: str


class ExecRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=100_000)
    cwd: Optional[str] = Field(default=None)
    timeout: int = Field(default=DEFAULT_SESSION_TIMEOUT, gt=0, le=600)
    env: Optional[Dict[str, str]] = Field(default=None)


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool


class SessionInfoResponse(BaseModel):
    session_id: str
    container_name: str
    workspace: str
    workspace_mount: str
    image: str
    network_enabled: bool


def create_sessions_router(manager: SessionManager) -> APIRouter:
    """Build the FastAPI router. Called from ``app.py`` with the singleton manager."""
    router = APIRouter()

    @router.post(
        "/sessions",
        response_model=CreateSessionResponse,
        status_code=201,
    )
    def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
        if (req.scope is None) == (req.workspace is None):
            raise HTTPException(
                status_code=400,
                detail="exactly one of 'scope' or 'workspace' must be set",
            )
        try:
            if req.scope is not None:
                workspace = resolve_scope_workspace(req.scope)
            else:
                # req.workspace is set (mypy: narrowed above)
                workspace = Path(req.workspace)  # type: ignore[arg-type]
            record = manager.create(
                workspace=workspace,
                image=req.image,
                memory_limit=req.memory_limit,
                cpu_limit=req.cpu_limit,
                network_enabled=req.network_enabled,
                workspace_mount=req.workspace_mount,
                enable_cap_restrictions=req.enable_cap_restrictions,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return CreateSessionResponse(
            session_id=record.session_id,
            container_name=record.container_name,
            workspace=str(record.workspace),
            workspace_mount=record.workspace_mount,
        )

    @router.post("/sessions/{session_id}/exec", response_model=ExecResponse)
    def exec_in_session(session_id: str, req: ExecRequest) -> ExecResponse:
        record = manager.get(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        cwd = req.cwd if req.cwd is not None else record.workspace_mount
        result = run_docker_exec(
            container_name=record.container_name,
            command=req.command,
            cwd=cwd,
            timeout=req.timeout,
            env=req.env,
        )
        return ExecResponse(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
        )

    @router.get("/sessions/{session_id}", response_model=SessionInfoResponse)
    def get_session(session_id: str) -> SessionInfoResponse:
        record = manager.get(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        return SessionInfoResponse(
            session_id=record.session_id,
            container_name=record.container_name,
            workspace=str(record.workspace),
            workspace_mount=record.workspace_mount,
            image=record.image,
            network_enabled=record.network_enabled,
        )

    @router.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> None:
        ok = manager.delete(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="session not found")
        return None

    return router
