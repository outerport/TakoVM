"""
Tako VM Sandbox Quickstart

This example shows how to use Tako VM as a library without running a server.
No setup required - just `pip install tako-vm` and you're ready to go.

Prerequisites:
- Docker installed and running
- Python 3.9+
"""

from tako_vm import Sandbox

# Basic execution
print("=== Basic Execution ===")
with Sandbox() as sb:
    result = sb.run("print('Hello from the sandbox!')")
    print(f"stdout: {result.stdout}")
    print(f"exit_code: {result.exit_code}")
    print()

# With input data
print("=== With Input Data ===")
with Sandbox() as sb:
    result = sb.run(
        """
import json

# Read input data
with open('/input/data.json') as f:
    data = json.load(f)

# Process it
total = sum(data['numbers'])
print(f"Sum of {data['numbers']} = {total}")

# Write output
with open('/output/result.json', 'w') as f:
    json.dump({'total': total}, f)
""",
        input_data={"numbers": [1, 2, 3, 4, 5]},
    )

    print(f"stdout: {result.stdout}")
    print(f"output: {result.output}")  # Parsed from /output/result.json
    print()

# With dependencies
print("=== With Dependencies ===")
with Sandbox() as sb:
    result = sb.run(
        """
import requests
print(f"requests version: {requests.__version__}")
""",
        requirements=["requests"],
    )
    print(f"stdout: {result.stdout}")
    print()

# Error handling
print("=== Error Handling ===")
with Sandbox() as sb:
    result = sb.run("raise ValueError('intentional error')")
    print(f"success: {result.success}")
    print(f"exit_code: {result.exit_code}")
    print(f"stderr: {result.stderr[:200]}...")  # Truncate long traceback
