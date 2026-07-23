"""Locks the behavior change 'reject unknown job_type' (was: silent fallback to
DEFAULT_JOB_TYPE). The /execute endpoint maps this ValueError to HTTP 400."""

import pytest

import tako_vm.execution.worker as worker_module
from tako_vm.config import TakoVMConfig
from tako_vm.execution.worker import CodeExecutor


@pytest.fixture(autouse=True)
def _permissive(monkeypatch):
    # Avoid a real gVisor probe; permissive falls back to runc.
    monkeypatch.setattr(worker_module, "_gvisor_available", False)


def _executor():
    return CodeExecutor(config=TakoVMConfig(container_runtime="runsc", security_mode="permissive"))


def test_unknown_job_type_raises():
    with pytest.raises(ValueError, match="not found"):
        _executor()._get_job_type("does-not-exist")


def test_default_job_type_resolves():
    # The built-in 'default' type is always present.
    assert _executor()._get_job_type("default") is not None


def test_none_job_type_resolves_to_default():
    assert _executor()._get_job_type(None) is not None
