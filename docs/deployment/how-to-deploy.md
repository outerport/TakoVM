# How to Deploy Tako VM

This guide covers common deployment scenarios for Tako VM.

## Deployment Options

| Method | Complexity | Use Case |
|--------|------------|----------|
| Direct on VM | Simple | Single server, full control |
| Docker Compose | Medium | Easy setup, portable |
| Kubernetes | Complex | Scalable, production |

---

## Option 1: Direct on a VM (Recommended for starters)

Deploy Tako VM directly on a Linux VM with Docker installed.

### 1. Provision a VM

Any cloud provider works (AWS EC2, GCP, DigitalOcean, etc.):

```bash
# Example: Ubuntu 22.04, 2 vCPU, 4GB RAM
```

### 2. Install Dependencies

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Install Python and uv
sudo apt update
sudo apt install -y python3
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone Tako VM
git clone https://github.com/YOUR_ORG/tako-vm.git
cd tako-vm

# Install Tako VM with server dependencies
uv pip install ".[server]"
```

### 3. Build the Executor Image

```bash
docker build -t code-executor:latest .
```

### 4. Configure

```bash
# Create config
cp tako_vm.yaml.example tako_vm.yaml

# Edit for production
nano tako_vm.yaml
```

```yaml
# tako_vm.yaml
production_mode: true
max_workers: 4
```

### 5. Run

```bash
# Run the server
tako-vm server

# Or with systemd (see production.md)
```

### 6. Test

```bash
curl http://YOUR_VM_IP:8000/health
```

---

## Option 2: Docker Compose (Recommended)

Run Tako VM itself in a container. The repo includes a ready-to-use `docker-compose.yaml`.

### Deploy

```bash
# Clone the repo
git clone https://github.com/YOUR_ORG/tako-vm.git
cd tako-vm

# Build both images and start the server
docker-compose --profile build up -d --build

# Check logs
docker-compose logs -f tako-vm
```

### What Gets Built

1. **tako-vm-server** - The API server (from `docker/Dockerfile.server`)
2. **code-executor** - The sandbox container for running code (from `docker/Dockerfile.executor`)

### docker-compose.yaml

The included `docker-compose.yaml` handles everything:

```yaml
services:
  tako-vm:
    build:
      context: .
      dockerfile: docker/Dockerfile.server
    image: tako-vm-server:latest
    ports:
      - "8000:8000"
    volumes:
      # Docker socket for spawning executor containers
      - /var/run/docker.sock:/var/run/docker.sock
      # Optional: mount custom config
      # - ./tako_vm.yaml:/app/tako_vm.yaml:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  # Build executor image (run once with --profile build)
  executor-build:
    build:
      context: .
      dockerfile: docker/Dockerfile.executor
    image: code-executor:latest
    command: ["echo", "Executor image built"]
    profiles:
      - build
```

### Customizing

To use a custom config:

```bash
# Create your config
cp tako_vm.yaml.example tako_vm.yaml
nano tako_vm.yaml

# Uncomment the volume mount in docker-compose.yaml, then restart
docker-compose down && docker-compose up -d
```

!!! warning "Security Note"
    Mounting `/var/run/docker.sock` gives Tako VM full Docker access. The executor containers are still isolated, but Tako VM itself has elevated privileges.

---

## Option 3: Kubernetes

For scalable production deployments.

### Prerequisites

- Kubernetes cluster
- Docker registry for images
- Persistent volume for data

### Build and Push Images

```bash
# Build Tako VM server
docker build -t your-registry/tako-vm:latest -f docker/Dockerfile.server .
docker push your-registry/tako-vm:latest

# Build executor image
docker build -t your-registry/code-executor:latest .
docker push your-registry/code-executor:latest
```

### Kubernetes Manifests

```yaml
# tako-vm-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tako-vm
spec:
  replicas: 2
  selector:
    matchLabels:
      app: tako-vm
  template:
    metadata:
      labels:
        app: tako-vm
    spec:
      containers:
      - name: tako-vm
        image: your-registry/tako-vm:latest
        ports:
        - containerPort: 8000
        env:
        - name: TAKO_VM_CONFIG
          value: /config/tako_vm.yaml
        - name: DOCKER_HOST
          value: tcp://localhost:2375
        volumeMounts:
        - name: config
          mountPath: /config
        - name: data
          mountPath: /root/.tako_vm

      # Docker-in-Docker sidecar
      - name: dind
        image: docker:dind
        securityContext:
          privileged: true
        env:
        - name: DOCKER_TLS_CERTDIR
          value: ""

      volumes:
      - name: config
        configMap:
          name: tako-vm-config
      - name: data
        persistentVolumeClaim:
          claimName: tako-vm-data
---
apiVersion: v1
kind: Service
metadata:
  name: tako-vm
spec:
  selector:
    app: tako-vm
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: tako-vm-config
data:
  tako_vm.yaml: |
    production_mode: true
    max_workers: 4
    docker_image: your-registry/code-executor:latest
```

### Deploy

```bash
kubectl apply -f tako-vm-deployment.yaml

# Check status
kubectl get pods -l app=tako-vm
kubectl logs -l app=tako-vm
```

---

## Quick Start Script

For the simplest deployment on a fresh Ubuntu VM:

```bash
#!/bin/bash
# deploy.sh - One-command Tako VM deployment

set -e

echo "=== Tako VM Deployment ==="

# Install Docker
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    newgrp docker
fi

# Install Python and uv
sudo apt-get update
sudo apt-get install -y python3 git
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone Tako VM
if [ ! -d "tako-vm" ]; then
    git clone https://github.com/YOUR_ORG/tako-vm.git
fi
cd tako-vm

# Install Tako VM with server dependencies
uv pip install ".[server]"

# Build executor image
docker build -t code-executor:latest .

# Create production config
cat > tako_vm.yaml << 'EOF'
production_mode: false
max_workers: 4
EOF

# Start server
echo "Starting Tako VM on port 8000..."
tako-vm server &

sleep 3
echo ""
echo "Tako VM is running!"
echo "Test with: curl http://localhost:8000/health"
```

Save and run:

```bash
chmod +x deploy.sh
./deploy.sh
```

---

## After Deployment

### Test Execution

```bash
curl -X POST http://YOUR_HOST:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "print(\"Hello from Tako VM!\")",
    "input_data": {}
  }'
```

### Monitor

```bash
# Health check
curl http://YOUR_HOST:8000/health

# View logs
tail -f /var/log/tako-vm.log  # if using systemd
docker-compose logs -f        # if using compose
```

---

## Firewall / Security Groups

Open only port 8000 (or your configured port):

```bash
# UFW (Ubuntu)
sudo ufw allow 8000/tcp

# AWS Security Group
# Inbound: TCP 8000 from your IP/range
```

For production, put behind a reverse proxy with TLS:

```
Client → Nginx (TLS) → Tako VM (localhost:8000)
```
