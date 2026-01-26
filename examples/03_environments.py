"""
Example 3: Using Different Environments

Execute code in containers with different pre-installed packages.

Run: python examples/03_environments.py
"""

import requests

BASE_URL = "http://localhost:8000"


def main():
    print("=== Using Different Environments ===\n")

    # 1. List available environments
    print("[1] Available environments:")
    response = requests.get(f"{BASE_URL}/job-types")
    for env in response.json():
        packages = ", ".join(env["requirements"]) or "(stdlib only)"
        print(f"    - {env['name']}: {packages}")

    # 2. Use data-processing environment (has pandas, numpy)
    print("\n[2] Running with 'data-processing' environment...")

    code = """
import json
import numpy as np

with open("/input/data.json") as f:
    data = json.load(f)

values = np.array(data["values"])
result = {
    "mean": float(np.mean(values)),
    "std": float(np.std(values)),
    "max": float(np.max(values)),
    "min": float(np.min(values))
}

with open("/output/result.json", "w") as f:
    json.dump(result, f)

print(f"Processed {len(values)} values with numpy")
"""

    response = requests.post(
        f"{BASE_URL}/execute",
        json={
            "code": code,
            "input_data": {"values": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
            "job_type": "data-processing"  # <-- Specify environment
        }
    )

    result = response.json()

    if result["success"]:
        print(f"    Output: {result['output']}")
        print(f"    Stdout: {result['stdout']}")
    else:
        print(f"    Error: {result['error']}")
        print("    (First run may need to build the container image)")


if __name__ == "__main__":
    main()
