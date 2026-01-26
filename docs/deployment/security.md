# Security

Tako VM implements defense-in-depth to safely execute untrusted code.

## Security Layers

```
┌─────────────────────────────────────────────────────────────┐
│                      Input Validation                        │
│                  (Size limits, sanitization)                 │
├─────────────────────────────────────────────────────────────┤
│                    Container Isolation                       │
│              (Docker with security restrictions)             │
├─────────────────────────────────────────────────────────────┤
│                     Syscall Filtering                        │
│                    (Seccomp whitelist)                       │
├─────────────────────────────────────────────────────────────┤
│                    Resource Limits                           │
│               (Memory, CPU, time, file size)                 │
├─────────────────────────────────────────────────────────────┤
│                    Output Sanitization                       │
│              (Capped output, error filtering)                │
└─────────────────────────────────────────────────────────────┘
```

## Container Security

### Network Isolation

By default, containers have no network access:

```bash
docker run --network=none ...
```

This prevents:
- Data exfiltration
- Command & control communication
- Attacks on internal services
- Cryptocurrency mining pools

**Selective Network Access**

For jobs that need network (e.g., API calls), configure per job type:

```yaml
job_types:
  - name: api-client
    network_enabled: true
    allowed_hosts:
      - "api.openai.com"
      - "*.amazonaws.com"
```

The `allowed_hosts` field is advisory by default. For enforcement, set up the egress proxy (see `scripts/proxy/`).

### Read-Only Filesystem

```bash
docker run --read-only ...
```

Writable locations:
- `/output/` - For results
- `/tmp/` - Temporary files (noexec)

### Capability Dropping

All Linux capabilities are dropped:

```bash
docker run --cap-drop=ALL --security-opt=no-new-privileges ...
```

### Non-Root Execution

Code runs as unprivileged user (uid 1000) inside the container:

```dockerfile
# In Dockerfile
USER sandbox
```

```bash
# At runtime (enforced by Tako VM)
docker run --user=1000:1000 ...
```

This is controlled by `enable_userns: true` (default). Even if container code somehow modifies the Dockerfile or image, the `--user` flag at runtime ensures non-root execution.

### Ephemeral Containers

Containers are destroyed after each execution:

```bash
docker run --rm ...
```

No persistent state between executions.

## Seccomp Filtering

Seccomp (Secure Computing Mode) restricts available syscalls.

### Enabled Syscalls

The whitelist includes safe operations:
- File I/O (read, write, open, close)
- Memory (mmap, brk)
- Process (exit, getpid)
- Time (clock_gettime)

### Blocked Syscalls

Dangerous syscalls are blocked:
- `ptrace` - Process debugging
- `mount` - Filesystem mounting
- `reboot` - System reboot
- `sethostname` - Hostname changes
- `init_module` - Kernel modules

### Custom Profile

