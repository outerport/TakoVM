#!/usr/bin/env zsh
# Tako VM Demo Script
# Compatible with zsh, uses uv for package management

set -e

# Colors for output
autoload -U colors && colors
info() { print -P "%F{blue}[INFO]%f $1" }
success() { print -P "%F{green}[OK]%f $1" }
warn() { print -P "%F{yellow}[WARN]%f $1" }
error() { print -P "%F{red}[ERROR]%f $1" }
header() { print -P "\n%F{cyan}═══════════════════════════════════════════════════════════%f\n%F{cyan}  $1%f\n%F{cyan}═══════════════════════════════════════════════════════════%f\n" }

# Check for uv
if ! command -v uv &> /dev/null; then
    error "uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Check for Docker
if ! command -v docker &> /dev/null; then
    error "Docker not found. Please install Docker first."
    exit 1
fi

DEMO_DIR="${0:A:h}"
cd "$DEMO_DIR"

# ============================================================================
header "1. SETUP - Install Tako VM with uv"
# ============================================================================

info "Creating virtual environment with uv..."
uv venv --quiet .venv 2>/dev/null || true
source .venv/bin/activate

info "Installing tako-vm..."
uv pip install -e ".[server]" --quiet

success "Tako VM installed"
tako-vm version

# ============================================================================
header "2. CONFIGURATION - Show the config system"
# ============================================================================

info "Displaying current configuration..."
tako-vm config

info "Validating example config..."
tako-vm validate tako_vm.yaml.example

# ============================================================================
header "3. CONFIG VALIDATION - Pydantic catches errors"
# ============================================================================

info "Testing invalid config (max_workers=100, limit is 64)..."
cat > /tmp/bad_config.yaml << 'EOF'
max_workers: 100
EOF

tako-vm validate /tmp/bad_config.yaml 2>&1 || success "Validation correctly rejected invalid config"

info "Testing invalid timeout relationship..."
cat > /tmp/bad_timeout.yaml << 'EOF'
default_timeout: 500
max_timeout: 100
EOF

tako-vm validate /tmp/bad_timeout.yaml 2>&1 || success "Validation caught default > max timeout"

# ============================================================================
header "4. BUILD BASE IMAGE"
# ============================================================================

info "Building code-executor base image..."
if [[ -f "Dockerfile.executor" ]]; then
    docker build -t code-executor:latest -f Dockerfile.executor . --quiet
    success "Base image built"
else
    warn "Dockerfile not found, skipping image build"
fi

# ============================================================================
header "5. START SERVER (background)"
# ============================================================================

info "Starting Tako VM server..."
cp tako_vm.yaml.example tako_vm.yaml 2>/dev/null || true

# Start server in background
tako-vm server --port 8000 &
SERVER_PID=$!
sleep 3

# Check if server is running
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    success "Server running on http://localhost:8000"
    curl -s http://localhost:8000/health | python3 -m json.tool
else
    error "Server failed to start"
    exit 1
fi

# ============================================================================
header "6. EXECUTE CODE"
# ============================================================================

info "Simple execution - print sum..."
curl -s -X POST http://localhost:8000/execute \
    -H "Content-Type: application/json" \
    -d '{"code": "print(sum(range(10)))", "input_data": {}}' | python3 -m json.tool

info "Execution with input data..."
curl -s -X POST http://localhost:8000/execute \
    -H "Content-Type: application/json" \
    -d '{
        "code": "import json\nwith open(\"/input/data.json\") as f:\n    data = json.load(f)\nprint(f\"Got: {data}\")",
        "input_data": {"message": "Hello from Tako VM!"}
    }' | python3 -m json.tool

# ============================================================================
header "7. SECURITY DEMO - Network isolation"
# ============================================================================

info "Testing network access (should fail - default is isolated)..."
RESULT=$(curl -s -X POST http://localhost:8000/execute \
    -H "Content-Type: application/json" \
    -d '{
        "code": "import socket; socket.create_connection((\"8.8.8.8\", 53), timeout=2)",
        "input_data": {}
    }')
echo "$RESULT" | python3 -m json.tool
if echo "$RESULT" | grep -q "error\|false"; then
    success "Network correctly blocked for isolated job"
fi

# ============================================================================
header "8. RESOURCE LIMITS"
# ============================================================================

info "Testing memory limit (trying to allocate 1GB in 512MB container)..."
curl -s -X POST http://localhost:8000/execute \
    -H "Content-Type: application/json" \
    -d '{
        "code": "x = \"A\" * (1024 * 1024 * 800)",
        "input_data": {},
        "timeout": 10
    }' | python3 -m json.tool

info "Testing timeout (infinite loop with 2s timeout)..."
curl -s -X POST http://localhost:8000/execute \
    -H "Content-Type: application/json" \
    -d '{
        "code": "while True: pass",
        "input_data": {},
        "timeout": 2
    }' | python3 -m json.tool

# ============================================================================
header "9. CLEANUP"
# ============================================================================

info "Stopping server..."
kill $SERVER_PID 2>/dev/null || true
wait $SERVER_PID 2>/dev/null || true
success "Server stopped"

# ============================================================================
header "DEMO COMPLETE"
# ============================================================================

print -P "
%F{green}Tako VM Features Demonstrated:%f
  - uv-based installation
  - YAML configuration with Pydantic validation
  - Config validation with clear error messages
  - Docker-based code execution
  - Network isolation (--network=none)
  - Memory and timeout limits
  - Configurable container limits

%F{cyan}Next steps:%f
  - Try job types: data-processing, ml-inference, api-client
  - Production mode: production_mode: true
  - Configure allowed_hosts for network-enabled jobs
  - Check /health endpoint for circuit breaker status
"
