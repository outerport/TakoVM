# Execution Environments

Environments are pre-configured containers with specific Python packages installed.

## Why Environments?

Installing packages at runtime is slow. Environments pre-install packages so execution is fast:

```
WITHOUT Environments (slow)          WITH Environments (fast)
─────────────────────────           ────────────────────────
Start container                     Start container (pre-built)
pip install numpy pandas  ← SLOW    Execute code
Execute code                        Return results
Return results
```

## Available Environments

| Environment | Packages | Use Case |
|-------------|----------|----------|
| `default` | Python stdlib | Simple scripts |
| `data-processing` | pandas, numpy | Data manipulation |
| `ml-inference` | numpy, scikit-learn | ML inference |

## Using an Environment

Specify the `job_type` parameter:

```python
import requests

code = """
import json
import numpy as np

with open("/input/data.json") as f:
    data = json.load(f)

values = np.array(data["values"])

result = {
    "mean": float(np.mean(values)),
    "std": float(np.std(values)),
    "sum": float(np.sum(values))
}

with open("/output/result.json", "w") as f:
    json.dump(result, f)
"""

response = requests.post("http://localhost:8000/execute", json={
    "code": code,
    "input_data": {"values": [1, 2, 3, 4, 5]},
    "job_type": "data-processing"  # ← Uses numpy
})

print(response.json()["output"])
# {'mean': 3.0, 'std': 1.414..., 'sum': 15.0}
```

## List Environments

```python
response = requests.get("http://localhost:8000/job-types")

for env in response.json():
    print(f"{env['name']}:")
    print(f"  Packages: {env['requirements']}")
    print(f"  Memory: {env['memory_limit']}")
    print(f"  Timeout: {env['timeout']}s")
```

## Creating Custom Environments

Define environments in your code:

```python
from tako_vm.job_types import JobType, JobTypeRegistry

registry = JobTypeRegistry()

# Register a custom environment
registry.register(JobType(
    name="web-scraping",
    requirements=["requests", "beautifulsoup4", "lxml"],
    memory_limit="1g",
    cpu_limit=1.0,
    timeout=120,
))

# Register ML environment
registry.register(JobType(
    name="deep-learning",
    requirements=["torch", "transformers"],
    memory_limit="4g",
    cpu_limit=2.0,
    timeout=300,
))
```

## Building Environment Images

Containers are auto-built on first use (in dev mode). To pre-build:

```python
from tako_vm.execution.builder import ContainerBuilder
from tako_vm.job_types import JobTypeRegistry

registry = JobTypeRegistry()
builder = ContainerBuilder()

# Build all registered environments
results = builder.build_all(registry)

for name, success in results.items():
    status = "✓" if success else "✗"
    print(f"[{status}] {name}")
```

Or via command line:

```bash
python -m tako_vm.execution.builder --init-defaults all
```

## Environment Configuration

Full `JobType` options:

```python
JobType(
    name="custom-env",

    # Packages to install
    requirements=["numpy", "pandas>=2.0"],

    # Base image (default: python:3.11-slim)
    base_image="python:3.11-slim",
    python_version="3.11",

    # Resource limits
    memory_limit="2g",
    cpu_limit=2.0,
    timeout=60,

    # Environment variables
    environment={
        "NUMBA_CACHE_DIR": "/tmp",
        "OMP_NUM_THREADS": "2"
    },

    # Shared code to include
    shared_code=["./lib/helpers.py"],
)
```

## Production Mode

In production mode (`production_mode: true`):

- Auto-build is disabled
- All environments must be pre-built
- Requests for missing environments fail

```yaml
# tako_vm.yaml
production_mode: true
```

This ensures consistent, tested images in production.
