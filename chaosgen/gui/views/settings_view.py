"""
Settings View — API keys, discovery hints, and observability auth configuration.

Keys are stored in XDG-compliant config dir via chaosgen.config.secrets.
Discovery hints are stored in settings.yaml via chaosgen.config.settings.
"""

from __future__ import annotations

import logging
import os
import stat

from PySide6.QtCore import Qt
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

logger = logging.getLogger(__name__)


class SettingsView(QWidget):
    """
    Settings view — discovery hints, observability auth, LLM provider API keys.

    Sidebar nav item: "Settings"
    """

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
        cloud_form = QFormLayout(cloud_group)

        self._openai_key = self._make_key_field("Enter OpenAI API key...")
        cloud_form.addRow("OpenAI API Key:", self._openai_key)

        self._anthropic_key = self._make_key_field("Enter Anthropic API key...")
        cloud_form.addRow("Anthropic API Key:", self._anthropic_key)

        self._groq_key = self._make_key_field("Enter Groq API key...")
        cloud_form.addRow("Groq API Key:", self._groq_key)

        root.addWidget(cloud_group)

        # --- Observability Auth ---
        obs_group = QGroupBox("Observability Auth (for tools behind auth)")
        obs_form = QFormLayout(obs_group)

        self._prom_token = self._make_key_field("Bearer token for Prometheus...")
        obs_form.addRow("Prometheus Token:", self._prom_token)

        self._loki_token = self._make_key_field("Bearer token for Loki...")
        obs_form.addRow("Loki Token:", self._loki_token)

        self._grafana_pw = self._make_key_field("Grafana password...")
        obs_form.addRow("Grafana Password:", self._grafana_pw)

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

    @staticmethod
    def _make_key_field(placeholder: str) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText(placeholder)
        return field

    # ------------------------------------------------------------------
    # Load existing values
    # ------------------------------------------------------------------

    def _load_current_values(self) -> None:
        try:
            from chaosgen.config.secrets import load_secrets
            from chaosgen.config.settings import load_settings

            secrets = load_secrets()
            self._ollama_url.setText(secrets.get("OLLAMA_URL") or "http://localhost:11434")
            for key_name, field in (
                ("OPENAI_API_KEY", self._openai_key),
                ("ANTHROPIC_API_KEY", self._anthropic_key),
                ("GROQ_API_KEY", self._groq_key),
            ):
                if secrets.get(key_name):
                    field.setPlaceholderText("(key configured - enter new value to update)")

            for key_name, field in (
                ("PROMETHEUS_TOKEN", self._prom_token),
                ("LOKI_TOKEN", self._loki_token),
                ("GRAFANA_PASSWORD", self._grafana_pw),
            ):
                if secrets.get(key_name):
                    field.setPlaceholderText("(configured)")

            settings = load_settings()
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
            from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType

            url = self._ollama_url.text().strip()
            if url:
                save_secret("OLLAMA_URL", url)

            for key_name, field in (
                ("OPENAI_API_KEY", self._openai_key),
                ("ANTHROPIC_API_KEY", self._anthropic_key),
                ("GROQ_API_KEY", self._groq_key),
                ("PROMETHEUS_TOKEN", self._prom_token),
                ("LOKI_TOKEN", self._loki_token),
                ("GRAFANA_PASSWORD", self._grafana_pw),
            ):
                value = field.text().strip()
                if value:
                    save_secret(key_name, value)
                    field.clear()

            settings = load_settings()
            arch_val = self._arch_combo.currentData()
            settings.hints.architecture = ArchitectureType(arch_val) if arch_val else None
            env_val = self._env_combo.currentData()
            settings.hints.environment = EnvironmentType(env_val) if env_val else None
            save_settings(settings)

            self._status.setText("Settings and keys saved.")
            self._status.setStyleSheet("color: #4ade80;")
            self._check_permissions()

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
            for field in (self._openai_key, self._anthropic_key, self._groq_key,
                          self._prom_token, self._loki_token, self._grafana_pw):
                field.clear()
                field.setPlaceholderText("(cleared)")
            self._status.setText("All keys cleared.")
            self._status.setStyleSheet("color: #fbbf24;")
        except Exception as exc:
            self._status.setText(f"Error: {exc}")
            logger.exception("Failed to clear secrets")
