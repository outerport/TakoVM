# Tako VM - Secure Code Executor

A production-ready, secure Python code execution system that runs untrusted code in isolated Docker containers.

## Overview

Tako VM executes Python code in isolated Docker containers with:
- **Security** - Network isolation, read-only filesystem, seccomp filtering, resource limits
- **Configurability** - Pydantic-validated YAML config with env var overrides
- **Job Types** - Pre-configured environments with network control per job type
- **Audit Trail** - Full execution records with timing, artifacts, and error details

## Quick Start

```zsh
# Clone and install with uv
git clone https://github.com/example/tako-vm.git && cd tako-vm
uv venv && source .venv/bin/activate
uv pip install -e ".[server]"

# Build base image and start
docker build -t code-executor:latest .
tako-vm server
```

Or run the interactive demo:
```bash
./demo.sh
```

## Installation

### Prerequisites

- Docker 20.10+
- Python 3.9+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### With uv (Recommended)

```zsh
uv venv && source .venv/bin/activate
uv pip install -e ".[server]"
docker build -t code-executor:latest .
```

### With pip

```bash
pip install tako-vm[server]
docker build -t code-executor:latest .
```

## CLI Commands

```bash
tako-vm --help                    # Show all commands
tako-vm server                    # Start the API server
tako-vm server --port 9000        # Custom port
tako-vm --config my.yaml server   # Use specific config file

tako-vm config                    # Show current configuration
tako-vm config --json             # Output as JSON
tako-vm validate                  # Validate current config
tako-vm validate my.yaml          # Validate specific file

tako-vm status                    # Check server health
tako-vm version                   # Show version
```

## Configuration

Tako VM uses YAML configuration with Pydantic validation. All values have sensible defaults.

### Quick Setup

```yaml
# tako_vm.yaml
production_mode: false
max_workers: 4
default_timeout: 30
max_timeout: 300
```

### Config File Search Order

1. `TAKO_VM_CONFIG` environment variable
2. `./tako_vm.yaml`
3. `./config/tako_vm.yaml`
4. `~/.tako_vm/config.yaml`
5. `/etc/tako_vm/config.yaml`

### Environment Variables

```bash
# Override config file location
export TAKO_VM_CONFIG=/path/to/config.yaml

# Override paths
export TAKO_VM_DATA_DIR=/var/lib/tako_vm
export TAKO_VM_DATABASE_FILE=/var/lib/tako_vm/db.sqlite
```

### Container Limits

Fine-grained control over container resources:

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

### Full Example

See [tako_vm.yaml.example](tako_vm.yaml.example) for all options with documentation.

## Job Types

Job types are pre-configured execution environments with specific dependencies and limits.

### Built-in Types

| Type | Packages | Network | Use Case |
|------|----------|---------|----------|
| `default` | stdlib only | isolated | Simple scripts |
| `data-processing` | pandas, numpy | isolated | Data manipulation |
| `ml-inference` | numpy, scikit-learn | isolated | ML inference |
| `api-client` | requests, httpx | enabled | External API calls |

### Define Custom Job Types

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
    allowed_hosts:
      - "api.openai.com"
      - "api.anthropic.com"
      - "*.amazonaws.com"
```

### Network Control

By default, containers have **no network access** (`--network=none`).

To enable network:
```yaml
job_types:
  - name: my-api-job
    network_enabled: true      # Allow outbound connections
    allowed_hosts:             # Optional: restrict to specific hosts
      - "api.example.com"
```

## API Usage

### Execute Code

```bash
# Simple execution
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"code": "print(1 + 1)", "input_data": {}}'

# With job type
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import pandas as pd; print(pd.__version__)",
    "input_data": {},
    "job_type": "data-processing"
  }'

# Async execution
curl -X POST http://localhost:8000/execute/async \
  -H "Content-Type: application/json" \
  -d '{"code": "...", "input_data": {}}'
# Returns: {"job_id": "abc123"}

# Get result
curl http://localhost:8000/jobs/abc123/result
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/execute` | POST | Execute code synchronously |
| `/execute/async` | POST | Submit job, returns job ID |
| `/jobs/{id}` | GET | Get job status |
| `/jobs/{id}/result` | GET | Wait for job result |
| `/jobs/{id}/cancel` | POST | Cancel pending/running job |
| `/job-types` | GET | List available job types |
| `/health` | GET | Health check |

## Security Features

| Feature | Description |
|---------|-------------|
| Network Isolation | `--network=none` by default |
| Read-Only Filesystem | `--read-only` with tmpfs for /tmp |
| Seccomp Filtering | Syscall whitelist via seccomp profile |
| Resource Limits | Memory, CPU, file size, process count |
| Non-Root Execution | Runs as uid 1000 inside container |
| Capability Drop | `--cap-drop=ALL` |
| No Privilege Escalation | `--security-opt=no-new-privileges` |

## SDK Usage

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

## Project Structure

```
tako-vm/
├── tako_vm/
│   ├── server/              # HTTP API layer
│   │   ├── app.py           # FastAPI application
│   │   └── queue.py         # Worker pool & job queue
│   ├── execution/           # Docker execution layer
│   │   ├── worker.py        # Container executor
│   │   └── builder.py       # Image builder
│   ├── sdk/                 # Python SDK
│   │   └── client.py        # TakoVM client
│   ├── cli.py               # CLI entry point
│   ├── config.py            # Pydantic configuration
│   ├── models.py            # Data models
│   ├── storage.py           # SQLite persistence
│   └── job_types.py         # Job type definitions
├── tako_vm.yaml.example     # Example configuration
├── demo.sh                  # Interactive demo script
├── Dockerfile.executor      # Sandbox container image
└── pyproject.toml           # Package definition
```

## License

MIT
