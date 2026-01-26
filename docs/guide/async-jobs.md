# Async Jobs

For long-running tasks, use async execution to avoid blocking.

## Sync vs Async

| Mode | Endpoint | Behavior |
|------|----------|----------|
| Sync | `POST /execute` | Blocks until complete |
| Async | `POST /execute/async` | Returns job ID immediately |

## Async Workflow

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  Submit  │────▶│  Get ID  │────▶│   Poll   │────▶│  Result  │
│   Job    │     │          │     │  Status  │     │          │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
```

## Submit Async Job

```python
import requests

response = requests.post("http://localhost:8000/execute/async", json={
    "code": """
import json
import time

with open("/input/data.json") as f:
    data = json.load(f)

# Simulate long processing
for i in range(10):
    print(f"Processing step {i+1}/10...")
    time.sleep(1)

with open("/output/result.json", "w") as f:
    json.dump({"processed": len(data["items"])}, f)
""",
    "input_data": {"items": list(range(100))}
})

job = response.json()
print(f"Job ID: {job['job_id']}")
print(f"Status: {job['status']}")
# Job ID: abc123-def456
# Status: pending
```

## Check Job Status

```python
job_id = "abc123-def456"

response = requests.get(f"http://localhost:8000/jobs/{job_id}")
status = response.json()

print(f"Status: {status['status']}")
# Status: running
```

### Status Values

| Status | Description |
|--------|-------------|
| `pending` | Queued, waiting for worker |
| `running` | Currently executing |
| `success` | Completed successfully |
| `error` | Failed with error |
| `timeout` | Exceeded time limit |
| `cancelled` | Cancelled by user |

## Wait for Result

Block until job completes:

```python
response = requests.get(
    f"http://localhost:8000/jobs/{job_id}/result",
    params={"timeout": 60}  # Wait up to 60 seconds
)

result = response.json()
print(f"Output: {result['output']}")
```

## Poll Pattern

```python
import time

def wait_for_job(job_id, poll_interval=1, max_wait=300):
    """Poll until job completes."""
    start = time.time()

    while time.time() - start < max_wait:
        response = requests.get(f"http://localhost:8000/jobs/{job_id}")
        status = response.json()

        if status["status"] in ["success", "error", "timeout", "cancelled"]:
            return status

        print(f"Status: {status['status']}...")
        time.sleep(poll_interval)

    raise TimeoutError(f"Job {job_id} did not complete in {max_wait}s")

# Usage
job_id = submit_job(...)
result = wait_for_job(job_id)
print(result)
```

## Cancel Job

Cancel a pending or running job:

```python
response = requests.post(f"http://localhost:8000/jobs/{job_id}/cancel")

result = response.json()
print(f"Cancelled: {result['cancelled']}")
```

!!! warning
    Running jobs may not stop immediately. The cancel request signals the worker, but execution may continue until a checkpoint.

## Concurrent Jobs

Submit multiple jobs in parallel:

```python
import concurrent.futures

def submit_job(data):
    response = requests.post("http://localhost:8000/execute/async", json={
        "code": "...",
        "input_data": data
    })
    return response.json()["job_id"]

# Submit 10 jobs concurrently
datasets = [{"id": i} for i in range(10)]

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    job_ids = list(executor.map(submit_job, datasets))

print(f"Submitted {len(job_ids)} jobs")

# Wait for all to complete
results = []
for job_id in job_ids:
    result = wait_for_job(job_id)
    results.append(result)
```

## Queue Status

Check the queue status:

```python
response = requests.get("http://localhost:8000/health")
stats = response.json()["queue_stats"]

print(f"Pending: {stats['pending']}")
print(f"Running: {stats['running']}")
print(f"Max workers: {stats['max_workers']}")
```
