# Tako VM - Secure Code Executor

A secure, isolated Python code execution system that runs AI-generated code in containerized environments.

## Overview

Tako VM executes untrusted Python code in isolated Docker containers with:
- Complete network isolation
- Read-only filesystem
- Configurable resource limits
- **Execution Environments** - Pre-configured containers with different dependencies
- **Typed SDK** - Execute Python functions with dataclass input/output

## What is an Execution Environment?

An **execution environment** (internally called "job type") is a pre-configured Docker container with specific Python packages installed. Instead of installing packages at runtime (slow), you define environments upfront:

| Environment | Pre-installed Packages | Use Case |
|-------------|----------------------|----------|
| `default` | Python stdlib only | Simple scripts, no dependencies |
| `data-processing` | pandas, numpy | Data manipulation |
| `ml-inference` | numpy, scikit-learn | Machine learning |

**Key benefit**: Containers are built once and reused. Each execution spawns a fresh container instance from the pre-built image (~100ms startup), not a fresh build.

```
BUILD (one-time)                    EXECUTE (per-request, fast)
────────────────                    ────────────────────────────
Build image with pandas/numpy  →    Spawn container instance
                                    Execute code
                                    Return results
                                    Destroy container
```

## Quick Start

### Prerequisites

- Docker 20.10+
- Python 3.11+

### 1. Build the Default Container

```bash
docker build -t code-executor:latest .
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the Server

```bash
python run_server.py
```

### 4. Run an Example

```bash
python examples/example_tako_vm.py
```

## Usage

### Typed SDK (Recommended)

```python
from dataclasses import dataclass
import tako_vm

@dataclass
class Input:
    x: int
    y: int

@dataclass
class Output:
    result: int

def add(input: Input) -> Output:
    return Output(result=input.x + input.y)

# Execute in isolated container
result = tako_vm.send(add, Input(x=10, y=20))
print(result.result)  # 30
```

### Using Different Environments

```python
import tako_vm
from dataclasses import dataclass

@dataclass
class StatsInput:
    values: list

@dataclass
class StatsOutput:
    mean: float

def compute_mean(input: StatsInput) -> StatsOutput:
    import numpy as np  # Available in data-processing environment
    return StatsOutput(mean=float(np.mean(input.values)))

# Uses the data-processing environment (has numpy)
result = tako_vm.send(compute_mean, StatsInput([1,2,3,4,5]), job_type="data-processing")
```

**Note**: If the environment's container doesn't exist, Tako VM will automatically build it on first use.

### Raw API

```bash
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import json\nwith open(\"/input/data.json\") as f: data=json.load(f)\nresult = {\"sum\": data[\"x\"] + data[\"y\"]}\nwith open(\"/output/result.json\", \"w\") as f: json.dump(result, f)",
    "input_data": {"x": 10, "y": 20}
  }'
```

## Creating Custom Environments

```python
from src.job_types import JobType, JobTypeRegistry

registry = JobTypeRegistry()
registry.register(JobType(
    name="web-scraping",
    requirements=["requests", "beautifulsoup4"],
    memory_limit="1g",
    timeout=60,
))

# The container will be auto-built on first use
```

## API Reference

### POST /execute

Execute Python code.

```json
{
  "code": "...",
  "input_data": {},
  "timeout": 30,
  "job_type": "data-processing"
}
```

### GET /job-types

List available execution environments.

### GET /health

Health check.

## SDK Reference

```python
# Execute function, returns typed output
tako_vm.send(func, input_data, timeout=None, job_type=None) -> OutputT

# Execute function, returns raw result (doesn't raise on failure)
tako_vm.send_raw(func, input_data, timeout=None, job_type=None) -> ExecutionResult

# List available environments
tako_vm.list_job_types() -> list

# Configure client
tako_vm.configure(base_url="http://localhost:8000", timeout=30)
```

## Security

| Feature | Description |
|---------|-------------|
| Network Isolation | `--network=none` |
| Read-Only Filesystem | `--read-only` |
| Resource Limits | Memory, CPU per environment |
| Non-Root | Runs as `sandbox` user |
| Ephemeral | Container destroyed after each execution |

## Project Structure

```
tako-vm/
├── src/                       # Core source code
│   ├── api_server.py          # FastAPI HTTP server
│   ├── worker.py              # Docker execution orchestrator
│   ├── tako_vm.py             # Typed Python SDK
│   ├── job_types.py           # Environment configuration
│   └── container_builder.py   # Docker image builder
├── tests/                     # Test files
├── examples/                  # Example scripts
│   ├── example_tako_vm.py     # SDK usage examples
│   └── example_api_client.py  # Raw API examples
├── custom_libs/               # Custom Python libraries
├── Dockerfile                 # Base container image
├── run_server.py              # Server entry point
├── tako_vm.py                 # SDK re-export for convenience
├── requirements.txt           # Dependencies
└── README.md
```

## Limitations

| Limitation | Details |
|------------|---------|
| No Network | Containers cannot make network requests |
| Sequential | One job at a time |
| No Auth | Anyone with access can execute code |
| Stdlib Types | SDK only supports JSON-serializable dataclasses |

See [LIMITATIONS.md](LIMITATIONS.md) for details.

## License

MIT
