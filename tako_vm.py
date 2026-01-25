"""
Tako VM - Typed function execution SDK.

This module re-exports the SDK from src/ for convenience.

Usage:
    import tako_vm

    result = tako_vm.send(my_func, input_data)
"""
import sys
from pathlib import Path

# Add src to path if not already there
src_path = str(Path(__file__).parent / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Re-export everything from the SDK
from src.tako_vm import (
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

__all__ = [
    "send",
    "send_raw",
    "configure",
    "list_job_types",
    "get_job_type",
    "TakoVM",
    "ExecutionResult",
    "TakoVMError",
    "ExecutionError",
    "ValidationError",
]
