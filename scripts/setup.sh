#!/bin/bash

# Setup script for Tako VM
set -e

echo "========================================="
echo "Tako VM - Setup Script"
echo "========================================="
echo ""

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo "Docker is not installed. Please install Docker first."
    echo "   Visit: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "Docker is not running. Please start Docker."
    exit 1
fi

echo "Docker is installed and running"

if ! command -v python3 &> /dev/null; then
    echo "Python 3 is not installed. Please install Python 3.9+."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "Python $PYTHON_VERSION is installed"

echo ""
echo "========================================="
echo "Building Docker image"
echo "========================================="

echo "Building code-executor:latest..."
docker build -t code-executor:latest .

echo "Docker image built successfully"

echo ""
echo "========================================="
echo "Installing Tako VM"
echo "========================================="

if command -v uv &> /dev/null; then
    echo "Using uv for installation..."
    uv pip install -e ".[server]"
else
    echo "Using pip for installation..."
    pip install -e ".[server]"
fi

echo "Tako VM installed"

echo ""
echo "========================================="
echo "Setup Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Start the API server:"
echo "   tako-vm server"
echo ""
echo "2. In another terminal, run the example:"
echo "   python examples/01_basic_execution.py"
echo ""
echo "3. View API documentation:"
echo "   Open http://localhost:8000/docs"
echo ""
