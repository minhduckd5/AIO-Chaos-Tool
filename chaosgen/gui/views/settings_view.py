"""
Settings View — API keys, architecture profile, connect block, observability auth.

Keys: chaosgen.config.secrets (.env)
Profile + connect: chaosgen.config.settings (settings.yaml)
"""

from __future__ import annotations

import logging
import os
import stat

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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
from chaosgen.config.profile_presets import default_environment_for, profile_priority_tier
from chaosgen.config.profile_validation import validate_profile_connect
from chaosgen.gui.theme import Colors
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType

logger = logging.getLogger(__name__)

_PLACEHOLDER_EMPTY_KEY = "Paste API key here…"
_PLACEHOLDER_SAVED_KEY = "✓ Key saved on disk — leave empty to keep, paste to replace"

_ARCH_VALUES = (
    "monolith",
    "modular_monolith",
    "microservices",
    "event_driven",
    "client_server",
    "serverless",
)

_ENV_VALUES = (
    "kubernetes",
    "docker_compose",
    "bare_metal",
    "cloud_vm",
    "serverless",
)

_BROKER_TYPES = ("kafka", "redpanda", "rabbitmq", "nats")


class SettingsView(QWidget):
    """
    Settings view — form-first architecture profile, connect credentials, API keys.

    Sidebar nav item: "Settings"
    """

    settings_saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self._arch_combo.currentIndexChanged.connect(self._on_arch_changed)
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
            "Form-first profile mode: select architecture + connect credentials manually. "
            "Heuristic auto-discovery is disabled. "
            f"Secrets: {SECRETS_FILE}"
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("viewSubtitle")
        root.addWidget(subtitle)

        self._perm_warning = QLabel()
        self._perm_warning.setObjectName("warningBanner")
        self._perm_warning.setWordWrap(True)
        self._perm_warning.setVisible(False)
        root.addWidget(self._perm_warning)

        # --- Telemetry ---
        telem_group = QGroupBox("Telemetry Endpoints (registry-vm)")
        telem_form = QFormLayout(telem_group)
        from chaosgen.config.telemetry_endpoints import DEFAULT_LOKI_URL, DEFAULT_PROMETHEUS_URL

        self._prom_url = QLineEdit(DEFAULT_PROMETHEUS_URL)
        self._loki_url = QLineEdit(DEFAULT_LOKI_URL)
        telem_form.addRow("Prometheus URL:", self._prom_url)
        telem_form.addRow("Loki URL:", self._loki_url)

        # MODIFIED: form-first telemetry packs (no live discovery)
        self._telem_pack_combo = QComboBox()
        self._telem_pack_combo.addItem("boutique", "boutique")
        self._telem_pack_combo.addItem("boutique + cadvisor", "boutique+cadvisor")
        self._telem_pack_combo.addItem(
            "boutique + cadvisor + loki", "boutique+cadvisor+loki"
        )
        telem_form.addRow("Telemetry pack:", self._telem_pack_combo)
        self._telem_namespace = QLineEdit("default")
        self._telem_namespace.setPlaceholderText("Kubernetes namespace scope")
        telem_form.addRow("Pack namespace:", self._telem_namespace)
        root.addWidget(telem_group)

        # --- Architecture profile ---
        profile_group = QGroupBox("Architecture Profile (manual — auto-detect disabled)")
        profile_form = QFormLayout(profile_group)

        arch_row = QWidget()
        arch_layout = QHBoxLayout(arch_row)
        arch_layout.setContentsMargins(0, 0, 0, 0)
        self._arch_combo = QComboBox()
        for val in _ARCH_VALUES:
            self._arch_combo.addItem(val.replace("_", " ").title(), val)
        arch_layout.addWidget(self._arch_combo, stretch=1)
        self._tier_badge = QLabel("")
        self._tier_badge.setMinimumWidth(100)
        arch_layout.addWidget(self._tier_badge)
        profile_form.addRow("Architecture:", arch_row)

        self._env_combo = QComboBox()
        self._env_combo.addItem("(preset default)", None)
        for val in _ENV_VALUES:
            self._env_combo.addItem(val.replace("_", " ").title(), val)
        profile_form.addRow("Environment:", self._env_combo)

        root.addWidget(profile_group)

        # --- Connect: Kubernetes ---
        k8s_group = QGroupBox("Connect — Kubernetes")
        k8s_form = QFormLayout(k8s_group)
        self._kubeconfig = QLineEdit()
        self._kubeconfig.setPlaceholderText("~/.kube/config")
        self._kube_context = QLineEdit()
        self._kube_namespace = QLineEdit("default")
        k8s_form.addRow("Kubeconfig:", self._kubeconfig)
        k8s_form.addRow("Context:", self._kube_context)
        k8s_form.addRow("Default namespace:", self._kube_namespace)
        root.addWidget(k8s_group)

        # --- Connect: Docker ---
        docker_group = QGroupBox("Connect — Docker / Compose")
        docker_form = QFormLayout(docker_group)
        self._docker_host = QLineEdit()
        self._docker_host.setPlaceholderText("unix:///var/run/docker.sock")
        self._compose_file = QLineEdit()
        self._compose_file.setPlaceholderText("labs/modular-monolith/docker-compose.yml")
        self._compose_project = QLineEdit()
        docker_form.addRow("Docker host:", self._docker_host)
        docker_form.addRow("Compose file:", self._compose_file)
        docker_form.addRow("Compose project:", self._compose_project)
        root.addWidget(docker_group)

        # --- Connect: Broker (event-driven) ---
        broker_group = QGroupBox("Connect — Message Broker (event-driven)")
        broker_form = QFormLayout(broker_group)
        self._broker_type = QComboBox()
        self._broker_type.addItem("(select type)", None)
        for bt in _BROKER_TYPES:
            self._broker_type.addItem(bt, bt)
        self._broker_bootstrap = QLineEdit()
        self._broker_bootstrap.setPlaceholderText("localhost:9092")
        self._broker_admin = QLineEdit()
        self._broker_admin.setPlaceholderText("http://127.0.0.1:9644 (Redpanda admin)")
        broker_form.addRow("Broker type:", self._broker_type)
        broker_form.addRow("Bootstrap *:", self._broker_bootstrap)
        broker_form.addRow("Admin API URL:", self._broker_admin)
        root.addWidget(broker_group)

        # --- Connect: Toxiproxy ---
        tox_group = QGroupBox("Connect — Toxiproxy (client-server)")
        tox_form = QFormLayout(tox_group)
        self._toxiproxy_url = QLineEdit()
        self._toxiproxy_url.setPlaceholderText("http://127.0.0.1:8474")
        tox_form.addRow("API URL:", self._toxiproxy_url)
        root.addWidget(tox_group)

        # --- Ollama ---
        ollama_group = QGroupBox("Local Ollama (default - no API key required)")
        ollama_form = QFormLayout(ollama_group)
        self._ollama_url = QLineEdit()
        self._ollama_url.setPlaceholderText("http://localhost:11434")
        ollama_form.addRow("Ollama URL:", self._ollama_url)
        root.addWidget(ollama_group)

        obs_group = QGroupBox("Observability Auth (for tools behind auth)")
        obs_form = QFormLayout(obs_group)
        self._prom_token_row, self._prom_token, self._prom_token_status = self._make_secret_field_row()
        obs_form.addRow("Prometheus Token:", self._prom_token_row)
        self._loki_token_row, self._loki_token, self._loki_token_status = self._make_secret_field_row()
        obs_form.addRow("Loki Token:", self._loki_token_row)
        self._grafana_pw_row, self._grafana_pw, self._grafana_pw_status = self._make_secret_field_row()
        obs_form.addRow("Grafana Password:", self._grafana_pw_row)
        root.addWidget(obs_group)

        self._advanced_toggle = QCheckBox("Show advanced settings (cloud LLM keys, developer mode)")
        self._advanced_toggle.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        root.addWidget(self._advanced_toggle)

        self._advanced_container = QWidget()
        advanced_layout = QVBoxLayout(self._advanced_container)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(16)

        dev_group = QGroupBox("Developer Options")
        dev_form = QFormLayout(dev_group)
        self._developer_mode_cb = QCheckBox("Enable Advanced/Developer Tuning Mode")
        dev_form.addRow(self._developer_mode_cb)
        advanced_layout.addWidget(dev_group)

        cloud_group = QGroupBox("Cloud LLM Providers")
        cloud_layout = QVBoxLayout(cloud_group)
        cloud_hint = QLabel(
            "API keys are stored in your local .env file and hidden after save for security."
        )
        cloud_hint.setWordWrap(True)
        cloud_hint.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")
        cloud_layout.addWidget(cloud_hint)
        cloud_form = QFormLayout()
        cloud_layout.addLayout(cloud_form)

        self._openai_key_row, self._openai_key, self._openai_key_status = self._make_secret_field_row()
        cloud_form.addRow("OpenAI API Key:", self._openai_key_row)
        self._openai_base_url = QLineEdit()
        self._openai_base_url.setPlaceholderText("http://127.0.0.1:20128/v1")
        cloud_form.addRow("OpenAI Base URL:", self._openai_base_url)
        self._anthropic_key_row, self._anthropic_key, self._anthropic_key_status = self._make_secret_field_row()
        cloud_form.addRow("Anthropic API Key:", self._anthropic_key_row)
        self._groq_key_row, self._groq_key, self._groq_key_status = self._make_secret_field_row()
        cloud_form.addRow("Groq API Key:", self._groq_key_row)
        advanced_layout.addWidget(cloud_group)
        self._advanced_container.setVisible(False)
        self._advanced_toggle.toggled.connect(self._advanced_container.setVisible)
        root.addWidget(self._advanced_container)

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

    def _update_tier_badge(self) -> None:
        arch_val = self._arch_combo.currentData()
        if not arch_val:
            self._tier_badge.setText("")
            return
        arch = ArchitectureType(arch_val)
        tier = profile_priority_tier(arch)
        if tier == "P0":
            label = "P0 Live"
            color = Colors.SUCCESS
        else:
            label = "P1 Dry-run"
            color = Colors.WARNING
        self._tier_badge.setText(label)
        self._tier_badge.setStyleSheet(
            f"color: {color}; font-weight: bold; padding: 2px 8px; "
            f"border: 1px solid {color}; border-radius: 4px;"
        )

    def _on_arch_changed(self) -> None:
        self._update_tier_badge()
        arch_val = self._arch_combo.currentData()
        if not arch_val:
            return
        default_env = default_environment_for(ArchitectureType(arch_val))
        idx = self._env_combo.findData(default_env.value)
        if idx >= 0 and self._env_combo.currentData() is None:
            self._env_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # Load / save helpers
    # ------------------------------------------------------------------

    def _load_current_values(self) -> None:
        try:
            from chaosgen.config.secrets import load_secrets
            from chaosgen.config.settings import load_settings
            from chaosgen.schemas.discovery import ObservabilityTool

            secrets = load_secrets()
            self._ollama_url.setText(secrets.get("OLLAMA_URL") or "http://localhost:11434")
            if secrets.get("OPENAI_BASE_URL"):
                self._openai_base_url.setText(secrets["OPENAI_BASE_URL"])
            self._sync_secret_fields_from_disk()

            settings = load_settings()
            for hint in settings.hints.observability:
                if hint.tool == ObservabilityTool.PROMETHEUS:
                    self._prom_url.setText(hint.url)
                elif hint.tool == ObservabilityTool.LOKI:
                    self._loki_url.setText(hint.url)

            arch = settings.hints.architecture or ArchitectureType.MICROSERVICES
            idx = self._arch_combo.findData(arch.value)
            if idx >= 0:
                self._arch_combo.setCurrentIndex(idx)

            if settings.hints.environment:
                eidx = self._env_combo.findData(settings.hints.environment.value)
                if eidx >= 0:
                    self._env_combo.setCurrentIndex(eidx)

            conn = settings.connect
            kc = conn.kubernetes.kubeconfig or settings.inject.kubeconfig
            if kc:
                self._kubeconfig.setText(kc)
            if conn.kubernetes.context:
                self._kube_context.setText(conn.kubernetes.context)
            if conn.kubernetes.default_namespace:
                self._kube_namespace.setText(conn.kubernetes.default_namespace)
            if conn.docker.host:
                self._docker_host.setText(conn.docker.host)
            if conn.docker.compose_file:
                self._compose_file.setText(conn.docker.compose_file)
            if conn.docker.project_name:
                self._compose_project.setText(conn.docker.project_name)
            if conn.broker.type:
                bidx = self._broker_type.findData(conn.broker.type)
                if bidx >= 0:
                    self._broker_type.setCurrentIndex(bidx)
            if conn.broker.bootstrap:
                self._broker_bootstrap.setText(conn.broker.bootstrap)
            if conn.broker.admin_api_url:
                self._broker_admin.setText(conn.broker.admin_api_url)
            if conn.toxiproxy.api_url:
                self._toxiproxy_url.setText(conn.toxiproxy.api_url)

            # MODIFIED: telemetry pack profile
            extras = list(settings.ingest.extra_packs or [])
            if "cadvisor" in extras and "loki_system" in extras:
                pack_key = "boutique+cadvisor+loki"
            elif "cadvisor" in extras:
                pack_key = "boutique+cadvisor"
            else:
                pack_key = settings.ingest.telemetry_profile or "boutique"
            pidx = self._telem_pack_combo.findData(pack_key)
            if pidx < 0:
                pidx = self._telem_pack_combo.findData("boutique")
            if pidx >= 0:
                self._telem_pack_combo.setCurrentIndex(pidx)
            self._telem_namespace.setText(settings.ingest.scope_namespace or "default")

            self._developer_mode_cb.setChecked(settings.developer_mode)
            self._update_tier_badge()

        except Exception as exc:
            logger.warning("Could not load settings: %s", exc)

        self._check_permissions()

    def _apply_form_to_settings(self, settings) -> None:
        from chaosgen.config.settings import AuthConfig, ObservabilityHint
        from chaosgen.schemas.discovery import ObservabilityTool

        arch_val = self._arch_combo.currentData()
        env_val = self._env_combo.currentData()

        settings.hints.architecture = ArchitectureType(arch_val) if arch_val else ArchitectureType.MICROSERVICES
        settings.hints.environment = EnvironmentType(env_val) if env_val else None
        settings.hints.skip_auto_detect = True

        settings.connect.kubernetes.kubeconfig = self._kubeconfig.text().strip() or None
        settings.connect.kubernetes.context = self._kube_context.text().strip() or None
        ns = self._kube_namespace.text().strip()
        settings.connect.kubernetes.default_namespace = ns or "default"

        settings.connect.docker.host = self._docker_host.text().strip() or None
        settings.connect.docker.compose_file = self._compose_file.text().strip() or None
        settings.connect.docker.project_name = self._compose_project.text().strip() or None

        broker_type = self._broker_type.currentData()
        settings.connect.broker.type = broker_type
        settings.connect.broker.bootstrap = self._broker_bootstrap.text().strip() or None
        settings.connect.broker.admin_api_url = self._broker_admin.text().strip() or None

        settings.connect.toxiproxy.api_url = self._toxiproxy_url.text().strip() or None

        # Legacy inject path — keep boutique kubeconfig working
        if settings.connect.kubernetes.kubeconfig:
            settings.inject.kubeconfig = settings.connect.kubernetes.kubeconfig
        if settings.connect.kubernetes.context:
            settings.inject.context = settings.connect.kubernetes.context
        settings.inject.default_namespace = settings.connect.kubernetes.default_namespace

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
        settings.developer_mode = self._developer_mode_cb.isChecked()

        # MODIFIED: persist telemetry pack selection
        pack_key = self._telem_pack_combo.currentData() or "boutique"
        if pack_key == "boutique+cadvisor+loki":
            settings.ingest.telemetry_profile = "boutique"
            settings.ingest.extra_packs = ["cadvisor", "loki_system"]
        elif pack_key == "boutique+cadvisor":
            settings.ingest.telemetry_profile = "boutique"
            settings.ingest.extra_packs = ["cadvisor"]
        else:
            settings.ingest.telemetry_profile = "boutique"
            settings.ingest.extra_packs = []
        settings.ingest.scope_namespace = (
            self._telem_namespace.text().strip()
            or settings.connect.kubernetes.default_namespace
            or "default"
        )

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
            from chaosgen.config.secrets import delete_secret, save_secret
            from chaosgen.config.settings import load_settings, save_settings

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
            self._apply_form_to_settings(settings)

            validation = validate_profile_connect(settings)
            if not validation.ok:
                QMessageBox.warning(
                    self,
                    "Profile validation failed",
                    "Cannot save — fix the following:\n\n• "
                    + "\n• ".join(validation.errors),
                )
                self._status.setText("Save blocked: connect profile incomplete.")
                self._status.setStyleSheet(f"color: {Colors.DANGER};")
                return

            save_settings(settings)

            stored = self._sync_secret_fields_from_disk()
            tier = validation.tier
            if updated_labels:
                msg = f"Saved ({tier}). Updated keys: {', '.join(updated_labels)}."
            elif stored:
                msg = f"Profile saved ({tier}). Keys on disk: {', '.join(stored)}."
            else:
                msg = f"Profile saved ({tier})."
            self._status.setText(msg)
            self._status.setStyleSheet(f"color: {Colors.SUCCESS};")
            self._check_permissions()
            self.settings_saved.emit()

        except Exception as exc:
            self._status.setText(f"Error: {exc}")
            self._status.setStyleSheet("color: #f87171;")
            logger.exception("Failed to save settings")

    def _on_clear(self) -> None:
        reply = QMessageBox.question(
            self,
            "Confirm Clear",
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
