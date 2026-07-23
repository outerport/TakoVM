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
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from tako_vm.config import TakoVMConfig, get_config
from tako_vm.constants import DEFAULT_IMAGE, SANDBOX_GID, SANDBOX_UID
from tako_vm.execution.docker import generate_container_name, is_native_linux, kill_container
from tako_vm.execution.worker import resolve_runtime
from tako_vm.security import cap_output, validate_env_key, validate_env_value

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_MOUNT = "/workspace"
DEFAULT_SESSION_TIMEOUT = 60
DEFAULT_SERVER_URL = os.environ.get("TAKO_SERVER_URL", "http://localhost:8000")

# Docker label applied to every session container so orphans (survivors of a
# non-graceful server restart) can be found and reaped by `docker ps --filter`.
SESSION_LABEL = "tako-vm-session"

# Workspace reconciliation modes. Directories get setgid so new entries inherit
# the sandbox gid; group-write (not world) because the sandbox uid is in the
# sandbox group.
WORKSPACE_DIR_MODE = 0o2770
WORKSPACE_FILE_MODE = 0o0660


@dataclass
class SessionExecResult:
    """Result of a single ``exec`` call against a session."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    timed_out: bool = False


def _reject_mount_csv_metachars(value: str, field: str) -> None:
    """Guard a value interpolated into the ``--mount`` CSV.

    ``--mount=type=bind,source={workspace},target={workspace_mount}`` is a
    comma-separated ``key=value`` list. A ``,`` or ``=`` in either interpolated
    value can inject a second ``source=`` and re-point the bind mount — e.g.
    ``workspace_mount="/workspace,source=/"`` mounts the host root. Docker's CSV
    parser honours the later ``source=``, so this is a real host-escape, not a
    parse error. Neither value may contain CSV or shell/whitespace metacharacters.
    """
    if any(c in value for c in ",=\n\r\t ") or "\x00" in value:
        raise ValueError(f"invalid {field} {value!r}: contains disallowed characters")


def build_session_docker_command(
    container_name: str,
    workspace: Path,
    image: str,
    memory_limit: str,
    cpu_limit: float,
    network_enabled: bool,
    workspace_mount: str,
    enable_cap_restrictions: bool,
    config: TakoVMConfig,
    runtime: Optional[str] = None,
) -> List[str]:
    """Assemble the ``docker run -d`` command for a session container.

    Shared by the local backend and the server-side session manager so both
    apply the same hardening the one-shot job path
    (:meth:`CodeExecutor._run_container`) uses: cap-drop, the gVisor runtime,
    the seccomp allowlist, the configured nofile/nproc/fsize ulimits and pids
    cap, and a noexec /tmp. Session containers never install deps at runtime
    (they run ``/bin/sleep``), so /tmp is always ``noexec`` and its size comes
    from config rather than a hardcoded literal.
    """
    _reject_mount_csv_metachars(str(workspace), "workspace")
    _reject_mount_csv_metachars(workspace_mount, "workspace_mount")
    if not workspace_mount.startswith("/"):
        raise ValueError(f"workspace_mount must be an absolute path, got {workspace_mount!r}")

    limits = config.container_limits
    cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        f"--name={container_name}",
        f"--label={SESSION_LABEL}",
        "--init",
        "--read-only",
        f"--user={SANDBOX_UID}:{SANDBOX_GID}",
        "--entrypoint=/bin/sleep",
    ]
    # Apply gVisor explicitly. runc is docker's default (and some daemons
    # reject ``--runtime=runc``), so we only pass the flag for runsc --
    # mirroring CodeExecutor._run_container. Without this, session containers
    # run under the host kernel (runc) regardless of strict mode.
    if runtime == "runsc":
        cmd.append(f"--runtime={runtime}")
    if enable_cap_restrictions:
        cmd.append("--cap-drop=ALL")
    cmd.append("--network=bridge" if network_enabled else "--network=none")
    cmd.extend(
        [
            f"--memory={memory_limit}",
            f"--memory-swap={memory_limit}",
            f"--cpus={cpu_limit}",
            f"--pids-limit={limits.pids_limit}",
            f"--ulimit=nofile={limits.nofile_soft}:{limits.nofile_hard}",
            f"--ulimit=nproc={limits.nproc_soft}:{limits.nproc_hard}",
            f"--ulimit=fsize={limits.fsize}",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size={limits.tmpfs_size}",
            f"--mount=type=bind,source={workspace},target={workspace_mount}",
        ]
    )
    # Same seccomp allowlist the job path applies (native Linux only; Docker
    # Desktop chokes on custom profiles).
    if config.enable_seccomp and config.seccomp_profile_path:
        if is_native_linux() and config.seccomp_profile_path.exists():
            cmd.append(f"--security-opt=seccomp={config.seccomp_profile_path}")
    cmd.extend([image, "infinity"])
    return cmd


def run_docker_exec(
    container_name: str,
    command: str,
    cwd: str,
    timeout: int,
    env: Optional[Dict[str, str]] = None,
    max_output_bytes: Optional[int] = None,
) -> SessionExecResult:
    """Run ``docker exec ... bash -c "command"`` and return the result.

    The command is untrusted, so three defenses are applied that the raw
    ``docker exec`` did not have:

    - The command is wrapped in a coreutils ``timeout`` so a runaway process is
      killed *inside* the container. A bare ``subprocess.run(timeout=...)`` only
      kills the ``docker exec`` client; the in-container process keeps running
      and eventually exhausts the ``--pids-limit`` cap, bricking the session.
    - Caller-supplied ``env`` is validated exactly as the job path validates it
      (``validate_env_key`` / ``validate_env_value``); unsafe entries are skipped
      rather than forwarded to ``docker exec -e``.
    - stdout/stderr are capped to ``max_output_bytes`` (default
      ``max_stdout_bytes`` from config) so a flood such as ``yes`` cannot OOM the
      host process that drains the pipe (the container memory cgroup does not
      bound the host-side read buffer).
    """
    cap = max_output_bytes if max_output_bytes is not None else get_config().max_stdout_bytes

    exec_cmd: List[str] = ["docker", "exec", "-w", cwd]
    if env:
        for k, v in env.items():
            if not validate_env_key(k):
                logger.warning("Skipping session env var with invalid key: %r", k)
                continue
            if not validate_env_value(v):
                logger.warning("Skipping session env var with unsafe value (key=%r)", k)
                continue
            exec_cmd.extend(["-e", f"{k}={v}"])
    # ``timeout -k 5 <n>s`` sends TERM at n seconds, then KILL 5s later; exit
    # code 124 signals the command was timed out.
    exec_cmd.extend([container_name, "timeout", "-k", "5", f"{timeout}s", "bash", "-c", command])

    start = time.time()
    # Outer backstop, larger than the in-container timeout + kill grace so the
    # container-side timeout fires first in the normal case.
    outer_timeout = timeout + 15
    try:
        proc = subprocess.run(
            exec_cmd, capture_output=True, text=True, timeout=outer_timeout, check=False
        )
        duration_ms = int((time.time() - start) * 1000)
        return SessionExecResult(
            stdout=cap_output(proc.stdout, cap),
            stderr=cap_output(proc.stderr, cap),
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            timed_out=proc.returncode == 124,
        )
    except subprocess.TimeoutExpired as e:
        # The in-container timeout never fired (missing binary / daemon hang);
        # kill the container so the process can't linger and hold pids.
        kill_container(container_name)
        duration_ms = int((time.time() - start) * 1000)
        stdout = e.stdout.decode("utf-8", "replace") if e.stdout else ""
        stderr = e.stderr.decode("utf-8", "replace") if e.stderr else ""
        return SessionExecResult(
            stdout=cap_output(stdout, cap),
            stderr=cap_output(stderr, cap),
            exit_code=-1,
            duration_ms=duration_ms,
            timed_out=True,
        )


def _reconcile_workspace_entry(path: Path, is_dir: bool) -> None:
    """Give one workspace entry the sandbox group + group-write, never following
    a symlink (so a link planted by the sandbox user can't redirect the op onto
    a host path)."""
    try:
        # lchown: operate on the link itself, not its target.
        os.chown(path, -1, SANDBOX_GID, follow_symlinks=False)
    except (PermissionError, OSError):
        logger.warning("Could not chgrp %s to gid %d", path, SANDBOX_GID)
    # os.chmod can't skip-follow on Linux for a path, so never chmod a symlink.
    if path.is_symlink():
        return
    try:
        os.chmod(path, WORKSPACE_DIR_MODE if is_dir else WORKSPACE_FILE_MODE)
    except (PermissionError, OSError):
        logger.warning("Could not chmod %s", path)


def ensure_workspace_writable(workspace: Path) -> None:
    """Make the workspace usable by the sandbox user (uid SANDBOX_UID).

    The container runs as SANDBOX_UID:SANDBOX_GID, but files placed into the
    workspace on the *host* (workspace hydrate, the fs_write / fs_edit roaming
    tools) are written by the worker process, often as root. A root-owned 0644
    file or dir cannot be overwritten (or chmod-ed) by the uid-1000 container,
    which is what breaks cross-turn notebook/script re-writes.

    Reconcile by walking the whole tree and giving *every existing entry* the
    sandbox group and group-write explicitly — chgrp to SANDBOX_GID, chmod
    directories to ``0o2770`` (setgid) and files to ``0o0660``. The mode is
    group-only (not world-writable) because the sandbox uid is a member of
    SANDBOX_GID. Group-write is set here directly rather than relying on the
    writer's umask.

    Scope note: the setgid bit makes *newly created* subdirectories inherit the
    gid, but not the group-write bit. So a host process that writes into the
    workspace *after* this call (e.g. a between-turn hydrate) must itself write
    group-writable — run the worker at ``umask 002``, ``chmod 0o660`` after each
    write, or call this again — otherwise that new file lands group read-only
    and the sandbox user cannot overwrite it. This function guarantees the
    invariant only for entries that exist when it runs.
    """
    if workspace.is_symlink():
        raise ValueError(f"Workspace must not be a symlink: {workspace}")
    if not workspace.exists():
        workspace.mkdir(parents=True)
    if not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")

    _reconcile_workspace_entry(workspace, is_dir=True)
    # followlinks=False so we never descend through a symlinked directory.
    for root, dirs, files in os.walk(workspace, followlinks=False):
        root_path = Path(root)
        for name in dirs:
            _reconcile_workspace_entry(root_path / name, is_dir=True)
        for name in files:
            _reconcile_workspace_entry(root_path / name, is_dir=False)


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
        container_name: Optional[str] = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.network_enabled = network_enabled
        self.workspace_mount = workspace_mount
        self.enable_cap_restrictions = enable_cap_restrictions
        # When the caller supplies a name, start() uses it instead of a
        # generated one -- letting the caller kill the container by a name it
        # already knows even if start() never returns (e.g. a queue/result
        # timeout in a remote driver).
        self._container_name_override = container_name
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
        self.container_name = self._container_name_override or generate_container_name(
            "tako-session"
        )
        config = get_config()
        runtime = resolve_runtime(config)
        cmd = build_session_docker_command(
            container_name=self.container_name,
            workspace=self.workspace,
            image=self.image,
            memory_limit=self.memory_limit,
            cpu_limit=self.cpu_limit,
            network_enabled=self.network_enabled,
            workspace_mount=self.workspace_mount,
            enable_cap_restrictions=self.enable_cap_restrictions,
            config=config,
            runtime=runtime,
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
