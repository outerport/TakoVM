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
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tako_vm.config import TakoVMConfig, get_config
from tako_vm.constants import DEFAULT_IMAGE
from tako_vm.execution.docker import generate_container_name, kill_container
from tako_vm.execution.worker import RuntimeUnavailableError, resolve_runtime
from tako_vm.security import validate_docker_image
from tako_vm.session import (
    DEFAULT_SESSION_TIMEOUT,
    DEFAULT_WORKSPACE_MOUNT,
    SESSION_LABEL,
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

# Shared roots that must never be bind-mounted directly: they are the parents of
# per-session workspaces, so mounting one exposes every session's files. A caller
# must always land on a leaf strictly under one of these.
_SHARED_ROOTS = {Path(p).resolve() for p in WORKSPACE_PREFIX_ALLOWLIST} | {
    AGENT_SESSION_WORKSPACE_ROOT.resolve(),
    ANONYMOUS_WORKSPACE_ROOT.resolve(),
}


class SessionLimitExceededError(RuntimeError):
    """Raised when the concurrent-session cap is reached."""


def _is_evictable_workspace(path: Path) -> bool:
    """Only ever rmtree a resolved path strictly under an allowlisted prefix and
    not a shared root — a guard so a bug elsewhere can't delete an arbitrary dir."""
    resolved = path.resolve()
    if resolved in _SHARED_ROOTS:
        return False
    return any(
        resolved != Path(p).resolve() and _is_relative_to(resolved, Path(p).resolve())
        for p in WORKSPACE_PREFIX_ALLOWLIST
    )


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def _evict_workspace(path: Path) -> None:
    """rmtree a session workspace, guarded to the allowlist."""
    if not _is_evictable_workspace(path):
        logger.warning("Refusing to evict non-allowlisted workspace %s", path)
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as e:
        logger.warning("Failed to evict workspace %s: %s", path, e)


def sweep_orphaned_session_containers() -> int:
    """Kill session containers left over from a previous (non-graceful) run.

    Session containers carry ``--label=SESSION_LABEL``. At startup the in-memory
    registry is empty, so every labelled container is an orphan the reaper/delete
    path never got to. Returns the number killed."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label={SESSION_LABEL}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Could not list orphaned session containers: %s", e)
        return 0
    ids = [c for c in result.stdout.split() if c]
    for cid in ids:
        kill_container(cid)
    if ids:
        logger.info("Reaped %d orphaned session container(s) at startup", len(ids))
    return len(ids)


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
            raise ValueError(f"agent-session id has disallowed characters: {session_id!r}")
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
    created_at: float
    last_used: float
    # Throwaway (anonymous-scope) workspaces are rmtree'd on cleanup; named
    # scopes hold cross-turn data and are left for the owning consumer.
    ephemeral: bool = False
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
        enable_cap_restrictions: bool,
        ephemeral: bool = False,
    ) -> _SessionRecord:
        # Canonicalize once and use the resolved path everywhere below (mount +
        # chmod), so a symlink swapped in after validation cannot redirect them.
        resolved = _validate_workspace(workspace)

        config = get_config()
        runtime = resolve_runtime(config)  # may raise RuntimeUnavailableError (strict mode)
        session_id = uuid.uuid4().hex
        container_name = generate_container_name("tako-session")
        now = time.time()
        record = _SessionRecord(
            session_id=session_id,
            container_name=container_name,
            workspace=resolved,
            workspace_mount=DEFAULT_WORKSPACE_MOUNT,
            image=image,
            network_enabled=network_enabled,
            created_at=now,
            last_used=now,
            ephemeral=ephemeral,
        )

        # Reserve the slot under the cap before the slow docker run, so N
        # concurrent creates can't overshoot max_sessions.
        with self._lock:
            if len(self._sessions) >= config.session.max_sessions:
                raise SessionLimitExceededError(
                    f"session limit reached ({config.session.max_sessions})"
                )
            self._sessions[session_id] = record

        try:
            ensure_workspace_writable(resolved)
            cmd = build_session_docker_command(
                container_name=container_name,
                workspace=resolved,
                image=image,
                memory_limit=memory_limit,
                cpu_limit=cpu_limit,
                network_enabled=network_enabled,
                workspace_mount=DEFAULT_WORKSPACE_MOUNT,
                enable_cap_restrictions=enable_cap_restrictions,
                config=config,
                runtime=runtime,
            )
            logger.info(
                "Starting session %s (container=%s, workspace=%s)",
                session_id,
                container_name,
                resolved,
            )
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=config.session.start_timeout_seconds,
                check=False,
            )
        except (subprocess.TimeoutExpired, Exception):
            with self._lock:
                self._sessions.pop(session_id, None)
            kill_container(container_name)  # in case it started despite the error
            raise

        if result.returncode != 0:
            with self._lock:
                self._sessions.pop(session_id, None)
            raise RuntimeError(f"docker run failed: {result.stderr.strip()}")

        return record

    def get(self, session_id: str) -> Optional[_SessionRecord]:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is not None:
                record.last_used = time.time()
            return record

    def delete(self, session_id: str) -> bool:
        with self._lock:
            record = self._sessions.pop(session_id, None)
        if record is None:
            return False
        kill_container(record.container_name)
        if record.ephemeral:
            _evict_workspace(record.workspace)
        return True

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    def reap_expired(self) -> int:
        """Kill sessions past their absolute lifetime or idle timeout.

        Long-lived session containers have no ``--rm``-on-exit trigger (they run
        ``sleep infinity``), so without this an abandoned session — the client
        crashed, never called DELETE — would live until server shutdown."""
        config = get_config()
        now = time.time()
        max_age = config.session.max_age_seconds
        idle = config.session.idle_timeout_seconds
        with self._lock:
            expired = [
                r
                for r in self._sessions.values()
                if (now - r.created_at) > max_age or (now - r.last_used) > idle
            ]
            for r in expired:
                self._sessions.pop(r.session_id, None)
        for r in expired:
            kill_container(r.container_name)
            if r.ephemeral:
                _evict_workspace(r.workspace)
        if expired:
            logger.info("Reaped %d expired session(s)", len(expired))
        return len(expired)

    def shutdown(self) -> None:
        """Kill every live session. Called on server shutdown."""
        with self._lock:
            records = list(self._sessions.values())
            self._sessions.clear()
        for r in records:
            try:
                kill_container(r.container_name)
                if r.ephemeral:
                    _evict_workspace(r.workspace)
            except Exception:
                logger.exception("Error killing session container %s", r.container_name)


def _validate_workspace(workspace: Path) -> Path:
    """Canonicalize and authorize a workspace path; return the resolved path.

    The resolved path must be strictly *under* (never equal to) an allowlisted
    prefix and must not be a shared root, so a caller cannot pass a parent like
    the agent-session root and have every session's files bind-mounted into
    their container. Callers must use the returned resolved path for all later
    operations (avoids a validate-one-path/use-another TOCTOU)."""
    resolved = workspace.resolve()
    if "," in str(resolved) or "=" in str(resolved):
        raise ValueError(f"workspace path has disallowed characters: {resolved}")
    if resolved in _SHARED_ROOTS:
        raise ValueError(f"workspace must not be a shared root directory: {resolved}")
    for allowed in WORKSPACE_PREFIX_ALLOWLIST:
        allowed_path = Path(allowed).resolve()
        if resolved == allowed_path:
            continue
        if _is_relative_to(resolved, allowed_path):
            return resolved
    raise ValueError(
        f"workspace must be strictly under one of {WORKSPACE_PREFIX_ALLOWLIST}, got {resolved}"
    )


def _parse_memory_mb(value: str) -> int:
    v = value.strip().lower()
    if not v:
        raise ValueError("memory_limit is empty")
    try:
        if v.endswith("g"):
            return int(float(v[:-1]) * 1024)
        if v.endswith("m"):
            return int(float(v[:-1]))
        if v.endswith("k"):
            return max(1, int(float(v[:-1]) / 1024))
        if v.endswith("b"):
            return max(1, int(v[:-1]) // (1024 * 1024))
        return max(1, int(v) // (1024 * 1024))
    except ValueError:
        raise ValueError(f"invalid memory_limit: {value!r}")


def _clamp_memory(value: str, max_mb: int) -> str:
    """Parse and clamp a caller-requested memory limit to the server maximum."""
    mb = _parse_memory_mb(value)
    if mb < 6:  # docker's own minimum is 6m
        raise ValueError(f"memory_limit too small: {value!r}")
    return f"{min(mb, max_mb)}m"


def _session_isolation(req: "CreateSessionRequest", config: TakoVMConfig) -> tuple[str, bool, bool]:
    """Resolve the (image, network_enabled, enable_cap_restrictions) a session
    actually launches with. Unless the server opts into ``allow_privileged``, the
    caller's relaxation knobs are ignored: image must be the default or on the
    allowlist, network is off, and capabilities are dropped."""
    if not validate_docker_image(req.image):
        raise ValueError(f"invalid image: {req.image!r}")
    if config.session.allow_privileged:
        return req.image, req.network_enabled, req.enable_cap_restrictions
    allowed = {DEFAULT_IMAGE, *config.session.image_allowlist}
    if req.image not in allowed:
        raise ValueError(f"image not allowed: {req.image!r}")
    return req.image, False, True


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
    # The following knobs relax isolation and are honoured ONLY when the server
    # is configured with session.allow_privileged=True. Otherwise the server
    # forces the hardened profile (image allowlist, network off, caps dropped)
    # and ignores caller-supplied values here. workspace_mount is intentionally
    # not a request field: the mount target is fixed server-side so a value like
    # "/workspace,source=/" cannot inject a second bind source.
    image: str = Field(default=DEFAULT_IMAGE)
    memory_limit: str = Field(default="512m")
    cpu_limit: float = Field(default=1.0, gt=0)
    network_enabled: bool = Field(default=False)
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
        config = get_config()
        if (req.scope is None) == (req.workspace is None):
            raise HTTPException(
                status_code=400,
                detail="exactly one of 'scope' or 'workspace' must be set",
            )
        # Path-based 'workspace' lets the caller name any host dir under the
        # allowlist; it's the legacy/dev escape hatch, so gate it behind
        # allow_privileged. The default, hardened path is scope-only.
        if req.workspace is not None and not config.session.allow_privileged:
            raise HTTPException(
                status_code=400,
                detail="path-based 'workspace' requires session.allow_privileged; use 'scope'",
            )
        try:
            if req.scope is not None:
                workspace = resolve_scope_workspace(req.scope)
                ephemeral = req.scope == "anonymous"
            else:
                workspace = Path(req.workspace)  # type: ignore[arg-type]
                ephemeral = False
            image, network_enabled, enable_cap = _session_isolation(req, config)
            memory_limit = _clamp_memory(req.memory_limit, config.session.max_memory_mb)
            cpu_limit = min(req.cpu_limit, config.session.max_cpu_limit)
            record = manager.create(
                workspace=workspace,
                image=image,
                memory_limit=memory_limit,
                cpu_limit=cpu_limit,
                network_enabled=network_enabled,
                enable_cap_restrictions=enable_cap,
                ephemeral=ephemeral,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except SessionLimitExceededError as e:
            raise HTTPException(status_code=429, detail=str(e))
        except RuntimeUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e))
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
