"""A session container's ``/tmp`` is a tmpfs whose size the caller chooses.

A caller that writes its whole input to ``/tmp`` (a project materialized for
one request) needs more than the default; every other caller keeps it.
"""

from pathlib import Path

from tako_vm.config import reset_config
from tako_vm.session import LocalTakoSession, build_session_docker_command


def _tmpfs_args(cmd):
    return [arg for arg in cmd if arg.startswith("--tmpfs=")]


def _session_cmd(**kwargs):
    return build_session_docker_command(
        container_name="tako-session-test",
        workspace=Path("/tmp/tako-vm-workspace/sessions/test"),
        image="code-executor:latest",
        memory_limit="512m",
        cpu_limit=1.0,
        network_enabled=False,
        workspace_mount="/workspace",
        enable_cap_restrictions=True,
        **kwargs,
    )


def test_tmp_is_300m_unless_the_caller_says_otherwise():
    assert _tmpfs_args(_session_cmd()) == ["--tmpfs=/tmp:rw,exec,nosuid,size=300m"]


def test_tmp_takes_the_size_the_caller_gives():
    assert _tmpfs_args(_session_cmd(tmpfs_size="768m")) == ["--tmpfs=/tmp:rw,exec,nosuid,size=768m"]


def test_a_local_session_starts_with_the_size_it_was_given(monkeypatch, tmp_path):
    monkeypatch.setenv("TAKO_VM_SECURITY_MODE", "permissive")
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
    LocalTakoSession(workspace=tmp_path, tmpfs_size="768m").start()
    reset_config()
    assert _tmpfs_args(captured["cmd"]) == ["--tmpfs=/tmp:rw,exec,nosuid,size=768m"]
