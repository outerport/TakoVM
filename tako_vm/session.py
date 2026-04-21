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

from tako_vm.constants import DEFAULT_IMAGE
from tako_vm.execution.docker import generate_container_name, kill_container

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_MOUNT = "/workspace"
DEFAULT_SESSION_TIMEOUT = 60
DEFAULT_SERVER_URL = os.environ.get("TAKO_SERVER_URL", "http://localhost:8000")


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
) -> List[str]:
    """Assemble the ``docker run -d`` command for a session container.

    Shared by the local backend and the server-side session manager so
    both end up with identical sandbox semantics.
    """
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
    if enable_cap_restrictions:
        cmd.append("--cap-drop=ALL")
    cmd.append("--network=bridge" if network_enabled else "--network=none")
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


def run_docker_exec(
    container_name: str,
    command: str,
    cwd: str,
    timeout: int,
    env: Optional[Dict[str, str]] = None,
) -> SessionExecResult:
    """Run ``docker exec ... bash -c "command"`` and return the result."""
    exec_cmd: List[str] = ["docker", "exec", "-w", cwd]
    if env:
        for k, v in env.items():
            exec_cmd.extend(["-e", f"{k}={v}"])
    exec_cmd.extend([container_name, "bash", "-c", command])

    start = time.time()
    try:
        proc = subprocess.run(
            exec_cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        duration_ms = int((time.time() - start) * 1000)
        return SessionExecResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.time() - start) * 1000)
        return SessionExecResult(
            stdout=e.stdout.decode("utf-8", "replace") if e.stdout else "",
            stderr=e.stderr.decode("utf-8", "replace") if e.stderr else "",
            exit_code=-1,
            duration_ms=duration_ms,
            timed_out=True,
        )


def ensure_workspace_writable(workspace: Path) -> None:
    """Make the workspace writable by the sandbox user (uid 1000).

    The image runs as uid 1000. For local dev we chmod 0o777; production
    should set up UID remapping or match host/container UIDs.
    """
    if not workspace.exists():
        workspace.mkdir(parents=True)
    if not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")
    try:
        os.chmod(workspace, 0o777)
    except PermissionError:
        logger.warning(
            "Could not chmod workspace %s to 0777; writes from the "
            "sandbox user may fail.",
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
        cmd = build_session_docker_command(
            container_name=self.container_name,
            workspace=self.workspace,
            image=self.image,
            memory_limit=self.memory_limit,
            cpu_limit=self.cpu_limit,
            network_enabled=self.network_enabled,
            workspace_mount=self.workspace_mount,
            enable_cap_restrictions=self.enable_cap_restrictions,
        )
        logger.debug("Starting session container: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to start session container: {result.stderr.strip()}"
            )
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
        return run_docker_exec(
            self.container_name, command, working_dir, timeout, env
        )

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

    - **Scope-based** (preferred): pass ``scope="agent-skill:<uuid>"`` (or
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
            raise ValueError(
                "RemoteTakoSession requires exactly one of 'workspace' or 'scope'"
            )
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
            self._client = httpx.Client(
                base_url=self.server_url, timeout=self.request_timeout
            )
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
            raise RuntimeError(
                f"Failed to create session ({resp.status_code}): {resp.text}"
            )
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
            raise RuntimeError(
                f"exec failed ({resp.status_code}): {resp.text}"
            )
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
