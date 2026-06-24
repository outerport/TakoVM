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

Both shapes require that the chosen path lives under the allowlisted
prefix that docker-compose bind-mounts at the *same absolute path* on
both the host and the tako-server container (so the host Docker daemon
can resolve the sibling-container mount) — see
``WORKSPACE_PREFIX_ALLOWLIST``.

Server-enforced isolation policy
--------------------------------
The client does *not* get to weaken the sandbox. Capability dropping,
the container runtime (gVisor vs runc), and seccomp are taken from the
server's :class:`~tako_vm.config.TakoVMConfig`, never from the request.
The requested image must be on the server's allowlist, network access is
only granted when the server config allows it, and resource limits are
bounded. There is a cap on concurrent sessions and an idle/age TTL so an
unauthenticated caller on the internal network can't exhaust the host.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from tako_vm.config import TakoVMConfig, get_config
from tako_vm.constants import DEFAULT_IMAGE
from tako_vm.execution.docker import generate_container_name, kill_container
from tako_vm.execution.worker import RuntimeUnavailableError, resolve_runtime
from tako_vm.security import sanitize_error, validate_docker_image
from tako_vm.session import (
    DEFAULT_SESSION_TIMEOUT,
    DEFAULT_WORKSPACE_MOUNT,
    build_session_docker_command,
    ensure_workspace_writable,
    run_docker_exec,
    session_ulimits,
)

logger = logging.getLogger(__name__)

# Single same-path-mounted prefix. Everything a session can bind-mount
# lives under here so the docker-compose ``/tmp/tako-vm-workspace`` mount
# (identical host and container path) always resolves for the sibling
# sandbox container. A second prefix that *isn't* same-path-mounted would
# silently produce empty/host-created mounts.
WORKSPACE_PREFIX_ALLOWLIST = ["/tmp/tako-vm-workspace"]

# Images a session is allowed to run. The client picks from this set only;
# it can't name an arbitrary image (which could ship a different user,
# entrypoint, or tooling than the hardened executor). Operators extend it
# by constructing SessionManager with a wider ``image_allowlist``.
DEFAULT_SESSION_IMAGE_ALLOWLIST = frozenset({DEFAULT_IMAGE})

# Resource ceilings for a single session (defense-in-depth alongside the
# request-model validators).
MAX_SESSION_CPU = 8.0
MAX_SESSION_MEMORY_MB = 32 * 1024  # 32g, matches config.memory_limit ceiling
MIN_SESSION_MEMORY_MB = 64

# Lifecycle caps so unowned sessions can't pile up forever.
DEFAULT_MAX_SESSIONS = 50
DEFAULT_SESSION_MAX_AGE_SECONDS = 60 * 60  # hard ceiling on total lifetime
DEFAULT_SESSION_IDLE_SECONDS = 30 * 60  # reap if untouched this long

# Scope → workspace path mapping. A scope is a short string the client
# hands the server instead of a host path; the server picks where on
# its own disk that scope's workspace lives. Format: "<type>:<id>".
# Recognised scopes today:
#  - ``agent-session:<uuid>`` — per-session workspace (one dir per
#    AgentSession), so two conversations against the same skill don't
#    trample each other's files.
#  - ``anonymous`` — throwaway workspace for unowned sessions.
# Both live under the single same-path-mounted allowlist prefix.
AGENT_SESSION_WORKSPACE_ROOT = Path("/tmp/tako-vm-workspace/sessions")
ANONYMOUS_WORKSPACE_ROOT = Path("/tmp/tako-vm-workspace/anonymous")


class SessionLimitError(Exception):
    """Raised when the concurrent-session cap is reached."""


