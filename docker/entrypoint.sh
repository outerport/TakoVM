#!/bin/bash
set -e

# Phase tracking file - Tako VM reads this to know which phase timed out
PHASE_FILE="/output/.tako_phase"

# Helper to get current time in milliseconds
get_time_ms() {
    # Use date with nanoseconds, convert to ms
    echo $(($(date +%s%N) / 1000000))
}

# Initialize phase tracking
START_TOTAL=$(get_time_ms)
echo "container_start_ms=$START_TOTAL" > "$PHASE_FILE"

# ============================================================
# Phase 1: Dependency Installation (startup phase)
# ============================================================
echo "phase=startup" >> "$PHASE_FILE"
START_STARTUP=$(get_time_ms)

if [ -n "$TAKO_REQUIREMENTS" ]; then
    echo "dep_install_started=true" >> "$PHASE_FILE"

    # Write requirements to a temporary file for safer handling
    # This avoids shell word splitting issues with package specifiers
    REQS_FILE=$(mktemp)
    echo "$TAKO_REQUIREMENTS" | tr ',' '\n' > "$REQS_FILE"

    # Install with uv to a writable target directory (avoids read-only filesystem issues)
    # Using --target instead of --system to install to /tmp/site-packages
    TARGET_DIR="/tmp/site-packages"
    mkdir -p "$TARGET_DIR"

    # Capture uv's chatter (resolution + per-package "+ pkg==ver" lines) so
    # it doesn't pollute the user-visible stdout. uv writes everything to
    # stderr; on success we drop it, on failure we forward it so callers
    # can see *why* installation failed.
    DEP_LOG=$(mktemp)
    set +e
    uv pip install --target "$TARGET_DIR" --link-mode=copy -r "$REQS_FILE" >"$DEP_LOG" 2>&1
    DEP_EXIT_CODE=$?
    set -e

    # Set PYTHONPATH so Python can find the installed packages
    export PYTHONPATH="$TARGET_DIR:$PYTHONPATH"

    # Cleanup
    rm -f "$REQS_FILE"

    # Record dep install completion
    END_DEP=$(get_time_ms)
    echo "dep_install_ms=$((END_DEP - START_STARTUP))" >> "$PHASE_FILE"
    echo "dep_install_exit_code=$DEP_EXIT_CODE" >> "$PHASE_FILE"

    # Exit if dep install failed (forward captured log to stderr first).
    if [ $DEP_EXIT_CODE -ne 0 ]; then
        cat "$DEP_LOG" >&2
        rm -f "$DEP_LOG"
        echo "phase=failed" >> "$PHASE_FILE"
        echo "failed_phase=startup" >> "$PHASE_FILE"
        exit $DEP_EXIT_CODE
    fi
    rm -f "$DEP_LOG"
else
    echo "dep_install_started=false" >> "$PHASE_FILE"
    echo "dep_install_ms=0" >> "$PHASE_FILE"
fi

END_STARTUP=$(get_time_ms)
echo "startup_ms=$((END_STARTUP - START_STARTUP))" >> "$PHASE_FILE"

# ============================================================
# Phase 2: Code Execution
# ============================================================
echo "phase=execution" >> "$PHASE_FILE"
START_EXEC=$(get_time_ms)
echo "execution_start_ms=$START_EXEC" >> "$PHASE_FILE"

# Redirect library cache/config dirs onto the writable /tmp tmpfs. The root
# filesystem is mounted --read-only and the sandbox user's HOME (/home/sandbox)
# lives there, so any library that wants $HOME/.cache (ezdxf, matplotlib,
# fontconfig, ...) would otherwise fail to create it and warn on every run.
export XDG_CACHE_HOME=/tmp/.cache
export MPLCONFIGDIR=/tmp/.cache/matplotlib
mkdir -p "$XDG_CACHE_HOME" "$MPLCONFIGDIR"
chmod -R 777 /tmp/.cache

# Drop privileges and run user code as sandbox user
# Using exec replaces this process, so we need a wrapper to capture timing
gosu sandbox python -u /code/main.py
EXEC_EXIT_CODE=$?

# Record execution completion
END_EXEC=$(get_time_ms)
echo "execution_ms=$((END_EXEC - START_EXEC))" >> "$PHASE_FILE"
echo "execution_exit_code=$EXEC_EXIT_CODE" >> "$PHASE_FILE"

# Final phase marker
if [ $EXEC_EXIT_CODE -eq 0 ]; then
    echo "phase=completed" >> "$PHASE_FILE"
else
    echo "phase=failed" >> "$PHASE_FILE"
    echo "failed_phase=execution" >> "$PHASE_FILE"
fi

# Total time
END_TOTAL=$(get_time_ms)
echo "total_ms=$((END_TOTAL - START_TOTAL))" >> "$PHASE_FILE"

exit $EXEC_EXIT_CODE
