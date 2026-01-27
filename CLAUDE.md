# Tako VM

Secure Python code execution in isolated Docker containers. REST API for sync/async job execution.

## Architecture

```
tako_vm/
├── server/app.py        # FastAPI routes
├── server/queue.py      # WorkerPool, async jobs
├── execution/worker.py  # CodeExecutor (runs Docker containers)
├── execution/builder.py # ContainerBuilder (for pre-built images)
├── config.py            # Pydantic config (TakoVMConfig)
├── models.py            # ExecutionRecord, JobStatus
├── storage.py           # SQLite persistence
docker/
├── Dockerfile.executor  # Base executor image (uv + gosu)
├── Dockerfile.server    # API server image
├── entrypoint.sh        # Installs deps at runtime, runs code as sandbox user
```

## Key Concepts

- **Runtime deps**: Dependencies installed via `uv pip install` at container startup (fast!)
- **ExecutionRecord** status: `queued`, `running`, `succeeded`, `failed`, `timeout`, `oom`, `cancelled`
- **Queue job** status: `pending`, `running`, `completed` (different from ExecutionRecord)
- **UV_CACHE_VOLUME**: Docker volume `tako-uv-cache` speeds up repeated installs
- Tests use temp database via `TAKO_VM_DATA_DIR` env var for isolation

## Build & Test

```bash
# One-time: build executor image
docker build -t code-executor:latest -f docker/Dockerfile.executor .

# Run tests
pytest tests/ -v

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

- **ImageNotFound: code-executor:latest** -> Build image first (see above)
- **sqlite3.OperationalError: no such column** -> Delete `~/.tako_vm/executions.db` (schema changed)
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
