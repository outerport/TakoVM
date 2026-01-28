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
# SERVER & LOGGING
# ==============================================================================
log_level: INFO            # DEBUG, INFO, WARNING, ERROR, CRITICAL
server_host: "0.0.0.0"     # Host to bind to
server_port: 8000          # Port to bind to

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
# RETRY CONFIGURATION
# ==============================================================================
max_retry_attempts: 2     # Retries for transient failures
retry_base_delay: 1.0     # Base delay between retries (seconds)
queue_wait_timeout: 1.0   # Queue wait timeout (seconds)

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

Tako VM supports the following environment variables for configuration:

```bash
# Override config file location
export TAKO_VM_CONFIG=/path/to/config.yaml

# Override data directory
export TAKO_VM_DATA_DIR=/var/lib/tako_vm

# Override database file path
export TAKO_VM_DATABASE_FILE=/var/lib/tako_vm/executions.db

# Override workspace directory for job file storage
export TAKO_VM_WORKSPACE=/var/tmp/tako_vm

# XDG Base Directory support
export XDG_DATA_HOME=/custom/data/path  # Tako VM will use $XDG_DATA_HOME/tako_vm
```

| Variable | Description | Default |
|----------|-------------|---------|
| `TAKO_VM_CONFIG` | Config file path | Search in standard locations |
| `TAKO_VM_DATA_DIR` | Data directory | `~/.tako_vm` or `$XDG_DATA_HOME/tako_vm` |
| `TAKO_VM_DATABASE_FILE` | SQLite database path | `$DATA_DIR/executions.db` |
| `TAKO_VM_WORKSPACE` | Job workspace directory | System temp directory |
| `XDG_DATA_HOME` | XDG base data directory | `~/.local/share` |

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
log_level: WARNING        # reduce log verbosity
max_workers: 8
max_timeout: 60
default_timeout: 15
enable_seccomp: true
enable_userns: true

# Retry configuration for production
max_retry_attempts: 3
retry_base_delay: 2.0

container_limits:
  pids_limit: 50
  nofile_soft: 128
  nofile_hard: 128
  tmpfs_size: "64m"
```

## New Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `log_level` | Logging level | `INFO` |
| `server_host` | Server bind host | `0.0.0.0` |
| `server_port` | Server bind port | `8000` |
| `max_retry_attempts` | Max retries for transient failures | `2` |
| `retry_base_delay` | Base delay between retries (seconds) | `1.0` |
| `queue_wait_timeout` | Queue wait timeout (seconds) | `1.0` |
