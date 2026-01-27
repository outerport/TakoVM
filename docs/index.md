# Tako VM

**Run AI-generated Python code safely. Local-first, no cloud required.**

Tako VM executes untrusted Python code in isolated Docker containers with enterprise-grade security. Use it as a library or deploy as a server.

## Quick Start (Library Mode)

```bash
pip install tako-vm
```

```python
from tako_vm import Sandbox

with Sandbox() as sb:
    result = sb.run("print(1 + 1)")
    print(result.stdout)  # "2"
```

No server setup required. The Docker image builds automatically on first run.

## Features

- **Library-First** - Use as a Python library or deploy as a server
- **Local-First** - No cloud account, no per-execution fees
- **Secure Isolation** - Network isolation, non-root execution, seccomp filtering
- **Fast Dependencies** - Runtime package installation via uv (~10x faster than pip)
- **Audit Trail** - Full execution records with timing and artifacts

## Installation

```bash
pip install tako-vm              # Library mode (Sandbox class)
pip install tako-vm[server]      # Server mode (REST API)
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
│   Client    │────▶│  Tako VM    │────▶│   Docker Container  │
│  (HTTP/SDK) │     │   Server    │     │   (Isolated)        │
└─────────────┘     └─────────────┘     └─────────────────────┘
                          │
                          ▼
                    ┌─────────────┐
                    │   SQLite    │
                    │  (Records)  │
                    └─────────────┘
```

## Next Steps

- [Installation](getting-started/installation.md) - Set up Tako VM
- [Quick Start](getting-started/quickstart.md) - Run your first code
- [API Reference](api/rest.md) - Full API documentation
