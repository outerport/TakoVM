"""
API key authentication and rate limiting for Tako VM.

Provides API key management, verification, and request rate limiting.
"""

import hashlib
import secrets
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Tuple

from tako_vm.models import APIKey
from tako_vm.storage import ExecutionStorage


class APIKeyManager:
    """
    Manages API keys with file-based persistence and hot-reload.

    Keys are stored hashed (SHA256) and never in plain text.
    """

    def __init__(self, keys_file: Path):
        """
        Initialize key manager.

        Args:
            keys_file: Path to JSON file storing API keys
        """
        self.keys_file = keys_file
        self._keys: Dict[str, APIKey] = {}
        self._key_hash_to_id: Dict[str, str] = {}
        self._last_modified: float = 0
        self._load()

    def _load(self) -> None:
        """Load keys from JSON file."""
        if not self.keys_file.exists():
            return

        try:
            with open(self.keys_file, 'r') as f:
                data = json.load(f)

            self._keys.clear()
            self._key_hash_to_id.clear()

            for key_data in data.get('keys', []):
                # Parse datetime fields
                if 'created_at' in key_data and isinstance(key_data['created_at'], str):
                    key_data['created_at'] = datetime.fromisoformat(key_data['created_at'])
                if 'expires_at' in key_data and isinstance(key_data['expires_at'], str):
                    key_data['expires_at'] = datetime.fromisoformat(key_data['expires_at'])

                key = APIKey(**key_data)
                self._keys[key.key_id] = key
                self._key_hash_to_id[key.key_hash] = key.key_id

            self._last_modified = self.keys_file.stat().st_mtime

        except (json.JSONDecodeError, IOError) as e:
            # Log error but don't fail - just use empty keys
            print(f"Warning: Could not load API keys from {self.keys_file}: {e}")

    def _save(self) -> None:
        """Save keys to JSON file."""
        self.keys_file.parent.mkdir(parents=True, exist_ok=True)

        data = {
            'keys': [
                {
                    **key.model_dump(),
                    'created_at': key.created_at.isoformat(),
                    'expires_at': key.expires_at.isoformat() if key.expires_at else None,
                }
                for key in self._keys.values()
            ]
        }

        with open(self.keys_file, 'w') as f:
            json.dump(data, f, indent=2)

        self._last_modified = self.keys_file.stat().st_mtime

    def reload(self) -> None:
        """Hot-reload keys from file if modified."""
        if not self.keys_file.exists():
            return

        current_mtime = self.keys_file.stat().st_mtime
        if current_mtime > self._last_modified:
            self._load()

    def verify(self, raw_key: str) -> Optional[APIKey]:
        """
        Verify API key and return if valid.

        Args:
            raw_key: Raw API key string

        Returns:
            APIKey if valid, None otherwise
        """
        # Hot-reload if file changed
        self.reload()

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_id = self._key_hash_to_id.get(key_hash)

        if not key_id:
            return None

        key = self._keys.get(key_id)
        if not key:
            return None

        if not key.is_valid():
            return None

        return key

    def get_key(self, key_id: str) -> Optional[APIKey]:
        """Get API key by ID."""
        return self._keys.get(key_id)

    def list_keys(self) -> list[APIKey]:
        """List all API keys."""
        return list(self._keys.values())

    def create_key(
        self,
        name: str,
        tenant_id: Optional[str] = None,
        rate_limit_per_minute: int = 60,
        rate_limit_per_hour: int = 1000,
        max_concurrent_jobs: int = 5,
        allowed_job_types: Optional[list[str]] = None,
        expires_at: Optional[datetime] = None
    ) -> Tuple[str, APIKey]:
        """
        Create new API key.

        Args:
            name: Human-readable name
            tenant_id: Optional tenant/project ID
            rate_limit_per_minute: Rate limit per minute
            rate_limit_per_hour: Rate limit per hour
            max_concurrent_jobs: Max concurrent jobs
            allowed_job_types: Allowed job types (None = all)
            expires_at: Optional expiration time

        Returns:
            Tuple of (raw_key, APIKey)
        """
        raw_key, key_hash = self.generate_key()
        key_id = str(uuid.uuid4())[:8]

        api_key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            tenant_id=tenant_id,
            rate_limit_per_minute=rate_limit_per_minute,
            rate_limit_per_hour=rate_limit_per_hour,
            max_concurrent_jobs=max_concurrent_jobs,
            allowed_job_types=allowed_job_types or ["*"],
            expires_at=expires_at,
        )

        self._keys[key_id] = api_key
        self._key_hash_to_id[key_hash] = key_id
        self._save()

        return raw_key, api_key

    def revoke_key(self, key_id: str) -> bool:
        """
        Revoke an API key.

        Args:
            key_id: Key ID to revoke

        Returns:
            True if key was found and revoked
        """
        key = self._keys.get(key_id)
        if not key:
            return False

        key.enabled = False
        self._save()
        return True

    def delete_key(self, key_id: str) -> bool:
        """
        Delete an API key.

        Args:
            key_id: Key ID to delete

        Returns:
            True if key was found and deleted
        """
        key = self._keys.pop(key_id, None)
        if not key:
            return False

        self._key_hash_to_id.pop(key.key_hash, None)
        self._save()
        return True

    @staticmethod
    def generate_key() -> Tuple[str, str]:
        """
        Generate new API key.

        Returns:
            Tuple of (raw_key, key_hash)
        """
        raw_key = f"tvmk_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        return raw_key, key_hash


