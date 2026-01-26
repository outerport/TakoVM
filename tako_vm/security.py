"""
Security utilities for Tako VM.

Provides output capping, error sanitization, and artifact validation.
"""

import re
import hashlib
from pathlib import Path
from typing import List, Tuple

# Patterns to sanitize from error messages (pattern, replacement)
SANITIZE_PATTERNS: List[Tuple[str, str]] = [
    # Temp directories
    (r'/tmp/job-[a-zA-Z0-9_-]+', '/tmp/job-***'),
    (r'/var/folders/[^\s]+', '/var/folders/***'),

    # User home directories
    (r'/Users/[^/\s]+', '/home/***'),
    (r'/home/[^/\s]+', '/home/***'),

    # Container internal paths that might leak info
    (r'/app/[^\s]+', '/app/***'),

    # IP addresses (internal)
    (r'172\.\d+\.\d+\.\d+', '172.***.***'),
    (r'10\.\d+\.\d+\.\d+', '10.***.***'),
    (r'192\.168\.\d+\.\d+', '192.168.***.***'),

    # Docker container IDs
    (r'[a-f0-9]{64}', '<container-id>'),
    (r'[a-f0-9]{12}(?![a-f0-9])', '<container-id>'),
]

# Default limits
DEFAULT_MAX_STDOUT_BYTES = 65536  # 64KB
DEFAULT_MAX_STDERR_BYTES = 65536  # 64KB
DEFAULT_MAX_ARTIFACT_BYTES = 10_485_760  # 10MB
DEFAULT_MAX_TOTAL_ARTIFACTS_BYTES = 52_428_800  # 50MB


def sanitize_error(error: str) -> str:
    """
    Remove sensitive information from error messages.

    Sanitizes:
    - File system paths
    - User directories
    - IP addresses
    - Container IDs

    Args:
        error: Raw error message

    Returns:
        Sanitized error message safe for external exposure
    """
    if not error:
        return ""

    result = error
    for pattern, replacement in SANITIZE_PATTERNS:
        result = re.sub(pattern, replacement, result)

    return result


def cap_output(output: str, max_bytes: int = DEFAULT_MAX_STDOUT_BYTES) -> str:
    """
    Cap output to maximum byte size.

    Truncates at UTF-8 character boundary and adds truncation notice.

    Args:
        output: Raw output string
        max_bytes: Maximum size in bytes

    Returns:
        Capped output with truncation notice if truncated
    """
    if not output:
        return ""

    encoded = output.encode('utf-8', errors='replace')
    if len(encoded) <= max_bytes:
        return output

    # Leave room for truncation message
    truncation_msg = f"\n\n[TRUNCATED: output exceeded {max_bytes} bytes]"
    truncation_bytes = len(truncation_msg.encode('utf-8'))
    available_bytes = max_bytes - truncation_bytes

    if available_bytes <= 0:
        return truncation_msg

    # Truncate at byte boundary, then decode
    truncated_bytes = encoded[:available_bytes]

    # Find last valid UTF-8 boundary
    while truncated_bytes:
        try:
            truncated = truncated_bytes.decode('utf-8')
            break
        except UnicodeDecodeError:
            truncated_bytes = truncated_bytes[:-1]
    else:
        truncated = ""

    return truncated + truncation_msg


def validate_artifact_size(
    path: Path,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES
) -> Tuple[bool, int]:
    """
    Validate artifact is within size limit.

    Args:
        path: Path to artifact file
        max_bytes: Maximum allowed size

    Returns:
        Tuple of (is_valid, actual_size)
    """
    if not path.exists():
        return False, 0

    size = path.stat().st_size
    return size <= max_bytes, size


