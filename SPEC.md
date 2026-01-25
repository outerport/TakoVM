# Secure Code Execution POC - Technical Specification

## Project Overview

Build a secure, isolated Python code execution system that runs AI-generated code in containerized environments with strict security controls. The system uses Docker containers with read-only code mounts, resource limits, and network isolation to safely execute untrusted code.

## Objectives

1. Execute AI-generated Python code in complete isolation
2. Prevent data exfiltration, resource exhaustion, and system compromise
3. Support custom in-house Python libraries
4. Provide clean input/output mechanism via JSON
5. Expose functionality via HTTP API
6. Create reusable, production-ready foundation for future expansion

## Architecture Overview

```
Client → HTTP API (FastAPI) → Worker Process → Docker Container (Isolated Execution)
                                    ↓
                            Temp Workspace:
                            - /code (read-only)
                            - /input (read-only)
                            - /output (read-write)
```

## Component Specifications

### 1. Docker Image (`code-executor`)

**Purpose**: Pre-built execution environment with custom libraries and security hardening

**Dockerfile Requirements**:
- Base image: `python:3.11-slim`
- Install custom libraries from `/custom_libs` directory (*.whl files)
- Create non-root user `sandbox` (UID 1000)
- Create directories: `/code`, `/input`, `/output`, `/tmp`
- Set ownership: `sandbox:sandbox` for `/output` and `/tmp`
- Set permissions: 755 for `/code` and `/input`, 777 for `/output` and `/tmp`
- USER: Switch to `sandbox` user
- WORKDIR: `/app`
- ENTRYPOINT: `["python", "-u", "/code/main.py"]`

**Security Hardening**:
- No root access
- Minimal installed packages
- Clean up installation artifacts

**Build Command**:
```bash
docker build -t code-executor:latest .
```

### 2. Worker Process (`worker.py`)

**Purpose**: Orchestrates job execution by preparing workspace and spawning isolated containers

**Class: `CodeExecutor`**

#### Constructor
```python
def __init__(self, docker_image="code-executor:latest", default_timeout=30)
```

#### Method: `execute_job(job: dict) -> dict`

**Input Job Format**:
```json
{
  "id": "job-123",
  "code": "python code as string",
  "input_data": {"key": "value"},
  "timeout": 30
}
```

**Execution Steps**:
1. Create temporary directory: `/tmp/job-{job_id}-{timestamp}/`
2. Create subdirectories: `code/`, `input/`, `output/`
3. Write code to `code/main.py` (chmod 444 - read-only)
4. Write input_data to `input/data.json` (chmod 444 - read-only)
5. Spawn container with security restrictions
6. Wait for completion or timeout
7. Read `output/result.json` if exists
8. Cleanup workspace
9. Return results

**Output Format**:
```json
{
  "success": true/false,
  "stdout": "container stdout",
  "stderr": "container stderr",
  "exit_code": 0,
  "output": {},
  "error": "error message if failed"
}
```

#### Method: `_run_container(code_dir, input_dir, output_dir, timeout) -> dict`

**Docker Run Command**:
```bash
docker run --rm --read-only --network=none --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --memory=512m --memory-swap=512m --cpus=1 --pids-limit=100 \
  --mount=type=bind,source={code_dir},target=/code,readonly \
  --mount=type=bind,source={input_dir},target=/input,readonly \
  --mount=type=bind,source={output_dir},target=/output \
  --tmpfs=/tmp:rw,noexec,nosuid,size=100m \
  code-executor:latest
```

### 3. HTTP API Server (`api_server.py`)

**Purpose**: Expose code execution as HTTP endpoints

**Framework**: FastAPI (async support, auto docs, validation)

**Endpoints**:

#### `POST /execute`
Execute code synchronously

**Request**:
```json
{
  "code": "python code",
  "input_data": {},
  "timeout": 30
}
```

**Response (Success - 200)**:
```json
{
  "success": true,
  "output": {},
  "execution_time": 1.23,
  "stdout": "",
  "stderr": ""
}
```

**Response (Failed - 200)**:
```json
{
  "success": false,
  "error": "timeout exceeded",
  "stdout": "",
  "stderr": "",
  "exit_code": 137
}
```

#### `GET /health`
Health check endpoint

