"""
ChaosGen Secret Management.

API keys and auth credentials are stored in the XDG-compliant config directory
(~/.config/chaosgen/.env on Linux, %APPDATA%/chaosgen/.env on Windows).
Keys are never hardcoded, never logged, never read from arbitrary environment
variables without going through this module.

On Linux/macOS, the .env file is enforced to chmod 600 (owner read/write only).
On Windows, NTFS ACL enforcement is skipped but a warning is emitted.
"""

from __future__ import annotations

import logging
import os
import stat
import warnings
from pathlib import Path

from chaosgen.config.paths import CONFIG_DIR, SECRETS_FILE, ensure_config_dir

logger = logging.getLogger(__name__)

_CHAOSGEN_DIR = CONFIG_DIR
_ENV_FILE = SECRETS_FILE

_KNOWN_KEYS = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "OLLAMA_URL",
    "PROMETHEUS_TOKEN",
    "LOKI_TOKEN",
    "GRAFANA_PASSWORD",
    "JAEGER_TOKEN",
}


class MissingAPIKeyError(ValueError):
    """
    Raised when a cloud LLM provider is selected but the corresponding
    API key has not been configured. Directs the user to the Settings tab.
    """

    def __init__(self, provider: str, key_name: str) -> None:
        super().__init__(
            f"API key '{key_name}' is required for provider '{provider}' but is not configured. "
            "Set it via the ChaosGen Settings tab (GUI) or run: "
            f"chaosgen config set-key {key_name} <your-key>"
        )
        self.provider = provider
        self.key_name = key_name


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_secrets() -> dict[str, str | None]:
    """
    Load secrets from ~/.chaosgen/.env.
    Validates file permissions on Linux/macOS.
    Returns a dict of known key names → values (None if not set).
    """
    if _ENV_FILE.exists():
        _validate_permissions(_ENV_FILE)
        _load_env_file(_ENV_FILE)

    return {
        "OPENAI_API_KEY":    os.environ.get("OPENAI_API_KEY"),
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
        "GROQ_API_KEY":      os.environ.get("GROQ_API_KEY"),
        "OLLAMA_URL":        os.environ.get("OLLAMA_URL", "http://localhost:11434"),
    }


def get_key(key_name: str, provider: str | None = None) -> str:
    """
    Return the value of a specific key, raising MissingAPIKeyError if absent.
    """
    secrets = load_secrets()
    value = secrets.get(key_name)
    if not value:
        raise MissingAPIKeyError(
            provider=provider or key_name.replace("_API_KEY", "").lower(),
            key_name=key_name,
        )
    return value


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_secret(key: str, value: str) -> None:
    """
    Persist a single key=value pair to ~/.chaosgen/.env.
    Existing entries for the same key are replaced.
    File is created with chmod 600 on Linux/macOS.
    """
    if key not in _KNOWN_KEYS:
        raise ValueError(f"Unknown secret key: '{key}'. Valid keys: {sorted(_KNOWN_KEYS)}")

    ensure_config_dir()

    lines: list[str] = []
    if _ENV_FILE.exists():
        lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()

    # Replace existing key or append
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")

    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Enforce restrictive permissions on POSIX systems
    if os.name != "nt":
        _ENV_FILE.chmod(0o600)
        logger.debug("Set %s permissions to 600", _ENV_FILE)
    else:
        logger.debug("Windows detected — skipping chmod 600 for %s", _ENV_FILE)


def delete_secret(key: str) -> None:
    """Remove a key from the .env file."""
    if not _ENV_FILE.exists():
        return
    lines = [
        line for line in _ENV_FILE.read_text(encoding="utf-8").splitlines()
        if not line.startswith(f"{key}=")
    ]
    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        _ENV_FILE.chmod(0o600)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_permissions(path: Path) -> None:
    """
    On POSIX systems, warn or raise if the .env file has world-readable
    permissions (modes other than 0o600 / 0o400).
    """
    if os.name == "nt":
        return

    mode = oct(stat.S_IMODE(path.stat().st_mode))
    safe_modes = {"0o600", "0o400"}
    if mode not in safe_modes:
        warnings.warn(
            f"[ChaosGen] Secrets file {path} has unsafe permissions {mode}. "
            f"Fix with: chmod 600 {path}",
            stacklevel=3,
        )


def _load_env_file(path: Path) -> None:
    """Parse a .env file and inject values into os.environ (without overwriting existing)."""
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(path, override=False)
    except ImportError:
        # Fallback if python-dotenv not installed — manual parse
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