def _memory_limit_to_mb(value: str) -> int:
    """Parse a ``512m`` / ``2g`` docker memory string to whole MB.

    Raises ``ValueError`` on anything that isn't ``<int>{m,g}`` so a free
    string can't reach ``docker run --memory=`` unvalidated.
    """
    v = value.strip().lower()
    if len(v) < 2 or v[-1] not in ("m", "g"):
        raise ValueError("memory_limit must be an integer followed by 'm' or 'g'")
    try:
        magnitude = int(v[:-1])
    except ValueError as exc:
        raise ValueError("memory_limit must be an integer followed by 'm' or 'g'") from exc
    if magnitude <= 0:
        raise ValueError("memory_limit must be positive")
    return magnitude * 1024 if v[-1] == "g" else magnitude


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
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    info_extra: Dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """In-memory registry of live sandbox sessions on this server.

    Isolation policy (runtime, capabilities, seccomp) is taken from the
    server ``config`` — never from the caller — so a session can't be
    requested with weaker isolation than the operator configured. Caller
    inputs (image, network, resources, workspace) are validated and
    clamped. Lifecycle is bounded by ``max_sessions`` and an idle/age TTL
    that is swept lazily on create/get (no background thread to fail).
    """

    def __init__(
        self,
        config: Optional[TakoVMConfig] = None,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_age_seconds: float = DEFAULT_SESSION_MAX_AGE_SECONDS,
        idle_seconds: float = DEFAULT_SESSION_IDLE_SECONDS,
        image_allowlist: Optional[frozenset] = None,
        allow_network: bool = False,
    ) -> None:
        self._sessions: Dict[str, _SessionRecord] = {}
        self._lock = threading.Lock()
        self._config = config
        self.max_sessions = max_sessions
        self.max_age_seconds = max_age_seconds
        self.idle_seconds = idle_seconds
        self.image_allowlist = (
            image_allowlist if image_allowlist is not None else DEFAULT_SESSION_IMAGE_ALLOWLIST
        )
        # Network is denied by default. Unlike the one-shot executor, network
        # for a session is an operator decision, not a per-request one — a
        # caller can request it but only gets it when the operator opted in
        # here. (There is no global config field for this; network in tako-vm
        # is otherwise a per-job-type setting.)
        self.allow_network = allow_network

    def _resolve_config(self) -> TakoVMConfig:
        # Resolve lazily so constructing the manager at import time (app.py)
        # doesn't force config loading before the server is configured.
        return self._config or get_config()

    def _expired(self, record: _SessionRecord, now: float) -> bool:
        return (now - record.created_at) > self.max_age_seconds or (
            now - record.last_used
        ) > self.idle_seconds

    def _reap_expired(self) -> None:
        """Kill + drop sessions past their age/idle TTL. Lazy, lock-safe."""
        now = time.monotonic()
        with self._lock:
            expired = [
                self._sessions.pop(sid)
                for sid, rec in list(self._sessions.items())
                if self._expired(rec, now)
            ]
        for rec in expired:
            logger.info("Reaping expired session %s", rec.session_id)
            try:
                kill_container(rec.container_name)
            except Exception:
                logger.exception("Error reaping session container %s", rec.container_name)

    def create(
        self,
        workspace: Path,
        image: str,
        memory_limit: str,
        cpu_limit: float,
        network_enabled: bool,
        workspace_mount: str,
    ) -> _SessionRecord:
        config = self._resolve_config()

        # Validate caller-controlled inputs before spending a container.
        resolved_ws = _validate_workspace(workspace)
        _validate_image(image, self.image_allowlist)
        _validate_resources(cpu_limit, memory_limit)

        # Reap stale sessions, then enforce the concurrency cap.
        self._reap_expired()
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise SessionLimitError(f"session limit reached ({self.max_sessions} active)")

        # Isolation comes from server config, NOT the request: a caller can
        # never drop capability restrictions, swap the runtime, or disable
        # seccomp. Network is only granted if the server config allows it.
        runtime = resolve_runtime(config)
        effective_network = bool(network_enabled) and self.allow_network
        if network_enabled and not effective_network:
            logger.warning(
                "Session requested network but operator policy denies it; forcing network=none"
            )
        seccomp_path = config.seccomp_profile_path if config.enable_seccomp else None

        ensure_workspace_writable(resolved_ws)
        session_id = uuid.uuid4().hex
        container_name = generate_container_name("tako-session")
        cmd = build_session_docker_command(
            container_name=container_name,
            workspace=resolved_ws,
            image=image,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            network_enabled=effective_network,
            workspace_mount=workspace_mount,
            enable_cap_restrictions=config.enable_cap_restrictions,
            runtime=runtime,
            enable_seccomp=config.enable_seccomp,
            seccomp_profile_path=seccomp_path,
            ulimits=session_ulimits(config),
        )
        logger.info(
            "Starting session %s (container=%s, workspace=%s, runtime=%s, network=%s)",
            session_id,
            container_name,
            resolved_ws,
            runtime,
            effective_network,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            # Sanitize: raw daemon stderr can leak host paths / image internals.
            raise RuntimeError(f"docker run failed: {sanitize_error(result.stderr.strip())}")

        record = _SessionRecord(
            session_id=session_id,
            container_name=container_name,
            workspace=resolved_ws,
            workspace_mount=workspace_mount,
            image=image,
            network_enabled=effective_network,
        )
        with self._lock:
            self._sessions[session_id] = record
        return record

    def get(self, session_id: str) -> Optional[_SessionRecord]:
        """Return a live session, reaping it instead if it has expired."""
        now = time.monotonic()
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            if self._expired(record, now):
                self._sessions.pop(session_id, None)
                expired = record
            else:
                record.last_used = now
                return record
        # Expired: tear down outside the lock, report as gone.
        logger.info("Reaping expired session %s on access", expired.session_id)
        try:
            kill_container(expired.container_name)
        except Exception:
            logger.exception("Error reaping session container %s", expired.container_name)
        return None

    def delete(self, session_id: str) -> bool:
        with self._lock:
            record = self._sessions.pop(session_id, None)
        if record is None:
            return False
        kill_container(record.container_name)
        return True

    def list_ids(self) -> List[str]:
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
                logger.exception("Error killing session container %s", r.container_name)


def _validate_workspace(workspace: Path) -> Path:
    """Confine ``workspace`` to the allowlist and return the *resolved* path.

    Returning the resolved path (and mounting that, not the raw input)
    closes the gap where validation resolves symlinks but the bind mount
    used the unresolved string.
    """
    resolved = workspace.resolve()
    for allowed in WORKSPACE_PREFIX_ALLOWLIST:
        allowed_path = Path(allowed).resolve()
        try:
            resolved.relative_to(allowed_path)
            return resolved
        except ValueError:
            continue
    raise ValueError(f"workspace must be under one of {WORKSPACE_PREFIX_ALLOWLIST}, got {resolved}")


def _validate_image(image: str, allowlist: frozenset) -> None:
    if not validate_docker_image(image):
        raise ValueError(f"invalid image name: {image!r}")
    if image not in allowlist:
        raise ValueError(f"image not allowed: {image!r} (allowed: {sorted(allowlist)})")


def _validate_resources(cpu_limit: float, memory_limit: str) -> None:
    if not (0 < cpu_limit <= MAX_SESSION_CPU):
        raise ValueError(f"cpu_limit must be in (0, {MAX_SESSION_CPU}], got {cpu_limit}")
    mb = _memory_limit_to_mb(memory_limit)  # raises ValueError on bad format
    if mb < MIN_SESSION_MEMORY_MB or mb > MAX_SESSION_MEMORY_MB:
        raise ValueError(
            f"memory_limit must be between {MIN_SESSION_MEMORY_MB}m and "
            f"{MAX_SESSION_MEMORY_MB}m, got {memory_limit}"
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
    cpu_limit: float = Field(default=1.0, gt=0, le=MAX_SESSION_CPU)
    # Only honored if the server config also allows network access.
    network_enabled: bool = Field(default=False)
    workspace_mount: str = Field(default=DEFAULT_WORKSPACE_MOUNT)
    # NOTE: capability dropping, runtime, and seccomp are server-policy and
    # intentionally NOT accepted from the client — a caller must not be able
    # to weaken the sandbox. (Any client-sent ``enable_cap_restrictions`` is
    # ignored by Pydantic's default extra='ignore'.)

    @field_validator("memory_limit")
    @classmethod
    def _check_memory_limit(cls, v: str) -> str:
        mb = _memory_limit_to_mb(v)  # raises ValueError on bad format
        if mb < MIN_SESSION_MEMORY_MB or mb > MAX_SESSION_MEMORY_MB:
            raise ValueError(
                f"memory_limit must be between {MIN_SESSION_MEMORY_MB}m and "
                f"{MAX_SESSION_MEMORY_MB}m"
            )
        return v


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
            )
        except SessionLimitError as e:
            raise HTTPException(status_code=429, detail=str(e))
        except RuntimeUnavailableError as e:
            # Server can't provide the configured isolation (e.g. gVisor
            # missing in strict mode). Fail loudly rather than downgrade.
            raise HTTPException(status_code=503, detail=str(e))
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
        try:
            result = run_docker_exec(
                container_name=record.container_name,
                command=req.command,
                cwd=cwd,
                timeout=req.timeout,
                env=req.env,
            )
        except ValueError as e:
            # Invalid cwd / env (e.g. injection attempt) — reject, don't 500.
            raise HTTPException(status_code=400, detail=str(e))
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
