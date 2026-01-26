# Tako VM Roadmap

## Current Status: v2.1.0

Production-ready sandbox runtime with:
- ✅ Docker container isolation
- ✅ Concurrent worker pool
- ✅ Execution records & audit logging
- ✅ Seccomp syscall filtering
- ✅ YAML configuration
- ✅ Multiple execution environments
- ✅ Circuit breaker for Docker availability
- ✅ Dead letter queue for failed jobs
- ✅ Correlation IDs for request tracing
- ✅ Structured logging with JSON format

---

## Planned Features

### Security Enhancements

| Feature | Priority | Description |
|---------|----------|-------------|
| **gVisor support** | High | Add `docker_runtime: runsc` config option for stronger isolation |
| Kata Containers support | Medium | Lightweight VM isolation |
| User namespace remapping | Medium | Run containers with mapped UIDs |

### Scalability

| Feature | Priority | Description |
|---------|----------|-------------|
| Redis queue backend | Medium | Replace in-memory queue for distributed deployments |
| Horizontal scaling | Medium | Multiple Tako VM instances with shared state |
| Job priority levels | Low | Priority queue for different job types |

### Developer Experience

| Feature | Priority | Description |
|---------|----------|-------------|
| Web UI dashboard | Medium | View jobs, logs, metrics |
| Webhook notifications | Low | Notify on job completion |
| OpenAPI spec | Low | Auto-generated API documentation |

### Observability

| Feature | Priority | Description |
|---------|----------|-------------|
| Prometheus metrics | Medium | `/metrics` endpoint |
| Tracing support | Low | OpenTelemetry integration |

---

## Future Considerations

### Alternative Runtimes

For use cases requiring stronger isolation than Docker:

1. **gVisor** - User-space kernel, ~50ms overhead
2. **Kata Containers** - Lightweight VMs
3. **Firecracker** - MicroVMs (used by AWS Lambda)

### Separated Execution Hosts

For high-security deployments, separate the API server from execution:

```
┌─────────────┐     ┌─────────────────────────────┐
│   Host A    │     │          Host B             │
│  ┌───────┐  │     │  ┌───────┐   ┌──────────┐  │
│  │ API   │──┼────▶│  │Docker │──▶│ Executor │  │
│  │Server │  │ RPC │  │ Agent │   │Container │  │
│  └───────┘  │     │  └───────┘   └──────────┘  │
└─────────────┘     └─────────────────────────────┘
```

This would require:
- Remote Docker API or custom agent
- Secure communication channel
- State synchronization

Not planned for near-term, but documented for future consideration.

### Language Support

Currently Python-only. Potential additions:
- JavaScript/Node.js
- Go
- Rust (WASM)

---

## Contributing

Interested in working on any of these? Open an issue to discuss!
