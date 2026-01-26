# Authentication

Tako VM supports API key authentication with rate limiting.

## Enabling Authentication

Set `require_auth: true` in your config:

```yaml
# tako_vm.yaml
require_auth: true
```

With authentication enabled, all requests require a valid API key.

## Creating API Keys

### Using Python

```python
from tako_vm.server.auth import APIKeyManager
from pathlib import Path

# Initialize manager
keys_file = Path.home() / ".tako_vm" / "api_keys.json"
manager = APIKeyManager(keys_file)

# Create a key
raw_key, api_key = manager.create_key(
    name="my-application",
    rate_limit_per_minute=60,
    rate_limit_per_hour=1000,
    max_concurrent_jobs=5
)

print(f"API Key: {raw_key}")
print(f"Key ID: {api_key.key_id}")
```

!!! warning
    The raw key is only shown once. Store it securely!

### Key Options

```python
manager.create_key(
    name="production-app",

    # Rate limits
    rate_limit_per_minute=120,
    rate_limit_per_hour=5000,

    # Concurrency
    max_concurrent_jobs=10,

    # Restrict to specific environments
    allowed_job_types=["default", "data-processing"],

    # Optional expiration
    expires_at=datetime(2025, 12, 31),

    # Tenant/project ID for multi-tenancy
    tenant_id="project-123"
)
```

## Using API Keys

### HTTP Header

```bash
curl -X POST http://localhost:8000/execute \
  -H "Authorization: Bearer tvmk_abc123..." \
  -H "Content-Type: application/json" \
  -d '{"code": "print(1)", "input_data": {}}'
```

### Python Requests

```python
import requests

API_KEY = "tvmk_abc123..."

response = requests.post(
    "http://localhost:8000/execute",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={"code": "print(1)", "input_data": {}}
)
```

## Rate Limiting

Each API key has rate limits:

| Limit | Default | Description |
|-------|---------|-------------|
| Per minute | 60 | Requests per minute |
| Per hour | 1000 | Requests per hour |
| Concurrent | 5 | Simultaneous running jobs |

When limits are exceeded, you'll receive a `429 Too Many Requests` response:

```json
{
  "detail": "Rate limit exceeded: 60/minute"
}
```

## Managing Keys

### List Keys

```python
keys = manager.list_keys()
for key in keys:
    print(f"{key.key_id}: {key.name} (enabled={key.enabled})")
```

### Revoke Key

Disable a key (keeps record):

```python
manager.revoke_key("key-id-here")
```

### Delete Key

Permanently remove:

```python
manager.delete_key("key-id-here")
```

## Key Storage

Keys are stored in `~/.tako_vm/api_keys.json`:

```json
{
  "keys": [
    {
      "key_id": "a1b2c3d4",
      "key_hash": "sha256:...",
      "name": "my-app",
      "rate_limit_per_minute": 60,
      "rate_limit_per_hour": 1000,
      "max_concurrent_jobs": 5,
      "allowed_job_types": ["*"],
      "enabled": true,
      "created_at": "2024-01-15T10:30:00"
    }
  ]
}
```

!!! note
    Keys are stored hashed (SHA256). The raw key cannot be recovered.

## Hot Reload

The key file is automatically reloaded when modified. You can add, revoke, or modify keys without restarting the server.

## Without Authentication

When `require_auth: false` (default):

- API keys are optional
- If provided, rate limiting still applies
- Useful for development and testing

## Best Practices

1. **Use unique keys per application** - Easier to revoke if compromised
2. **Set appropriate rate limits** - Prevent abuse
3. **Use tenant IDs** - For multi-tenant deployments
4. **Rotate keys periodically** - Create new key, update apps, delete old
5. **Monitor usage** - Check execution records for anomalies
