"""
Tests for chaosgen.config.secrets — Blind Spot 2 (API key management).

Validates:
  - save_secret writes to ~/.chaosgen/.env
  - load_secrets reads values back
  - MissingAPIKeyError is raised by get_key when key is absent
  - delete_secret removes the key
  - Unknown key names raise ValueError
  - Permission validation emits a warning on unsafe POSIX modes
"""

from __future__ import annotations

import os
import stat
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest


# Redirect config dir to a tmp_path and clear env vars between tests
@pytest.fixture(autouse=True)
def isolated_secrets_dir(tmp_path, monkeypatch):
    fake_dir = tmp_path / "chaosgen_config"
    fake_dir.mkdir()
    fake_env = fake_dir / ".env"

    monkeypatch.setattr("chaosgen.config.paths.CONFIG_DIR", fake_dir)
    monkeypatch.setattr("chaosgen.config.paths.SECRETS_FILE", fake_env)
    monkeypatch.setattr("chaosgen.config.secrets._CHAOSGEN_DIR", fake_dir)
    monkeypatch.setattr("chaosgen.config.secrets._ENV_FILE", fake_env)

    for key in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OLLAMA_URL",
        "PROMETHEUS_TOKEN", "LOKI_TOKEN", "GRAFANA_PASSWORD", "JAEGER_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    yield

    for key in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OLLAMA_URL",
        "PROMETHEUS_TOKEN", "LOKI_TOKEN", "GRAFANA_PASSWORD", "JAEGER_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


class TestSaveAndLoad:
    def test_save_and_reload_key(self):
        from chaosgen.config.secrets import save_secret, load_secrets

        save_secret("OPENAI_API_KEY", "sk-test-123")
        secrets = load_secrets()
        assert secrets["OPENAI_API_KEY"] == "sk-test-123"

    def test_overwrite_existing_key(self):
        from chaosgen.config.secrets import save_secret, load_secrets

        save_secret("OPENAI_API_KEY", "sk-first")
        save_secret("OPENAI_API_KEY", "sk-second")
        secrets = load_secrets()
        assert secrets["OPENAI_API_KEY"] == "sk-second"
        # Only one entry in the file
        from chaosgen.config.secrets import _ENV_FILE
        lines = [l for l in _ENV_FILE.read_text().splitlines() if l.startswith("OPENAI_API_KEY")]
        assert len(lines) == 1

    def test_multiple_keys_coexist(self):
        from chaosgen.config.secrets import save_secret, load_secrets

        save_secret("OPENAI_API_KEY", "oai-key")
        save_secret("GROQ_API_KEY", "groq-key")
        secrets = load_secrets()
        assert secrets["OPENAI_API_KEY"] == "oai-key"
        assert secrets["GROQ_API_KEY"] == "groq-key"

    def test_delete_key(self):
        from chaosgen.config.secrets import save_secret, delete_secret, load_secrets

        save_secret("ANTHROPIC_API_KEY", "ant-key")
        delete_secret("ANTHROPIC_API_KEY")
        secrets = load_secrets()
        assert secrets.get("ANTHROPIC_API_KEY") is None

    def test_unknown_key_raises_value_error(self):
        from chaosgen.config.secrets import save_secret

        with pytest.raises(ValueError, match="Unknown secret key"):
            save_secret("NOT_A_VALID_KEY", "value")


class TestGetKey:
    def test_get_key_returns_value(self):
        from chaosgen.config.secrets import save_secret, get_key

        save_secret("GROQ_API_KEY", "groq-123")
        value = get_key("GROQ_API_KEY")
        assert value == "groq-123"

    def test_get_key_raises_missing_api_key_error(self):
        from chaosgen.config.secrets import MissingAPIKeyError, get_key

        with pytest.raises(MissingAPIKeyError) as exc_info:
            get_key("OPENAI_API_KEY", provider="openai")

        exc = exc_info.value
        assert exc.provider == "openai"
        assert exc.key_name == "OPENAI_API_KEY"
        assert "Settings" in str(exc) or "chaosgen config" in str(exc)


class TestPermissions:
    def test_unsafe_permissions_emit_warning(self, tmp_path):
        from chaosgen.config.secrets import _validate_permissions

        if os.name == "nt":
            pytest.skip("Permission checks skipped on Windows")

        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=test\n")
        env_file.chmod(0o644)   # world-readable — should warn

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _validate_permissions(env_file)

        assert len(w) == 1
        assert "unsafe permissions" in str(w[0].message).lower()

    def test_safe_permissions_no_warning(self, tmp_path):
        from chaosgen.config.secrets import _validate_permissions

        if os.name == "nt":
            pytest.skip("Permission checks skipped on Windows")

        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=test\n")
        env_file.chmod(0o600)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _validate_permissions(env_file)

        assert len(w) == 0
