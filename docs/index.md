# Tako VM

**Job queue infrastructure for AI agents. Not just a sandbox.**

Other tools give you isolated code execution. Tako VM gives you the complete job system: queue, workers, execution history, retries, and replay—all in one package.

## Quick Start (Library Mode)

```bash
uv pip install tako-vm
```

```python
from tako_vm import Sandbox

with Sandbox() as sb:
    result = sb.run("print(1 + 1)")
    print(result.stdout)  # "2"
```

No server setup required. The Docker image builds automatically on first run.

## Why Tako VM?

Sandbox-only tools (e2b, microsandbox) give you isolated execution. You still need to build the job system yourself.

| Feature | Sandbox-only | Tako VM |
|---------|--------------|---------|
| Job queue | ❌ Build with Redis/Celery | ✅ Built-in |
| Execution history | ❌ Build with Postgres | ✅ SQLite included |
| Retries | ❌ Write retry logic | ✅ Automatic |
| Replay/debugging | ❌ Build custom tooling | ✅ Rerun/fork API |
| Idempotency | ❌ Implement deduplication | ✅ `idempotency_key` |

**Tako VM includes:**

- **Job queue + workers** - No Redis/Celery setup needed
- **Execution history** - Every job persisted with timing and artifacts
- **Replay to debug** - Rerun past jobs with exact same inputs
- **Docker isolation** - Seccomp filtering, network isolation
- **Self-hosted** - Zero per-execution cost, works offline

## Installation

```bash
uv pip install tako-vm              # Library mode (Sandbox class)
uv pip install tako-vm[server]      # Server mode (REST API)
```

## Library Mode Examples

```python
from tako_vm import Sandbox

# Basic execution
with Sandbox() as sb:
    result = sb.run("print('Hello!')")
    print(result.stdout)

# With dependencies
with Sandbox() as sb:
    result = sb.run("""
import pandas as pd
print(pd.__version__)
""", requirements=["pandas"])

# With local packages
sb = Sandbox(package_dirs=["./my_utils"])
result = sb.run("from my_utils import helper; helper.process()")
```

## Server Mode

For production workloads with job queuing, retries, and audit trails:

```bash
# Build image and start server
docker build -t code-executor:latest -f docker/Dockerfile.executor .
tako-vm server
```

```python
import requests

response = requests.post('http://localhost:8000/execute', json={
    'code': 'print(1 + 1)',
    'requirements': ['requests']
})
print(response.json())
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────────┐
│   Client    │────▶│  Tako VM    │────▶│   Docker + gVisor   │
│  (HTTP/SDK) │     │   Server    │     │   (Isolated)        │
└─────────────┘     └─────────────┘     └─────────────────────┘
                          │
                          ▼
                    ┌─────────────┐
                    │   SQLite    │
                    │  (Records)  │
                    └─────────────┘
```

**Security:** Tako VM uses gVisor (runsc) by default for strong container isolation with a userspace kernel.

## Next Steps

- [Installation](getting-started/installation.md) - Set up Tako VM
- [Quick Start](getting-started/quickstart.md) - Run your first code
- [Basic Execution](guide/basic-execution.md) - Input/output patterns, artifacts
- [Async Jobs](guide/async-jobs.md) - Long-running tasks, artifact downloads
- [API Reference](api/rest.md) - Full API documentation
