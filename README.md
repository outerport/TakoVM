<p align="center">
  <img src="assets/logo.png" alt="Tako VM" width="256">
</p>

<p align="center">
  <strong>Run untrusted Python safely. Job queues and Docker isolation built-in.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/tako-vm/"><img src="https://img.shields.io/pypi/v/tako-vm" alt="PyPI"></a>
  <a href="https://github.com/las7/TakoVM/actions"><img src="https://github.com/las7/TakoVM/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/las7/TakoVM/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue" alt="License"></a>
</p>

Run AI-generated code in isolated Docker containers with gVisor sandboxing.
Job queues, retries, and execution history included.

**Requires:** [Docker](https://docs.docker.com/get-docker/) and Python 3.9+

```bash
pip install tako-vm        # install the package
tako-vm setup              # pull the executor Docker image (~30s one-time)
python -c "
from tako_vm import Sandbox
with Sandbox() as sb:
    result = sb.run('print(1 + 1)')
    print(result.stdout)   # 2
"
```

## Why Tako VM?

Sandbox solutions like [e2b](https://e2b.dev) and [microsandbox](https://github.com/microsandbox/microsandbox) give you isolated code execution—but that's it. You still need to build:

| You build | With sandbox-only | With Tako VM |
|-----------|-------------------|--------------|
| Job queue | Redis + Celery/Bull | Built-in |
| Execution history | Postgres + schema | PostgreSQL included |
| Retry logic | Custom code | Automatic |
| Idempotency | Deduplication logic | `idempotency_key` |
| Replay/debugging | Custom tooling | Rerun/fork API |

**Tako VM is the complete package:**

- **Job queue + workers** - Async execution with worker pool, no Redis/Celery setup
- **Execution history** - Every job persisted with stdout, stderr, timing, artifacts
- **Replay to debug** - Rerun past jobs with exact same code and inputs
- **Docker isolation** - Each job in its own container with seccomp filtering
- **Network isolation** - No network by default, optional allowlist per job type
- **Self-hosted** - Your machine, offline-capable, zero per-execution cost

## Installation

### Prerequisites

- Docker 20.10+
- Python 3.9+

### From PyPI

```bash
uv pip install tako-vm    # or: pip install tako-vm
tako-vm setup             # pulls the executor Docker image from GHCR
```

`tako-vm setup` checks that Docker is running, pulls the executor image, and verifies it works.

### For server mode (adds FastAPI + uvicorn)

```bash
uv pip install "tako-vm[server]"
```

### From source (development)

```bash
git clone https://github.com/las7/TakoVM.git && cd TakoVM
uv sync --all-extras
docker build -t code-executor:latest -f docker/Dockerfile.executor .
```

## Quick Start: Library Mode

No server or database needed — just Docker.

```bash
uv pip install tako-vm
tako-vm setup
```

```python
from tako_vm import Sandbox

# Basic execution
with Sandbox() as sb:
    result = sb.run("print('Hello from sandbox!')")
    print(result.stdout)

# With dependencies (installed via uv, cached for speed)
with Sandbox() as sb:
    result = sb.run("""
import pandas as pd
print(pd.__version__)
""", requirements=["pandas"])

# With input/output data
with Sandbox() as sb:
    result = sb.run("""
import json
with open("/input/data.json") as f:
    data = json.load(f)
result = {"sum": data["x"] + data["y"]}
with open("/output/result.json", "w") as f:
    json.dump(result, f)
""", input_data={"x": 10, "y": 20})
    print(result.output)  # {"sum": 30}

# Network access (disabled by default)
with Sandbox(network_enabled=True) as sb:
    result = sb.run("""
import urllib.request
resp = urllib.request.urlopen("https://httpbin.org/get")
print(resp.status)
""")
```

Your code runs in a container with these paths:
- `/input/data.json` - Your `input_data` as JSON (read-only)
- `/output/` - Write output files here, returned as `result.output`
- `/tmp/` - Temporary files (read-write)

## Quick Start: Server Mode

For production workloads with job queuing, retries, and execution history.

Requires Docker (for PostgreSQL + executor containers):

```bash
# Install with server dependencies
uv pip install "tako-vm[server]"
tako-vm setup

# Start server (auto-starts local PostgreSQL via Docker)
tako-vm server
```

`tako-vm server` automatically pulls and starts a PostgreSQL container on port 55432 when no database is configured. To manage the database separately:

```bash
tako-vm dev up           # Start PostgreSQL only
tako-vm dev status       # Check if it's running
tako-vm dev down         # Stop it
```

```bash
# Execute code via API
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"code": "print(1 + 1)", "requirements": ["requests"]}'
```

For an existing PostgreSQL instance:

```bash
export TAKO_VM_DATABASE_URL=postgresql://user:pass@host:5432/tako_vm
tako-vm server
```

## SDK

```python
from dataclasses import dataclass
import tako_vm

tako_vm.configure('http://localhost:8000')

@dataclass
class Input:
    x: int
    y: int

@dataclass
class Output:
    result: int

def add(input: Input) -> Output:
    return Output(result=input.x + input.y)

result = tako_vm.send(add, Input(10, 20))
print(result.result)  # 30
```

## CLI

```bash
tako-vm setup                     # Pull executor image and verify Docker
tako-vm server                    # Start the API server
tako-vm server --port 9000        # Custom port
tako-vm dev up                    # Start local PostgreSQL for development
tako-vm dev up --with-server      # Start PostgreSQL + API server
tako-vm dev status                # Check local PostgreSQL status
tako-vm dev down                  # Stop local PostgreSQL
tako-vm config                    # Show current configuration
tako-vm config --json             # Output as JSON
tako-vm validate                  # Validate current config
tako-vm validate my.yaml          # Validate specific file
tako-vm status                    # Check server health
tako-vm version                   # Show version
tako-vm --config my.yaml server   # Use specific config file
```

## Configuration

Tako VM uses YAML configuration with Pydantic validation. All values have sensible defaults.

```yaml
# tako_vm.yaml
production_mode: false
max_workers: 4
default_timeout: 30
max_timeout: 300
```

### Config file search order

1. `TAKO_VM_CONFIG` environment variable
2. `./tako_vm.yaml`
3. `./config/tako_vm.yaml`
4. `~/.tako_vm/config.yaml`
5. `/etc/tako_vm/config.yaml`

### Environment variables

All optional — override config file values or built-in defaults.

```bash
export TAKO_VM_CONFIG=/path/to/config.yaml
export TAKO_VM_DATA_DIR=/var/lib/tako_vm
export TAKO_VM_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/tako_vm
export TAKO_VM_SECURITY_MODE=permissive   # strict or permissive
export TAKO_VM_API_RATE_LIMIT_ENABLED=true
```

### Container limits

```yaml
container_limits:
  pids_limit: 100        # max processes (10-1000)
  nofile_soft: 256       # file descriptors (64-65536)
  nofile_hard: 256
  nproc_soft: 50         # process limit (10-1000)
  nproc_hard: 50
  fsize: 104857600       # max file size: 100MB (1MB-1GB)
  tmpfs_size: "100m"     # /tmp size (10m-2g)
```

See [tako_vm.yaml.example](tako_vm.yaml.example) for all options.

## Job Types

Job types are pre-configured execution environments with specific dependencies and limits.

### Built-in types

| Type | Packages | Network | Use Case |
|------|----------|---------|----------|
| `default` | stdlib only | isolated | Simple scripts |
| `data-processing` | pandas, numpy | isolated | Data manipulation |
| `ml-inference` | numpy, scikit-learn | isolated | ML inference |
| `api-client` | requests, httpx | enabled | External API calls |

### Custom job types

```yaml
job_types:
  - name: data-processing
    requirements:
      - pandas
      - numpy
    memory_limit: "1g"
    cpu_limit: 2.0
    timeout: 60

  - name: api-client
    requirements:
      - requests
      - httpx
    network_enabled: true
```

### How dependencies work

Tako VM uses **runtime dependency installation** with [uv](https://github.com/astral-sh/uv):

1. A single base image (`code-executor:latest`) handles all job types
2. When a job runs, dependencies are installed via `uv pip install` (~10x faster than pip)
3. Dependencies are cached in a Docker volume for repeated installs

For true network isolation with dependencies, pre-build images:

```bash
tako-vm build job-type data-processing
```

## API

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/execute` | POST | Execute code synchronously |
| `/execute/async` | POST | Submit job, returns job ID |
| `/jobs/{id}` | GET | Get job status |
| `/jobs/{id}/result` | GET | Wait for job result |
| `/jobs/{id}/cancel` | POST | Cancel pending/running job |
| `/jobs/{id}/rerun` | POST | Rerun with same code and inputs |
| `/jobs/{id}/fork` | POST | Fork with new code, same inputs |
| `/jobs/{id}/artifacts/{name}` | GET | Download artifact |
| `/job-types` | GET | List available job types |
| `/health` | GET | Health check |
| `/pool/stats` | GET | Worker pool status |
| `/dlq/stats` | GET | Dead letter queue stats |

### Timing

Every response includes a timing breakdown:

```json
{
  "status": "succeeded",
  "timing": {
    "startup_ms": 2500,
    "dep_install_ms": 2100,
    "execution_ms": 150,
    "total_ms": 2650,
    "phase_at_exit": "completed"
  }
}
```

Timeouts are configured separately for startup (`startup_timeout`) and code execution (`timeout`).

### Idempotency

```bash
curl -X POST http://localhost:8000/execute/async \
  -H "Content-Type: application/json" \
  -d '{"code": "...", "input_data": {}, "idempotency_key": "my-unique-key"}'
```

See [docs/api/rest.md](docs/api/rest.md) for complete API reference.

## Security

**Layers applied to every container:**

| Feature | Description |
|---------|-------------|
| gVisor Runtime | Userspace kernel isolation (default, `runsc`) |
| Network Isolation | `--network=none` by default |
| Read-Only Filesystem | `--read-only` with tmpfs for /tmp |
| Seccomp Filtering | Blocks 47+ dangerous syscalls |
| Resource Limits | Memory, CPU, file size, process count |
| Non-Root Execution | Code runs as uid 1000 via gosu |
| Capability Drop | `--cap-drop=ALL` (except SETUID/SETGID for gosu) |

### Security modes

```yaml
# Production (require gVisor)
security_mode: strict
container_runtime: runsc

# Development (allow fallback if gVisor not available)
security_mode: permissive
container_runtime: runsc
```

See [docs/security/honest-assessment.md](docs/security/honest-assessment.md) for threat model analysis.

## Development

### Running tests

```bash
# Clone and install
git clone https://github.com/las7/TakoVM.git && cd TakoVM
uv sync --all-extras
docker build -t code-executor:latest -f docker/Dockerfile.executor .

# Run tests without PostgreSQL (skips DB-dependent tests)
TAKO_VM_SECURITY_MODE=permissive pytest tests/ -v

# Run full test suite with PostgreSQL
tako-vm dev up
TAKO_VM_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/tako_vm \
  TAKO_VM_SECURITY_MODE=permissive pytest tests/ -v

# Lint
ruff check tako_vm/ tests/
ruff format --check tako_vm/ tests/
```

### Docker images

Pre-built images are published to GHCR on every release:

```bash
# Executor (used by Sandbox to run code)
docker pull ghcr.io/las7/takovm/executor:latest

# Server (API server with all dependencies)
docker pull ghcr.io/las7/takovm/server:latest
```

### Docker Compose

For a full local stack (server + PostgreSQL):

```bash
docker compose up -d
curl http://localhost:8000/health
```

## Project Structure

```
tako-vm/
├── tako_vm/
│   ├── server/              # HTTP API layer
│   │   ├── app.py           # FastAPI application
│   │   ├── queue.py         # Worker pool & job queue
│   │   └── limits.py        # Rate limiting & payload protection
│   ├── execution/           # Docker execution layer
│   │   ├── worker.py        # Container executor
│   │   └── builder.py       # Image builder (for pre-built images)
│   ├── sdk/                 # Python SDK
│   │   └── client.py        # TakoVM client
│   ├── cli.py               # CLI entry point
│   ├── config.py            # Pydantic configuration
│   ├── models.py            # Data models
│   ├── sandbox.py           # Direct Docker execution (library mode)
│   ├── storage.py           # PostgreSQL persistence
│   └── job_types.py         # Job type definitions
├── docker/
│   ├── Dockerfile.executor  # Base executor image
│   ├── Dockerfile.server    # API server image
│   └── entrypoint.sh        # Container entrypoint
├── tests/                   # Test suite (230+ tests)
├── docs/                    # Documentation
└── tako_vm.yaml.example     # Example configuration
```

## License

Apache License 2.0
