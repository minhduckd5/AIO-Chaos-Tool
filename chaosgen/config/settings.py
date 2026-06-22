"""
ChaosGen Settings — user-provided hints for Hybrid Discovery.

Stored as ~/.config/chaosgen/settings.yaml (XDG) or %APPDATA%/chaosgen/settings.yaml.
Secret values (tokens, passwords) are NEVER stored here — only key *names*
referencing entries in .env (e.g. token_ref: "PROMETHEUS_TOKEN").
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from chaosgen.config.paths import CONFIG_DIR, SETTINGS_FILE, ensure_config_dir
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType, ObservabilityTool

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = re.compile(
    r"^(sk-|ghp_|gho_|xoxb-|xoxp-|Bearer\s|eyJ[A-Za-z0-9])",
    re.IGNORECASE,
)
_REF_FIELDS = {"token_ref", "password_ref"}
_INLINE_SECRET_LENGTH_THRESHOLD = 40


# ---------------------------------------------------------------------------
# Auth config
# ---------------------------------------------------------------------------


class AuthConfig(BaseModel):
    """
    Authentication configuration for an observability endpoint.

    token_ref / password_ref hold KEY NAMES in .env, not raw secret values.
    At probe time, the actual value is resolved via secrets.get_key().
    """

    auth_type: Literal["none", "bearer", "basic", "mtls"] = "none"
    token_ref: str | None = None
    username: str | None = None
    password_ref: str | None = None
    cert_path: str | None = None
    key_path: str | None = None
    ca_path: str | None = None


# ---------------------------------------------------------------------------
# Observability hint
# ---------------------------------------------------------------------------


class ObservabilityHint(BaseModel):
    tool: ObservabilityTool
    url: str
    auth: AuthConfig = Field(default_factory=AuthConfig)


# ---------------------------------------------------------------------------
# User hints
# ---------------------------------------------------------------------------


class UserHints(BaseModel):
    architecture: ArchitectureType | None = None
    environment: EnvironmentType | None = None
    observability: list[ObservabilityHint] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    skip_auto_detect: bool = False


# ---------------------------------------------------------------------------
# Gatekeeper settings (P1 — Gatekeeper Real Filter)
# ---------------------------------------------------------------------------


class GatekeeperSettings(BaseModel):
    """Thresholds and rules for the incident gatekeeper (`?? real ??`)."""

    frequency_low_threshold: float = 0.5   # events/hour
    frequency_high_threshold: float = 2.0
    severity_low_threshold: float = 0.4
    severity_high_threshold: float = 0.75
    log_correlation_boost: bool = True
    strict_log_boost: bool = True
    severe_log_keywords: list[str] = Field(
        default_factory=lambda: ["error", "fatal", "critical"]
    )
    ignore_log_keywords: list[str] = Field(
        default_factory=lambda: ["warning", "warn", "deprecation", "info"]
    )


# ---------------------------------------------------------------------------
# History settings (P5 — SQLite History Loop)
# ---------------------------------------------------------------------------


class HistorySettings(BaseModel):
    """SQLite analytics history (not runtime SOT — see P5 plan §0)."""

    enabled: bool = True
    db_path: str | None = None
    async_writes: bool = True


# ---------------------------------------------------------------------------
# Top-level settings
# ---------------------------------------------------------------------------


class ChaosGenSettings(BaseModel):
    hints: UserHints = Field(default_factory=UserHints)
    llm_provider: str = "ollama"
    llm_model: str | None = None
    gatekeeper: GatekeeperSettings = Field(default_factory=GatekeeperSettings)
    history: HistorySettings = Field(default_factory=HistorySettings)


# ---------------------------------------------------------------------------
# Secret-leak guard
# ---------------------------------------------------------------------------


class InlineSecretError(ValueError):
    """Raised when settings.yaml appears to contain an inline secret value."""


def _check_no_inline_secrets(raw: dict[str, Any]) -> None:
    """
    Scan raw YAML dict for values that look like pasted secrets.
    Raises InlineSecretError if suspicious content is found.
    """
    for hint in raw.get("hints", {}).get("observability", []):
        auth = hint.get("auth", {})
        for field in _REF_FIELDS:
            val = auth.get(field)
            if not val or not isinstance(val, str):
                continue
            if _SECRET_PATTERNS.match(val):
                raise InlineSecretError(
                    f"settings.yaml field 'auth.{field}' value starts with a known "
                    f"secret prefix. Store the secret in .env via "
                    f"`chaosgen config set-key <NAME> <VALUE>` and put only "
                    f"the key name (e.g. 'PROMETHEUS_TOKEN') in settings.yaml."
                )
            if len(val) > _INLINE_SECRET_LENGTH_THRESHOLD:
                raise InlineSecretError(
                    f"settings.yaml field 'auth.{field}' value is {len(val)} chars long, "
                    f"which looks like an inline secret. Store it in .env and reference "
                    f"the key name here instead."
                )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_settings(path: str | None = None) -> ChaosGenSettings:
    """
    Load settings from YAML. Falls back to defaults if file doesn't exist.
    Runs secret-leak guard before parsing into Pydantic models.
    """
    settings_path = SETTINGS_FILE if path is None else __import__("pathlib").Path(path)

    if not settings_path.exists():
        logger.debug("No settings file at %s — using defaults", settings_path)
        return ChaosGenSettings()

    raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    _check_no_inline_secrets(raw)
    return ChaosGenSettings.model_validate(raw)


def save_settings(settings: ChaosGenSettings, path: str | None = None) -> None:
    """Persist settings to YAML."""
    settings_path = SETTINGS_FILE if path is None else __import__("pathlib").Path(path)
    ensure_config_dir()
    data = settings.model_dump(mode="json", exclude_none=True)
    settings_path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("Settings saved to %s", settings_path)
