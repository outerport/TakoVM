#!/bin/bash
# Tako VM Quick Start Script
# Run this after cloning the repo

set -e

echo "=== Tako VM Quick Start ==="
echo ""

# 1. Check prerequisites
echo "[1/5] Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker not found. Please install Docker first."
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "ERROR: Docker daemon not running. Please start Docker."
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found."
    exit 1
fi

echo "  ✓ Docker available"
echo "  ✓ Python 3 available"

# 2. Install dependencies
echo ""
echo "[2/5] Installing Tako VM..."
if command -v uv &> /dev/null; then
    uv pip install -e ".[server]" -q
else
    pip3 install -e ".[server]" -q
fi
echo "  ✓ Tako VM installed"

# 3. Build Docker image
echo ""
echo "[3/5] Building Docker image (this may take a minute)..."
docker build -t code-executor:latest . -q
echo "  ✓ Docker image built"

# 4. Start server in background
echo ""
echo "[4/5] Starting Tako VM server..."
tako-vm server &
SERVER_PID=$!
sleep 3

# Check if server started
if ! curl -s http://localhost:8000/health > /dev/null; then
    echo "ERROR: Server failed to start"
    kill $SERVER_PID 2>/dev/null
    exit 1
fi
echo "  ✓ Server running at http://localhost:8000"

# 5. Run test
echo ""
echo "[5/5] Running test execution..."
RESULT=$(curl -s -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import json\nwith open(\"/input/data.json\") as f: data=json.load(f)\nresult={\"sum\": data[\"x\"] + data[\"y\"]}\nwith open(\"/output/result.json\",\"w\") as f: json.dump(result,f)\nprint(\"Executed!\")",
    "input_data": {"x": 10, "y": 20}
  }')

echo "  Response: $RESULT"

# Cleanup
kill $SERVER_PID 2>/dev/null

echo ""
echo "=== Quick Start Complete ==="
echo ""
echo "To run the server:"
echo "  tako-vm server"
echo ""
echo "To execute code:"
echo "  curl -X POST http://localhost:8000/execute -H 'Content-Type: application/json' -d '{\"code\": \"...\", \"input_data\": {}}'"
echo ""
echo "See examples/ for more usage patterns."
