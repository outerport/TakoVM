#!/bin/bash

# Setup script for Secure Code Executor POC
# This script helps automate the initial setup process

set -e  # Exit on error

echo "========================================="
echo "Secure Code Executor - Setup Script"
echo "========================================="
echo ""

# Check prerequisites
echo "Checking prerequisites..."

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    echo "   Visit: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "❌ Docker is not running. Please start Docker."
    exit 1
fi

echo "✅ Docker is installed and running"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.11+."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "✅ Python $PYTHON_VERSION is installed"

echo ""
echo "========================================="
echo "Step 1: Building example custom library"
echo "========================================="

cd custom_libs/example_lib

if [ ! -f "setup.py" ]; then
    echo "❌ setup.py not found in custom_libs/example_lib"
    exit 1
fi

echo "Building example_lib wheel..."
python3 setup.py bdist_wheel

if [ -f "dist/example_lib-1.0.0-py3-none-any.whl" ]; then
    cp dist/example_lib-1.0.0-py3-none-any.whl ../
    echo "✅ example_lib wheel built and copied to custom_libs/"
else
    echo "⚠️  Warning: Wheel file not found, but continuing..."
fi

cd ../..

echo ""
echo "========================================="
echo "Step 2: Building Docker image"
echo "========================================="

echo "Building code-executor:latest..."
docker build -t code-executor:latest .

if [ $? -eq 0 ]; then
    echo "✅ Docker image built successfully"
else
    echo "❌ Docker image build failed"
    exit 1
fi

echo ""
echo "========================================="
echo "Step 3: Installing Python dependencies"
echo "========================================="

# Check if virtual environment exists
if [ -d "venv" ]; then
    echo "Virtual environment already exists at ./venv"
    read -p "Do you want to use it? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        source venv/bin/activate
    fi
else
    read -p "Create a virtual environment? (recommended) (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        python3 -m venv venv
        source venv/bin/activate
        echo "✅ Virtual environment created and activated"
    fi
fi

echo "Installing requirements..."
pip install -r requirements.txt

echo "Installing dev requirements..."
pip install -r dev-requirements.txt

echo "✅ Dependencies installed"

echo ""
echo "========================================="
echo "Setup Complete! 🎉"
echo "========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Start the API server:"
echo "   python api_server.py"
echo ""
echo "2. In another terminal, test the API:"
echo "   python example_api_client.py"
echo ""
echo "3. View API documentation:"
echo "   Open http://localhost:8000/docs in your browser"
echo ""
echo "4. Run tests (optional):"
echo "   pytest -v"
echo ""
echo "For more information, see README.md"
echo ""
