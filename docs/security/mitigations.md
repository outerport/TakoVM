# Security Mitigations for /proc Exposure

This document provides actionable steps to mitigate the /proc filesystem exposure vulnerability identified in the [Equixly security research](https://equixly.com/blog/2025/11/04/path-traversal-ai-containers/).

## Quick Reference

| Mitigation | Priority | Effort | Impact |
|-----------|----------|--------|--------|
| Enable seccomp profile | **HIGH** | Low | Blocks dangerous syscalls |
| Mask /proc paths | **HIGH** | Medium | Hides sensitive /proc files |
| Remove env var secrets | **CRITICAL** | Medium | Prevents secret leakage |
| Document limitations | **HIGH** | Low | User awareness |
| Add AppArmor/SELinux | Medium | High | Additional MAC layer |

## 1. Enable Seccomp Profile (Priority: HIGH)

### Implementation

The seccomp profile is located at `tako_vm/seccomp_profile.json`:

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "defaultErrnoRet": 1,
  "archMap": [
    {
      "architecture": "SCMP_ARCH_X86_64",
      "subArchitectures": ["SCMP_ARCH_X86", "SCMP_ARCH_X32"]
    },
    {
      "architecture": "SCMP_ARCH_AARCH64",
      "subArchitectures": ["SCMP_ARCH_ARM"]
    }
  ],
  "syscalls": [
    {
      "names": [
        "accept", "accept4", "access", "arch_prctl", "bind", "brk",
        "capget", "capset", "chdir", "chmod", "chown", "clock_getres",
        "clock_gettime", "clock_nanosleep", "close", "connect", "copy_file_range",
        "creat", "dup", "dup2", "dup3", "epoll_create", "epoll_create1",
        "epoll_ctl", "epoll_ctl_old", "epoll_pwait", "epoll_wait", "epoll_wait_old",
        "eventfd", "eventfd2", "execve", "execveat", "exit", "exit_group",
        "faccessat", "faccessat2", "fadvise64", "fallocate", "fanotify_mark",
        "fchdir", "fchmod", "fchmodat", "fchown", "fchownat", "fcntl", "fdatasync",
        "fgetxattr", "flistxattr", "flock", "fork", "fremovexattr", "fsetxattr",
        "fstat", "fstatfs", "fsync", "ftruncate", "futex", "getcpu", "getcwd",
        "getdents", "getdents64", "getegid", "geteuid", "getgid", "getgroups",
        "getitimer", "getpeername", "getpgid", "getpgrp", "getpid", "getppid",
        "getpriority", "getrandom", "getresgid", "getresuid", "getrlimit",
        "get_robust_list", "getrusage", "getsid", "getsockname", "getsockopt",
        "get_thread_area", "gettid", "gettimeofday", "getuid", "getxattr",
        "inotify_add_watch", "inotify_init", "inotify_init1", "inotify_rm_watch",
        "io_cancel", "ioctl", "io_destroy", "io_getevents", "ioprio_get",
        "ioprio_set", "io_setup", "io_submit", "kill", "lchown", "lgetxattr",
        "link", "linkat", "listen", "listxattr", "llistxattr", "lremovexattr",
        "lseek", "lsetxattr", "lstat", "madvise", "memfd_create", "mincore",
        "mkdir", "mkdirat", "mknod", "mknodat", "mlock", "mlock2", "mlockall",
        "mmap", "mprotect", "mq_getsetattr", "mq_notify", "mq_open", "mq_timedreceive",
        "mq_timedsend", "mq_unlink", "mremap", "msgctl", "msgget", "msgrcv",
        "msgsnd", "msync", "munlock", "munlockall", "munmap", "nanosleep",
        "newfstatat", "open", "openat", "pause", "pipe", "pipe2", "poll",
        "ppoll", "prctl", "pread64", "preadv", "preadv2", "prlimit64", "pselect6",
        "pwrite64", "pwritev", "pwritev2", "read", "readahead", "readlink",
        "readlinkat", "readv", "recv", "recvfrom", "recvmmsg", "recvmsg",
        "remap_file_pages", "removexattr", "rename", "renameat", "renameat2",
        "restart_syscall", "rmdir", "rt_sigaction", "rt_sigpending", "rt_sigprocmask",
        "rt_sigqueueinfo", "rt_sigreturn", "rt_sigsuspend", "rt_sigtimedwait",
        "rt_tgsigqueueinfo", "sched_getaffinity", "sched_getattr", "sched_getparam",
        "sched_get_priority_max", "sched_get_priority_min", "sched_getscheduler",
        "sched_rr_get_interval", "sched_setaffinity", "sched_setattr", "sched_setparam",
        "sched_setscheduler", "sched_yield", "seccomp", "select", "semctl",
        "semget", "semop", "semtimedop", "send", "sendfile", "sendmmsg", "sendmsg",
        "sendto", "setfsgid", "setfsuid", "setgid", "setgroups", "setitimer",
        "setpgid", "setpriority", "setregid", "setresgid", "setresuid", "setreuid",
        "setrlimit", "set_robust_list", "setsid", "setsockopt", "set_thread_area",
        "set_tid_address", "setuid", "setxattr", "shmat", "shmctl", "shmdt",
        "shmget", "shutdown", "sigaltstack", "signalfd", "signalfd4", "socket",
        "socketpair", "splice", "stat", "statfs", "statx", "symlink", "symlinkat",
        "sync", "sync_file_range", "syncfs", "sysinfo", "tee", "tgkill", "time",
        "timer_create", "timer_delete", "timerfd_create", "timerfd_gettime",
        "timerfd_settime", "timer_getoverrun", "timer_gettime", "timer_settime",
        "times", "tkill", "truncate", "umask", "uname", "unlink", "unlinkat",
        "utime", "utimensat", "utimes", "vfork", "vmsplice", "wait4", "waitid",
        "write", "writev"
      ],
      "action": "SCMP_ACT_ALLOW"
    },
    {
      "names": ["ptrace", "process_vm_readv", "process_vm_writev"],
      "action": "SCMP_ACT_ERRNO",
      "errnoRet": 1
    }
  ]
}
```

### Update worker.py

```python
# In _run_container method (tako_vm/execution/worker.py)