class RateLimiter:
    """
    Rate limiter using SQLite for persistence.

    Supports per-minute and per-hour rate limits.
    """

    def __init__(self, storage: ExecutionStorage):
        """
        Initialize rate limiter.

        Args:
            storage: ExecutionStorage instance for persistence
        """
        self.storage = storage

    def check_rate(self, key: APIKey) -> Tuple[bool, Optional[str]]:
        """
        Check if request is within rate limits.

        Args:
            key: API key to check

        Returns:
            Tuple of (allowed, reason) - reason is set if not allowed
        """
        now = datetime.utcnow()

        # Check per-minute limit
        minute_window = now.strftime('%Y-%m-%dT%H:%M')
        minute_count = self.storage.get_request_count(key.key_id, minute_window)

        if minute_count >= key.rate_limit_per_minute:
            return False, f"Rate limit exceeded: {key.rate_limit_per_minute}/minute"

        # Check per-hour limit
        hour_window = now.strftime('%Y-%m-%dT%H')
        hour_count = self.storage.get_request_count(key.key_id, f"h:{hour_window}")

        if hour_count >= key.rate_limit_per_hour:
            return False, f"Rate limit exceeded: {key.rate_limit_per_hour}/hour"

        return True, None

    def record_request(self, key: APIKey) -> None:
        """
        Record a request for rate limiting.

        Args:
            key: API key that made the request
        """
        now = datetime.utcnow()

        # Increment minute counter
        minute_window = now.strftime('%Y-%m-%dT%H:%M')
        self.storage.increment_request_count(key.key_id, minute_window)

        # Increment hour counter
        hour_window = now.strftime('%Y-%m-%dT%H')
        self.storage.increment_request_count(key.key_id, f"h:{hour_window}")

    def get_usage(self, key: APIKey) -> dict:
        """
        Get current usage for API key.

        Args:
            key: API key to check

        Returns:
            Dict with current usage stats
        """
        now = datetime.utcnow()

        minute_window = now.strftime('%Y-%m-%dT%H:%M')
        minute_count = self.storage.get_request_count(key.key_id, minute_window)

        hour_window = now.strftime('%Y-%m-%dT%H')
        hour_count = self.storage.get_request_count(key.key_id, f"h:{hour_window}")

        return {
            'requests_this_minute': minute_count,
            'requests_this_hour': hour_count,
            'limit_per_minute': key.rate_limit_per_minute,
            'limit_per_hour': key.rate_limit_per_hour,
        }
