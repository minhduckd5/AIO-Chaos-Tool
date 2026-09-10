"""
Experiments view -- experiment list + creation form + lifecycle safety strip.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QSpinBox, QCheckBox, QPushButton,
    QListWidget, QListWidgetItem, QSplitter, QScrollArea, QFrame,
    QStackedWidget, QMessageBox,
)

from chaosgen.gui.theme import Colors, Spacing, set_semantic_role
from chaosgen.schemas.faults import (
    ChaosExperiment, TargetSpec, TargetType, FaultType,
    NetworkFaultSpec, ProcessFaultSpec, ResourceFaultSpec, FaultSpec,
)

_ACTIVE_STATES = {
    "pending_approval",
    "steady_state_check",
    "injecting",
    "verifying",
    "rollback",
}

# --- START MODIFICATION ---
# GUI environment matrix (matches EnvironmentType; P0 live vs P1 dry-run)
# --- END MODIFICATION ---
_P0_LIVE_ENVS = frozenset({"kubernetes", "docker_compose", "docker"})
_P1_DRYRUN_ENVS = frozenset({"bare_metal", "cloud_vm", "serverless"})
_DOCKER_ENVS = frozenset({"docker_compose", "docker"})
_HOST_ENVS = frozenset({"bare_metal", "cloud_vm"})
_ENV_STACK_INDEX = {
    "kubernetes": 0,
    "docker_compose": 1,
    "docker": 1,
    "bare_metal": 2,
    "cloud_vm": 2,
    "serverless": 3,
}


class ExperimentsView(QWidget):
    """Split view: experiment history list (left) + creation form (right)."""

    navigate_requested = Signal(str)  # evaluation | advisor

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._lifecycle_phase = "Pending"
        self._conn_ok = False
        self._init_ui()
        self._connect_signals()
        # --- START MODIFICATION ---
        # Seed History from CTK journals / verdict artifacts (read-only)
        # --- END MODIFICATION ---
        self._seed_history()
        self._history_list.itemClicked.connect(self._on_history_item_clicked)

    def showEvent(self, event):
        super().showEvent(event)
        self._seed_history()

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.XL, Spacing.XL, Spacing.XL, Spacing.XL)
        layout.setSpacing(Spacing.LG)

        title = QLabel("Experiments")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "What is running / what do I run next? — inject → observe → finish"
        )
        subtitle.setObjectName("sectionSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # --- START MODIFICATION ---
        # Lifecycle strip + pinned Halt + Evaluation handoff
        # --- END MODIFICATION ---
        life = QHBoxLayout()
        life.setSpacing(Spacing.MD)
        self._phase_labels: dict[str, QLabel] = {}
        for name in ("Pending", "Injecting", "Running", "Finished"):
            lbl = QLabel(name)
            # MODIFIED: phase token via objectName (theme global QSS)
            lbl.setObjectName("phaseIdle")
            self._phase_labels[name] = lbl
            life.addWidget(lbl)
        life.addStretch()

        self._halt_btn = QPushButton("HALT / ABORT")
        self._halt_btn.setEnabled(False)
        self._halt_btn.setObjectName("btnDanger")
        self._halt_btn.clicked.connect(self._on_halt)
        life.addWidget(self._halt_btn)

        self._open_eval_btn = QPushButton("Open Evaluation outcome")
        self._open_eval_btn.setObjectName("btnLink")
        self._open_eval_btn.clicked.connect(
            lambda: self.navigate_requested.emit("evaluation")
        )
        life.addWidget(self._open_eval_btn)
        layout.addLayout(life)

        self._lifecycle_hint = QLabel("State: idle")
        self._lifecycle_hint.setObjectName("textSecondary")
        layout.addWidget(self._lifecycle_hint)

        splitter = QSplitter(Qt.Horizontal)

        # Left: history list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        list_header = QLabel("History")
        list_header.setObjectName("listSectionHeader")
        left_layout.addWidget(list_header)
        self._history_list = QListWidget()
        self._history_list.setObjectName("historyList")
        left_layout.addWidget(self._history_list)
        splitter.addWidget(left)

        # Right: creation form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("transparentScroll")

        form_container = QWidget()
        form_layout = QVBoxLayout(form_container)
        form_layout.setContentsMargins(Spacing.LG, 0, 0, 0)
        form_layout.setSpacing(Spacing.LG)

        form_header = QLabel("Create Experiment")
        form_header.setObjectName("listSectionHeader")
        form_layout.addWidget(form_header)
        self._executor_badge = QLabel("Executor: —")
        self._executor_badge.setObjectName("mutedHint")
        form_layout.addWidget(self._executor_badge)

        # --- START MODIFICATION ---
        # Staged Scenario card (hidden until Catalog pushes a scenario)
        # --- END MODIFICATION ---
        self._staged_card = self._make_card("Staged Scenario (from Catalog)")
        self._staged_card.setObjectName("cardAccent")
        staged_inner = QVBoxLayout()
        self._staged_info_label = QLabel("No scenario staged.")
        self._staged_info_label.setObjectName("bodyPrimary")
        self._staged_info_label.setWordWrap(True)
        staged_inner.addWidget(self._staged_info_label)

        staged_btn_row = QHBoxLayout()
        self._exec_staged_btn = QPushButton("Execute Staged Experiment")
        self._exec_staged_btn.setObjectName("btnPrimary")
        self._exec_staged_btn.clicked.connect(self._on_submit)
        self._dismiss_staged_btn = QPushButton("Dismiss")
        self._dismiss_staged_btn.setObjectName("btnGhost")
        self._dismiss_staged_btn.clicked.connect(lambda: self._staged_card.setVisible(False))
        staged_btn_row.addWidget(self._exec_staged_btn)
        staged_btn_row.addWidget(self._dismiss_staged_btn)
        staged_btn_row.addStretch()
        staged_inner.addLayout(staged_btn_row)
        self._staged_card.layout().addLayout(staged_inner)
        self._staged_card.setVisible(False)
        form_layout.addWidget(self._staged_card)

        # --- START MODIFICATION ---
        # Target Environment & Connection: full EnvironmentType matrix (P0/P1)
        # --- END MODIFICATION ---
        conn_card = self._make_card("Target Environment & Connection")
        conn_outer = QVBoxLayout()
        env_row = QFormLayout()
        self._env_combo = QComboBox()
        self._env_combo.addItem("Kubernetes (P0 Live)", "kubernetes")
        self._env_combo.addItem("Docker Compose (P0 Live)", "docker_compose")
        self._env_combo.addItem("Bare Metal (P1 Dry-run)", "bare_metal")
        self._env_combo.addItem("Cloud VM (P1 Dry-run)", "cloud_vm")
        self._env_combo.addItem("Serverless (P1 Dry-run)", "serverless")
        self._env_combo.currentIndexChanged.connect(self._on_env_changed)
        env_row.addRow("Environment:", self._env_combo)
        conn_outer.addLayout(env_row)

        self._conn_stack = QStackedWidget()

        # Index 0 — Kubernetes
        self._k8s_container = QWidget()
        k8s_fl = QFormLayout(self._k8s_container)
        k8s_fl.setContentsMargins(0, 0, 0, 0)
        self._kubeconfig = QLineEdit("")
        self._kubeconfig.setPlaceholderText("~/.kube/config")
        self._style_input(self._kubeconfig)
        self._kubeconfig.textChanged.connect(self._invalidate_conn)
        self._context = QComboBox()
        self._context.setEditable(True)
        self._context.currentTextChanged.connect(self._invalidate_conn)
        self._ssh_host = QLineEdit("")
        self._ssh_host.setPlaceholderText("optional — only if API :6443 unreachable")
        self._style_input(self._ssh_host)
        self._ssh_user = QLineEdit("")
        self._style_input(self._ssh_user)
        self._ssh_identity = QLineEdit("")
        self._ssh_identity.setPlaceholderText("~/.ssh/id_ed25519")
        self._style_input(self._ssh_identity)
        self._test_conn_btn = QPushButton("Test connection")
        self._test_conn_btn.clicked.connect(self._on_test_connection)
        self._refresh_ctx_btn = QPushButton("Refresh contexts")
        self._refresh_ctx_btn.clicked.connect(self._on_refresh_contexts)
        k8s_fl.addRow("Kubeconfig:", self._kubeconfig)
        k8s_fl.addRow("Context:", self._context)
        k8s_conn_row = QHBoxLayout()
        k8s_conn_row.addWidget(self._test_conn_btn)
        k8s_conn_row.addWidget(self._refresh_ctx_btn)
        k8s_fl.addRow("", k8s_conn_row)
        k8s_fl.addRow("SSH bastion:", self._ssh_host)
        k8s_fl.addRow("SSH user:", self._ssh_user)
        k8s_fl.addRow("SSH identity:", self._ssh_identity)
        self._conn_stack.addWidget(self._k8s_container)

        # Index 1 — Docker Compose (connect.docker)
        self._docker_container = QWidget()
        docker_fl = QFormLayout(self._docker_container)
        docker_fl.setContentsMargins(0, 0, 0, 0)
        self._docker_host = QLineEdit("")
        self._docker_host.setPlaceholderText("unix:///var/run/docker.sock or npipe:////./pipe/docker_engine")
        self._style_input(self._docker_host)
        self._docker_host.textChanged.connect(self._invalidate_conn)
        self._docker_compose = QLineEdit("")
        self._docker_compose.setPlaceholderText("labs/modular-monolith/docker-compose.yml")
        self._style_input(self._docker_compose)
        self._docker_project = QLineEdit("")
        self._docker_project.setPlaceholderText("chaosgen-monolith")
        self._style_input(self._docker_project)
        self._test_docker_btn = QPushButton("Test Docker connection")
        self._test_docker_btn.clicked.connect(self._on_test_docker_connection)
        docker_fl.addRow("Docker host:", self._docker_host)
        docker_fl.addRow("Compose file:", self._docker_compose)
        docker_fl.addRow("Project name:", self._docker_project)
        docker_fl.addRow("", self._test_docker_btn)
        self._conn_stack.addWidget(self._docker_container)

        # Index 2 — Bare Metal / Cloud VM (P1)
        self._host_container = QWidget()
        host_fl = QFormLayout(self._host_container)
        host_fl.setContentsMargins(0, 0, 0, 0)
        self._host_target = QLineEdit("")
        self._host_target.setPlaceholderText("10.0.0.12 or chaos-host.lab")
        self._style_input(self._host_target)
        self._host_ssh_port = QSpinBox()
        self._host_ssh_port.setRange(1, 65535)
        self._host_ssh_port.setValue(22)
        self._host_ssh_user = QLineEdit("")
        self._host_ssh_user.setPlaceholderText("root or deploy")
        self._style_input(self._host_ssh_user)
        self._host_ssh_key = QLineEdit("")
        self._host_ssh_key.setPlaceholderText("~/.ssh/id_ed25519")
        self._style_input(self._host_ssh_key)
        host_help = QLabel(
            "P1 Profile — Generates host-level chaos spec "
            "(stress-ng / process kill / systemd). No live mutation."
        )
        host_help.setWordWrap(True)
        host_help.setObjectName("mutedHint")
        host_fl.addRow("Target Host / IP:", self._host_target)
        host_fl.addRow("SSH Port:", self._host_ssh_port)
        host_fl.addRow("SSH User:", self._host_ssh_user)
        host_fl.addRow("Auth Key Path:", self._host_ssh_key)
        host_fl.addRow("", host_help)
        self._conn_stack.addWidget(self._host_container)

        # Index 3 — Serverless / FaaS (P1)
        self._serverless_container = QWidget()
        faas_fl = QFormLayout(self._serverless_container)
        faas_fl.setContentsMargins(0, 0, 0, 0)
        self._faas_provider = QComboBox()
        self._faas_provider.addItem("AWS Lambda", "aws_lambda")
        self._faas_provider.addItem("LocalStack", "localstack")
        self._faas_provider.addItem("Google Cloud Functions", "gcf")
        self._faas_function = QLineEdit("")
        self._faas_function.setPlaceholderText("arn:aws:lambda:... or my-func")
        self._style_input(self._faas_function)
        faas_help = QLabel(
            "P1 Profile — Boundary dependency injection via CTK manifest. "
            "No cloud credentials / runtime driver in thesis scope."
        )
        faas_help.setWordWrap(True)
        faas_help.setObjectName("mutedHint")
        faas_fl.addRow("Provider:", self._faas_provider)
        faas_fl.addRow("Function ARN / Name:", self._faas_function)
        faas_fl.addRow("", faas_help)
        self._conn_stack.addWidget(self._serverless_container)

        conn_outer.addWidget(self._conn_stack)

        self._dry_run = QCheckBox("Dry-run (no cluster / container mutation)")
        self._dry_run.setChecked(True)
        conn_outer.addWidget(self._dry_run)

        self._conn_status = QLabel("Status: not tested — required before Create (Kubernetes)")
        set_semantic_role(self._conn_status, "muted")
        conn_outer.addWidget(self._conn_status)
        conn_card.layout().addLayout(conn_outer)
        form_layout.addWidget(conn_card)

        # Card: Experiment Info
        info_card = self._make_card("Experiment Details")
        info_fl = QFormLayout()
        self._name_input = QLineEdit("My Experiment")
        self._desc_input = QLineEdit("Chaos Test")
        self._style_input(self._name_input)
        self._style_input(self._desc_input)
        info_fl.addRow("Name:", self._name_input)
        info_fl.addRow("Description:", self._desc_input)
        info_card.layout().addLayout(info_fl)
        form_layout.addWidget(info_card)

        # --- START MODIFICATION ---
        # Fault-centric: each fault row contains target multi-select (subset of fault)
        # --- END MODIFICATION ---
        fault_card = self._make_card("Fault Injection")
        fault_header = QFormLayout()
        self._target_ns = QLineEdit("default")
        self._style_input(self._target_ns)
        self._label_key = QLineEdit("app")
        self._style_input(self._label_key)
        self._suite_delay = QSpinBox()
        self._suite_delay.setRange(0, 300)
        self._suite_delay.setValue(0)
        self._suite_delay.setSuffix(" s")
        self._refresh_svc_btn = QPushButton("Refresh services")
        self._refresh_svc_btn.clicked.connect(self._on_refresh_services)
        self._svc_hint = QLabel(
            "Each fault row: pick targets (Ctrl/Cmd+click). "
            "CTK cascade = one action per target with optional pause."
        )
        self._svc_hint.setObjectName("mutedHint")
        self._svc_hint.setWordWrap(True)
        fault_header.addRow("Namespace:", self._target_ns)
        fault_header.addRow("Label key:", self._label_key)
        fault_header.addRow("Action pause:", self._suite_delay)
        fault_header.addRow("", self._refresh_svc_btn)
        fault_header.addRow("", self._svc_hint)
        fault_card.layout().addLayout(fault_header)

        self._fault_rows_layout = QVBoxLayout()
        self._fault_row_widgets: list[dict] = []
        fault_card.layout().addLayout(self._fault_rows_layout)
        fault_btns = QHBoxLayout()
        self._add_fault_btn = QPushButton("Add fault")
        self._add_fault_btn.clicked.connect(lambda: self._add_fault_row())
        fault_btns.addWidget(self._add_fault_btn)
        fault_btns.addStretch()
        fault_card.layout().addLayout(fault_btns)
        form_layout.addWidget(fault_card)
        self._add_fault_row()

        # Legacy Docker/kubectl path still uses TargetType on experiments
        self._target_type = QComboBox()
        for t in TargetType:
            self._target_type.addItem(t.value)
        idx = self._target_type.findText(TargetType.SERVICE.value)
        if idx >= 0:
            self._target_type.setCurrentIndex(idx)
        self._target_type.hide()

        # Options
        self._rollback = QCheckBox("Auto-recover / rollback (network CR cleanup)")
        self._rollback.setChecked(True)
        form_layout.addWidget(self._rollback)

        btn_row = QHBoxLayout()
        self._preview_btn = QPushButton("Preview experiment JSON")
        self._preview_btn.clicked.connect(self._on_preview_ctk_json)
        btn_row.addWidget(self._preview_btn)
        btn_row.addStretch()
        form_layout.addLayout(btn_row)

        # Submit
        self._submit_btn = QPushButton("Create Experiment")
        self._submit_btn.setObjectName("btnPrimary")
        self._submit_btn.clicked.connect(self._on_submit)
        form_layout.addWidget(self._submit_btn)

        form_layout.addStretch()
        scroll.setWidget(form_container)
        splitter.addWidget(scroll)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)

        self._set_lifecycle_phase("Pending")
        self._load_inject_defaults()
        self._on_env_changed()
        self._update_executor_badge()

    def _update_executor_badge(self) -> None:
        env = self._selected_environment()
        executor = "ctk"
        try:
            from chaosgen.config.settings import load_settings

            executor = load_settings().inject.executor or "ctk"
        except Exception:
            pass
        if env == "kubernetes" and executor == "ctk":
            self._executor_badge.setText("Executor: Chaos Toolkit (ctk) — fault → targets[] → method[]")
        elif env == "kubernetes":
            self._executor_badge.setText("Executor: kubectl-chaos (legacy)")
        elif env in _DOCKER_ENVS:
            self._executor_badge.setText("Executor: Docker Compose / Pumba (P0 Live)")
        elif env in _HOST_ENVS:
            self._executor_badge.setText("Executor: Host chaos spec (P1 Dry-run)")
        elif env == "serverless":
            self._executor_badge.setText("Executor: Serverless CTK manifest (P1 Dry-run)")
        else:
            self._executor_badge.setText(f"Executor: {env}")

    def _is_ctk_path(self) -> bool:
        if self._selected_environment() != "kubernetes":
            return False
        try:
            from chaosgen.config.settings import load_settings

            return (load_settings().inject.executor or "ctk") == "ctk"
        except Exception:
            return True

    def _selected_environment(self) -> str:
        data = self._env_combo.currentData()
        if data:
            return str(data)
        return self._env_combo.currentText().strip().lower() or "kubernetes"

    def _is_p1_environment(self, env: str | None = None) -> bool:
        return (env or self._selected_environment()) in _P1_DRYRUN_ENVS

    def _is_docker_environment(self, env: str | None = None) -> bool:
        return (env or self._selected_environment()) in _DOCKER_ENVS

    def _invalidate_conn(self, *_args) -> None:
        self._conn_ok = False
        env = self._selected_environment()
        if env in _P1_DRYRUN_ENVS:
            self._conn_status.setText(
                "Status: P1 Dry-run Mode — Generates experiment manifest (no runtime mutation)"
            )
            set_semantic_role(self._conn_status, 'warning')
        elif env == "kubernetes":
            self._conn_status.setText("Status: not tested — required before Create (Kubernetes)")
            set_semantic_role(self._conn_status, 'muted')
        elif env in _DOCKER_ENVS:
            self._conn_status.setText("Status: not tested — Test Docker connection recommended")
            set_semantic_role(self._conn_status, 'muted')
        else:
            self._conn_status.setText("Status: not tested")
            set_semantic_role(self._conn_status, 'muted')

    def _on_env_changed(self, *_args) -> None:
        # --- START MODIFICATION ---
        # Swap connect panels + lock dry-run for P1 environments
        # --- END MODIFICATION ---
        env = self._selected_environment()
        self._conn_stack.setCurrentIndex(_ENV_STACK_INDEX.get(env, 0))
        if env in _P1_DRYRUN_ENVS:
            self._dry_run.setChecked(True)
            self._dry_run.setEnabled(False)
            self._conn_status.setText(
                "Status: P1 Dry-run Mode — Generates experiment manifest (no runtime mutation)"
            )
            set_semantic_role(self._conn_status, 'warning')
        else:
            self._dry_run.setEnabled(True)
            self._invalidate_conn()
        self._update_executor_badge()

    def reload_settings(self) -> None:
        """Re-read settings.yaml after Settings Save (wired from MainWindow)."""
        self._populate_connect_fields_from_settings()
        self._on_env_changed()
        self._update_executor_badge()

    def _populate_connect_fields_from_settings(self) -> None:
        try:
            from chaosgen.config.settings import load_settings
            from pathlib import Path
            import os

            settings = load_settings()
            inj = settings.inject
            conn = settings.connect

            # Prefer connect.kubernetes; fall back to legacy inject.*
            kube = (conn.kubernetes.kubeconfig if conn.kubernetes else None) or inj.kubeconfig
            if kube:
                self._kubeconfig.setText(kube)
            elif not self._kubeconfig.text().strip():
                default_kube = Path(os.path.expanduser("~/.kube/config"))
                if default_kube.is_file():
                    self._kubeconfig.setText(str(default_kube))

            ctx = (conn.kubernetes.context if conn.kubernetes else None) or inj.context
            if ctx:
                self._context.setEditText(ctx)

            docker = conn.docker
            if docker:
                if docker.host:
                    self._docker_host.setText(docker.host)
                elif not self._docker_host.text().strip():
                    # Sensible platform default when settings empty
                    if os.name == "nt":
                        self._docker_host.setText("npipe:////./pipe/docker_engine")
                    else:
                        self._docker_host.setText("unix:///var/run/docker.sock")
                if docker.compose_file:
                    self._docker_compose.setText(docker.compose_file)
                if docker.project_name:
                    self._docker_project.setText(docker.project_name)

            self._dry_run.setChecked(bool(inj.dry_run))
            self._target_ns.setText(
                (conn.kubernetes.default_namespace if conn.kubernetes else None)
                or inj.default_namespace
                or "default"
            )
            self._label_key.setText(inj.label_key or "app")

            bastion = (
                conn.kubernetes.ssh_bastion
                if conn.kubernetes and conn.kubernetes.ssh_bastion
                else inj.ssh_bastion
            )
            if bastion and bastion.host:
                self._ssh_host.setText(bastion.host)
            if bastion and bastion.user:
                self._ssh_user.setText(bastion.user)
            if bastion and bastion.identity_file:
                self._ssh_identity.setText(bastion.identity_file)

            # Prefer env hint / architecture when selecting stack
            env_hint = getattr(settings.hints, "environment", None)
            arch = getattr(settings.hints, "architecture", None)
            target = "kubernetes"
            if env_hint is not None:
                hint_val = getattr(env_hint, "value", str(env_hint))
                if hint_val in ("docker_compose", "docker"):
                    target = "docker_compose"
                elif hint_val in ("bare_metal", "cloud_vm", "serverless"):
                    target = hint_val
            elif arch is not None:
                arch_val = getattr(arch, "value", str(arch))
                if arch_val == "serverless":
                    target = "serverless"
                elif arch_val in ("modular_monolith", "monolith", "event_driven", "client_server"):
                    target = "docker_compose"
            elif docker and (docker.compose_file or docker.host) and not kube:
                target = "docker_compose"

            idx = self._env_combo.findData(target)
            if idx >= 0:
                self._env_combo.blockSignals(True)
                self._env_combo.setCurrentIndex(idx)
                self._env_combo.blockSignals(False)
        except Exception:
            pass

    def _load_inject_defaults(self) -> None:
        self._populate_connect_fields_from_settings()

    def _sync_docker_connect_to_settings(self) -> None:
        """Push Docker form fields into orchestrator settings.connect.docker."""
        orch = self._controller.orchestrator
        settings = getattr(orch, "_cg_settings", None)
        if settings is None:
            return
        settings.connect.docker.host = self._docker_host.text().strip() or None
        settings.connect.docker.compose_file = self._docker_compose.text().strip() or None
        settings.connect.docker.project_name = self._docker_project.text().strip() or None
        settings.inject.dry_run = self._dry_run.isChecked()

    def _kubectl_module(self):
        orch = self._controller.orchestrator
        mod = orch.get_module("kubectl-chaos")
        if mod is None:
            return None
        # Live-update config from form
        mod.kubeconfig = mod._expand(self._kubeconfig.text().strip() or None)
        ctx = self._context.currentText().strip()
        mod.context = ctx or None
        mod.dry_run = self._dry_run.isChecked()
        mod.default_namespace = self._target_ns.text().strip() or "default"
        ssh_host = self._ssh_host.text().strip()
        mod.ssh_host = ssh_host or None
        mod.ssh_user = self._ssh_user.text().strip() or None
        ident = self._ssh_identity.text().strip()
        mod.ssh_identity_file = ident or None
        mod.ssh_enabled = bool(ssh_host)
        mod.ssh_auto_on_fail = True
        mod.invalidate_client()
        return mod

    def _on_test_connection(self) -> None:
        if self._selected_environment() != "kubernetes":
            self._conn_status.setText("Status: switch Environment to Kubernetes to test")
            return
        mod = self._kubectl_module()
        if not mod:
            self._conn_ok = False
            self._conn_status.setText("Status: kubectl-chaos module missing")
            return
        if not (self._kubeconfig.text().strip()):
            self._conn_ok = False
            self._conn_status.setText("Status: FAIL — kubeconfig path required")
            set_semantic_role(self._conn_status, 'danger')
            return
        result = mod.execute("test_connection", {})
        if result.get("success"):
            self._conn_ok = True
            backend = result.get("backend") or ""
            via = " via SSH bastion" if result.get("bastion") else ""
            self._conn_status.setText(f"Status: OK — cluster reachable ({backend}){via}")
            set_semantic_role(self._conn_status, 'success')
            self._controller.log_message.emit(result.get("message") or "connection OK")
        else:
            self._conn_ok = False
            err = result.get("error") or result.get("message") or "failed"
            self._conn_status.setText(f"Status: FAIL — {err}")
            set_semantic_role(self._conn_status, 'danger')
            self._controller.log_message.emit(f"Test connection failed: {err}")

    def _on_test_docker_connection(self) -> None:
        """Validate Docker engine reachability via Pumba module (docker info)."""
        if not self._is_docker_environment():
            self._conn_status.setText("Status: switch Environment to Docker Compose to test")
            return
        self._sync_docker_connect_to_settings()
        orch = self._controller.orchestrator
        from chaosgen.config.connect_routing import apply_connect_profile_to_orchestrator

        if getattr(orch, "_cg_settings", None) is not None:
            apply_connect_profile_to_orchestrator(orch)

        mod = orch.get_module("pumba")
        if mod is None:
            self._conn_ok = False
            self._conn_status.setText("Status: FAIL — pumba module missing")
            set_semantic_role(self._conn_status, 'danger')
            return

        # Refresh module config from form
        mod.config = {
            **getattr(mod, "config", {}),
            "docker_host": self._docker_host.text().strip() or None,
            "compose_file": self._docker_compose.text().strip() or None,
            "project_name": self._docker_project.text().strip() or None,
            "dry_run": self._dry_run.isChecked(),
        }
        mod.docker_host = self._docker_host.text().strip() or None
        mod.compose_file = self._docker_compose.text().strip() or None
        mod.project_name = self._docker_project.text().strip() or None
        mod.dry_run = self._dry_run.isChecked()

        ok = False
        try:
            probe = getattr(mod, "_docker_available", None)
            ok = bool(probe()) if callable(probe) else bool(mod.validate_config())
        except Exception as exc:
            self._conn_ok = False
            self._conn_status.setText(f"Status: FAIL — {exc}")
            set_semantic_role(self._conn_status, 'danger')
            return

        if ok:
            self._conn_ok = True
            host = self._docker_host.text().strip() or "(default socket)"
            self._conn_status.setText(f"Status: OK — Docker reachable ({host})")
            set_semantic_role(self._conn_status, 'success')
            self._controller.log_message.emit(f"Docker connection OK: {host}")
        else:
            self._conn_ok = False
            self._conn_status.setText(
                "Status: FAIL — Docker engine not reachable (check host / Desktop)"
            )
            set_semantic_role(self._conn_status, 'danger')
            self._controller.log_message.emit("Docker Test connection failed")

    def _on_refresh_contexts(self) -> None:
        mod = self._kubectl_module()
        if not mod:
            return
        result = mod.execute("list_contexts", {})
        self._context.clear()
        for name in result.get("contexts") or []:
            self._context.addItem(name)

    def _on_refresh_services(self) -> None:
        mod = self._kubectl_module()
        if not mod:
            return
        result = mod.execute(
            "list_workloads",
            {"namespace": self._target_ns.text().strip() or "default"},
        )
        workloads = result.get("workloads") or []
        if not result.get("success"):
            self._controller.log_message.emit(
                f"Refresh services failed: {result.get('error') or result.get('message')}"
            )
            workloads = ["frontend"]
        for row in self._fault_row_widgets:
            svc_list = row["services"]
            selected = {i.text() for i in svc_list.selectedItems()}
            svc_list.clear()
            for name in workloads:
                item = QListWidgetItem(name)
                svc_list.addItem(item)
                if name in selected:
                    item.setSelected(True)
            if svc_list.count() and not svc_list.selectedItems():
                svc_list.item(0).setSelected(True)
        self._controller.log_message.emit(f"Loaded {len(workloads)} workload(s)")

    def _selected_services(self) -> list[str]:
        """Union of services from all fault rows (legacy path)."""
        names: list[str] = []
        for row in self._fault_row_widgets:
            for i in row["services"].selectedItems():
                t = i.text().strip()
                if t and t not in names:
                    names.append(t)
        return names

    def _build_fault_row_dicts(self) -> list[dict]:
        rows: list[dict] = []
        for row in self._fault_row_widgets:
            services = [
                i.text().strip()
                for i in row["services"].selectedItems()
                if i.text().strip()
            ]
            rows.append(
                {
                    "ftype": row["ftype"].currentText(),
                    "duration": row["duration"].text().strip() or "30s",
                    "latency": row["latency"].text().strip() or "100ms",
                    "loss_percentage": float(row["loss"].value()),
                    "signal": row["signal"].text().strip() or "SIGKILL",
                    "services": services,
                }
            )
        return rows

    def _build_ctk_fault_payload(self) -> list[dict]:
        ns = self._target_ns.text().strip() or "default"
        label_key = self._label_key.text().strip() or "app"
        faults: list[dict] = []
        for row in self._build_fault_row_dicts():
            services = row["services"]
            if not services:
                continue
            faults.append(
                {
                    "fault_type": row["ftype"],
                    "duration": row["duration"],
                    "latency": row["latency"],
                    "loss_percentage": row["loss_percentage"],
                    "signal": row["signal"],
                    "targets": [
                        {
                            "service": svc,
                            "namespace": ns,
                            "label_key": label_key,
                        }
                        for svc in services
                    ],
                }
            )
        return faults

    def _on_preview_ctk_json(self) -> None:
        try:
            from chaosgen.gui.ctk_form import (
                build_ctk_experiment_for_environment,
                experiment_to_json,
            )

            env = self._selected_environment()
            exp = build_ctk_experiment_for_environment(
                environment=env,
                title=self._name_input.text().strip() or "My Experiment",
                description=self._desc_input.text() or "ChaosGen CTK experiment",
                fault_rows=self._build_fault_row_dicts(),
                default_namespace=self._target_ns.text().strip() or "default",
                default_label_key=self._label_key.text().strip() or "app",
                action_pause_seconds=float(self._suite_delay.value()),
                auto_rollback=self._rollback.isChecked(),
                max_actions=self._max_services_per_suite(),
                host_target=self._host_target.text().strip() or None,
                host_ssh_user=self._host_ssh_user.text().strip() or None,
                host_ssh_port=int(self._host_ssh_port.value()),
                faas_provider=str(
                    self._faas_provider.currentData() or self._faas_provider.currentText()
                ),
                faas_function=self._faas_function.text().strip() or None,
                kube_context=self._context.currentText().strip() or None,
            )
            text = experiment_to_json(exp)
            # Guardrail banner for demo: prove provider matches environment
            providers = {
                (a.provider or {}).get("type")
                for a in (exp.method or [])
            }
            header = f"Environment: {env}  |  provider types: {sorted(p for p in providers if p)}\n\n"
            QMessageBox.information(self, "CTK experiment preview", header + text)
        except Exception as e:
            QMessageBox.critical(self, "Preview failed", str(e))

    def _max_services_per_suite(self) -> int:
        try:
            from chaosgen.config.settings import load_settings

            return int(load_settings().safety.max_services_per_suite)
        except Exception:
            return 3

    def _build_faults(self) -> list[FaultSpec]:
        faults: list[FaultSpec] = []
        for row in self._fault_row_widgets:
            ftype = row["ftype"].currentText()
            common = {
                "fault_type": FaultType(ftype),
                "duration": row["duration"].text().strip() or "30s",
            }
            if ftype in ("network_latency", "packet_loss"):
                faults.append(
                    NetworkFaultSpec(
                        **common,
                        latency=row["latency"].text().strip() or "100ms",
                        jitter="10ms",
                        loss_percentage=float(row["loss"].value()) if ftype == "packet_loss" else None,
                    )
                )
            elif ftype == "process_kill":
                faults.append(
                    ProcessFaultSpec(
                        **common,
                        signal=row["signal"].text().strip() or "SIGKILL",
                    )
                )
            elif ftype == "resource_exhaustion":
                faults.append(ResourceFaultSpec(**common, cpu_percent=80))
            else:
                faults.append(FaultSpec(**common))
        return faults

    def _submit_p1_dryrun(
        self,
        env: str,
        services: list[str],
        faults: list,
        ns: str,
        label_key: str,
        desc: str,
    ) -> None:
        """P1 safe path: write env-correct CTK manifest; no live inject."""
        from chaosgen.gui.ctk_form import (
            build_ctk_experiment_for_environment,
            experiment_to_json,
        )

        base_name = self._name_input.text().strip() or "staged-dryrun"
        fault_types = []
        for f in faults:
            raw = getattr(f, "fault_type", None)
            fault_types.append(getattr(raw, "value", str(raw or "unknown")))

        # Prefer connect-panel targets for P1 when fault-row services are placeholders
        host_target = self._host_target.text().strip() or None
        faas_fn = self._faas_function.text().strip() or None
        if env in _HOST_ENVS and host_target:
            services = [host_target]
        elif env == "serverless" and faas_fn:
            services = [faas_fn]

        exp = build_ctk_experiment_for_environment(
            environment=env,
            title=base_name,
            description=desc or "ChaosGen P1 dry-run",
            fault_rows=self._build_fault_row_dicts(),
            default_namespace=ns,
            default_label_key=label_key,
            action_pause_seconds=float(self._suite_delay.value()),
            auto_rollback=self._rollback.isChecked(),
            max_actions=self._max_services_per_suite(),
            host_target=host_target,
            host_ssh_user=self._host_ssh_user.text().strip() or None,
            host_ssh_port=int(self._host_ssh_port.value()),
            faas_provider=str(
                self._faas_provider.currentData() or self._faas_provider.currentText()
            ),
            faas_function=faas_fn,
        )
        payload = experiment_to_json(exp)

        # Persist under scratch/ctk/experiments/ for closed-loop demo evidence
        out_dir = self._project_root() / "scratch" / "ctk" / "experiments"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in base_name).strip("-") or "p1"
        out_path = out_dir / f"{safe}-{env}.json"
        out_path.write_text(payload, encoding="utf-8")

        self._controller.log_message.emit(
            f"[P1 DRY-RUN] {env} — CTK manifest written: {out_path}\n{payload}"
        )
        self._history_list.insertItem(
            0,
            QListWidgetItem(
                f"[STAGED-DRYRUN] {base_name} → {', '.join(services)} "
                f"({', '.join(sorted(set(fault_types)))}) [{env}]"
            ),
        )
        self._set_lifecycle_phase("Pending")
        self._lifecycle_hint.setText(
            f"P1 dry-run: {out_path.name} (no live inject)"
        )

    def _on_submit(self):
        try:
            env = self._selected_environment()
            services = self._selected_services()
            # --- START MODIFICATION ---
            # P1 panels may supply Host IP / Function ARN instead of fault-row services
            # --- END MODIFICATION ---
            if not services and self._is_p1_environment(env):
                if env in _HOST_ENVS and self._host_target.text().strip():
                    services = [self._host_target.text().strip()]
                elif env == "serverless" and self._faas_function.text().strip():
                    services = [self._faas_function.text().strip()]
            if not services:
                raise ValueError(
                    "Select at least one target in a fault row "
                    "(or Host IP / Function ARN for P1 environments)"
                )
            max_n = self._max_services_per_suite()
            total_targets = sum(len(r["services"]) for r in self._build_fault_row_dicts())
            if self._is_ctk_path() and total_targets > max_n:
                raise ValueError(
                    f"Total {total_targets} targets exceeds max_services_per_suite={max_n}"
                )
            if not self._is_ctk_path() and len(services) > max_n:
                raise ValueError(
                    f"Selected {len(services)} services exceeds max_services_per_suite={max_n}"
                )
            label_key = self._label_key.text().strip() or "app"
            ns = self._target_ns.text().strip() or "default"
            faults = self._build_faults()
            if not faults:
                raise ValueError("Add at least one fault")
            desc = self._desc_input.text()

            # --- START MODIFICATION ---
            # P1 environments: enforce dry-run and emit manifest intent only
            # --- END MODIFICATION ---
            if self._is_p1_environment(env):
                self._dry_run.setChecked(True)
                self._submit_p1_dryrun(env, services, faults, ns, label_key, desc)
                return

            kubeconfig = self._kubeconfig.text().strip() or None
            if env == "kubernetes":
                if not kubeconfig:
                    raise ValueError("Kubernetes: kubeconfig path is required")
                if not self._conn_ok:
                    raise ValueError(
                        "Kubernetes: run Test connection successfully before Create "
                        "(no silent fallback to Docker/Pumba)"
                    )
                self._kubectl_module()
            elif self._is_docker_environment(env):
                self._sync_docker_connect_to_settings()

            orch = self._controller.orchestrator
            orch.configure_inject_from_form(
                environment=env,
                kubeconfig=kubeconfig,
                context=self._context.currentText().strip() or None,
                dry_run=self._dry_run.isChecked(),
                default_namespace=ns,
                label_key=label_key,
            )

            # --- START MODIFICATION ---
            # CTK canonical path: multi-select → method[] → chaos run
            executor = "ctk"
            try:
                from chaosgen.config.settings import load_settings

                executor = load_settings().inject.executor or "ctk"
            except Exception:
                pass

            if env == "kubernetes" and executor == "ctk":
                fault_payload = self._build_ctk_fault_payload()
                if not fault_payload:
                    raise ValueError("Each fault row needs at least one target service")
                self._controller.run_ctk_experiment_async(
                    title=self._name_input.text().strip() or "My Experiment",
                    description=self._desc_input.text() or "ChaosGen CTK experiment",
                    faults=fault_payload,
                    dry_run=self._dry_run.isChecked(),
                    label_key=label_key,
                    kube_context=self._context.currentText().strip() or None,
                    action_pause_seconds=float(self._suite_delay.value()),
                    auto_rollback=self._rollback.isChecked(),
                )
                n_actions = sum(len(f["targets"]) for f in fault_payload)
                label = f"ctk×{n_actions}"
                fault_types = ", ".join({f["fault_type"] for f in fault_payload})
                self._history_list.insertItem(
                    0,
                    QListWidgetItem(
                        f"[STARTED] {label} → {', '.join(services)} "
                        f"({fault_types}) [ctk]"
                    ),
                )
                self._controller.log_message.emit(
                    f"CTK experiment started: {len(fault_payload)} fault(s) "
                    f"targets={services} dry_run={self._dry_run.isChecked()}"
                )
                self._set_lifecycle_phase("Injecting")
                return
            # --- END MODIFICATION ---

            self._controller.log_message.emit(
                f"Inject path locked: env={env} translator={orch.translator.env.value}"
            )

            base_name = self._name_input.text().strip() or "suite"

            experiments: list[ChaosExperiment] = []
            for svc in services:
                experiments.append(
                    ChaosExperiment(
                        name=f"{base_name}-{svc}" if len(services) > 1 else base_name,
                        description=desc,
                        target=TargetSpec(
                            type=TargetType(self._target_type.currentText()),
                            name=svc,
                            namespace=ns,
                            selector={label_key: svc},
                        ),
                        faults=faults,
                        rollback=self._rollback.isChecked(),
                        steady_state_check=None,
                    )
                )

            if len(experiments) == 1:
                self._controller.run_experiment_async(experiments[0])
                label = experiments[0].name
            else:
                self._controller.run_experiment_suite_async(
                    experiments,
                    delay_seconds=float(self._suite_delay.value()),
                )
                label = f"suite×{len(experiments)}"

            self._history_list.insertItem(
                0,
                QListWidgetItem(
                    f"[STARTED] {label} → {', '.join(services)} ({len(faults)} fault(s)) [{env}]"
                ),
            )
            self._controller.log_message.emit(
                f"Experiment started: {label} targets={services} faults={len(faults)} env={env}"
            )
            self._set_lifecycle_phase("Injecting")

        except Exception as e:
            QMessageBox.critical(self, "Validation Error", str(e))

    def _add_fault_row(self, fault_type: str = "process_kill") -> None:
        row = QWidget()
        fl = QFormLayout(row)
        ftype = QComboBox()
        for f in FaultType:
            ftype.addItem(f.value)
        idx = ftype.findText(fault_type)
        if idx >= 0:
            ftype.setCurrentIndex(idx)
        duration = QLineEdit("30s")
        self._style_input(duration)
        latency = QLineEdit("100ms")
        self._style_input(latency)
        loss = QSpinBox()
        loss.setRange(1, 100)
        loss.setValue(10)
        loss.setSuffix(" %")
        signal = QLineEdit("SIGKILL")
        self._style_input(signal)
        services = QListWidget()
        services.setSelectionMode(QListWidget.ExtendedSelection)
        services.setMinimumHeight(80)
        services.addItem("frontend")
        services.item(0).setSelected(True)
        remove_btn = QPushButton("Remove")
        fl.addRow("Type:", ftype)
        fl.addRow("Duration:", duration)
        fl.addRow("Latency (net):", latency)
        fl.addRow("Loss % (net):", loss)
        fl.addRow("Signal (kill):", signal)
        fl.addRow("Targets:", services)
        fl.addRow("", remove_btn)
        entry = {
            "widget": row,
            "ftype": ftype,
            "duration": duration,
            "latency": latency,
            "loss": loss,
            "signal": signal,
            "services": services,
        }
        remove_btn.clicked.connect(lambda: self._remove_fault_row(entry))
        self._fault_row_widgets.append(entry)
        self._fault_rows_layout.addWidget(row)

    def _remove_fault_row(self, entry: dict) -> None:
        if len(self._fault_row_widgets) <= 1:
            QMessageBox.information(self, "Faults", "At least one fault row is required.")
            return
        self._fault_row_widgets.remove(entry)
        entry["widget"].setParent(None)
        entry["widget"].deleteLater()

    def _make_card(self, title: str) -> QWidget:
        card = QWidget()
        card.setObjectName("cardWidget")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        card_layout.setSpacing(Spacing.SM)
        header = QLabel(title)
        # MODIFIED: cardTitle objectName from theme
        header.setObjectName("cardTitle")
        card_layout.addWidget(header)
        return card

    def _style_input(self, widget):
        # MODIFIED: formInput objectName from theme (no inline QSS)
        widget.setObjectName("formInput")

    def _update_params(self, fault_type):
        # Kept for compatibility; multi-fault rows carry their own params.
        return

    # --- START MODIFICATION ---
    # History sidebar: seed from journals + ExpectationVerdictReport JSON
    # --- END MODIFICATION ---

    @staticmethod
    def _is_session_history_text(text: str) -> bool:
        return text.startswith("[STARTED]") or text.startswith("[HALT]")

    @staticmethod
    def _journal_badge(summary) -> str:
        status = (summary.status or "").lower()
        if status in ("failed", "aborted", "interrupted"):
            return "FAIL"
        if summary.deviated:
            return "FAIL"
        for act in summary.activities or []:
            if (act.status or "").lower() in ("failed", "error", "timeout", "deviated"):
                return "FAIL"
        if status == "completed":
            return "PASS"
        return "PARTIAL"

    def _collect_history_entries(self) -> list[dict]:
        root = self._project_root()
        entries: list[dict] = []

        # 1) CTK journals under scratch/ctk/journals (fixtures only if scratch empty)
        scratch_journals = root / "scratch" / "ctk" / "journals"
        fixture_journals = root / "tests" / "fixtures" / "ctk_journal"
        journal_files: list[Path] = []
        if scratch_journals.is_dir():
            journal_files = sorted(
                set(scratch_journals.glob("journal-*.json")) | set(scratch_journals.glob("*.json")),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )
        if not journal_files and fixture_journals.is_dir():
            journal_files = sorted(
                fixture_journals.glob("*.json"),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )

        try:
            from chaosgen.evaluation.ctk_journal import parse_ctk_journal
        except Exception:
            parse_ctk_journal = None

        for path in journal_files:
            try:
                if parse_ctk_journal is None:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    exp = data.get("experiment") or {}
                    title = exp.get("title") or path.stem
                    status = str(data.get("status") or "unknown")
                    verdict = (
                        "PASS"
                        if status.lower() == "completed" and not data.get("deviated")
                        else "FAIL"
                    )
                    start = str(data.get("start") or "")
                    acts = data.get("run") or []
                    act_names = []
                    for a in acts[:5]:
                        if isinstance(a, dict):
                            act = a.get("activity") or {}
                            act_names.append(str(act.get("name") or a.get("name") or ""))
                    summary_text = (
                        f"CTK Journal: {title}\nVerdict: {verdict}\nStatus: {status}\n"
                        f"Activities: {', '.join(n for n in act_names if n) or '—'}\n"
                        f"File: {path.name}"
                    )
                else:
                    summary = parse_ctk_journal(path)
                    if summary.parse_errors and not summary.title and not summary.status:
                        continue
                    title = summary.title or path.stem
                    verdict = self._journal_badge(summary)
                    status = summary.status or "unknown"
                    start = summary.start or ""
                    act_names = [a.name for a in (summary.activities or [])[:5] if a.name]
                    summary_text = (
                        f"CTK Journal: {title}\nVerdict: {verdict}\nStatus: {status}\n"
                        f"Deviated: {summary.deviated}\n"
                        f"Activities: {', '.join(act_names) or '—'}\n"
                        f"File: {path.name}"
                    )
                entries.append(
                    {
                        "name": title,
                        "verdict": verdict,
                        "timestamp": (start[:19].replace("T", " ") if start else ""),
                        "path": str(path),
                        "type": "CTK Journal",
                        "mtime": path.stat().st_mtime,
                        "summary": summary_text,
                    }
                )
            except Exception:
                continue

        # 2) ExpectationVerdictReport JSON (docs/ + scratch/) — skip alignment artifacts
        verdict_files: list[Path] = []
        for folder, patterns in (
            (root / "docs", ("*verdict*.json",)),
            (root / "scratch", ("verdict-*.json", "*verdict*.json")),
        ):
            if not folder.is_dir():
                continue
            for pattern in patterns:
                verdict_files.extend(folder.glob(pattern))

        seen_verdict: set[str] = set()
        for path in sorted(
            verdict_files,
            key=lambda x: x.stat().st_mtime if x.is_file() else 0,
            reverse=True,
        ):
            key = str(path.resolve())
            if key in seen_verdict or not path.is_file():
                continue
            seen_verdict.add(key)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if "verdict" not in data or "checks" not in data:
                continue
            if "combined_alignment" in data or "alignment_rate" in data:
                continue
            try:
                exp_name = data.get("experiment_name") or path.stem
                verdict = str(data.get("verdict") or "UNKNOWN").upper()
                checks = data.get("checks") or []
                passed_count = sum(1 for c in checks if c.get("passed"))
                total_count = len(checks)
                ts = str(data.get("evaluated_at") or data.get("timestamp") or "")
                claim = (data.get("claim") or "").strip()
                if len(claim) > 160:
                    claim = claim[:157] + "..."
                entries.append(
                    {
                        "name": exp_name,
                        "verdict": verdict,
                        "timestamp": (ts[:19].replace("T", " ") if ts else ""),
                        "path": str(path),
                        "type": "Verdict Report",
                        "mtime": path.stat().st_mtime,
                        "summary": (
                            f"Verdict Report: {exp_name}\n"
                            f"Verdict: {verdict} ({passed_count}/{total_count} checks passed)\n"
                            f"Claim: {claim or '—'}\n"
                            f"File: {path.name}"
                        ),
                    }
                )
            except Exception:
                continue

        # Prefer Verdict Report over raw CTK journal when same experiment name
        by_name: dict[str, dict] = {}
        for ent in sorted(entries, key=lambda e: e.get("mtime") or 0, reverse=True):
            name_key = str(ent.get("name") or "").strip().lower() or f"path:{ent.get('path')}"
            existing = by_name.get(name_key)
            if existing is None:
                by_name[name_key] = ent
                continue
            if existing.get("type") == "CTK Journal" and ent.get("type") == "Verdict Report":
                by_name[name_key] = ent
            elif (
                existing.get("type") == ent.get("type")
                and (ent.get("mtime") or 0) > (existing.get("mtime") or 0)
            ):
                by_name[name_key] = ent

        winners = list(by_name.values())
        winners.sort(key=lambda e: e.get("mtime") or 0, reverse=True)
        return winners

    def _seed_history(self) -> None:
        """Populate History from CTK journals + verdict JSON (read-only seed)."""
        live_items: list[str] = []
        for i in range(self._history_list.count()):
            item = self._history_list.item(i)
            if item is None:
                continue
            text = item.text()
            if self._is_session_history_text(text):
                live_items.append(text)

        self._history_list.clear()
        for txt in live_items:
            self._history_list.addItem(QListWidgetItem(txt))

        for ent in self._collect_history_entries():
            verdict = str(ent.get("verdict") or "UNKNOWN").upper()
            badge = f"[{verdict}]"
            ts = f" ({ent['timestamp']})" if ent.get("timestamp") else ""
            item = QListWidgetItem(f"{badge} {ent.get('name', 'run')}{ts}")
            item.setData(Qt.ItemDataRole.UserRole, ent)
            if verdict == "PASS":
                item.setForeground(QColor(Colors.SUCCESS))
            elif verdict == "FAIL":
                item.setForeground(QColor(Colors.DANGER))
            elif verdict == "PARTIAL":
                item.setForeground(QColor(Colors.WARNING))
            self._history_list.addItem(item)

    def load_staged_scenario(self, experiment, metadata: dict | None = None) -> None:
        """Receive a ChaosExperiment from Catalog, prefill form fields, show Staged card."""
        if experiment is None:
            return

        name = getattr(experiment, "name", "") or "Staged Experiment"
        desc = getattr(experiment, "description", "") or ""
        target_spec = getattr(experiment, "target", None)
        target_name = getattr(target_spec, "name", "") if target_spec else ""
        ns = getattr(target_spec, "namespace", "default") if target_spec else "default"
        faults = getattr(experiment, "faults", []) or []

        # Fill basic text fields
        self._name_input.setText(name)
        self._desc_input.setText(desc)
        self._target_ns.setText(ns or "default")

        # --- START MODIFICATION ---
        # Environment from catalog metadata (full EnvironmentType matrix).
        # --- END MODIFICATION ---
        meta = metadata or {}
        env_str = str(meta.get("environment") or "").lower()
        arch_str = str(meta.get("architecture") or "").lower()
        if env_str == "serverless" or arch_str == "serverless":
            env_key = "serverless"
        elif env_str in ("bare_metal", "cloud_vm"):
            env_key = env_str
        elif env_str in _DOCKER_ENVS or arch_str in (
            "modular_monolith",
            "monolith",
            "event_driven",
            "client_server",
        ):
            env_key = "docker_compose"
        else:
            env_key = "kubernetes"
        idx = self._env_combo.findData(env_key)
        if idx >= 0:
            self._env_combo.setCurrentIndex(idx)

        # Prefill first fault row
        ftype_str = "process_kill"
        if faults:
            f0 = faults[0]
            raw_ftype = getattr(f0, "fault_type", None)
            if raw_ftype is not None:
                ftype_str = getattr(raw_ftype, "value", str(raw_ftype))
            # Duration / latency / signal from fault spec
            duration_val = getattr(f0, "duration", None) or "30s"
            signal_val = getattr(f0, "signal", None) or "SIGKILL"
            latency_val = getattr(f0, "latency", None) or "100ms"
            loss_val = getattr(f0, "loss_percentage", None)
        else:
            duration_val, signal_val, latency_val, loss_val = "30s", "SIGKILL", "100ms", None

        if self._fault_row_widgets:
            first_row = self._fault_row_widgets[0]
            fidx = first_row["ftype"].findText(ftype_str)
            if fidx >= 0:
                first_row["ftype"].setCurrentIndex(fidx)

            first_row["duration"].setText(str(duration_val))
            first_row["signal"].setText(str(signal_val))
            first_row["latency"].setText(str(latency_val))
            if loss_val is not None:
                first_row["loss"].setValue(int(loss_val))

            # Add target to services list and select it
            if target_name:
                svc_list = first_row["services"]
                existing = {svc_list.item(i).text() for i in range(svc_list.count())}
                if target_name not in existing:
                    svc_list.addItem(target_name)
                for i in range(svc_list.count()):
                    item = svc_list.item(i)
                    item.setSelected(item.text() == target_name)

        # Show Staged card with summary
        env_display = self._selected_environment().upper()
        arch_display = arch_str or "—"
        self._staged_info_label.setText(
            f"<b>Scenario:</b> {name}<br>"
            f"<b>Architecture:</b> {arch_display} &nbsp;|&nbsp; "
            f"<b>Target:</b> {target_name or 'N/A'} (ns: {ns}) &nbsp;|&nbsp; "
            f"<b>Fault:</b> {ftype_str} &nbsp;|&nbsp; <b>Environment:</b> {env_display}"
        )
        self._staged_card.setVisible(True)
        self._set_lifecycle_phase("Pending")
        self._lifecycle_hint.setText(f"Staged from catalog: {name}")

    def _on_history_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            self._lifecycle_hint.setText(f"Selected: {item.text()}")
            return
        name = data.get("name") or item.text()
        verdict = data.get("verdict") or "—"
        self._lifecycle_hint.setText(f"Selected run: {name} | Verdict: {verdict}")
        summary = data.get("summary") or item.text()
        self._controller.log_message.emit(f"--- History Run Inspection ---\n{summary}")

    def _connect_signals(self):
        self._controller.experiment_finished.connect(self._on_experiment_done)
        self._controller.experiment_started.connect(self._on_experiment_started)
        self._controller.state_changed.connect(self._on_state_changed)

    def _set_lifecycle_phase(self, phase: str) -> None:
        self._lifecycle_phase = phase
        # MODIFIED: phaseActive / phaseIdle objectNames (theme global QSS)
        for name, lbl in self._phase_labels.items():
            lbl.setObjectName("phaseActive" if name == phase else "phaseIdle")
            style = lbl.style()
            if style is not None:
                style.unpolish(lbl)
                style.polish(lbl)

    def _on_halt(self):
        try:
            self._controller.trigger_rollback()
            self._controller.log_message.emit("HALT requested — triggering rollback")
            self._history_list.insertItem(0, QListWidgetItem("[HALT] Rollback requested"))
            self._set_lifecycle_phase("Finished")
            self._halt_btn.setEnabled(False)
        except Exception as exc:
            QMessageBox.critical(self, "Halt failed", str(exc))

    @Slot(str)
    def _on_experiment_started(self, name: str):
        self._set_lifecycle_phase("Running")
        self._halt_btn.setEnabled(True)
        self._lifecycle_hint.setText(f"Active: {name}")

    @Slot(str)
    def _on_state_changed(self, state: str):
        self._lifecycle_hint.setText(f"State: {state}")
        active = state.lower() in _ACTIVE_STATES
        self._halt_btn.setEnabled(active)
        mapping = {
            "idle": "Pending",
            "pending_approval": "Pending",
            "steady_state_check": "Injecting",
            "injecting": "Injecting",
            "verifying": "Running",
            "rollback": "Finished",
        }
        phase = mapping.get(state.lower())
        if phase:
            self._set_lifecycle_phase(phase)

    @Slot(bool, str)
    def _on_experiment_done(self, success, msg):
        status = "PASSED" if success else "FAILED"
        self._history_list.insertItem(0, QListWidgetItem(f"[{status}] {msg}"))
        self._set_lifecycle_phase("Finished")
        self._halt_btn.setEnabled(False)
        self._lifecycle_hint.setText(
            f"Finished ({status}). Open Evaluation outcome to review the claim."
        )
