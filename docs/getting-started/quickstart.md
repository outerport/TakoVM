# Quick Start

This guide walks you through executing your first code in Tako VM.

## Library Mode (Recommended for Development)

The simplest way to use Tako VM - no server required:

```bash
uv pip install tako-vm
```

```python
from tako_vm import Sandbox

# Basic execution
with Sandbox() as sb:
    result = sb.run("print('Hello from sandbox!')")
    print(result.stdout)  # "Hello from sandbox!"

# With dependencies
with Sandbox() as sb:
    result = sb.run("""
import requests
print(f"requests version: {requests.__version__}")
""", requirements=["requests"])
    print(result.stdout)

# With input/output data
with Sandbox() as sb:
    result = sb.run("""
import json
with open("/input/data.json") as f:
    data = json.load(f)
result = {"sum": data["x"] + data["y"]}
with open("/output/result.json", "w") as f:
    json.dump(result, f)
print("Done!")
""", input_data={"x": 10, "y": 20})

    print(result.stdout)   # "Done!"
    print(result.output)   # {"sum": 30}
```

The first run builds the Docker image automatically (~30 seconds one-time setup).

## Server Mode (For Production)

For production workloads with job queuing, retries, and persistence:

### Start the Server

```bash
# Build the executor image first
docker build -t code-executor:latest -f docker/Dockerfile.executor .

# Start the server
tako-vm server
```

The server starts on `http://localhost:8000` by default. Use `--port` to change:

```bash
tako-vm server --port 9000
```

### Execute Code via API

#### Using curl

```bash
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "print(1 + 1)",
    "requirements": ["requests"]
  }'
```

#### Using Python

```python
import requests

code = """
import json

# Read input from /input/data.json
with open("/input/data.json") as f:
    data = json.load(f)

# Process
result = {
    "sum": data["x"] + data["y"],
    "product": data["x"] * data["y"]
}

# Write output to /output/result.json
with open("/output/result.json", "w") as f:
    json.dump(result, f)

print("Done!")
"""

response = requests.post(
    "http://localhost:8000/execute",
    json={
        "code": code,
        "input_data": {"x": 10, "y": 20},
        "requirements": ["numpy"]  # Optional: ad-hoc dependencies
    }
)

result = response.json()
print(f"Success: {result['success']}")
print(f"Output: {result['output']}")
print(f"Stdout: {result['stdout']}")
```

## Understanding the Execution Model

Your code runs in an isolated Docker container with three mounted directories:

| Path | Permission | Purpose |
|------|------------|---------|
| `/input/data.json` | Read-only | Input data (JSON) |
| `/output/` | Read-write | Output files |
| `/code/main.py` | Read-only | Your code |

```
Container Filesystem
├── /input/
│   └── data.json      ← Your input_data as JSON
├── /output/
│   └── result.json    ← Write your output here
├── /code/
│   └── main.py        ← Your code
└── /tmp/              ← Temporary files (writable)
```

## Response Format

```json
{
  "success": true,
  "output": {"sum": 30, "product": 200},
  "stdout": "Done!\n",
  "stderr": "",
  "exit_code": 0,
  "execution_time": 0.35,
  "job_type": "default"
}
```

| Field | Description |
|-------|-------------|
| `success` | Whether execution completed without errors |
| `output` | Contents of `/output/result.json` (parsed) |
| `stdout` | Standard output from your code |
| `stderr` | Standard error from your code |
| `exit_code` | Process exit code (0 = success) |
| `execution_time` | Wall clock time in seconds |
| `job_type` | Environment used |

## Next Steps

- [Configuration](configuration.md) - Customize Tako VM settings
- [Environments](../guide/environments.md) - Use different packages
- [Async Jobs](../guide/async-jobs.md) - Long-running tasks
