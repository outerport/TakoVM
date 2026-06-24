"""Long-lived sandbox sessions for roaming agents.

A *session* is a container that stays up across many ``exec`` calls
against a shared workspace. Two backends are provided:

- :class:`LocalTakoSession` — talks directly to the local Docker daemon
  via ``docker run -d`` / ``docker exec``. Useful for offline dev or
  machines that don't have a tako-vm server running.
- :class:`RemoteTakoSession` — talks to a tako-vm server over HTTP
  (``POST /sessions`` / ``POST /sessions/{id}/exec`` /
  ``DELETE /sessions/{id}``). This is the default; it matches the
  production architecture where the server owns the sandbox plane.

``TakoSession`` is an alias for :class:`RemoteTakoSession`. Instantiate
that directly if you want the HTTP backend explicitly, or use
:class:`LocalTakoSession` if you want the local-docker fallback.

Example (remote)::

    from pathlib import Path
    from tako_vm import TakoSession

    with TakoSession(workspace=Path("/tmp/tako-vm-workspace/agent-abc")) as s:
        print(s.exec("ls /workspace").stdout)

Example (local)::

    from tako_vm import LocalTakoSession

    with LocalTakoSession(workspace=Path("/tmp/agent-ws")) as s:
        print(s.exec("echo hi").stdout)
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from tako_vm.constants import DEFAULT_IMAGE
from tako_vm.execution.docker import (
    generate_container_name,
    is_native_linux,
    kill_container,
)
from tako_vm.security import validate_env_key, validate_env_value

logger = logging.getLogger(__name__)

# Absolute container path with a conservative charset. Critically this
# forbids the comma and ``=`` that Docker's ``--mount=k=v,k=v`` syntax uses:
# without this, a ``workspace_mount`` (or cwd) like
# ``/workspace,source=/var/run`` would inject an extra ``source=`` key into the
# --mount spec and re-point the bind mount to an arbitrary HOST path (e.g. the
# Docker socket → host root). The validated workspace source is otherwise
# meaningless if the target can override it.
_SAFE_CONTAINER_PATH = re.compile(r"^/[A-Za-z0-9._\-/]*$")


def _validate_container_path(value: str, field: str) -> None:
    """Reject a container path that could inject docker mount/flag options."""
    if not value or not _SAFE_CONTAINER_PATH.match(value):
        raise ValueError(
            f"{field} must be an absolute path matching {_SAFE_CONTAINER_PATH.pattern!r}; "
            f"got {value!r}"
        )


def _validate_mount_source(value: str) -> None:
    """Reject a bind-mount source that could inject extra --mount keys."""
    if "," in value or "=" in value:
        raise ValueError(f"workspace path contains disallowed characters: {value!r}")


def session_ulimits(config: Any) -> List[str]:
    """Build the ``--ulimit`` flags from config, mirroring the executor.

    Sessions otherwise set only ``--pids-limit``; the executor also bounds
    open files, processes (per-uid), and file size.
    """
    limits = config.container_limits
    return [
        f"--ulimit=nofile={limits.nofile_soft}:{limits.nofile_hard}",
        f"--ulimit=nproc={limits.nproc_soft}:{limits.nproc_hard}",
        f"--ulimit=fsize={limits.fsize}",
    ]


DEFAULT_WORKSPACE_MOUNT = "/workspace"
DEFAULT_SESSION_TIMEOUT = 60
DEFAULT_SERVER_URL = os.environ.get("TAKO_SERVER_URL", "http://localhost:8000")

# Cap the stdout/stderr we return from a single exec so a runaway command
# (``yes``, an infinite log loop) can't balloon the server's response or the
# caller's memory. The command itself is still bounded by the in-container
# ``timeout`` wrapper; this bounds the *output volume* we hand back.
MAX_OUTPUT_CHARS = 1_000_000

# Host-side ``subprocess`` timeout is set a little above the in-container
# ``timeout`` so the container-side limit fires first (killing the process
# tree) and we still capture whatever it produced, instead of the host
# client timing out while the in-container process keeps running.
EXEC_HOST_TIMEOUT_BUFFER = 10

# Exit code GNU coreutils ``timeout`` uses when it has to kill the command.
_TIMEOUT_EXIT_CODE = 124


@dataclass
class SessionExecResult:
    """Result of a single ``exec`` call against a session."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    timed_out: bool = False


