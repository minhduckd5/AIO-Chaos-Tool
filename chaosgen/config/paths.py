"""
ChaosGen configuration paths — XDG Base Directory compliant.

Linux/macOS: ~/.config/chaosgen/
Windows:     %APPDATA%/chaosgen/

Backward compatibility: if the legacy ~/.chaosgen/ directory exists and the
new XDG path does not, auto-migrate on first access.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import warnings
from pathlib import Path

logger = logging.getLogger(__name__)

_LEGACY_DIR = Path.home() / ".chaosgen"


def _resolve_config_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "chaosgen"


CONFIG_DIR: Path = _resolve_config_dir()
SETTINGS_FILE: Path = CONFIG_DIR / "settings.yaml"
SECRETS_FILE: Path = CONFIG_DIR / ".env"


def ensure_config_dir() -> Path:
    """Create the config directory if it doesn't exist, migrating legacy data if needed."""
    if not CONFIG_DIR.exists():
        _maybe_migrate_legacy()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def _maybe_migrate_legacy() -> None:
    """
    If ~/.chaosgen/ exists but the XDG path does not, copy contents over
    and leave a breadcrumb file in the old location.
    """
    if not _LEGACY_DIR.exists() or CONFIG_DIR.exists():
        return

    logger.info("Migrating legacy config from %s to %s", _LEGACY_DIR, CONFIG_DIR)
    warnings.warn(
        f"[ChaosGen] Legacy config directory {_LEGACY_DIR} detected. "
        f"Migrating to {CONFIG_DIR}. You can safely remove the old directory.",
        DeprecationWarning,
        stacklevel=3,
    )

    try:
        shutil.copytree(_LEGACY_DIR, CONFIG_DIR, dirs_exist_ok=True)
        breadcrumb = _LEGACY_DIR / "_MIGRATED_TO"
        breadcrumb.write_text(str(CONFIG_DIR), encoding="utf-8")
    except OSError as exc:
        logger.warning("Migration failed: %s. Falling back to legacy path.", exc)
