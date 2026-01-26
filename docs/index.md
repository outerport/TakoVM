# Tako VM

**Secure Python Code Execution System**

Tako VM executes untrusted Python code in isolated Docker containers with enterprise-grade security, concurrency, and audit logging.

## Features

- **Secure Isolation** - Network isolation, non-root execution, seccomp filtering
- **Job Types** - Pre-configured environments with specific packages and limits
- **Worker Pool** - Concurrent execution with configurable parallelism
- **Audit Trail** - Full execution records with timing and artifacts
- **Network Control** - Per-job-type network access with domain allowlists
- **Typed SDK** - Python client with dataclass serialization

## Installation

```bash
# Install from PyPI
pip install tako-vm            # SDK client only
pip install tako-vm[server]    # Full server installation

# Build the Docker image
docker build -t code-executor:latest .

# Start the server
tako-vm server
```

## Quick Example

```python
import requests

response = requests.post('http://localhost:8000/execute', json={
    'code': '''
import json
with open("/input/data.json") as f:
    data = json.load(f)
result = {"sum": data["x"] + data["y"]}
with open("/output/result.json", "w") as f:
    json.dump(result, f)
''',
    'input_data': {'x': 10, 'y': 20}
})

print(response.json())
# {'success': True, 'output': {'sum': 30}, ...}
```

Or use the typed SDK:

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