def build_session_docker_command(
    container_name: str,
    workspace: Path,
    image: str,
    memory_limit: str,
    cpu_limit: float,
    network_enabled: bool,
    workspace_mount: str,
    enable_cap_restrictions: bool,
    runtime: str = "runc",
    enable_seccomp: bool = False,
    seccomp_profile_path: Optional[Path] = None,
    no_new_privileges: bool = True,
    ulimits: Optional[List[str]] = None,
) -> List[str]:
    """Assemble the ``docker run -d`` command for a session container.

    Shared by the local backend and the server-side session manager so
    both end up with identical sandbox semantics.

    ``workspace_mount`` (the in-container target) and ``workspace`` (the
    host source) are validated to forbid the comma/``=`` that Docker's
    ``--mount=k=v,...`` syntax uses, so neither can inject extra mount keys
    (e.g. a second ``source=`` that re-points the bind mount at an arbitrary
    host path such as the Docker socket).

    ``runtime`` is the resolved container runtime ('runsc' for gVisor,
    'runc' otherwise). It must be resolved by the caller via
    :func:`tako_vm.execution.worker.resolve_runtime` so a session honors
    the same ``container_runtime`` / ``security_mode`` policy as the
    one-shot ``/execute`` path — otherwise a session would silently run
    under runc on a gVisor host. Only ``runsc`` is passed explicitly;
    runc is Docker's default and some daemons reject ``--runtime=runc``.

    Unlike the one-shot executor, a session drops *all* capabilities with
    no ``--cap-add`` for gosu: the container's entrypoint is ``/bin/sleep``
    and every ``exec`` runs directly as uid 1000, so no setuid step is
    needed. That also lets us add ``--security-opt=no-new-privileges``,
    which the executor path can't use because gosu needs setuid.
    """
    # Validate everything that flows into the --mount spec BEFORE building it.
    _validate_container_path(workspace_mount, "workspace_mount")
    _validate_mount_source(str(workspace))

    cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        f"--name={container_name}",
        "--init",
        "--read-only",
        "--user=1000:1000",
        "--entrypoint=/bin/sleep",
    ]
    # Only specify the runtime explicitly for gVisor; runc is the default.
    if runtime == "runsc":
        cmd.append("--runtime=runsc")
    if enable_cap_restrictions:
        cmd.append("--cap-drop=ALL")
    if no_new_privileges:
        cmd.append("--security-opt=no-new-privileges")
    # Apply the seccomp profile on native Linux only — Docker Desktop and
    # some CI runners reject custom profiles. Mirrors CodeExecutor.
    if enable_seccomp and seccomp_profile_path is not None:
        if is_native_linux() and Path(seccomp_profile_path).exists():
            cmd.append(f"--security-opt=seccomp={seccomp_profile_path}")
        else:
            logger.debug("Skipping custom seccomp profile (not native Linux or missing)")
    cmd.append("--network=bridge" if network_enabled else "--network=none")
    # ulimits (nofile/nproc/fsize) for parity with the one-shot executor.
    if ulimits:
        cmd.extend(ulimits)
    cmd.extend(
        [
            f"--memory={memory_limit}",
            f"--memory-swap={memory_limit}",
            f"--cpus={cpu_limit}",
            "--pids-limit=200",
            "--tmpfs=/tmp:rw,exec,nosuid,size=300m",
            f"--mount=type=bind,source={workspace},target={workspace_mount}",
            image,
            "infinity",
        ]
    )
    return cmd


