# Installation

## Prerequisites

- **Docker** 20.10 or later
- **Python** 3.9 or later

## Install Tako VM

### Option 1: Install from PyPI (Recommended)

```bash
# SDK client only - for connecting to an existing Tako VM server
pip install tako-vm

# Full installation with server components
pip install tako-vm[server]

# All dependencies including dev tools
pip install tako-vm[all]
```

### Option 2: Install from Git

```bash
# SDK only
pip install git+https://github.com/example/tako-vm.git

# With server dependencies
pip install "tako-vm[server] @ git+https://github.com/example/tako-vm.git"
```

### Option 3: Install from Source

```bash
# Clone the repository
git clone https://github.com/example/tako-vm.git
cd tako-vm

# Install in development mode
pip install -e .              # SDK only
pip install -e ".[server]"    # With server
pip install -e ".[dev]"       # With dev dependencies
```

## Build the Docker Image

The Docker image is required to execute code:

```bash
docker build -t code-executor:latest .
```

This builds the base execution container with Python 3.11.

## Verify Installation

### Check the CLI

```bash
tako-vm version
# tako-vm 2.0.0
```

### Start the Server

```bash
tako-vm server
# or
python run_server.py
```

### Check Server Health

```bash
tako-vm status
# or
curl http://localhost:8000/health
```

Expected output:
```json
{
  "status": "healthy",
  "docker_available": true,
  "version": "2.0.0"
}
```

## Installation Extras

| Extra | Command | Description |
|-------|---------|-------------|
| `server` | `pip install tako-vm[server]` | FastAPI server, uvicorn, YAML config |
| `dev` | `pip install tako-vm[dev]` | pytest, ruff, development tools |
| `docs` | `pip install tako-vm[docs]` | MkDocs for documentation |
| `all` | `pip install tako-vm[all]` | All production dependencies |

## Directory Structure

After installation, your directory should look like:

```
tako-vm/
├── tako_vm/           # Main package
├── examples/          # Usage examples
├── docs/              # Documentation
├── scripts/           # Utility scripts
├── Dockerfile         # Container image
├── pyproject.toml     # Package configuration
├── tako_vm.yaml.example  # Config template
└── run_server.py      # Server entry point
```

## Troubleshooting

### Docker Not Found

If you see "Docker not available" errors:

```bash
# Check Docker is running
docker info

# On macOS/Windows, ensure Docker Desktop is running
```

### Permission Denied

If you get permission errors with Docker:

```bash
# Add your user to the docker group (Linux)
sudo usermod -aG docker $USER
# Log out and back in for changes to take effect
```

### Port Already in Use

If port 8000 is busy:

```bash
# Use a different port
tako-vm server --port 8001
```

## Next Steps

- Continue to [Quick Start](quickstart.md) to run your first code
- See [Configuration](configuration.md) for customization options
