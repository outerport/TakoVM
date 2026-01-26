# Tako VM Egress Proxy

This directory contains an **optional** egress proxy setup for enforcing `allowed_hosts` rules.

## How It Works

When a job type has `network_enabled: true` and `allowed_hosts` configured:

1. Tako VM checks if the `tako-proxy` Docker network exists
2. If yes: Container connects to proxy network, routes through Squid
3. If no: Warning logged, container gets unrestricted bridge network

## Current Limitations

The current implementation has limitations:

- **Header-based validation**: Relies on containers passing `X-Tako-Allowed-Hosts` header
- **Trust model**: Container code could omit the header or forge it
- **Not fully enforced**: This is advisory, not a security boundary

For production environments requiring strict enforcement, consider:

1. **Network policies** (Kubernetes NetworkPolicy)
2. **Service mesh** (Istio, Linkerd with egress policies)
3. **Cloud firewalls** (AWS Security Groups, GCP Firewall Rules)

## Quick Start (Development)

```bash
# Start the proxy
cd scripts/proxy
docker-compose up -d

# Verify network exists
docker network ls | grep tako-proxy
```

## Architecture

```
                     ┌─────────────────┐
                     │   Tako VM API   │
                     └────────┬────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
      ┌───────────┐   ┌───────────┐   ┌───────────┐
      │ Container │   │ Container │   │ Container │
      │ (no net)  │   │ (bridge)  │   │ (proxy)   │
      └───────────┘   └─────┬─────┘   └─────┬─────┘
                            │               │
                            │               ▼
                            │       ┌─────────────┐
                            │       │ Squid Proxy │
                            │       │ (allowlist) │
                            │       └─────┬───────┘
                            │             │
                            ▼             ▼
                     ┌─────────────────────────┐
                     │       Internet          │
                     └─────────────────────────┘
```

## Configuration Examples

```yaml
job_types:
  # No network access (most secure)
  - name: compute-only
    network_enabled: false

  # Unrestricted network (least secure)
  - name: web-scraper
    network_enabled: true

  # Allowlist-based (requires proxy)
  - name: openai-caller
    network_enabled: true
    allowed_hosts:
      - "api.openai.com"
      - "*.openai.azure.com"
```

## Future Improvements

For strict enforcement, we plan to support:

1. **DNS-based filtering**: Custom DNS that only resolves allowed domains
2. **Per-container iptables**: Automatic firewall rules based on allowlist
3. **Integration with cloud providers**: Native VPC/firewall rule generation
