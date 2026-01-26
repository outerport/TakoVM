# Error Handling

This guide covers handling various error scenarios in Tako VM.

## Error Response Format

When execution fails, the response includes error details:

```json
{
  "success": false,
  "output": null,
  "stdout": "",
  "stderr": "Traceback (most recent call last):\n...",
  "exit_code": 1,
  "error": "ZeroDivisionError: division by zero",
  "execution_time": 0.15
}
```

## Common Errors

### Syntax Errors

```python
response = requests.post("http://localhost:8000/execute", json={
    "code": "def broken(",  # Invalid syntax
    "input_data": {}
})

result = response.json()
# success: false
# stderr: "SyntaxError: unexpected EOF while parsing"
```

### Runtime Errors

```python
code = """
import json
with open("/input/data.json") as f:
    data = json.load(f)
result = 100 / data["value"]  # Division by zero if value=0
"""

response = requests.post("http://localhost:8000/execute", json={
    "code": code,
    "input_data": {"value": 0}
})

result = response.json()
# success: false
# stderr contains: "ZeroDivisionError: division by zero"
```

### Timeout Errors

```python
response = requests.post("http://localhost:8000/execute", json={
    "code": "import time; time.sleep(60)",
    "input_data": {},
    "timeout": 5
})

result = response.json()
# success: false
# error: "Execution timeout exceeded (5s)"
```

### Import Errors

```python
code = """
import pandas  # Not available in 'default' environment
"""

response = requests.post("http://localhost:8000/execute", json={
    "code": code,
    "input_data": {},
    "job_type": "default"
})

result = response.json()
# success: false
# stderr: "ModuleNotFoundError: No module named 'pandas'"
```

**Solution**: Use the correct environment:

```python
response = requests.post("http://localhost:8000/execute", json={
    "code": code,
    "input_data": {},
    "job_type": "data-processing"  # Has pandas
})
```

## Handling Errors in Python

```python
def execute_safely(code, input_data, **kwargs):
    """Execute code with error handling."""
    try:
        response = requests.post(
            "http://localhost:8000/execute",
            json={"code": code, "input_data": input_data, **kwargs},
            timeout=kwargs.get("timeout", 30) + 10  # HTTP timeout
        )
        response.raise_for_status()

    except requests.exceptions.Timeout:
        return {"success": False, "error": "HTTP request timed out"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "Cannot connect to Tako VM"}
    except requests.exceptions.HTTPError as e:
        return {"success": False, "error": f"HTTP error: {e}"}

    result = response.json()

    if not result["success"]:
        # Log or handle the error
        print(f"Execution failed: {result.get('error')}")
        print(f"Stderr: {result.get('stderr')}")

    return result
```

## Error Categories

| Error Type | Cause | Solution |
|------------|-------|----------|
| `SyntaxError` | Invalid Python syntax | Fix code syntax |
| `ModuleNotFoundError` | Package not installed | Use correct environment |
| `FileNotFoundError` | Input file missing | Check input_data is provided |
| `JSONDecodeError` | Invalid JSON input | Validate input data |
| `MemoryError` | Exceeded memory limit | Reduce data size or increase limit |
| `TimeoutError` | Exceeded time limit | Optimize code or increase timeout |

## Memory Errors (OOM)

When code exceeds memory limits:

```json
{
  "success": false,
  "exit_code": 137,
  "error": "Execution exceeded memory limit"
}
```

Exit code 137 indicates the process was killed (OOM).

## Validating Input

Prevent errors by validating before execution:

```python
def validate_request(code, input_data, timeout=None):
    """Validate execution request."""
    errors = []

    # Check code
    if not code or not code.strip():
        errors.append("Code cannot be empty")

    if len(code) > 100_000:  # 100KB limit
        errors.append("Code exceeds size limit")

    # Check input
    try:
        json.dumps(input_data)
    except (TypeError, ValueError) as e:
        errors.append(f"Input not JSON serializable: {e}")

    # Check timeout
    if timeout is not None:
        if timeout < 1 or timeout > 300:
            errors.append("Timeout must be between 1 and 300 seconds")

    return errors

# Usage
errors = validate_request(code, input_data)
if errors:
    print(f"Validation failed: {errors}")
else:
    result = execute(code, input_data)
```

## Retrying Failed Jobs

```python
import time

def execute_with_retry(code, input_data, max_retries=3, **kwargs):
    """Execute with automatic retry on transient failures."""

    for attempt in range(max_retries):
        result = execute_safely(code, input_data, **kwargs)

        if result["success"]:
            return result

        # Don't retry on permanent failures
        error = result.get("error", "")
        if any(e in error for e in ["SyntaxError", "ModuleNotFound"]):
            return result  # No point retrying

        # Retry on transient failures
        if attempt < max_retries - 1:
            wait = 2 ** attempt  # Exponential backoff
            print(f"Retrying in {wait}s...")
            time.sleep(wait)

    return result
```

## Debugging Tips

1. **Check stdout/stderr** - Often contains stack traces
2. **Use print statements** - Debug output is captured
3. **Test locally first** - Run code outside Tako VM
4. **Check environment** - Ensure packages are available
5. **Review limits** - Memory, CPU, timeout settings
