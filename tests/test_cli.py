"""
Tests for Tako VM CLI (command-line interface).

Tests command parsing and execution using subprocess/mock approaches.
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestCLIVersion:
    """Tests for version command."""

    def test_version_command(self):
        """tako-vm version prints version."""
        result = subprocess.run(
            [sys.executable, "-m", "tako_vm.cli", "version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "tako-vm" in result.stdout.lower()
        assert "2.0.0" in result.stdout


class TestCLIHelp:
    """Tests for help output."""

    def test_help_without_command(self):
        """tako-vm without command shows help."""
        result = subprocess.run(
            [sys.executable, "-m", "tako_vm.cli"],
            capture_output=True,
            text=True,
        )
        # Should exit with error and show help
        assert result.returncode == 1
        assert "usage" in result.stderr.lower() or "usage" in result.stdout.lower()

    def test_help_flag(self):
        """tako-vm --help shows help."""
        result = subprocess.run(
            [sys.executable, "-m", "tako_vm.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "server" in result.stdout
        assert "status" in result.stdout
        assert "validate" in result.stdout
        assert "config" in result.stdout


class TestCLIValidate:
    """Tests for validate command."""

    def test_validate_valid_config(self):
        """validate command accepts valid config."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(
                """
max_workers: 4
default_timeout: 30
security_mode: permissive
"""
            )
            f.flush()
            config_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, "-m", "tako_vm.cli", "validate", config_path],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "valid" in result.stdout.lower()
        finally:
            Path(config_path).unlink()

    def test_validate_invalid_config(self):
        """validate command rejects invalid config."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(
                """
max_workers: -1
"""
            )
            f.flush()
            config_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, "-m", "tako_vm.cli", "validate", config_path],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 1
            assert "failed" in result.stdout.lower() or "error" in result.stderr.lower()
        finally:
            Path(config_path).unlink()

    def test_validate_nonexistent_file(self):
        """validate command handles missing file."""
        result = subprocess.run(
            [sys.executable, "-m", "tako_vm.cli", "validate", "/nonexistent/config.yaml"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1


class TestCLIConfig:
    """Tests for config command."""

    def test_config_show(self):
        """config command shows configuration."""
        result = subprocess.run(
            [sys.executable, "-m", "tako_vm.cli", "config"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "TAKO_VM_SECURITY_MODE": "permissive"},
        )
        assert result.returncode == 0
        assert "Tako VM Configuration" in result.stdout
        assert "max_workers" in result.stdout

    def test_config_json_output(self):
        """config --json outputs JSON."""
        result = subprocess.run(
            [sys.executable, "-m", "tako_vm.cli", "config", "--json"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "TAKO_VM_SECURITY_MODE": "permissive"},
        )
        assert result.returncode == 0
        # Should be valid JSON
        import json

        data = json.loads(result.stdout)
        assert "max_workers" in data


class TestCLIStatus:
    """Tests for status command."""

    def test_status_server_not_running(self):
        """status command handles server not running."""
        result = subprocess.run(
            [sys.executable, "-m", "tako_vm.cli", "status", "--url", "http://localhost:59999"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "cannot connect" in result.stdout.lower() or "error" in result.stdout.lower()


class TestCLIConfigPath:
    """Tests for --config global option."""

    def test_config_path_nonexistent(self):
        """--config with nonexistent file shows error."""
        result = subprocess.run(
            [sys.executable, "-m", "tako_vm.cli", "--config", "/nonexistent.yaml", "version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "not found" in result.stderr.lower()

    def test_config_path_valid(self):
        """--config with valid file is accepted."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("max_workers: 4\n")
            f.flush()
            config_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, "-m", "tako_vm.cli", "--config", config_path, "version"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
        finally:
            Path(config_path).unlink()


class TestCLIMain:
    """Tests for main() function directly."""

    def test_main_import(self):
        """Can import main from cli module."""
        from tako_vm.cli import main

        assert callable(main)

    def test_main_check_status_function(self):
        """check_status function handles connection errors."""
        from tako_vm.cli import check_status

        # Create mock args
        args = MagicMock()
        args.url = "http://localhost:59999"

        with pytest.raises(SystemExit) as exc_info:
            check_status(args)
        assert exc_info.value.code == 1

    def test_main_show_config_function(self):
        """show_config function works."""
        from tako_vm.cli import show_config
        from tako_vm.config import reset_config

        reset_config()

        args = MagicMock()
        args.json = False
        args.show_defaults = False

        # Should not raise
        show_config(args)

        reset_config()

    def test_main_show_config_json(self, capsys):
        """show_config with --json outputs JSON."""
        from tako_vm.cli import show_config
        from tako_vm.config import reset_config

        reset_config()

        args = MagicMock()
        args.json = True
        args.show_defaults = False

        show_config(args)

        captured = capsys.readouterr()
        import json

        data = json.loads(captured.out)
        assert "max_workers" in data

        reset_config()
