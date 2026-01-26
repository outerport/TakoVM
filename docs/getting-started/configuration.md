# Configuration

Tako VM uses YAML configuration with sensible defaults.

## Quick Start

Create `tako_vm.yaml` in your project directory:

```yaml
production_mode: false    # strict mode (no auto-build)
max_workers: 4            # concurrent execution slots
```

That's it for basic setup. Everything else has sensible defaults.

## Full Configuration

```yaml
# tako_vm.yaml

# ==============================================================================
# QUICK START
# ==============================================================================
production_mode: false
max_workers: 4

# ==============================================================================
# EXECUTION LIMITS
# ==============================================================================
default_timeout: 30       # seconds
max_timeout: 300          # maximum allowed

max_code_bytes: 102400    # 100KB
max_input_bytes: 1048576  # 1MB
max_stdout_bytes: 65536   # 64KB
max_stderr_bytes: 65536   # 64KB

# ==============================================================================
# CONTAINER SECURITY
# ==============================================================================
docker_image: code-executor:latest
enable_seccomp: true      # syscall filtering
enable_userns: true       # non-root execution

container_limits:
  pids_limit: 100
  nofile_soft: 256
  nofile_hard: 256
  nproc_soft: 50
  nproc_hard: 50
  fsize: 104857600        # 100MB max file size
  tmpfs_size: "100m"

# ==============================================================================
# JOB TYPES
# ==============================================================================
job_types:
  - name: data-processing
    requirements:
      - pandas
      - numpy
    memory_limit: "1g"
    cpu_limit: 2.0
    timeout: 60

  - name: api-client
    requirements:
      - requests
      - httpx
    memory_limit: "256m"
    timeout: 30
    network_enabled: true
    allowed_hosts:
      - "api.openai.com"
      - "*.amazonaws.com"

# ==============================================================================
# OTHER
# ==============================================================================
max_queue_size: 100
max_artifact_bytes: 10485760
max_total_artifacts_bytes: 52428800
execution_record_ttl_days: 30
```

## Config File Search Order

Tako VM searches for configuration in this order:

1. `TAKO_VM_CONFIG` environment variable
2. `./tako_vm.yaml` (current directory)
3. `./config/tako_vm.yaml`
4. `~/.tako_vm/config.yaml`
5. `/etc/tako_vm/config.yaml`

## Environment Variables

```bash
# Override config file location
export TAKO_VM_CONFIG=/path/to/config.yaml

# Override data directory
export TAKO_VM_DATA_DIR=/var/lib/tako_vm
```

## Job Types

Job types define pre-configured execution environments:

| Field | Description | Default |
|-------|-------------|---------|
| `name` | Unique identifier | required |
| `requirements` | pip packages | `[]` |
| `memory_limit` | Container memory | `"512m"` |
| `cpu_limit` | CPU cores | `1.0` |
| `timeout` | Default timeout | `30` |
| `network_enabled` | Allow network | `false` |
| `allowed_hosts` | Domain allowlist | `[]` |

When you define a job type, Tako VM builds a Docker image with the specified packages pre-installed (`tako-vm-{name}:latest`).

## Container Limits

| Limit | Description | Default | Range |
|-------|-------------|---------|-------|
| `pids_limit` | Max processes | 100 | 10-1000 |
| `nofile_soft/hard` | File descriptors | 256 | 64-65536 |
| `nproc_soft/hard` | Process count | 50 | 10-1000 |
| `fsize` | Max file size | 100MB | 1MB-1GB |
| `tmpfs_size` | /tmp size | 100m | 10m-2g |

## Validating Configuration

```bash
# Validate config file
tako-vm validate

# Show current configuration
tako-vm config

# Show as JSON
tako-vm config --json
```

Or in Python:

```python
from tako_vm.config import load_config, validate_config_file

# Validate without loading
errors = validate_config_file(Path("tako_vm.yaml"))
if errors:
    print(f"Invalid: {errors}")

# Load and inspect
config = load_config()
print(f"Workers: {config.max_workers}")
print(f"Job types: {len(config.job_types)}")
```

## Production Configuration

```yaml
production_mode: true     # require pre-built images
max_workers: 8
max_timeout: 60
default_timeout: 15
enable_seccomp: true
enable_userns: true

container_limits:
  pids_limit: 50
  nofile_soft: 128
  nofile_hard: 128
  tmpfs_size: "64m"
```
