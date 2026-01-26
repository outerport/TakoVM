"""
Configuration management for Tako VM.

Loads configuration from YAML file with optional env var overrides for secrets.
"""

import os
from pathlib import Path
from typing import Optional, Any
import yaml

# Default config file locations (searched in order)
CONFIG_SEARCH_PATHS = [
    Path("tako_vm.yaml"),                     # Current directory
    Path("config/tako_vm.yaml"),              # Config subdirectory
    Path.home() / ".tako_vm" / "config.yaml", # User home
    Path("/etc/tako_vm/config.yaml"),         # System-wide
]


def get_default_data_dir() -> Path:
    """Get default data directory."""
    xdg_data = os.environ.get('XDG_DATA_HOME')
    if xdg_data:
        return Path(xdg_data) / 'tako_vm'
    return Path.home() / '.tako_vm'


# Default configuration values
DEFAULTS = {
    # Mode
    "production_mode": False,

    # Paths
    "data_dir": str(get_default_data_dir()),
    "api_keys_file": None,  # Defaults to data_dir/api_keys.json
    "database_file": None,  # Defaults to data_dir/executions.db
    "seccomp_profile_path": None,  # Uses bundled profile

    # Authentication
    "require_auth": False,

    # Queue
    "max_workers": 4,
    "max_queue_size": 100,

    # Output limits
    "max_stdout_bytes": 65536,      # 64KB
    "max_stderr_bytes": 65536,      # 64KB
    "max_artifact_bytes": 10485760, # 10MB
    "max_total_artifacts_bytes": 52428800,  # 50MB
    "max_input_bytes": 1048576,     # 1MB
    "max_code_bytes": 102400,       # 100KB

    # Execution
    "default_timeout": 30,
    "max_timeout": 300,

    # Retention
    "execution_record_ttl_days": 30,

    # Docker
    "docker_image": "code-executor:latest",
    "enable_seccomp": True,
    "enable_userns": True,  # Force non-root execution (--user=1000:1000)
}


class TakoVMConfig:
    """Tako VM configuration loaded from YAML file."""

    def __init__(self, config_dict: dict):
        """Initialize from config dictionary."""
        self._config = {**DEFAULTS, **config_dict}
        self._resolve_paths()

    def _resolve_paths(self):
        """Convert path strings to Path objects and set defaults."""
        # Convert data_dir to Path
        self._config["data_dir"] = Path(self._config["data_dir"])
        self._config["data_dir"].mkdir(parents=True, exist_ok=True)

        # Set default paths relative to data_dir
        data_dir = self._config["data_dir"]

        if self._config["api_keys_file"] is None:
            self._config["api_keys_file"] = data_dir / "api_keys.json"
        else:
            self._config["api_keys_file"] = Path(self._config["api_keys_file"])

        if self._config["database_file"] is None:
            self._config["database_file"] = data_dir / "executions.db"
        else:
            self._config["database_file"] = Path(self._config["database_file"])

        if self._config["seccomp_profile_path"] is None:
            self._config["seccomp_profile_path"] = Path(__file__).parent / "seccomp_profile.json"
        else:
            self._config["seccomp_profile_path"] = Path(self._config["seccomp_profile_path"])

    # Properties for type-safe access
    @property
    def production_mode(self) -> bool:
        return self._config["production_mode"]

    @property
    def data_dir(self) -> Path:
        return self._config["data_dir"]

    @property
    def api_keys_file(self) -> Path:
        return self._config["api_keys_file"]

    @property
    def database_file(self) -> Path:
        return self._config["database_file"]

    @property
    def seccomp_profile_path(self) -> Path:
        return self._config["seccomp_profile_path"]

    @property
    def require_auth(self) -> bool:
        return self._config["require_auth"]

    @property
    def max_workers(self) -> int:
        return self._config["max_workers"]

    @property
    def max_queue_size(self) -> int:
        return self._config["max_queue_size"]

    @property
    def max_stdout_bytes(self) -> int:
        return self._config["max_stdout_bytes"]

    @property
    def max_stderr_bytes(self) -> int:
        return self._config["max_stderr_bytes"]

    @property
    def max_artifact_bytes(self) -> int:
        return self._config["max_artifact_bytes"]

    @property
    def max_total_artifacts_bytes(self) -> int:
        return self._config["max_total_artifacts_bytes"]

    @property
    def max_input_bytes(self) -> int:
        return self._config["max_input_bytes"]

    @property
    def max_code_bytes(self) -> int:
        return self._config["max_code_bytes"]

    @property
    def default_timeout(self) -> int:
        return self._config["default_timeout"]

    @property
    def max_timeout(self) -> int:
        return self._config["max_timeout"]

    @property
    def execution_record_ttl_days(self) -> int:
        return self._config["execution_record_ttl_days"]

    @property
    def docker_image(self) -> str:
        return self._config["docker_image"]

    @property
    def enable_seccomp(self) -> bool:
        return self._config["enable_seccomp"]

    @property
    def enable_userns(self) -> bool:
        return self._config["enable_userns"]

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by key."""
        return self._config.get(key, default)


def find_config_file() -> Optional[Path]:
    """Find config file from search paths."""
    # Check env var override first
    env_config = os.environ.get("TAKO_VM_CONFIG")
    if env_config:
        path = Path(env_config)
        if path.exists():
            return path

    # Search default paths
    for path in CONFIG_SEARCH_PATHS:
        if path.exists():
            return path

    return None


def load_config(config_path: Optional[Path] = None) -> TakoVMConfig:
    """
    Load configuration from YAML file.

    Search order:
    1. Explicit path argument
    2. TAKO_VM_CONFIG environment variable
    3. ./tako_vm.yaml
    4. ./config/tako_vm.yaml
    5. ~/.tako_vm/config.yaml
    6. /etc/tako_vm/config.yaml
    7. Built-in defaults
    """
    config_dict = {}

    # Find config file
    if config_path is None:
        config_path = find_config_file()

    # Load from file if found
    if config_path and config_path.exists():
        with open(config_path) as f:
            loaded = yaml.safe_load(f)
            if loaded:
                config_dict = loaded

    # Apply env var overrides for sensitive paths
    if "TAKO_VM_DATA_DIR" in os.environ:
        config_dict["data_dir"] = os.environ["TAKO_VM_DATA_DIR"]
    if "TAKO_VM_API_KEYS_FILE" in os.environ:
        config_dict["api_keys_file"] = os.environ["TAKO_VM_API_KEYS_FILE"]
    if "TAKO_VM_DATABASE_FILE" in os.environ:
        config_dict["database_file"] = os.environ["TAKO_VM_DATABASE_FILE"]

    return TakoVMConfig(config_dict)


# Global config instance (lazy loaded)
_config: Optional[TakoVMConfig] = None


def get_config() -> TakoVMConfig:
    """Get global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: TakoVMConfig) -> None:
    """Set global configuration instance."""
    global _config
    _config = config
