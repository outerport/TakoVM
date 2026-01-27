#!/bin/bash
set -e

# Install runtime dependencies if TAKO_REQUIREMENTS is set
# Format: comma-separated list of pip requirements (e.g., "pandas,numpy>=1.20,requests")
if [ -n "$TAKO_REQUIREMENTS" ]; then
    # Write requirements to a temporary file for safer handling
    # This avoids shell word splitting issues with package specifiers
    REQS_FILE=$(mktemp)
    echo "$TAKO_REQUIREMENTS" | tr ',' '\n' > "$REQS_FILE"

    # Install with uv to a writable target directory (avoids read-only filesystem issues)
    # Using --target instead of --system to install to /tmp/site-packages
    TARGET_DIR="/tmp/site-packages"
    mkdir -p "$TARGET_DIR"
    uv pip install --target "$TARGET_DIR" --no-cache --link-mode=copy -r "$REQS_FILE"

    # Set PYTHONPATH so Python can find the installed packages
    export PYTHONPATH="$TARGET_DIR:$PYTHONPATH"

    # Cleanup
    rm -f "$REQS_FILE"
fi

# Drop privileges and run user code as sandbox user
exec gosu sandbox python -u /code/main.py
