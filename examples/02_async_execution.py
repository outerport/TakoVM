"""
Example 2: Async Job Execution

Submit jobs asynchronously and poll for results.
Useful for long-running tasks.

Run: python examples/02_async_execution.py
"""

import requests
import time

BASE_URL = "http://localhost:8000"


def main():
    print("=== Async Job Execution ===\n")

    # Long-running code (simulated with sleep)
    code = """
import json
import time

with open("/input/data.json") as f:
    data = json.load(f)

# Simulate processing
print("Starting processing...")
time.sleep(2)
print("Processing complete!")

result = {"processed": data["items"], "count": len(data["items"])}

with open("/output/result.json", "w") as f:
    json.dump(result, f)
"""

    # 1. Submit job (returns immediately)
    print("[1] Submitting async job...")
    response = requests.post(
        f"{BASE_URL}/execute/async",
        json={
            "code": code,
            "input_data": {"items": ["a", "b", "c"]}
        }
    )

    job = response.json()
    job_id = job["job_id"]
    print(f"    Job ID: {job_id}")
    print(f"    Status: {job['status']}")

    # 2. Poll for status
    print("\n[2] Polling for completion...")
    while True:
        status_response = requests.get(f"{BASE_URL}/jobs/{job_id}")
        status = status_response.json()

        print(f"    Status: {status['status']}")

        if status["status"] in ["success", "error", "timeout"]:
            break

        time.sleep(0.5)

    # 3. Get result
    print("\n[3] Getting result...")
    result_response = requests.get(f"{BASE_URL}/jobs/{job_id}/result")
    result = result_response.json()

    print(f"    Success: {result.get('status') == 'success'}")
    print(f"    Output: {result.get('output')}")


if __name__ == "__main__":
    main()