The profile is at `tako_vm/seccomp_profile.json`:

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "syscalls": [
    {
      "names": ["read", "write", "open", ...],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
```

## Resource Limits

### Memory Limits

```bash
docker run --memory=512m --memory-swap=512m ...
```

Prevents:
- Memory exhaustion attacks
- Fork bombs consuming RAM

### CPU Limits

```bash
docker run --cpus=1.0 ...
```

Prevents CPU starvation of other processes.

### Process Limits

```bash
docker run --pids-limit=100 ...
```

Prevents fork bombs.

### File Size Limits

```bash
docker run --ulimit=fsize=104857600 ...  # 100MB
```

Prevents disk filling attacks.

### Time Limits

Enforced timeout kills long-running processes:

```python
timeout = job.get("timeout", 30)
subprocess.run(..., timeout=timeout)
```

## Input Validation

### Size Limits

| Input | Limit | Configuration |
|-------|-------|---------------|
| Code | 100KB | `max_code_bytes` |
| Input data | 1MB | `max_input_bytes` |
| Timeout | 300s | `max_timeout` |

### Output Limits

| Output | Limit | Configuration |
|--------|-------|---------------|
| stdout | 64KB | `max_stdout_bytes` |
| stderr | 64KB | `max_stderr_bytes` |
| Single artifact | 10MB | `max_artifact_bytes` |
| Total artifacts | 50MB | `max_total_artifacts_bytes` |

## Error Sanitization

Stack traces are sanitized to prevent information leakage:

```python
# Internal path: /var/lib/tako-vm/workspace/job-123/code/main.py
# Sanitized:     /code/main.py
```

## API Security

### HTTPS

Always use TLS in production:

```nginx
listen 443 ssl http2;
ssl_protocols TLSv1.2 TLSv1.3;
```

## Threat Model

### In Scope

Tako VM protects against:

| Threat | Mitigation |
|--------|------------|
| Code execution escape | Container isolation, seccomp |
| Resource exhaustion | Memory, CPU, time limits |
| Data exfiltration | Network isolation |
| Disk filling | File size limits |
| Information leakage | Output sanitization |

### Out of Scope

Tako VM does NOT protect against:

| Threat | Reason |
|--------|--------|
| Docker daemon compromise | Requires Docker access |
| Host kernel exploits | Containers share kernel |
| Side-channel attacks | Shared CPU/memory |
| Timing attacks | Execution time visible |

For higher security, consider:
- gVisor or Kata Containers
- Dedicated execution hosts
- VM-based isolation

## Docker Isolation Limitations

Docker containers share the host kernel, which has security implications:

### What Docker Provides

| Protection | Level | Notes |
|------------|-------|-------|
| Filesystem isolation | Good | Separate root filesystem |
| Process isolation | Good | Separate PID namespace |
| Network isolation | Good | `--network=none` blocks all |
| User isolation | Moderate | UID mapping available |
| Syscall filtering | Good | Seccomp whitelist |

### What Docker Does NOT Provide

| Risk | Description | Mitigation |
|------|-------------|------------|
| Kernel exploits | Container escapes via kernel bugs | Keep kernel updated, use gVisor |
| Resource side-channels | CPU cache timing attacks | Dedicated hosts |
| `/proc` information | Process info leakage | Restrict `/proc` access |
| Device access | Hardware access if not restricted | `--cap-drop=ALL` |

### Stronger Isolation Options

For high-security environments:

**1. gVisor** (Google)
- User-space kernel that intercepts syscalls
- Significant performance overhead
- Strong isolation without VMs

```bash
docker run --runtime=runsc ...
```

**2. Kata Containers**
- Lightweight VMs with container UX
- Hardware-level isolation
- Higher resource overhead

**3. Firecracker** (AWS)
- MicroVMs for serverless
- Used by AWS Lambda
- Sub-second boot times

**4. Dedicated Hosts**
- Run Tako VM on isolated machines
- Network segmentation
- Physical separation

### Recommendation

| Use Case | Recommended Isolation |
|----------|----------------------|
| Development | Docker (default) |
| Internal tools | Docker + seccomp |
| Multi-tenant SaaS | gVisor or Kata |
| High-security | Firecracker or dedicated VMs |

## Architecture Considerations

### Does Containerizing the API Server Add Security?

**Short answer: No.** The API server needs Docker socket access to spawn executor containers. Docker socket access effectively grants root privileges on the host, so containerizing the server doesn't create a meaningful security boundary.

```
Current Model (adequate for most cases):
┌─────────────────────────────────────────┐
│              Host/VM                     │
│  ┌──────────────┐    ┌───────────────┐  │
│  │  Tako VM     │───▶│   Executor    │  │
│  │  Server      │    │   Container   │  │
│  │  (trusted)   │    │  (untrusted)  │  │
│  └──────────────┘    └───────────────┘  │
└─────────────────────────────────────────┘
```

**Why containerize the server anyway?**

- Easier deployment (Docker Compose, Kubernetes)
- Consistent environment across machines
- Simpler updates and rollbacks

**For true separation (future consideration):**

```
High-Security Model (separate hosts):
┌─────────────┐     ┌─────────────────────────────┐
│   Host A    │     │          Host B             │
│  ┌───────┐  │     │  ┌───────┐   ┌──────────┐  │
│  │ API   │──┼────▶│  │Docker │──▶│ Executor │  │
│  │Server │  │ RPC │  │ Agent │   │Container │  │
│  └───────┘  │     │  └───────┘   └──────────┘  │
└─────────────┘     └─────────────────────────────┘
```

This separates the API server from the execution environment entirely, but adds significant complexity. See the project ROADMAP.md for future plans.

## Security Checklist

- [ ] Enable `enable_seccomp: true`
- [ ] Enable `enable_userns: true` (non-root execution)
- [ ] Use HTTPS in production
- [ ] Set appropriate resource limits
- [ ] Keep Docker updated
- [ ] For network-enabled jobs, use `allowed_hosts` to restrict domains
- [ ] Monitor for anomalies
- [ ] Review execution logs
- [ ] Test security controls regularly
