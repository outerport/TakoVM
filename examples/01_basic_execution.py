"""
Example 1: Basic Code Execution

The simplest way to execute code in Tako VM.
Run: python examples/01_basic_execution.py
"""

import requests

# Server URL (start with: tako-vm server)
BASE_URL = "http://localhost:8000"


def main():
    print("=== Basic Code Execution ===\n")

    # The code to execute
    # - Reads input from /input/data.json
    # - Writes output to /output/result.json
    code = """
import json

# Read input
with open("/input/data.json") as f:
    data = json.load(f)

# Process
x = data["x"]
y = data["y"]
result = {"sum": x + y, "product": x * y}

# Write output
with open("/output/result.json", "w") as f:
    json.dump(result, f)

print(f"Calculated: {x} + {y} = {result['sum']}")
"""

    # Execute
    response = requests.post(
        f"{BASE_URL}/execute",
        json={
            "code": code,
            "input_data": {"x": 10, "y": 20}
        }
    )

    result = response.json()

    print(f"Success: {result['success']}")
    print(f"Output: {result['output']}")
    print(f"Stdout: {result['stdout']}")
    print(f"Execution time: {result['execution_time']:.3f}s")


if __name__ == "__main__":
    main()
