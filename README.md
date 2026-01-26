# Tako VM - Secure Code Executor

A production-ready, secure Python code execution system that runs untrusted code in isolated Docker containers.

## Overview

Tako VM executes Python code in isolated Docker containers with:
- **Security** - Network isolation, read-only filesystem, seccomp filtering, resource limits
- **Concurrency** - Worker pool with configurable parallelism
- **Authentication** - API key management with rate limiting
- **Audit Trail** - Full execution records with timing, artifacts, and error details
- **Typed SDK** - Execute Python functions with dataclass input/output

## Installation

### Prerequisites

- Docker 20.10+
- Python 3.9+

### Option 1: Install from PyPI

```bash
# SDK client only (for connecting to a Tako VM server)
pip install tako-vm

# Full installation with server
pip install tako-vm[server]
```

### Option 2: Install from Source

```bash
git clone https://github.com/example/tako-vm.git
cd tako-vm

# SDK only
pip install -e .

# Full server installation
pip install -e ".[server]"
```

### Build the Docker Image

```bash
docker build -t code-executor:latest .
```

### 2. Start the Server

```bash
python run_server.py
```

### 3. Execute Code

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

## Configuration

Tako VM loads configuration from YAML files. Create `tako_vm.yaml` in your project directory:

```yaml
# tako_vm.yaml
production_mode: false
require_auth: false
max_workers: 4
max_queue_size: 100
default_timeout: 30
max_timeout: 300
```

**Config search order:**
1. `./tako_vm.yaml`
2. `./config/tako_vm.yaml`
3. `~/.tako_vm/config.yaml`
4. `/etc/tako-vm/config.yaml`

See `tako_vm.yaml.example` for all options.

## Execution Environments

An **execution environment** (job type) is a pre-configured Docker container with specific packages:

| Environment | Packages | Use Case |
|-------------|----------|----------|
| `default` | Python stdlib | Simple scripts |
| `data-processing` | pandas, numpy | Data manipulation |
| `ml-inference` | numpy, scikit-learn | ML inference |

```python
# Use a specific environment
response = requests.post('http://localhost:8000/execute', json={
    'code': '...',
    'input_data': {...},
    'job_type': 'data-processing'
})
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/execute` | POST | Execute code synchronously |
| `/execute/async` | POST | Submit job, returns job ID |
| `/jobs/{id}` | GET | Get job status |
| `/jobs/{id}/result` | GET | Wait for job result |
| `/jobs/{id}/cancel` | POST | Cancel pending/running job |
| `/job-types` | GET | List environments |
| `/health` | GET | Health check |

## Authentication

Enable authentication in config:

```yaml
require_auth: true
```

Create API keys via the API or programmatically:

```python
from tako_vm.server.auth import APIKeyManager
from pathlib import Path

manager = APIKeyManager(Path("~/.tako_vm/api_keys.json").expanduser())
raw_key, api_key = manager.create_key("my-project")
print(f"API Key: {raw_key}")  # Save this - shown only once
```

Use the key in requests:

```bash
curl -H "Authorization: Bearer tvmk_..." http://localhost:8000/execute ...
```

## Security

| Feature | Description |
|---------|-------------|
| Network Isolation | `--network=none` |
| Read-Only Filesystem | `--read-only` with tmpfs for /tmp |
| Seccomp Filtering | Syscall whitelist |
| Resource Limits | Memory, CPU, file size, process count |
| Non-Root | Runs as `sandbox` user (uid 1000) |
| Ephemeral | Container destroyed after each execution |

## Project Structure

```
tako-vm/
├── tako_vm/
│   ├── server/              # HTTP API layer
│   │   ├── app.py           # FastAPI application
│   │   ├── auth.py          # API key auth & rate limiting
│   │   └── queue.py         # Worker pool & job queue
│   ├── execution/           # Docker execution layer
│   │   ├── worker.py        # Container executor
│   │   └── builder.py       # Image builder
│   ├── sdk/                 # Python SDK
│   │   └── client.py        # TakoVM client
│   ├── config.py            # Configuration loader
│   ├── models.py            # Data models
│   ├── storage.py           # SQLite persistence
│   └── job_types.py         # Environment definitions
├── tako_vm.yaml.example     # Example configuration
├── Dockerfile               # Base container image
└── requirements.txt
```

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

## License

MIT
