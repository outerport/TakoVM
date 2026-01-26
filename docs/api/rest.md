# REST API Reference

Complete reference for the Tako VM HTTP API.

## Base URL

```
http://localhost:8000
```

## Headers

### Request Headers

| Header | Description |
|--------|-------------|
| `Content-Type` | `application/json` for all POST requests |
| `X-Correlation-ID` | Optional. Pass your own correlation ID for tracing |

### Response Headers

| Header | Description |
|--------|-------------|
| `X-Correlation-ID` | Correlation ID for request tracing |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |

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
  "status": "queued"
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
| `wait` | boolean | false | Wait for completion |
| `timeout` | integer | 30 | Max seconds to wait (if wait=true) |

### Response

```json
{
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "success",
  "job_type": "default",
  "job_version": "latest",
  "created_at": "2024-01-15T10:30:00Z",
  "started_at": "2024-01-15T10:30:01Z",
  "ended_at": "2024-01-15T10:30:02Z",
  "duration_ms": 350,
  "exit_code": 0,
  "stdout": "",
  "stderr": "",
  "output": {"sum": 30},
  "error": null
}
```

---

## Cancel Job

Cancel a pending or running job.

```
POST /jobs/{job_id}/cancel
```

### Response

```json
{
  "status": "cancelled",
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
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

## Build Job Type

Explicitly build a job type container image.

```
POST /job-types/{name}/build
```

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `version_tag` | string | null | Semantic version tag (e.g., "v1.0.0") |

### Response

```json
{
  "status": "built",
  "job_type": "data-processing",
  "version": "data-processing@sha256:a1b2c3d4e5f6",
  "digest": "a1b2c3d4e5f6",
  "image_ref": "tako-vm-data-processing:latest"
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
  "circuit_breaker": {
    "state": "closed",
    "failure_count": 0,
    "success_count": 5,
    "last_failure": null
  },
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
| `degraded` | Docker unavailable or circuit breaker open |

### Circuit Breaker States

| State | Description |
|-------|-------------|
| `closed` | Normal operation |
| `open` | Docker failing, requests rejected |
| `half_open` | Testing recovery |

---

## Worker Pool Stats

Get worker pool statistics.

```
GET /pool/stats
```

### Response

```json
{
  "pending": 5,
  "running": 4,
  "max_workers": 4,
  "max_queue_size": 100
}
```

---

## List Executions

List execution records with pagination.

```
GET /executions
```

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | null | Filter by status |
| `job_type` | string | null | Filter by job type |
| `limit` | integer | 100 | Max records (1-1000) |
| `offset` | integer | 0 | Pagination offset |

### Response

Array of execution records (same format as job result).

---

## Get Execution

Get a specific execution record.

```
GET /executions/{execution_id}
```

### Response

Same format as job result.

---

## Dead Letter Queue

### Get DLQ Statistics

Get statistics about failed jobs in the dead letter queue.

```
GET /dlq/stats
```

### Response

```json
{
  "total": 3,
  "by_error_type": {
    "internal_error": 2,
    "service_unavailable": 1
  }
}
```

---

### List DLQ Entries

List entries in the dead letter queue.

```
GET /dlq
```

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `error_type` | string | null | Filter by error type |
| `limit` | integer | 100 | Max entries (1-1000) |
| `offset` | integer | 0 | Pagination offset |

### Response

```json
[
  {
    "id": 1,
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "job_data": {
      "code": "...",
      "input_data": {},
      "job_type": "default"
    },
    "error_type": "internal_error",
    "error_message": "Docker daemon not responding",
    "retry_count": 0,
    "created_at": "2024-01-15T10:30:00Z",
    "client_ip": "192.168.1.100",
    "correlation_id": "abc-123-def"
  }
]
```

---

### Remove DLQ Entry

Remove a processed entry from the dead letter queue.

```
DELETE /dlq/{entry_id}
```

### Response

```json
{
  "status": "removed",
  "entry_id": 1
}
```

---

## Error Responses

### 400 Bad Request

Invalid request format.

```json
{
  "detail": "Invalid JSON in request body"
}
```

### 404 Not Found

Resource not found.

```json
{
  "detail": "Job not found"
}
```

### 408 Request Timeout

Timeout waiting for job completion.

```json
{
  "detail": "Timeout waiting for job"
}
```

### 503 Service Unavailable

Queue full or service degraded.

```json
{
  "detail": "Job queue is full, try again later"
}
```

### 500 Internal Server Error

Server error.

```json
{
  "detail": "Internal server error"
}
```