# Add seccomp profile ALWAYS (not just when enabled)
seccomp_path = Path(__file__).parent / "seccomp_profile.json"
if seccomp_path.exists():
    cmd.append(f"--security-opt=seccomp={seccomp_path}")
else:
    logger.warning("Default seccomp profile not found at %s", seccomp_path)
```

### Update config.py

```python
# Change default from False to True
enable_seccomp: bool = True  # Enable by default

# Point to default profile
seccomp_profile_path: Optional[Path] = Field(
    default=Path(__file__).parent / "seccomp_profile.json",
    description="Path to seccomp profile JSON"
)
```

## 2. Mask Sensitive /proc Paths (Priority: HIGH)

### Option A: Use Docker's built-in masking (Docker 20.10+)

```python
# In _run_container method
cmd.extend([
    # Mask environment variables
    "--security-opt=mask=/proc/self/environ",
    "--security-opt=mask=/proc/*/environ",
])
```

### Option B: Custom AppArmor profile

Create `config/apparmor-takvm` profile:

```
#include <tunables/global>

profile takvm flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  #include <abstractions/python>

  # Allow reading most of /proc
  /proc/** r,

  # DENY access to sensitive /proc paths
  deny /proc/*/environ r,
  deny /proc/self/environ r,
  deny /proc/*/fd/** r,
  deny /proc/self/fd/** r,

  # Allow normal operations
  /code/** r,
  /input/** r,
  /output/** rw,
  /tmp/** rw,

  # Python runtime
  /usr/bin/python* rix,
  /usr/lib/python*/** r,
}
```

Apply in Docker:

```python
cmd.append("--security-opt=apparmor=takvm")
```

## 3. Remove Secrets from Environment Variables (Priority: CRITICAL)

### Current Problem

```python
# INSECURE: Secrets passed as environment variables
for key, value in job_type.environment.items():
    cmd.append(f"--env={key}={value}")
```

### Solution: Use Read-Only Config Files

```python
def _prepare_config_file(self, job_type: JobType, input_dir: Path) -> None:
    """Write job type configuration to read-only file instead of env vars."""
    if job_type.environment:
        config_data = {
            "environment": job_type.environment,
            "allowed_hosts": job_type.allowed_hosts if job_type.allowed_hosts else [],
        }

        config_file = input_dir / "_config.json"
        config_file.write_text(json.dumps(config_data))
        config_file.chmod(0o444)  # Read-only

# In _run_container method:
self._prepare_config_file(job_type, input_dir)

# User code reads config from file:
# with open('/input/_config.json') as f:
#     config = json.load(f)
```

### Update Documentation

Add to user-facing docs:

```markdown
## Accessing Configuration

Configuration values are provided in `/input/_config.json` (not environment variables):

```python
import json

with open('/input/_config.json') as f:
    config = json.load(f)

api_key = config['environment'].get('API_KEY')
```

