"""
Tako VM - Secure Code Execution System

Core modules:
- api_server: FastAPI HTTP server with async job queue
- worker: Docker container execution orchestrator
- tako_vm: Typed Python SDK for function execution
- job_types: Execution environment configuration
- models: ExecutionRecord and other data models
- auth: API key authentication and rate limiting
- config: Configuration management
"""

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
from tako_vm.models import ExecutionRecord, ResourceUsage, Artifact, APIKey, JobVersion
from tako_vm.config import TakoVMConfig, get_config

__all__ = [
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
    "APIKey",
    "JobVersion",
    # Configuration
    "TakoVMConfig",
    "get_config",
    # Exceptions
    "TakoVMError",
    "ExecutionError",
    "ValidationError",
]
