"""
Example 4: Using API Authentication

Create and use API keys for authenticated requests.

Run: python examples/04_with_auth.py

Note: Set require_auth: true in tako_vm.yaml to enforce authentication.
"""

import requests
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tako_vm.server.auth import APIKeyManager

BASE_URL = "http://localhost:8000"


def main():
    print("=== API Authentication ===\n")

    # 1. Create an API key
    print("[1] Creating API key...")
    keys_file = Path.home() / ".tako_vm" / "api_keys.json"
    manager = APIKeyManager(keys_file)

    raw_key, api_key = manager.create_key(
        name="example-app",
        rate_limit_per_minute=60,
        rate_limit_per_hour=1000
    )

    print(f"    Key ID: {api_key.key_id}")
    print(f"    API Key: {raw_key}")
    print(f"    (Save this key - it won't be shown again!)")

    # 2. Make authenticated request
    print("\n[2] Making authenticated request...")

    code = """
import json
with open("/input/data.json") as f:
    data = json.load(f)
result = {"message": f"Hello, {data['name']}!"}
with open("/output/result.json", "w") as f:
    json.dump(result, f)
"""

    response = requests.post(
        f"{BASE_URL}/execute",
        headers={
            "Authorization": f"Bearer {raw_key}",  # <-- Auth header
            "Content-Type": "application/json"
        },
        json={
            "code": code,
            "input_data": {"name": "Developer"}
        }
    )

    result = response.json()
    print(f"    Response: {result}")

    # 3. Check rate limit headers (if server includes them)
    print("\n[3] Request completed with authentication")

    # Cleanup - delete the test key
    manager.delete_key(api_key.key_id)
    print("    (Test key deleted)")


if __name__ == "__main__":
    main()
