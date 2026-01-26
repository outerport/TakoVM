"""
Tako VM Command Line Interface.

Usage:
    tako-vm server          Start the Tako VM server
    tako-vm execute <file>  Execute a Python file
    tako-vm status          Check server health
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="tako-vm",
        description="Tako VM - Secure Python code execution",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Server command
    server_parser = subparsers.add_parser("server", help="Start the Tako VM server")
    server_parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    server_parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    server_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    # Status command
    status_parser = subparsers.add_parser("status", help="Check server health")
    status_parser.add_argument("--url", default="http://localhost:8000", help="Server URL")

    # Version command
    subparsers.add_parser("version", help="Show version")

    args = parser.parse_args()

    if args.command == "server":
        run_server(args)
    elif args.command == "status":
        check_status(args)
    elif args.command == "version":
        print("tako-vm 2.0.0")
    else:
        parser.print_help()
        sys.exit(1)


def run_server(args):
    """Start the Tako VM server."""
    try:
        import uvicorn
        from tako_vm.server.app import app
    except ImportError:
        print("Error: Server dependencies not installed.")
        print("Install with: pip install tako-vm[server]")
        sys.exit(1)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def check_status(args):
    """Check server health status."""
    import requests

    try:
        response = requests.get(f"{args.url}/health", timeout=5)
        data = response.json()
        print(f"Status: {data.get('status', 'unknown')}")
        print(f"Docker: {'available' if data.get('docker_available') else 'unavailable'}")
        print(f"Version: {data.get('version', 'unknown')}")
    except requests.exceptions.ConnectionError:
        print(f"Error: Cannot connect to {args.url}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
