"""
Shared constants for Tako VM.

Centralized constants to avoid duplication and ensure consistency
across worker, sandbox, and other modules.
"""

import os
import tempfile

# Sandbox user/group the executor image runs as (docker/Dockerfile.executor
# creates it; session containers launch with --user=SANDBOX_UID:SANDBOX_GID).
# Single source of truth: the container --user AND the host-side workspace
# group-ownership both derive from these, so code that runs inside the sandbox
# and code that prepares the workspace on the host agree on one identity.
SANDBOX_UID = 1000
SANDBOX_GID = 1000

# Docker image for code execution
DEFAULT_IMAGE = "code-executor:latest"

# Docker volume name for uv cache (speeds up repeated dependency installs)
UV_CACHE_VOLUME = "tako-uv-cache"

# Workspace directory for job files (can be set via TAKO_VM_WORKSPACE env var)
# When running the server in a container with Docker socket mounted, this must
# be a path that exists on the host and is mounted into the server container.
WORKSPACE_DIR = os.environ.get("TAKO_VM_WORKSPACE", tempfile.gettempdir())

# Maximum number of runtime requirements to prevent env var overflow and slow startups
MAX_REQUIREMENTS = 50