**Security Note:** Never pass secrets via job submission. Use your platform's
secret management for sensitive credentials.
```

## 4. Runtime Dependency Isolation (Priority: MEDIUM)

### Current Problem

```python
# TAKO_REQUIREMENTS exposed in environment
cmd.append(f"--env=TAKO_REQUIREMENTS={reqs_str}")
```

### Solution: Pre-install deps in entrypoint from file

```bash
# In docker/entrypoint.sh

# Read requirements from file instead of env var
if [ -f /input/_requirements.txt ]; then
    uv pip install -r /input/_requirements.txt
fi
```

```python
# In _run_container method
if validated_reqs:
    reqs_file = input_dir / "_requirements.txt"
    reqs_file.write_text("\n".join(validated_reqs))
    reqs_file.chmod(0o444)
    # No longer need: cmd.append(f"--env=TAKO_REQUIREMENTS={reqs_str}")
```

## 5. Documentation Updates (Priority: HIGH)

### Add to docs/deployment/security.md

```markdown
## Known Limitations

### /proc Filesystem Access

User code has read access to the `/proc` filesystem, which can expose:
- Container process information
- File descriptors of open files
- Limited host system metadata

**Mitigations:**
1. Do NOT pass secrets via environment variables
2. Use seccomp profiles to block dangerous syscalls
3. Consider AppArmor/SELinux for additional restrictions

**Reference:** [/proc Exposure Vulnerability Analysis](../security/proc-exposure-vulnerability.md)
```

### Add to README.md

```markdown
## Security Considerations

Tako VM uses Docker for isolation but has known limitations:

- ⚠️ User code can read `/proc` filesystem (process info, file descriptors)
- ✅ Network isolated by default (`--network=none`)
- ✅ Read-only root filesystem
- ✅ Runs as non-root user (uid 1000)
- ✅ Seccomp profile blocks dangerous syscalls

**For production use:**
1. Enable all security features in `tako_vm.yaml`
2. Never pass secrets via environment variables
3. Use pre-built images for network-isolated execution
4. Monitor execution logs for suspicious activity

See [Security Documentation](docs/deployment/security.md) for details.
```

## 6. Testing Mitigations

Create `tests/test_security_mitigations.py`:

```python
def test_proc_environ_blocked_with_seccomp(executor):
    """Verify /proc/self/environ is blocked when seccomp is enabled."""
    code = """
try:
    with open('/proc/self/environ', 'rb') as f:
        data = f.read()
    result = "FAIL: /proc/self/environ is readable"
except PermissionError:
    result = "PASS: /proc/self/environ is blocked"
except Exception as e:
    result = f"PASS: Access prevented ({type(e).__name__})"

with open('/output/result.json', 'w') as f:
    import json
    json.dump({"result": result}, f)
"""
    # Test with seccomp enabled
    executor.config.enable_seccomp = True
    job = {"code": code, "input_data": {}}
    result = executor.execute_job(job)

    assert "PASS" in result["output"]["result"]
```

## Implementation Roadmap

1. **Phase 1 - Quick Wins (Week 1)**
   - [ ] Add seccomp profile to `config/`
   - [ ] Enable seccomp by default in config.py
   - [ ] Update documentation with limitations
   - [ ] Add warning to logs when env vars contain "secret", "key", "token", "password"

2. **Phase 2 - Environment Variable Migration (Week 2)**
   - [ ] Implement config file approach for job_type.environment
   - [ ] Update entrypoint.sh to read requirements from file
   - [ ] Migrate examples to use config files
   - [ ] Add deprecation warning for env var secrets

3. **Phase 3 - Advanced Hardening (Week 3-4)**
   - [ ] Add AppArmor profile option
   - [ ] Implement /proc masking
   - [ ] Add security audit logging
   - [ ] Create security test suite

4. **Phase 4 - Production Hardening (Week 5+)**
   - [ ] Add runtime security monitoring
   - [ ] Implement secret scanning in submissions
   - [ ] Add rate limiting and abuse detection
   - [ ] Security audit by third party

## Testing Plan

```bash
# Run security tests
pytest tests/test_proc_exposure.py -v

# Run with mitigations enabled
TAKO_VM_ENABLE_SECCOMP=true pytest tests/test_security_mitigations.py -v

# Verify no secrets in logs
grep -r "password\|secret\|token" ~/.tako_vm/logs/
```

## References

- [Equixly: The False Security of AI Containers](https://equixly.com/blog/2025/11/04/path-traversal-ai-containers/)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)
- [Linux Security Modules (LSM)](https://www.kernel.org/doc/html/latest/admin-guide/LSM/)
- [Seccomp BPF](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html)