def compute_file_hash(path: Path) -> str:
    """
    Compute SHA256 hash of file.

    Args:
        path: Path to file

    Returns:
        Hex-encoded SHA256 hash
    """
    sha256 = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def compute_content_hash(content: str) -> str:
    """
    Compute SHA256 hash of string content.

    Args:
        content: String to hash

    Returns:
        Hex-encoded SHA256 hash
    """
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def classify_error(
    exit_code: int,
    stderr: str,
    timed_out: bool = False
) -> Tuple[str, str]:
    """
    Classify error type from execution results.

    Args:
        exit_code: Container exit code
        stderr: Captured stderr
        timed_out: Whether execution timed out

    Returns:
        Tuple of (error_type, sanitized_message)
    """
    if timed_out:
        return "timeout", "Execution exceeded time limit"

    # Signal-based exit codes (128 + signal_number)
    if exit_code == 137:  # SIGKILL (9)
        return "oom", "Execution exceeded memory limit"

    if exit_code == 143:  # SIGTERM (15)
        return "cancelled", "Execution was terminated"

    if exit_code == 139:  # SIGSEGV (11)
        return "segfault", "Segmentation fault in executed code"

    if exit_code == 134:  # SIGABRT (6)
        return "abort", "Process aborted (assertion failure or abort() called)"

    if exit_code == 136:  # SIGFPE (8)
        return "arithmetic_error", "Floating point exception (division by zero)"

    if exit_code == 135:  # SIGBUS (7)
        return "bus_error", "Bus error (invalid memory access)"

    if exit_code == 141:  # SIGPIPE (13)
        return "pipe_error", "Broken pipe"

    # Check stderr for common Python error patterns
    stderr_lower = stderr.lower() if stderr else ""

    # Memory errors
    if "memoryerror" in stderr_lower or "out of memory" in stderr_lower:
        return "oom", "Execution exceeded memory limit"

    if "cannot allocate memory" in stderr_lower:
        return "oom", "Cannot allocate memory"

    # Process termination
    if "killed" in stderr_lower:
        return "killed", "Process was killed by system"

    # Permission errors
    if "permission denied" in stderr_lower:
        return "permission", "Permission denied during execution"

    if "operation not permitted" in stderr_lower:
        return "permission", "Operation not permitted"

    # Syntax errors
    if "syntaxerror" in stderr_lower:
        return "syntax_error", sanitize_error(stderr[:200] if stderr else "Syntax error")

    if "indentationerror" in stderr_lower:
        return "syntax_error", sanitize_error(stderr[:200] if stderr else "Indentation error")

    # Import errors
    if "importerror" in stderr_lower or "modulenotfounderror" in stderr_lower:
        return "import_error", sanitize_error(stderr[:200] if stderr else "Import error")

    # Type errors
    if "typeerror" in stderr_lower:
        return "type_error", sanitize_error(stderr[:200] if stderr else "Type error")

    # Value errors
    if "valueerror" in stderr_lower:
        return "value_error", sanitize_error(stderr[:200] if stderr else "Value error")

    # Key/Index errors
    if "keyerror" in stderr_lower:
        return "key_error", sanitize_error(stderr[:200] if stderr else "Key error")

    if "indexerror" in stderr_lower:
        return "index_error", sanitize_error(stderr[:200] if stderr else "Index error")

    # Attribute errors
    if "attributeerror" in stderr_lower:
        return "attribute_error", sanitize_error(stderr[:200] if stderr else "Attribute error")

    # Name errors
    if "nameerror" in stderr_lower:
        return "name_error", sanitize_error(stderr[:200] if stderr else "Name error (undefined variable)")

    # File errors
    if "filenotfounderror" in stderr_lower:
        return "file_not_found", sanitize_error(stderr[:200] if stderr else "File not found")

    if "isadirectoryerror" in stderr_lower:
        return "file_error", "Expected file but found directory"

    if "notadirectoryerror" in stderr_lower:
        return "file_error", "Expected directory but found file"

    # OS errors
    if "oserror" in stderr_lower or "ioerror" in stderr_lower:
        return "os_error", sanitize_error(stderr[:200] if stderr else "OS/IO error")

    # Recursion errors
    if "recursionerror" in stderr_lower or "maximum recursion depth" in stderr_lower:
        return "recursion_error", "Maximum recursion depth exceeded"

    # Assertion errors
    if "assertionerror" in stderr_lower:
        return "assertion_error", sanitize_error(stderr[:200] if stderr else "Assertion failed")

    # Zero division
    if "zerodivisionerror" in stderr_lower:
        return "division_error", "Division by zero"

    # Overflow errors
    if "overflowerror" in stderr_lower:
        return "overflow_error", "Numeric overflow"

    # Unicode errors
    if "unicodeerror" in stderr_lower or "unicodedecodeerror" in stderr_lower or "unicodeencodeerror" in stderr_lower:
        return "encoding_error", "Unicode encoding/decoding error"

    # JSON errors
    if "jsondecodeerror" in stderr_lower:
        return "json_error", "Invalid JSON format"

    # Network errors (if network is enabled)
    if "connectionerror" in stderr_lower or "connectionrefusederror" in stderr_lower:
        return "network_error", "Connection error"

    if "timeouterror" in stderr_lower:
        return "network_timeout", "Network request timed out"

    # Docker/container specific
    if "docker" in stderr_lower and "not found" in stderr_lower:
        return "docker_error", "Docker image or command not found"

    if "circuit breaker" in stderr_lower:
        return "service_unavailable", "Service temporarily unavailable"

    # Generic error
    if exit_code != 0:
        msg = sanitize_error(stderr[:200]) if stderr else f"Exit code {exit_code}"
        return "runtime_error", msg

    return "unknown", "Unknown error"


def is_safe_filename(filename: str) -> bool:
    """
    Check if filename is safe (no path traversal).

    Args:
        filename: Filename to check

    Returns:
        True if filename is safe
    """
    if not filename:
        return False

    # No path separators
    if '/' in filename or '\\' in filename:
        return False

    # No parent directory references
    if filename == '..' or filename.startswith('..'):
        return False

    # No hidden files (optional, can be relaxed)
    if filename.startswith('.'):
        return False

    return True