def _truncate_output(text: str) -> str:
    """Cap a single stream to ``MAX_OUTPUT_CHARS`` with a visible marker."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...[truncated, {len(text)} chars total]"


def run_docker_exec(
    container_name: str,
    command: str,
    cwd: str,
    timeout: int,
    env: Optional[Dict[str, str]] = None,
) -> SessionExecResult:
    """Run ``command`` inside the session container and return the result.

    The command is wrapped in the container-side ``timeout`` coreutil so a
    runaway/backgrounded process is killed *inside* the container when the
    deadline passes. Without this, a host-side ``subprocess`` timeout only
    kills the ``docker exec`` client while the in-container process keeps
    running (orphaned miner/scanner). The host-side timeout is kept as a
    backstop, set above the in-container one so the container limit fires
    first and we still capture partial output.

    ``cwd`` and ``env`` are validated for parity with the one-shot executor:
    ``cwd`` must be a safe absolute path, and env keys/values must pass the
    same ``validate_env_key``/``validate_env_value`` checks (rejecting
    control chars, ``$``/backticks/backslashes, oversized values).
    """
    _validate_container_path(cwd, "cwd")
    exec_cmd: List[str] = ["docker", "exec", "-w", cwd]
    if env:
        for k, v in env.items():
            if not validate_env_key(k):
                raise ValueError(f"invalid env key: {k!r}")
            if not validate_env_value(v):
                raise ValueError(f"invalid env value for {k!r}")
            exec_cmd.extend(["-e", f"{k}={v}"])
    # ``timeout --kill-after`` escalates to SIGKILL if the command ignores
    # SIGTERM. ``--`` guards against a command that looks like an option.
    exec_cmd.extend(
        [
            container_name,
            "timeout",
            "--kill-after=5s",
            "--signal=TERM",
            f"{timeout}s",
            "bash",
            "-c",
            command,
        ]
    )

    start = time.time()
    try:
        proc = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=timeout + EXEC_HOST_TIMEOUT_BUFFER,
            check=False,
        )
        duration_ms = int((time.time() - start) * 1000)
        return SessionExecResult(
            stdout=_truncate_output(proc.stdout),
            stderr=_truncate_output(proc.stderr),
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            timed_out=proc.returncode == _TIMEOUT_EXIT_CODE,
        )
    except subprocess.TimeoutExpired as e:
        # Backstop: the container-side timeout didn't return in time. The
        # in-container process tree is already being torn down by ``timeout``.
        duration_ms = int((time.time() - start) * 1000)
        out = e.stdout.decode("utf-8", "replace") if e.stdout else ""
        err = e.stderr.decode("utf-8", "replace") if e.stderr else ""
        return SessionExecResult(
            stdout=_truncate_output(out),
            stderr=_truncate_output(err),
            exit_code=-1,
            duration_ms=duration_ms,
            timed_out=True,
        )


def ensure_workspace_writable(workspace: Path) -> None:
    """Make the workspace writable by the sandbox user (uid 1000).

    The image runs as uid 1000. For local dev we chmod 0o777; production
    should set up UID remapping or match host/container UIDs.

    Refuses to operate through a symlink so a pre-planted symlink under the
    workspace root can't redirect the ``mkdir``/``chmod`` (or the later bind
    mount) at a path outside the allowlist.
    """
    if workspace.is_symlink():
        raise ValueError(f"Workspace must not be a symlink: {workspace}")
    if not workspace.exists():
        workspace.mkdir(parents=True)
    if not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")
    try:
        os.chmod(workspace, 0o777)
    except PermissionError:
        logger.warning(
            "Could not chmod workspace %s to 0777; writes from the sandbox user may fail.",
            workspace,
        )


class LocalTakoSession:
    """Session backed by the local Docker daemon.

    Use when you don't have a tako-vm server running (tests, CLI tools,
    offline dev).
    """

    def __init__(
        self,
        workspace: Path,
        image: str = DEFAULT_IMAGE,
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        network_enabled: bool = False,
        workspace_mount: str = DEFAULT_WORKSPACE_MOUNT,
        enable_cap_restrictions: bool = True,
    ) -> None:
        self.workspace = workspace.resolve()
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.network_enabled = network_enabled
        self.workspace_mount = workspace_mount
        self.enable_cap_restrictions = enable_cap_restrictions
        self.container_name: Optional[str] = None
        self._started = False

    def __enter__(self) -> "LocalTakoSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    def start(self) -> None:
        if self._started:
            return
        ensure_workspace_writable(self.workspace)
        self.container_name = generate_container_name("tako-session")
        # Resolve the runtime + seccomp the same way the one-shot executor
        # does, so a local session gets gVisor when the host/config provides
        # it instead of silently running under runc. Imported lazily to keep
        # ``tako_vm`` package import (which pulls in this module) light.
        from tako_vm.config import get_config
        from tako_vm.execution.worker import resolve_runtime

        config = get_config()
        runtime = resolve_runtime(config)
        seccomp_path = config.seccomp_profile_path if config.enable_seccomp else None
        cmd = build_session_docker_command(
            container_name=self.container_name,
            workspace=self.workspace,
            image=self.image,
            memory_limit=self.memory_limit,
            cpu_limit=self.cpu_limit,
            network_enabled=self.network_enabled,
            workspace_mount=self.workspace_mount,
            enable_cap_restrictions=self.enable_cap_restrictions,
            runtime=runtime,
            enable_seccomp=config.enable_seccomp,
            seccomp_profile_path=seccomp_path,
            ulimits=session_ulimits(config),
        )
        logger.debug("Starting session container: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start session container: {result.stderr.strip()}")
        self._started = True

    def exec(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: int = DEFAULT_SESSION_TIMEOUT,
        env: Optional[Dict[str, str]] = None,
    ) -> SessionExecResult:
        if not self._started or self.container_name is None:
            raise RuntimeError("Session is not started; call start() first")
        working_dir = cwd if cwd is not None else self.workspace_mount
        return run_docker_exec(self.container_name, command, working_dir, timeout, env)

    def stop(self) -> None:
        if not self._started:
            return
        if self.container_name is not None:
            kill_container(self.container_name)
        self._started = False
        self.container_name = None

    def info(self) -> Dict[str, Any]:
        return {
            "backend": "local",
            "container_name": self.container_name,
            "started": self._started,
            "workspace": str(self.workspace),
            "workspace_mount": self.workspace_mount,
            "image": self.image,
        }


class RemoteTakoSession:
    """Session backed by a tako-vm server over HTTP.

    Two ways to specify where the sandbox's workspace lives:

    - **Scope-based** (preferred): pass ``scope="agent-session:<uuid>"`` (or
      ``"anonymous"``) and the server picks the host path itself. The
      client never touches the filesystem — it only reads the resolved
      path back from the response.
    - **Path-based** (legacy / dev): pass ``workspace=Path(...)`` with
      an absolute host path. Both sides must be able to see that path
      via the docker-compose shared bind mount. The client calls
      ``ensure_workspace_writable`` on its side for dev convenience.
    """

    def __init__(
        self,
        workspace: Optional[Path] = None,
        scope: Optional[str] = None,
        server_url: str = DEFAULT_SERVER_URL,
        image: Optional[str] = None,
        memory_limit: Optional[str] = None,
        cpu_limit: Optional[float] = None,
        network_enabled: Optional[bool] = None,
        workspace_mount: str = DEFAULT_WORKSPACE_MOUNT,
        enable_cap_restrictions: Optional[bool] = None,
        request_timeout: float = 30.0,
    ) -> None:
        if (workspace is None) == (scope is None):
            raise ValueError("RemoteTakoSession requires exactly one of 'workspace' or 'scope'")
        # When scope is set, the resolved workspace is filled in by start()
        # from the server's response.
        self.workspace: Optional[Path] = workspace.resolve() if workspace else None
        self.scope = scope
        self.server_url = server_url.rstrip("/")
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.network_enabled = network_enabled
        self.workspace_mount = workspace_mount
        self.enable_cap_restrictions = enable_cap_restrictions
        self.request_timeout = request_timeout
        self.session_id: Optional[str] = None
        self.container_name: Optional[str] = None
        self._client: Optional[httpx.Client] = None

    def __enter__(self) -> "RemoteTakoSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.server_url, timeout=self.request_timeout)
        return self._client

    def start(self) -> None:
        if self.session_id is not None:
            return

        payload: Dict[str, Any] = {"workspace_mount": self.workspace_mount}
        if self.scope is not None:
            # Scope-based: server owns the workspace. Don't touch the FS.
            payload["scope"] = self.scope
        else:
            # Path-based (legacy): client preps its side of the shared mount
            # so writes from the sandbox user land with the right perms.
            assert self.workspace is not None
            ensure_workspace_writable(self.workspace)
            payload["workspace"] = str(self.workspace)

        if self.image is not None:
            payload["image"] = self.image
        if self.memory_limit is not None:
            payload["memory_limit"] = self.memory_limit
        if self.cpu_limit is not None:
            payload["cpu_limit"] = self.cpu_limit
        if self.network_enabled is not None:
            payload["network_enabled"] = self.network_enabled
        if self.enable_cap_restrictions is not None:
            payload["enable_cap_restrictions"] = self.enable_cap_restrictions

        resp = self._http().post("/sessions", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Failed to create session ({resp.status_code}): {resp.text}")
        data = resp.json()
        self.session_id = data["session_id"]
        self.container_name = data.get("container_name")
        # Server-resolved workspace always comes back in the response.
        returned_ws = data.get("workspace")
        if returned_ws is not None:
            self.workspace = Path(returned_ws)

    def exec(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: int = DEFAULT_SESSION_TIMEOUT,
        env: Optional[Dict[str, str]] = None,
    ) -> SessionExecResult:
        if self.session_id is None:
            raise RuntimeError("Session is not started; call start() first")
        payload: Dict[str, Any] = {"command": command, "timeout": timeout}
        if cwd is not None:
            payload["cwd"] = cwd
        if env:
            payload["env"] = env
        resp = self._http().post(
            f"/sessions/{self.session_id}/exec",
            json=payload,
            timeout=timeout + self.request_timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"exec failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        return SessionExecResult(
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            exit_code=data.get("exit_code", 0),
            duration_ms=data.get("duration_ms", 0),
            timed_out=data.get("timed_out", False),
        )

    def stop(self) -> None:
        if self.session_id is None:
            return
        try:
            self._http().delete(f"/sessions/{self.session_id}")
        except Exception as e:
            logger.warning("Failed to stop session %s: %s", self.session_id, e)
        finally:
            self.session_id = None
            self.container_name = None
            if self._client is not None:
                self._client.close()
                self._client = None

    def info(self) -> Dict[str, Any]:
        return {
            "backend": "remote",
            "server_url": self.server_url,
            "session_id": self.session_id,
            "container_name": self.container_name,
            "workspace": str(self.workspace),
            "workspace_mount": self.workspace_mount,
        }


# ``TakoSession`` is the default client class. The HTTP backend is the
# production path; use ``LocalTakoSession`` explicitly when you want the
# offline fallback.
TakoSession = RemoteTakoSession
