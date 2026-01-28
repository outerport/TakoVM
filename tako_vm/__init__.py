"""
Tako VM - Secure Code Execution System

Core modules:
- server: FastAPI HTTP server with async job queue (server/app.py, server/queue.py)
- execution: Docker container execution orchestrator (execution/worker.py, execution/builder.py)
- sdk: Typed Python SDK for function execution (sdk/client.py)
- sandbox: Direct Docker execution without a server
- job_types: Execution environment configuration
- models: ExecutionRecord and other data models
- config: Configuration management
"""

# Suppress LibreSSL warnings on macOS (urllib3 v2 requires OpenSSL 1.1.1+)
import warnings
try:
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except ImportError:
    pass

from tako_vm.sdk.client import (
    send,
    send_raw,
    configure,
    list_job_types,
    get_job_type,
    TakoVM,
    ExecutionResult,
    TakoVMError,
    ExecutionError,
    ValidationError,
)

from tako_vm.job_types import JobType, JobTypeRegistry
from tako_vm.models import ExecutionRecord, ResourceUsage, Artifact, JobVersion
from tako_vm.config import TakoVMConfig, get_config
from tako_vm.sandbox import Sandbox, SandboxResult, run as sandbox_run

__all__ = [
    # Sandbox (library-first interface)
    "Sandbox",
    "SandboxResult",
    "sandbox_run",
    # Main SDK functions
    "send",
    "send_raw",
    "configure",
    "list_job_types",
    "get_job_type",
    # SDK Classes
    "TakoVM",
    "ExecutionResult",
    # Job Types
    "JobType",
    "JobTypeRegistry",
    # Production Models
    "ExecutionRecord",
    "ResourceUsage",
    "Artifact",
    "JobVersion",
    # Configuration
    "TakoVMConfig",
    "get_config",
    # Exceptions
    "TakoVMError",
    "ExecutionError",
    "ValidationError",
]
