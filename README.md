# Secure Code Executor

A secure, isolated Python code execution system that runs AI-generated code in containerized environments with strict security controls.

## Overview

This system executes untrusted Python code in isolated Docker containers with:
- No network access
- Read-only code and input mounts
- Strict resource limits (512MB RAM, 1 CPU, configurable timeout)
- Complete filesystem isolation
- Custom library support

## Architecture

```
Client → HTTP API (FastAPI) → Worker Process → Docker Container (Isolated Execution)
                                    ↓
                            Temp Workspace:
                            - /code (read-only)
                            - /input (read-only)
                            - /output (read-write)
```

## Quick Start

### Prerequisites

- Docker 20.10+ installed and running
- Python 3.11+
- Linux (recommended) or macOS with Docker Desktop

### 1. Build the Docker Image

```bash
docker build -t code-executor:latest .
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the API Server

```bash
python api_server.py
```

The API will be available at http://localhost:8000

### 4. View API Documentation

Open http://localhost:8000/docs in your browser for interactive API documentation.

### 5. Test the API

Using curl:
```bash
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import json\nwith open(\"/input/data.json\") as f: data=json.load(f)\nresult = {k: v*2 for k,v in data.items()}\nwith open(\"/output/result.json\", \"w\") as f: json.dump(result, f)",
    "input_data": {"x": 10, "y": 20}
  }'
```

Using Python:
```bash
python example_api_client.py
```

## API Reference

### POST /execute

Execute Python code in an isolated container.

**Request:**
```json
{
  "code": "python code as string",
  "input_data": {
    "key": "value"
  },
  "timeout": 30
}
```

**Response (Success):**
```json
{
  "success": true,
  "output": {
    "result": "processed data"
  },
  "execution_time": 1.23,
  "stdout": "Processing complete!",
  "stderr": ""
}
```

**Response (Failure):**
```json
{
  "success": false,
  "error": "timeout exceeded",
  "stdout": "",
  "stderr": "error messages",
  "exit_code": 137
}
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "docker_available": true,
  "version": "1.0.0"
}
```

## Security Features

✅ **Complete Network Isolation** - No external connections possible  
✅ **Read-Only Filesystem** - Cannot modify system files  
✅ **Resource Limits** - CPU, memory, process limits enforced  
✅ **Timeout Protection** - Jobs killed after timeout  
✅ **Non-Root Execution** - Runs as unprivileged user  
✅ **Capability Dropping** - No special Linux capabilities  
✅ **Ephemeral Containers** - Destroyed after each execution  

## Limitations

This is a POC with several important limitations:

**Resource Constraints**: 512MB RAM, 1 CPU, 30s timeout (configurable up to 5min)  
**No Network Access**: Completely isolated, cannot make HTTP requests  
**No Direct Database**: Data must be passed via JSON input  
**Synchronous Only**: API blocks until execution completes  
**No Authentication**: Anyone with network access can execute code  
**Sequential Processing**: One job at a time  

For complete details, see [LIMITATIONS.md](LIMITATIONS.md)

**Production Readiness**: This POC is suitable for:
- ✅ Development and testing
- ✅ Internal tools with low throughput
- ✅ Proof of concept demonstrations

**Not suitable for**:
- ❌ Public-facing production services (without auth)
- ❌ High-throughput workloads (>100 jobs/hour)
- ❌ Long-running batch processing (>5min)
- ❌ Highly sensitive data (no encryption at rest)

## Custom Libraries

To add your own Python libraries:

1. Place wheel files (`.whl`) in the `custom_libs/` directory
2. Rebuild the Docker image: `docker build -t code-executor:latest .`
3. Restart the API server

Example:
```bash
# Build your library
cd my_library
python setup.py bdist_wheel
cp dist/*.whl ../custom_libs/

# Rebuild image
docker build -t code-executor:latest .
```

## Testing

Run the test suite:

```bash
# Install test dependencies
pip install -r dev-requirements.txt

# Run unit tests
pytest test_executor.py -v

# Run API tests
pytest test_api.py -v

# Run limitation tests
pytest test_limitations.py -v

# Run all tests
pytest -v
```

## Code Execution Examples

### Example 1: Simple Data Transformation

```python
code = """
import json

with open('/input/data.json') as f:
    data = json.load(f)

result = {k: v * 2 for k, v in data.items()}

with open('/output/result.json', 'w') as f:
    json.dump(result, f)

print("Transformation complete!")
"""

response = requests.post('http://localhost:8000/execute', json={
    'code': code,
    'input_data': {'a': 1, 'b': 2, 'c': 3}
})
```

### Example 2: Using Custom Libraries

```python
code = """
import json
from example_lib import DataClass

with open('/input/data.json') as f:
    data = json.load(f)

obj = DataClass.from_dict(data)
transformed = obj.transform()

with open('/output/result.json', 'w') as f:
    json.dump(transformed.to_dict(), f)
"""

response = requests.post('http://localhost:8000/execute', json={
    'code': code,
    'input_data': {'x': 10, 'y': 20}
})
```

## Deployment

### Development
```bash
# Run with auto-reload
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
```

### Production (Future)
```bash
# Using gunicorn with multiple workers
gunicorn api_server:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

## Troubleshooting

### Docker Not Available
```
Error: Cannot connect to Docker daemon
```
**Solution**: Ensure Docker is running: `docker info`

### Permission Denied
```
Error: Permission denied while trying to connect to Docker
```
**Solution**: Add user to docker group: `sudo usermod -aG docker $USER` (logout/login required)

### Container Creation Fails
```
Error: Container failed to start
```
**Solution**: Check Docker logs: `docker logs <container_id>`

### Timeout Issues
```
Error: Execution timeout exceeded
```
**Solution**: Increase timeout in request or optimize code to run faster

## Project Structure

```
secure-code-executor/
├── Dockerfile                  # Container image definition
├── README.md                   # This file
├── LIMITATIONS.md              # Detailed limitations
├── requirements.txt            # API server dependencies
├── dev-requirements.txt        # Development/testing dependencies
├── .gitignore                  # Git ignore rules
├── .dockerignore              # Docker build ignore rules
├── api_server.py              # FastAPI HTTP server
├── worker.py                  # Core execution orchestrator
├── example_api_client.py      # Example API usage
├── example_usage.py           # Direct worker usage example
├── test_executor.py           # Unit tests for worker
├── test_api.py               # API integration tests
├── test_limitations.py        # Limitation enforcement tests
├── custom_libs/               # Custom Python libraries
│   └── example_lib/          # Example library
└── examples/                  # Example code templates
    └── example_generated_code.py
```

## Contributing

This is a POC. For production use, consider adding:
- Authentication/authorization
- Rate limiting
- Job queue for async execution
- Result persistence
- gVisor runtime for enhanced isolation
- Monitoring and metrics

## License

[Your License Here]

## Contact

[Your Contact Information]
