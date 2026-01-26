# REST API Reference

Complete reference for the Tako VM HTTP API.

## Base URL

```
http://localhost:8000
```

## Authentication

When authentication is enabled, include the API key in the `Authorization` header:

```
Authorization: Bearer tvmk_your_api_key_here
```

---

## Execute Code (Sync)

Execute code and wait for the result.

```
POST /execute
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | string | Yes | Python code to execute |
| `input_data` | object | Yes | Input data (available at `/input/data.json`) |
| `timeout` | integer | No | Timeout in seconds (default: 30) |
| `job_type` | string | No | Environment name (default: "default") |

### Example Request

```bash
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import json\nwith open(\"/input/data.json\") as f: d=json.load(f)\nwith open(\"/output/result.json\",\"w\") as f: json.dump({\"sum\":d[\"a\"]+d[\"b\"]},f)",
    "input_data": {"a": 10, "b": 20},
    "timeout": 30,
    "job_type": "default"
  }'
```

### Response

```json
{
  "success": true,
  "output": {"sum": 30},
  "stdout": "",
  "stderr": "",
  "exit_code": 0,
  "execution_time": 0.35,
  "error": null,
  "job_type": "default"
}
```

---

## Execute Code (Async)

Submit code for execution and return immediately.

```
POST /execute/async
```

### Request Body

Same as `/execute`.

### Response

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending"
}
```

---

## Get Job Status

Get the current status of a job.

```
GET /jobs/{job_id}
```

### Response

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "created_at": "2024-01-15T10:30:00Z",
  "started_at": "2024-01-15T10:30:01Z"
}
```

### Status Values

| Status | Description |
|--------|-------------|
| `pending` | Queued, waiting for worker |
| `running` | Currently executing |
| `success` | Completed successfully |
| `error` | Failed with error |
| `timeout` | Exceeded time limit |
| `oom` | Out of memory |
| `cancelled` | Cancelled by user |

---

## Get Job Result

Wait for job completion and return the result.

```
GET /jobs/{job_id}/result
```

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `timeout` | integer | 30 | Max seconds to wait |

### Response

Same format as `/execute` response.

---

## Cancel Job

Cancel a pending or running job.

```
POST /jobs/{job_id}/cancel
```

### Response

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "cancelled": true
}
```

---

## List Job Types

Get available execution environments.

```
GET /job-types
```

### Response

```json
[
  {
    "name": "default",
    "requirements": [],
    "python_version": "3.11",
    "memory_limit": "512m",
    "cpu_limit": 1.0,
    "timeout": 30,
    "image_exists": true
  },
  {
    "name": "data-processing",
    "requirements": ["pandas", "numpy"],
    "python_version": "3.11",
    "memory_limit": "1g",
    "cpu_limit": 2.0,
    "timeout": 60,
    "image_exists": true
  }
]
```

---

## Get Job Type

Get a specific environment's configuration.

```
GET /job-types/{name}
```

### Response

```json
{
  "name": "data-processing",
  "requirements": ["pandas", "numpy"],
  "python_version": "3.11",
  "memory_limit": "1g",
  "cpu_limit": 2.0,
  "timeout": 60,
  "image_exists": true
}
```

---

## Health Check

Check server health status.

```
GET /health
```

### Response

```json
{
  "status": "healthy",
  "docker_available": true,
  "version": "2.0.0",
  "production_mode": false,
  "queue_stats": {
    "pending": 0,
    "running": 2,
    "max_workers": 4,
    "max_queue_size": 100
  }
}
```

### Status Values

| Status | Description |
|--------|-------------|
| `healthy` | All systems operational |
| `degraded` | Docker unavailable |

---

## Error Responses

### 400 Bad Request

Invalid request format.

```json
{
  "detail": "Invalid JSON in request body"
}
```

### 401 Unauthorized

Missing or invalid API key (when auth required).

```json
{
  "detail": "API key required"
}
```

### 404 Not Found

Resource not found.

```json
{
  "detail": "Job not found"
}
```

### 429 Too Many Requests

Rate limit exceeded.

```json
{
  "detail": "Rate limit exceeded: 60/minute"
}
```

### 500 Internal Server Error

Server error.

```json
{
  "detail": "Internal server error"
}
```
