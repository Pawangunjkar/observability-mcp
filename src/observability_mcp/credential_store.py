"""
Credential Store for Observability MCP Server.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

Manages observability backend credentials with a layered lookup strategy:
1. Environment variables (PROMETHEUS_URL, LOKI_URL, TEMPO_URL, GRAFANA_URL, GRAFANA_TOKEN)
2. In-memory cache (current server session)
3. Local file cache (~/.observability-mcp-cache/credentials.json)
4. If none found → returns None (server will prompt user)
"""

import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class ObsCredentials:
    """Observability backend connection credentials."""
    prometheus_url: str = ""
    loki_url: str = ""
    tempo_url: str = ""
    grafana_url: str = ""
    grafana_token: str = ""
    timeout: int = 30

    def to_dict(self) -> dict:
        """Convert to dictionary (excludes None values)."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "ObsCredentials":
        """Create from dictionary."""
        return cls(
            prometheus_url=data.get("prometheus_url", "").rstrip("/"),
            loki_url=data.get("loki_url", "").rstrip("/"),
            tempo_url=data.get("tempo_url", "").rstrip("/"),
            grafana_url=data.get("grafana_url", "").rstrip("/"),
            grafana_token=data.get("grafana_token", ""),
            timeout=int(data.get("timeout", 30)),
        )

    def masked_dict(self) -> dict:
        """Return dict with token masked (for display)."""
        d = self.to_dict()
        if "grafana_token" in d and d["grafana_token"]:
            token = d["grafana_token"]
            d["grafana_token"] = token[:4] + "****" + token[-4:] if len(token) > 8 else "****"
        return d


class CredentialStore:
    """
    Multi-layer credential store for observability connections.

    Credentials are cached in memory and optionally persisted to a JSON file.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        """Initialize the credential store."""
        self._memory_cache: dict[str, ObsCredentials] = {}

        # Determine cache directory
        if cache_dir:
            self._cache_dir = Path(cache_dir)
        else:
            env_cache_dir = os.environ.get("OBS_MCP_CACHE_DIR")
            if env_cache_dir:
                self._cache_dir = Path(env_cache_dir)
            else:
                self._cache_dir = Path.home() / ".observability-mcp-cache"

        self._cache_file = self._cache_dir / "credentials.json"
        self._ensure_cache_dir()
        self._load_file_cache()

    def _ensure_cache_dir(self):
        """Create cache directory if it doesn't exist."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                os.chmod(self._cache_dir, 0o700)
        except OSError as e:
            print(f"Warning: Could not create cache directory: {e}", file=sys.stderr)

    def _load_file_cache(self):
        """Load credentials from the JSON cache file."""
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r") as f:
                    data = json.load(f)
                for name, cred_data in data.items():
                    if name not in self._memory_cache:
                        self._memory_cache[name] = ObsCredentials.from_dict(cred_data)
                print(f"Loaded {len(data)} cached credential(s) from {self._cache_file}", file=sys.stderr)
            except (json.JSONDecodeError, KeyError, OSError) as e:
                print(f"Warning: Could not load credential cache: {e}", file=sys.stderr)

    def _save_file_cache(self):
        """Save all credentials to the JSON cache file."""
        try:
            data = {name: cred.to_dict() for name, cred in self._memory_cache.items()}
            with open(self._cache_file, "w") as f:
                json.dump(data, f, indent=2)
            if sys.platform != "win32":
                os.chmod(self._cache_file, 0o600)
        except OSError as e:
            print(f"Warning: Could not save credential cache: {e}", file=sys.stderr)

    def get_from_env(self) -> Optional[ObsCredentials]:
        """
        Try to get credentials from environment variables.

        Looks for: PROMETHEUS_URL, LOKI_URL, TEMPO_URL, GRAFANA_URL, GRAFANA_TOKEN
        """
        has_any = any(
            os.environ.get(key)
            for key in ("PROMETHEUS_URL", "LOKI_URL", "TEMPO_URL", "GRAFANA_URL")
        )
        if not has_any:
            return None

        return ObsCredentials(
            prometheus_url=os.environ.get("PROMETHEUS_URL", "").rstrip("/"),
            loki_url=os.environ.get("LOKI_URL", "").rstrip("/"),
            tempo_url=os.environ.get("TEMPO_URL", "").rstrip("/"),
            grafana_url=os.environ.get("GRAFANA_URL", "").rstrip("/"),
            grafana_token=os.environ.get("GRAFANA_TOKEN", ""),
            timeout=int(os.environ.get("OBS_TIMEOUT", "30")),
        )

    def get_credentials(self, session_name: str = "default") -> Optional[ObsCredentials]:
        """
        Get credentials using layered lookup.

        Order: env vars (for 'default') → memory cache → file cache → None
        """
        if session_name == "default":
            env_creds = self.get_from_env()
            if env_creds:
                return env_creds

        if session_name in self._memory_cache:
            return self._memory_cache[session_name]

        return None

    def save_credentials(
        self,
        session_name: str,
        prometheus_url: str = "",
        loki_url: str = "",
        tempo_url: str = "",
        grafana_url: str = "",
        grafana_token: str = "",
        timeout: int = 30,
    ) -> ObsCredentials:
        """Save credentials to memory and file cache."""
        creds = ObsCredentials(
            prometheus_url=prometheus_url.rstrip("/"),
            loki_url=loki_url.rstrip("/"),
            tempo_url=tempo_url.rstrip("/"),
            grafana_url=grafana_url.rstrip("/"),
            grafana_token=grafana_token,
            timeout=timeout,
        )
        self._memory_cache[session_name] = creds
        self._save_file_cache()
        return creds

    def delete_credentials(self, session_name: str) -> bool:
        """Delete credentials for a session from all caches."""
        if session_name in self._memory_cache:
            del self._memory_cache[session_name]
            self._save_file_cache()
            return True
        return False

    def list_sessions(self) -> dict[str, dict]:
        """List all cached session credentials (with tokens masked)."""
        result = {}

        env_creds = self.get_from_env()
        if env_creds and "default" not in self._memory_cache:
            result["default (env)"] = env_creds.masked_dict()

        for name, cred in self._memory_cache.items():
            result[name] = cred.masked_dict()

        return result

    def clear_all(self):
        """Clear all cached credentials."""
        self._memory_cache.clear()
        if self._cache_file.exists():
            try:
                self._cache_file.unlink()
            except OSError:
                pass
