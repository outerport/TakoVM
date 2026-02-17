# Tako VM

Secure Python code execution in isolated Docker containers. REST API for sync/async job execution.

## Architecture

```
tako_vm/
├── server/app.py        # FastAPI routes
├── server/queue.py      # WorkerPool, async jobs
├── execution/worker.py  # CodeExecutor (runs Docker containers)
├── execution/docker.py  # Docker utilities (container naming, cleanup)
├── execution/builder.py # ContainerBuilder (for pre-built images)
├── config.py            # Pydantic config (TakoVMConfig)
├── models.py            # ExecutionRecord, JobStatus
├── storage.py           # PostgreSQL persistence
docker/
├── Dockerfile.executor  # Base executor image (uv + gosu)
├── Dockerfile.server    # API server image
├── entrypoint.sh        # Installs deps at runtime, runs code as sandbox user, writes timing to /output/.tako_phase
lima-gvisor.yaml         # Lima VM config for macOS/Windows development with gVisor
```

## Key Concepts

- **gVisor by default**: Uses `runsc` runtime for strong isolation (userspace kernel). Required in `strict` mode.
- **Security modes**: `strict` (default) fails if gVisor unavailable; `permissive` allows fallback to runc
- **Runtime deps**: Dependencies installed via `uv pip install` at container startup (fast!)
- **ExecutionRecord** status: `queued`, `running`, `succeeded`, `failed`, `timeout`, `oom`, `cancelled`
- **ExecutionRecord.timing**: Phase breakdown (startup, execution durations) from `/output/.tako_phase`
- **Timeouts**: `startup_timeout` (dep install) vs `timeout` (code execution) - configured separately
- **Queue job** status: `pending`, `running`, `completed` (different from ExecutionRecord)
- **UV_CACHE_VOLUME**: Docker volume `tako-uv-cache` speeds up repeated installs
- Tests use temp database via `TAKO_VM_DATA_DIR` env var for isolation

## Code Quality

**IMPORTANT**: Always run lint checks before completing any Python code changes.

```bash
# Run ruff linter (required before committing)
ruff check tako_vm/ tests/

# Auto-fix lint issues
ruff check --fix tako_vm/ tests/

# Format code
ruff format tako_vm/ tests/
```

When modifying Python code:
1. Run `ruff check` on changed files before considering the task complete
2. Fix any lint errors before committing
3. If lint errors cannot be resolved, explain why and get user approval

## Build & Test

```bash
# One-time: install gVisor (required for production)
# See: https://gvisor.dev/docs/user_guide/install/

# One-time: build executor image
docker build -t code-executor:latest -f docker/Dockerfile.executor .

# Run tests (use permissive mode if gVisor not installed)
TAKO_VM_SECURITY_MODE=permissive pytest tests/ -v

# Start server
tako-vm server --port 8000
```

## Dependency Flow

1. Job submitted with `job_type: "data-processing"` (has `requirements: [pandas, numpy]`)
2. Worker passes requirements via `TAKO_REQUIREMENTS` env var
3. Container starts, `entrypoint.sh` runs `uv pip install pandas numpy`
4. Code runs as `sandbox` user (uid 1000) via `gosu`

For network-isolated jobs with deps, use pre-built images:
```bash
tako-vm build job-type data-processing
# Then set base_image in job type config
```

## Common Issues

- **RuntimeUnavailableError: gVisor not available** -> Install gVisor: https://gvisor.dev/docs/user_guide/install/ or set `security_mode: permissive` for dev
- **ImageNotFound: code-executor:latest** -> Build image first (see above)
- **PostgreSQL migration/connection errors** -> Verify `database_url` and database reachability
- **Test isolation** -> Use `reset_config()` and temp database pattern from `tests/test_api.py`
- **Dep install fails (network)** -> Jobs with requirements need network; use pre-built for true isolation

## Debugging

```bash
curl http://localhost:8000/health           # Health check
curl http://localhost:8000/pool/stats       # Worker pool status
curl http://localhost:8000/dlq/stats        # Dead letter queue
```

## References

- [README.md](README.md) - Full docs, CLI commands, job types
- [docs/api/rest.md](docs/api/rest.md) - API reference
- [tako_vm.yaml.example](tako_vm.yaml.example) - Config options
