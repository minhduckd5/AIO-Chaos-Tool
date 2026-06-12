"""
Settings View — API keys, discovery hints, and observability auth configuration.

Keys are stored in XDG-compliant config dir via chaosgen.config.secrets.
Discovery hints are stored in settings.yaml via chaosgen.config.settings.
"""

from __future__ import annotations

import logging
import os
import stat

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from chaosgen.config.paths import SECRETS_FILE
from chaosgen.gui.theme import Colors

logger = logging.getLogger(__name__)

_PLACEHOLDER_EMPTY_KEY = "Paste API key here…"
_PLACEHOLDER_SAVED_KEY = "✓ Key saved on disk — leave empty to keep, paste to replace"


class SettingsView(QWidget):
    """
    Settings view — discovery hints, observability auth, LLM provider API keys.

    Sidebar nav item: "Settings"
    """

    settings_saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self._load_current_values()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Settings")
        title.setObjectName("viewTitle")
        root.addWidget(title)

        subtitle = QLabel(
            f"Discovery hints (settings.yaml) and API keys ({SECRETS_FILE})."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("viewSubtitle")
        root.addWidget(subtitle)

        # --- Permission warning banner ---
        self._perm_warning = QLabel()
        self._perm_warning.setObjectName("warningBanner")
        self._perm_warning.setWordWrap(True)
        self._perm_warning.setVisible(False)
        root.addWidget(self._perm_warning)

        # --- Telemetry endpoints (Way 1 live stack) ---
        telem_group = QGroupBox("Telemetry Endpoints (registry-vm)")
        telem_form = QFormLayout(telem_group)
        from chaosgen.config.telemetry_endpoints import DEFAULT_LOKI_URL, DEFAULT_PROMETHEUS_URL

        self._prom_url = QLineEdit(DEFAULT_PROMETHEUS_URL)
        self._loki_url = QLineEdit(DEFAULT_LOKI_URL)
        telem_form.addRow("Prometheus URL:", self._prom_url)
        telem_form.addRow("Loki URL:", self._loki_url)
        root.addWidget(telem_group)

        # --- Discovery Hints ---
        hints_group = QGroupBox("Discovery Hints")
        hints_form = QFormLayout(hints_group)

        self._arch_combo = QComboBox()
        self._arch_combo.addItem("Auto-detect", None)
        for val in ("monolith", "modular_monolith", "microservices", "event_driven", "client_server", "serverless"):
            self._arch_combo.addItem(val, val)
        hints_form.addRow("Architecture:", self._arch_combo)

        self._env_combo = QComboBox()
        self._env_combo.addItem("Auto-detect", None)
        for val in ("kubernetes", "docker_compose", "bare_metal", "cloud_vm", "serverless"):
            self._env_combo.addItem(val, val)
        hints_form.addRow("Environment:", self._env_combo)

        root.addWidget(hints_group)

        # --- Local Ollama ---
        ollama_group = QGroupBox("Local Ollama (default - no API key required)")
        ollama_form = QFormLayout(ollama_group)
        self._ollama_url = QLineEdit()
        self._ollama_url.setPlaceholderText("http://localhost:11434")
        ollama_form.addRow("Ollama URL:", self._ollama_url)
        root.addWidget(ollama_group)

        # --- Cloud LLM providers ---
        cloud_group = QGroupBox("Cloud LLM Providers")
        cloud_layout = QVBoxLayout(cloud_group)
        cloud_hint = QLabel(
            "API keys are stored in your local .env file and hidden after save for security. "
            "A ✓ Saved badge means the key is on disk — you do not need to paste it again. "
            "For 9router on the same machine, prefer http://127.0.0.1:PORT/v1 over a Tailscale IP."
        )
        cloud_hint.setWordWrap(True)
        cloud_hint.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")
        cloud_layout.addWidget(cloud_hint)
        cloud_form = QFormLayout()
        cloud_layout.addLayout(cloud_form)

        self._openai_key_row, self._openai_key, self._openai_key_status = self._make_secret_field_row()
        cloud_form.addRow("OpenAI API Key:", self._openai_key_row)

        self._openai_base_url = QLineEdit()
        self._openai_base_url.setPlaceholderText(
            "9router on this PC: http://127.0.0.1:20128/v1 — tailnet IP only if Tailscale is up"
        )
        cloud_form.addRow("OpenAI Base URL:", self._openai_base_url)

        self._anthropic_key_row, self._anthropic_key, self._anthropic_key_status = self._make_secret_field_row()
        cloud_form.addRow("Anthropic API Key:", self._anthropic_key_row)

        self._groq_key_row, self._groq_key, self._groq_key_status = self._make_secret_field_row()
        cloud_form.addRow("Groq API Key:", self._groq_key_row)

        root.addWidget(cloud_group)

        # --- Observability Auth ---
        obs_group = QGroupBox("Observability Auth (for tools behind auth)")
        obs_form = QFormLayout(obs_group)

        self._prom_token_row, self._prom_token, self._prom_token_status = self._make_secret_field_row()
        obs_form.addRow("Prometheus Token:", self._prom_token_row)

        self._loki_token_row, self._loki_token, self._loki_token_status = self._make_secret_field_row()
        obs_form.addRow("Loki Token:", self._loki_token_row)

        self._grafana_pw_row, self._grafana_pw, self._grafana_pw_status = self._make_secret_field_row()
        obs_form.addRow("Grafana Password:", self._grafana_pw_row)

        root.addWidget(obs_group)

        # --- Save / clear buttons ---
        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save All")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        clear_btn = QPushButton("Clear All Keys")
        clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._status = QLabel("")
        root.addWidget(self._status)

        root.addStretch()

        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _make_secret_field_row(self) -> tuple[QWidget, QLineEdit, QLabel]:
        """Password field + green 'Saved' badge (keys are never re-displayed)."""
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText(_PLACEHOLDER_EMPTY_KEY)

        status = QLabel("")
        status.setMinimumWidth(72)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(field, stretch=1)
        layout.addWidget(status)
        return row, field, status

    def _secret_field_entries(self) -> list[tuple[str, QLineEdit, QLabel, str]]:
        return [
            ("OPENAI_API_KEY", self._openai_key, self._openai_key_status, "OpenAI"),
            ("ANTHROPIC_API_KEY", self._anthropic_key, self._anthropic_key_status, "Anthropic"),
            ("GROQ_API_KEY", self._groq_key, self._groq_key_status, "Groq"),
            ("PROMETHEUS_TOKEN", self._prom_token, self._prom_token_status, "Prometheus"),
            ("LOKI_TOKEN", self._loki_token, self._loki_token_status, "Loki"),
            ("GRAFANA_PASSWORD", self._grafana_pw, self._grafana_pw_status, "Grafana"),
        ]

    def _sync_secret_fields_from_disk(self) -> list[str]:
        """Refresh placeholders/badges from .env; return human names of stored keys."""
        from chaosgen.config.secrets import load_secrets

        secrets = load_secrets()
        stored: list[str] = []
        for key_name, field, status, label in self._secret_field_entries():
            if secrets.get(key_name):
                field.clear()
                field.setPlaceholderText(_PLACEHOLDER_SAVED_KEY)
                status.setText("✓ Saved")
                status.setStyleSheet(f"color: {Colors.SUCCESS}; font-weight: bold;")
                stored.append(label)
            else:
                field.setPlaceholderText(_PLACEHOLDER_EMPTY_KEY)
                status.setText("")
        return stored

    # ------------------------------------------------------------------
    # Load existing values
    # ------------------------------------------------------------------

    def _load_current_values(self) -> None:
        try:
            from chaosgen.config.secrets import load_secrets
            from chaosgen.config.settings import load_settings

            secrets = load_secrets()
            self._ollama_url.setText(secrets.get("OLLAMA_URL") or "http://localhost:11434")
            if secrets.get("OPENAI_BASE_URL"):
                self._openai_base_url.setText(secrets["OPENAI_BASE_URL"])
            self._sync_secret_fields_from_disk()

            settings = load_settings()
            from chaosgen.schemas.discovery import ObservabilityTool

            for hint in settings.hints.observability:
                if hint.tool == ObservabilityTool.PROMETHEUS:
                    self._prom_url.setText(hint.url)
                elif hint.tool == ObservabilityTool.LOKI:
                    self._loki_url.setText(hint.url)
            if settings.hints.architecture:
                idx = self._arch_combo.findData(settings.hints.architecture.value)
                if idx >= 0:
                    self._arch_combo.setCurrentIndex(idx)
            if settings.hints.environment:
                idx = self._env_combo.findData(settings.hints.environment.value)
                if idx >= 0:
                    self._env_combo.setCurrentIndex(idx)

        except Exception as exc:
            logger.warning("Could not load settings: %s", exc)

        self._check_permissions()

    def _check_permissions(self) -> None:
        if os.name == "nt" or not SECRETS_FILE.exists():
            return
        try:
            mode = oct(stat.S_IMODE(SECRETS_FILE.stat().st_mode))
            if mode not in ("0o600", "0o400"):
                self._perm_warning.setText(
                    f"Warning: {SECRETS_FILE} has unsafe permissions ({mode}). "
                    f"Fix with: chmod 600 {SECRETS_FILE}"
                )
                self._perm_warning.setVisible(True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        try:
            from chaosgen.config.secrets import save_secret
            from chaosgen.config.settings import load_settings, save_settings
            from chaosgen.config.settings import AuthConfig, ObservabilityHint
            from chaosgen.schemas.discovery import (
                ArchitectureType,
                EnvironmentType,
                ObservabilityTool,
            )

            from chaosgen.config.secrets import delete_secret

            url = self._ollama_url.text().strip()
            if url:
                save_secret("OLLAMA_URL", url)

            openai_base = self._openai_base_url.text().strip()
            if openai_base:
                save_secret("OPENAI_BASE_URL", openai_base.rstrip("/"))
            else:
                delete_secret("OPENAI_BASE_URL")

            updated_labels: list[str] = []
            for key_name, field, _status, label in self._secret_field_entries():
                value = field.text().strip()
                if value:
                    save_secret(key_name, value)
                    updated_labels.append(label)

            settings = load_settings()
            arch_val = self._arch_combo.currentData()
            settings.hints.architecture = ArchitectureType(arch_val) if arch_val else None
            env_val = self._env_combo.currentData()
            settings.hints.environment = EnvironmentType(env_val) if env_val else None
            settings.hints.skip_auto_detect = True
            settings.hints.architecture = settings.hints.architecture or ArchitectureType.MICROSERVICES

            prom_url = self._prom_url.text().strip()
            loki_url = self._loki_url.text().strip()
            obs = []
            if prom_url:
                obs.append(ObservabilityHint(
                    tool=ObservabilityTool.PROMETHEUS, url=prom_url, auth=AuthConfig(),
                ))
            if loki_url:
                obs.append(ObservabilityHint(
                    tool=ObservabilityTool.LOKI, url=loki_url, auth=AuthConfig(),
                ))
            settings.hints.observability = obs
            save_settings(settings)

            stored = self._sync_secret_fields_from_disk()
            if updated_labels:
                msg = f"Saved. Updated: {', '.join(updated_labels)}."
            elif stored:
                msg = f"Settings saved. Keys on disk: {', '.join(stored)}."
            else:
                msg = "Settings saved (no API keys stored yet)."
            self._status.setText(msg)
            self._status.setStyleSheet("color: #4ade80;")
            self._check_permissions()
            self.settings_saved.emit()

        except Exception as exc:
            self._status.setText(f"Error: {exc}")
            self._status.setStyleSheet("color: #f87171;")
            logger.exception("Failed to save settings")

    def _on_clear(self) -> None:
        reply = QMessageBox.question(
            self, "Confirm Clear",
            f"Delete all stored API keys from {SECRETS_FILE}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from chaosgen.config.secrets import _KNOWN_KEYS, delete_secret
            for key in _KNOWN_KEYS:
                delete_secret(key)
            self._openai_base_url.clear()
            self._sync_secret_fields_from_disk()
            self._status.setText("All keys cleared.")
            self._status.setStyleSheet("color: #fbbf24;")
        except Exception as exc:
            self._status.setText(f"Error: {exc}")
            logger.exception("Failed to clear secrets")
