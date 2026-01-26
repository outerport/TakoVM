# Production Deployment

Guide for deploying Tako VM in production environments.

## Production Configuration

Create a production config file:

```yaml
# /etc/tako-vm/config.yaml

production_mode: true      # Require pre-built images
require_auth: true         # Require API keys

# Scale workers based on CPU cores
max_workers: 8
max_queue_size: 500

# Tighter limits
default_timeout: 30
max_timeout: 120
max_stdout_bytes: 32768    # 32KB
max_artifact_bytes: 5242880  # 5MB

# Security
enable_seccomp: true
```

## Pre-build Images

In production mode, images must be pre-built:

```bash
# Build all registered environments
python -m tako_vm.execution.builder --init-defaults all

# Verify images exist
docker images | grep tako-vm
```

## Running with Systemd

Create a systemd service:

```ini
# /etc/systemd/system/tako-vm.service

[Unit]
Description=Tako VM Code Execution Service
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=tako-vm
Group=tako-vm
WorkingDirectory=/opt/tako-vm
Environment=TAKO_VM_CONFIG=/etc/tako-vm/config.yaml
ExecStart=/usr/bin/python3 -m uvicorn tako_vm.server.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/tako-vm

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable tako-vm
sudo systemctl start tako-vm
sudo systemctl status tako-vm
```

## Running with Docker Compose

```yaml
# docker-compose.yml

version: '3.8'

services:
  tako-vm:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./config.yaml:/etc/tako-vm/config.yaml:ro
      - tako-vm-data:/var/lib/tako-vm
    environment:
      - TAKO_VM_CONFIG=/etc/tako-vm/config.yaml
    restart: unless-stopped

volumes:
  tako-vm-data:
```

!!! warning
    Mounting the Docker socket gives Tako VM access to the Docker daemon. In high-security environments, consider using Docker-in-Docker or a separate Docker host.

## Reverse Proxy (Nginx)

```nginx
# /etc/nginx/sites-available/tako-vm

upstream tako_vm {
    server 127.0.0.1:8000;
}

server {
    listen 443 ssl http2;
    server_name tako-vm.example.com;

    ssl_certificate /etc/ssl/certs/tako-vm.crt;
    ssl_certificate_key /etc/ssl/private/tako-vm.key;

    # Security headers
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;

    # Request size limits
    client_max_body_size 2M;

    location / {
        proxy_pass http://tako_vm;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts for long-running jobs
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
    }

    # Health check endpoint (no auth)
    location /health {
        proxy_pass http://tako_vm/health;
    }
}
```

## Monitoring

### Health Checks

```bash
# Kubernetes/Docker health check
curl -f http://localhost:8000/health || exit 1
```

### Metrics to Monitor

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| Queue depth | Pending jobs | > 50 |
| Worker utilization | Running / max_workers | > 80% |
| Error rate | Failed / total | > 5% |
| P95 latency | 95th percentile execution time | > 10s |

### Log Aggregation

Tako VM logs to stdout. Configure your log aggregator:

```bash
# View logs
journalctl -u tako-vm -f

# JSON logging (add to uvicorn)
--log-config logging.yaml
```

## Scaling

### Horizontal Scaling

Run multiple Tako VM instances behind a load balancer:

```
                    ┌─────────────┐
                    │   Nginx     │
                    │   (LB)      │
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  Tako VM 1  │ │  Tako VM 2  │ │  Tako VM 3  │
    └─────────────┘ └─────────────┘ └─────────────┘
           │               │               │
           └───────────────┼───────────────┘
                           ▼
                    ┌─────────────┐
                    │   Docker    │
                    │   Host      │
                    └─────────────┘
```

### Vertical Scaling

Increase workers per instance:

```yaml
max_workers: 16  # More concurrent executions
```

## Backup & Recovery

### Data to Backup

| Path | Contents | Frequency |
|------|----------|-----------|
| `/var/lib/tako-vm/executions.db` | Execution records | Daily |
| `/var/lib/tako-vm/api_keys.json` | API keys | On change |
| `/etc/tako-vm/config.yaml` | Configuration | On change |

### Backup Script

```bash
#!/bin/bash
# /opt/tako-vm/backup.sh

BACKUP_DIR=/var/backups/tako-vm
DATE=$(date +%Y%m%d)

mkdir -p $BACKUP_DIR

# Backup database
sqlite3 /var/lib/tako-vm/executions.db ".backup '$BACKUP_DIR/executions-$DATE.db'"

# Backup config
cp /var/lib/tako-vm/api_keys.json $BACKUP_DIR/api_keys-$DATE.json

# Retain 30 days
find $BACKUP_DIR -mtime +30 -delete
```

## Checklist

Before going to production:

- [ ] Enable `production_mode: true`
- [ ] Enable `require_auth: true`
- [ ] Pre-build all required images
- [ ] Configure TLS/HTTPS
- [ ] Set up monitoring and alerting
- [ ] Configure log aggregation
- [ ] Set up automated backups
- [ ] Test failure scenarios
- [ ] Document API keys distribution
- [ ] Set appropriate rate limits
