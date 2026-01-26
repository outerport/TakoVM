"""
Tako VM Command Line Interface.

Usage:
    tako-vm server          Start the Tako VM server
    tako-vm status          Check server health
    tako-vm validate        Validate configuration file
    tako-vm config          Show current configuration
    tako-vm version         Show version
"""

# Suppress LibreSSL warnings on macOS before any other imports
import warnings
try:
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except ImportError:
    pass

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="tako-vm",
        description="Tako VM - Secure Python code execution",
    )

    # Global options
    parser.add_argument(
        "--config", "-c",
        type=Path,
        help="Path to configuration file (overrides default search paths)",
        metavar="FILE"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Server command
    server_parser = subparsers.add_parser("server", help="Start the Tako VM server")
    server_parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    server_parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    server_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    server_parser.add_argument("--workers", type=int, help="Number of worker processes (overrides config)")

    # Status command
    status_parser = subparsers.add_parser("status", help="Check server health")
    status_parser.add_argument("--url", default="http://localhost:8000", help="Server URL")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate configuration file")
    validate_parser.add_argument(
        "config_file",
        type=Path,
        nargs="?",
        help="Configuration file to validate (uses --config or default search if not specified)"
    )

    # Config command
    config_parser = subparsers.add_parser("config", help="Show current configuration")
    config_parser.add_argument("--json", action="store_true", help="Output as JSON")
    config_parser.add_argument("--show-defaults", action="store_true", help="Show all values including defaults")

    # Version command
    subparsers.add_parser("version", help="Show version")

    args = parser.parse_args()

    # Set global config path if provided
    if args.config:
        from tako_vm.config import set_config_path
        if not args.config.exists():
            print(f"Error: Config file not found: {args.config}", file=sys.stderr)
            sys.exit(1)
        set_config_path(args.config)

    if args.command == "server":
        run_server(args)
    elif args.command == "status":
        check_status(args)
    elif args.command == "validate":
        validate_config(args)
    elif args.command == "config":
        show_config(args)
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
        from tako_vm.config import get_config, ConfigurationError
    except ImportError:
        print("Error: Server dependencies not installed.")
        print("Install with: pip install tako-vm[server]")
        sys.exit(1)

    # Validate config before starting
    try:
        config = get_config()
        print("Configuration loaded successfully")
        if config.production_mode:
            print("Running in PRODUCTION mode")
        else:
            print("Running in DEVELOPMENT mode")
    except ConfigurationError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
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


def validate_config(args):
    """Validate a configuration file."""
    from tako_vm.config import validate_config_file, find_config_file, ConfigurationError

    # Determine which file to validate
    config_file = args.config_file
    if config_file is None:
        # Use --config if provided, otherwise search
        if hasattr(args, 'config') and args.config:
            config_file = args.config
        else:
            config_file = find_config_file()

    if config_file is None:
        print("No configuration file found.")
        print("Create tako_vm.yaml or specify a file with --config or as argument.")
        sys.exit(1)

    print(f"Validating: {config_file}")

    errors = validate_config_file(config_file)

    if errors:
        print("\nValidation FAILED:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    else:
        print("Configuration is valid!")

        # Show summary
        try:
            from tako_vm.config import load_config
            config = load_config(config_file)
            print("\nSummary:")
            print(f"  Mode: {'production' if config.production_mode else 'development'}")
            print(f"  Workers: {config.max_workers}")
            print(f"  Max timeout: {config.max_timeout}s")
            print(f"  Job types defined: {len(config.job_types)}")
            if config.job_types:
                for jt in config.job_types:
                    print(f"    - {jt.name}")
        except Exception:
            pass


def show_config(args):
    """Show current configuration."""
    from tako_vm.config import get_config, get_config_path, ConfigurationError

    try:
        config = get_config()
    except ConfigurationError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    config_file = get_config_path()

    if args.json:
        import json
        # Export as JSON
        data = config.model_dump(exclude={'_resolved_data_dir',
                                          '_resolved_database_file', '_resolved_seccomp_profile_path'})
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Tako VM Configuration")
        print("=" * 50)
        if config_file:
            print(f"Config file: {config_file}")
        else:
            print("Config file: (using defaults)")
        print()

        print("[Mode]")
        print(f"  production_mode: {config.production_mode}")
        print()

        print("[Paths]")
        print(f"  data_dir: {config.data_dir}")
        print(f"  database_file: {config.database_file}")
        print()

        print("[Queue & Workers]")
        print(f"  max_workers: {config.max_workers}")
        print(f"  max_queue_size: {config.max_queue_size}")
        print()

        print("[Limits]")
        print(f"  default_timeout: {config.default_timeout}s")
        print(f"  max_timeout: {config.max_timeout}s")
        print(f"  max_stdout_bytes: {config.max_stdout_bytes}")
        print(f"  max_code_bytes: {config.max_code_bytes}")
        print()

        print("[Container Limits]")
        limits = config.container_limits
        print(f"  nofile: {limits.nofile_soft}:{limits.nofile_hard}")
        print(f"  nproc: {limits.nproc_soft}:{limits.nproc_hard}")
        print(f"  fsize: {limits.fsize}")
        print(f"  tmpfs_size: {limits.tmpfs_size}")
        print(f"  pids_limit: {limits.pids_limit}")
        print()

        print("[Docker]")
        print(f"  docker_image: {config.docker_image}")
        print(f"  enable_seccomp: {config.enable_seccomp}")
        print(f"  enable_userns: {config.enable_userns}")
        print()

        if config.job_types:
            print("[Job Types]")
            for jt in config.job_types:
                print(f"  - {jt.name}:")
                print(f"      memory: {jt.memory_limit}, cpu: {jt.cpu_limit}, timeout: {jt.timeout}s")
                if jt.requirements:
                    print(f"      requirements: {', '.join(jt.requirements)}")


if __name__ == "__main__":
    main()
