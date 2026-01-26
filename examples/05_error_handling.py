"""
Example 5: Error Handling

Handle various error scenarios gracefully.

Run: python examples/05_error_handling.py
"""

import requests

BASE_URL = "http://localhost:8000"


def execute(code, input_data, **kwargs):
    """Helper to execute code and return result."""
    response = requests.post(
        f"{BASE_URL}/execute",
        json={"code": code, "input_data": input_data, **kwargs},
        timeout=kwargs.get("timeout", 30) + 10
    )
    return response.json()


def main():
    print("=== Error Handling ===\n")

    # 1. Syntax error
    print("[1] Syntax Error:")
    result = execute("def broken(", {})
    print(f"    Success: {result['success']}")
    print(f"    Error: {result.get('error', 'N/A')[:80]}...")

    # 2. Runtime error
    print("\n[2] Runtime Error (division by zero):")
    code = """
import json
with open("/input/data.json") as f:
    data = json.load(f)
result = 1 / data["value"]  # Will fail if value is 0
with open("/output/result.json", "w") as f:
    json.dump({"result": result}, f)
"""
    result = execute(code, {"value": 0})
    print(f"    Success: {result['success']}")
    print(f"    Stderr: {result.get('stderr', 'N/A')[:100]}...")

    # 3. Timeout
    print("\n[3] Timeout (5 second limit):")
    code = """
import time
time.sleep(10)  # Will timeout
"""
    result = execute(code, {}, timeout=5)
    print(f"    Success: {result['success']}")
    print(f"    Error: {result.get('error', 'N/A')}")

    # 4. Missing output file
    print("\n[4] Missing output (no result.json written):")
    code = """
print("I forgot to write the output file!")
"""
    result = execute(code, {})
    print(f"    Success: {result['success']}")
    print(f"    Output: {result.get('output')}")
    print(f"    Stdout: {result.get('stdout')}")

    # 5. Invalid job type
    print("\n[5] Invalid job type:")
    result = execute("print('hi')", {}, job_type="nonexistent-env")
    print(f"    Success: {result['success']}")
    # Will use default environment with warning

    print("\n=== Error handling complete ===")


if __name__ == "__main__":
    main()
