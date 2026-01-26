# Configuration

Tako VM is configured via YAML files with sensible defaults.

## Configuration File

Create `tako_vm.yaml` in your project directory:

```yaml
# tako_vm.yaml

# =============================================================================
# Mode
# =============================================================================
production_mode: false     # Disable auto-build, require explicit versions

# =============================================================================
# Authentication
# =============================================================================
require_auth: false        # Require API key for all requests

# =============================================================================
# Workers & Queue
# =============================================================================
max_workers: 4             # Concurrent execution workers
max_queue_size: 100        # Maximum pending jobs

# =============================================================================
# Execution Limits
# =============================================================================
default_timeout: 30        # Default timeout (seconds)
max_timeout: 300           # Maximum allowed timeout

# =============================================================================
# Output Limits
# =============================================================================
max_stdout_bytes: 65536          # 64KB
max_stderr_bytes: 65536          # 64KB
max_artifact_bytes: 10485760     # 10MB per file
max_total_artifacts_bytes: 52428800  # 50MB total

# =============================================================================
# Input Limits
# =============================================================================
max_input_bytes: 1048576   # 1MB
max_code_bytes: 102400     # 100KB

# =============================================================================
# Storage
# =============================================================================
execution_record_ttl_days: 30  # Days to keep execution records

# =============================================================================
# Docker
# =============================================================================
docker_image: code-executor:latest
enable_seccomp: true       # Enable syscall filtering
```

## Config File Search Order

Tako VM searches for configuration in this order:

1. `./tako_vm.yaml` (current directory)
2. `./config/tako_vm.yaml`
3. `~/.tako_vm/config.yaml` (user home)
4. `/etc/tako_vm/config.yaml` (system-wide)

The first file found is used. If none exist, defaults are used.

## Environment Variable Overrides

Paths can be overridden via environment variables:

```bash
# Override config file location
export TAKO_VM_CONFIG=/path/to/config.yaml

# Override data directory
export TAKO_VM_DATA_DIR=/var/lib/tako_vm

# Override specific paths
export TAKO_VM_API_KEYS_FILE=/etc/tako_vm/keys.json
export TAKO_VM_DATABASE_FILE=/var/lib/tako_vm/db.sqlite
```

## Data Directory

Tako VM stores data in `~/.tako_vm/` by default:

```
~/.tako_vm/
├── config.yaml       # User configuration
├── api_keys.json     # API keys (hashed)
└── executions.db     # SQLite database
```

## Production Configuration

For production deployments:

```yaml
# tako_vm.yaml (production)
production_mode: true      # Require pre-built images
require_auth: true         # Require API keys
max_workers: 8             # Scale workers
enable_seccomp: true       # Keep security enabled

# Tighter limits
max_timeout: 60
max_stdout_bytes: 32768
max_artifact_bytes: 5242880
```

## Validating Configuration

Check your configuration is loaded correctly:

```python
from tako_vm.config import load_config

config = load_config()
print(f"Production mode: {config.production_mode}")
print(f"Max workers: {config.max_workers}")
print(f"Data dir: {config.data_dir}")
```