**Response (200)**:
```json
{
  "status": "healthy",
  "docker_available": true,
  "version": "1.0.0"
}
```

**Request Validation**:
- Code length: max 100KB
- Input data size: max 1MB
- Timeout: min 1s, max 300s

**Security Headers**:
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- X-XSS-Protection: 1; mode=block

### 4. Custom Library Integration

**Directory**: `custom_libs/`

**Format**: Python wheel files (`.whl`)

**Installation**: Copied into Docker image during build

**Example Library Structure**:
```
custom_libs/
├── example_lib/
│   ├── setup.py
│   └── example_lib/
│       └── __init__.py
└── *.whl (built wheels)
```

**Build Process**:
```bash
cd custom_libs/example_lib
python setup.py bdist_wheel
cp dist/*.whl ../
```

## File Structure

```
secure-code-executor/
├── Dockerfile
├── README.md
├── LIMITATIONS.md
├── requirements.txt
├── dev-requirements.txt
├── .gitignore
├── .dockerignore
├── api_server.py
├── worker.py
├── example_api_client.py
├── custom_libs/
│   ├── README.md
│   └── example_lib/
│       ├── setup.py
│       └── example_lib/
│           └── __init__.py
└── (test files - to be created)
```

## Security Requirements Checklist

The implementation MUST enforce:

- ✅ No network access (`--network=none`)
- ✅ Read-only root filesystem (`--read-only`)
- ✅ Read-only code mount
- ✅ Read-only input mount
- ✅ Non-root user execution (UID 1000, user `sandbox`)
- ✅ All Linux capabilities dropped (`--cap-drop=ALL`)
- ✅ No privilege escalation (`--security-opt=no-new-privileges`)
- ✅ Memory limit: 512MB
- ✅ CPU limit: 1 core
- ✅ Process limit: 100 PIDs
- ✅ Temporary filesystem limit: 100MB
- ✅ Execution timeout: 30 seconds (configurable)
- ✅ Container removal after execution (`--rm`)
- ✅ Workspace cleanup after job completion

## System Limitations

See LIMITATIONS.md for complete details.

**Key Limitations**:
- No network access (by design)
- No database access (POC)
- Synchronous execution only
- Sequential job processing (one at a time)
- No authentication (POC)
- No rate limiting (POC)
- 512MB memory limit per job
- 30s default timeout (300s max)

## Testing Requirements

All security tests MUST pass:

1. Network isolation (socket connections fail)
2. Timeout enforcement (long jobs killed)
3. Memory limits (excessive allocation fails)
4. Read-only code (write attempts fail)
5. Process limits (fork bombs contained)
6. Custom library imports work
7. Input/output mechanism works
8. Multiple consecutive jobs execute cleanly

## Development Workflow

### Initial Setup

1. Clone repository
2. Build Docker image: `docker build -t code-executor:latest .`
3. Install dependencies: `pip install -r requirements.txt`
4. Run API server: `python api_server.py`
5. Test: `python example_api_client.py`

### Adding Custom Libraries

1. Place `.whl` files in `custom_libs/`
2. Rebuild Docker image
3. Restart API server

### Running Tests

```bash
pip install -r dev-requirements.txt
pytest -v
```

## Deployment

### Development
```bash
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
```

### Production (Future)
```bash
gunicorn api_server:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

## Future Roadmap

Not in POC, but designed to support:

1. Scoped S3 access with temporary credentials
2. Async job queue (non-blocking API)
3. Job history and result caching
4. API authentication (keys, JWT)
5. Rate limiting per client
6. Worker pool for concurrency
7. gVisor runtime for stronger isolation
8. Stateful execution patterns
9. Import whitelisting
10. Monitoring and metrics

## Success Criteria

The POC is successful when:

1. ✅ Docker image builds successfully
2. ✅ API server starts without errors
3. ✅ Example client executes successfully
4. ✅ All security restrictions enforced
5. ✅ Custom library imports work
6. ✅ No manual cleanup required
7. ✅ Code is clean and documented

## Non-Functional Requirements

- **Performance**: Job overhead < 2 seconds
- **Cleanup**: Zero leftover containers/files
- **Logging**: Clear stdout/stderr capture
- **Error Handling**: Graceful failure modes
- **Portability**: Linux (primary), macOS (dev)

## Getting Started

See README.md for detailed setup instructions.
