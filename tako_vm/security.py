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

    # OOM killer typically uses exit code 137 (128 + SIGKILL)
    if exit_code == 137:
        return "oom", "Execution exceeded memory limit"

    # SIGTERM (143 = 128 + 15)
    if exit_code == 143:
        return "cancelled", "Execution was terminated"

    # SIGSEGV (139 = 128 + 11)
    if exit_code == 139:
        return "segfault", "Segmentation fault in executed code"

    # Check stderr for common patterns
    stderr_lower = stderr.lower() if stderr else ""

    if "memoryerror" in stderr_lower or "out of memory" in stderr_lower:
        return "oom", "Execution exceeded memory limit"

    if "killed" in stderr_lower:
        return "killed", "Process was killed by system"

    if "permission denied" in stderr_lower:
        return "permission", "Permission denied during execution"

    if "syntaxerror" in stderr_lower:
        return "syntax_error", sanitize_error(stderr[:200] if stderr else "Syntax error")

    if "importerror" in stderr_lower or "modulenotfounderror" in stderr_lower:
        return "import_error", sanitize_error(stderr[:200] if stderr else "Import error")

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
