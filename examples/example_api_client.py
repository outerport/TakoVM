"""
Example client demonstrating how to call the API.
"""
import json

import requests

API_URL = "http://localhost:8000"


def execute_code(code: str, input_data: dict, timeout: int = 30):
    """
    Execute code via the API.

    Args:
        code: Python code to execute
        input_data: Input data dictionary
        timeout: Timeout in seconds

    Returns:
        API response as dictionary
    """
    response = requests.post(
        f"{API_URL}/execute",
        json={
            "code": code,
            "input_data": input_data,
            "timeout": timeout
        },
        timeout=timeout + 10
    )
    response.raise_for_status()
    return response.json()


def main():
    """Run example API calls."""

    # Example 1: Simple transformation
    print("=== Example 1: Simple Transformation ===")

    code1 = """
import json

with open('/input/data.json') as f:
    data = json.load(f)

# Double all values
result = {k: v * 2 for k, v in data.items()}

with open('/output/result.json', 'w') as f:
    json.dump(result, f)

print("Transformation complete!")
"""

    try:
        result1 = execute_code(code1, {"a": 10, "b": 20})
        print(json.dumps(result1, indent=2))
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")

    # Example 2: Using custom library (if available)
    print("\n=== Example 2: Custom Library ===")

    code2 = """
import json

try:
    from example_lib import DataClass

    with open('/input/data.json') as f:
        data = json.load(f)

    obj = DataClass.from_dict(data)
    transformed = obj.transform()

    with open('/output/result.json', 'w') as f:
        json.dump(transformed.to_dict(), f)

    print("Processing with custom library complete!")
except ImportError:
    # Custom library not installed
    with open('/output/result.json', 'w') as f:
        json.dump({"error": "Custom library not available"}, f)
    print("Custom library not installed in image")
"""

    try:
        result2 = execute_code(code2, {"x": 100, "y": 200})
        print(json.dumps(result2, indent=2))
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")

    # Example 3: Error handling (timeout)
    print("\n=== Example 3: Timeout Handling ===")

    code3 = """
# This will cause a timeout
import time
time.sleep(60)
"""

    try:
        result3 = execute_code(code3, {}, timeout=2)
        print(json.dumps(result3, indent=2))
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")

    # Example 4: Network isolation (verify it fails)
    print("\n=== Example 4: Network Isolation Test ===")

    code4 = """
import socket
import json

try:
    s = socket.socket()
    s.connect(('google.com', 80))
    result = {'network_accessible': True}
except Exception as e:
    result = {'network_accessible': False, 'error': str(e)}

with open('/output/result.json', 'w') as f:
    json.dump(result, f)

print("Network test complete")
"""

    try:
        result4 = execute_code(code4, {})
        print(json.dumps(result4, indent=2))
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
