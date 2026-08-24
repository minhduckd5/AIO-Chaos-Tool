"""
Experiments view -- experiment list + creation form + lifecycle safety strip.
"""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QSpinBox, QCheckBox, QPushButton,
    QListWidget, QListWidgetItem, QSplitter, QScrollArea, QFrame,
    QStackedWidget, QMessageBox,
)

from chaosgen.gui.theme import Colors, Fonts, Spacing
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


class ExperimentsView(QWidget):
    """Split view: experiment history list (left) + creation form (right)."""

    navigate_requested = Signal(str)  # evaluation | advisor

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._lifecycle_phase = "Pending"
        self._init_ui()
        self._connect_signals()

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
            lbl.setStyleSheet(
                f"color: {Colors.TEXT_MUTED}; padding: 4px 10px; "
                f"border: 1px solid {Colors.BORDER}; border-radius: 4px;"
            )
            self._phase_labels[name] = lbl
            life.addWidget(lbl)
        life.addStretch()

        self._halt_btn = QPushButton("HALT / ABORT")
        self._halt_btn.setEnabled(False)
        self._halt_btn.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.DANGER}; color: white; "
            f"border: none; border-radius: 6px; padding: 10px 20px; font-weight: bold; }}"
            f"QPushButton:disabled {{ background-color: {Colors.BG_HOVER}; color: {Colors.TEXT_MUTED}; }}"
        )
        self._halt_btn.clicked.connect(self._on_halt)
        life.addWidget(self._halt_btn)

        self._open_eval_btn = QPushButton("Open Evaluation outcome")
        self._open_eval_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Colors.ACCENT}; "
            f"border: 1px solid {Colors.ACCENT}; border-radius: 6px; padding: 10px 16px; }}"
        )
        self._open_eval_btn.clicked.connect(
            lambda: self.navigate_requested.emit("evaluation")
        )
        life.addWidget(self._open_eval_btn)
        layout.addLayout(life)

        self._lifecycle_hint = QLabel("State: idle")
        self._lifecycle_hint.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: {Fonts.SIZE_SMALL}px;"
        )
        layout.addWidget(self._lifecycle_hint)

        splitter = QSplitter(Qt.Horizontal)

        # Left: history list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        list_header = QLabel("History")
        list_header.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: 11px; font-weight: bold; letter-spacing: 1px;"
        )
        left_layout.addWidget(list_header)
        self._history_list = QListWidget()
        self._history_list.setStyleSheet(
            f"QListWidget {{ background-color: {Colors.BG_CARD}; border: 1px solid {Colors.BORDER}; }}"
            f"QListWidget::item {{ padding: 8px; }}"
            f"QListWidget::item:selected {{ background-color: {Colors.BG_SELECTED}; }}"
        )
        left_layout.addWidget(self._history_list)
        splitter.addWidget(left)

        # Right: creation form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        form_container = QWidget()
        form_layout = QVBoxLayout(form_container)
        form_layout.setContentsMargins(Spacing.LG, 0, 0, 0)
        form_layout.setSpacing(Spacing.LG)

        form_header = QLabel("Create Experiment")
        form_header.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: 11px; font-weight: bold; letter-spacing: 1px;"
        )
        form_layout.addWidget(form_header)

        # --- START MODIFICATION ---
        # Cluster connection (Lens-parity kubeconfig) + multi-fault rows
        # --- END MODIFICATION ---
        conn_card = self._make_card("Cluster Connection")
        conn_fl = QFormLayout()
        self._kubeconfig = QLineEdit("")
        self._kubeconfig.setPlaceholderText("~/.kube/config")
        self._style_input(self._kubeconfig)
        self._context = QComboBox()
        self._context.setEditable(True)
        self._dry_run = QCheckBox("Dry-run (no cluster mutation)")
        self._dry_run.setChecked(True)
        self._test_conn_btn = QPushButton("Test connection")
        self._test_conn_btn.clicked.connect(self._on_test_connection)
        self._refresh_ctx_btn = QPushButton("Refresh contexts")
        self._refresh_ctx_btn.clicked.connect(self._on_refresh_contexts)
        conn_fl.addRow("Kubeconfig:", self._kubeconfig)
        conn_fl.addRow("Context:", self._context)
        conn_row = QHBoxLayout()
        conn_row.addWidget(self._test_conn_btn)
        conn_row.addWidget(self._refresh_ctx_btn)
        conn_fl.addRow("", conn_row)
        conn_fl.addRow("", self._dry_run)
        self._conn_status = QLabel("Status: not tested")
        self._conn_status.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        conn_fl.addRow("", self._conn_status)
        conn_card.layout().addLayout(conn_fl)
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

        # Card: Microservice target
        target_card = self._make_card("Microservice Target")
        target_fl = QFormLayout()
        self._target_type = QComboBox()
        for t in TargetType:
            self._target_type.addItem(t.value)
        idx = self._target_type.findText(TargetType.SERVICE.value)
        if idx >= 0:
            self._target_type.setCurrentIndex(idx)
        self._target_ns = QLineEdit("default")
        self._style_input(self._target_ns)
        self._service_list = QListWidget()
        self._service_list.setSelectionMode(QListWidget.ExtendedSelection)
        self._service_list.setMinimumHeight(100)
        self._service_list.addItem("frontend")
        self._service_list.item(0).setSelected(True)
        self._label_key = QLineEdit("app")
        self._style_input(self._label_key)
        self._suite_delay = QSpinBox()
        self._suite_delay.setRange(0, 300)
        self._suite_delay.setValue(0)
        self._suite_delay.setSuffix(" s")
        self._refresh_svc_btn = QPushButton("Refresh services")
        self._refresh_svc_btn.clicked.connect(self._on_refresh_services)
        self._svc_hint = QLabel("Ctrl/Cmd+click for multi-service suite (cascading)")
        self._svc_hint.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 11px;")
        target_fl.addRow("Type:", self._target_type)
        target_fl.addRow("Namespace:", self._target_ns)
        target_fl.addRow("Services:", self._service_list)
        target_fl.addRow("", self._svc_hint)
        target_fl.addRow("Label key:", self._label_key)
        target_fl.addRow("Suite stagger:", self._suite_delay)
        target_fl.addRow("", self._refresh_svc_btn)
        target_card.layout().addLayout(target_fl)
        form_layout.addWidget(target_card)

        # Card: multi-fault list
        fault_card = self._make_card("Fault Injection (multi-fault)")
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

        # Options
        self._rollback = QCheckBox("Auto-Rollback")
        self._rollback.setChecked(True)
        form_layout.addWidget(self._rollback)

        # Submit
        self._submit_btn = QPushButton("Create Experiment")
        self._submit_btn.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.ACCENT}; color: white; "
            f"border: none; border-radius: 6px; padding: 10px 24px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {Colors.ACCENT_HOVER}; }}"
        )
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

    def _load_inject_defaults(self) -> None:
        try:
            from chaosgen.config.settings import load_settings

            settings = load_settings()
            inj = settings.inject
            if inj.kubeconfig:
                self._kubeconfig.setText(inj.kubeconfig)
            if inj.context:
                self._context.setEditText(inj.context)
            self._dry_run.setChecked(bool(inj.dry_run))
            self._target_ns.setText(inj.default_namespace or "default")
            self._label_key.setText(inj.label_key or "app")
        except Exception:
            pass

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
        return mod

    def _on_test_connection(self) -> None:
        mod = self._kubectl_module()
        if not mod:
            self._conn_status.setText("Status: kubectl-chaos module missing")
            return
        result = mod.execute("test_connection", {})
        if result.get("success"):
            self._conn_status.setText("Status: OK — cluster reachable")
            self._conn_status.setStyleSheet(f"color: {Colors.SUCCESS};")
            self._controller.log_message.emit(result.get("message") or "connection OK")
        else:
            err = result.get("error") or result.get("message") or "failed"
            self._conn_status.setText(f"Status: FAIL — {err}")
            self._conn_status.setStyleSheet(f"color: {Colors.DANGER};")
            self._controller.log_message.emit(f"Test connection failed: {err}")

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
        selected = {i.text() for i in self._service_list.selectedItems()}
        self._service_list.clear()
        workloads = result.get("workloads") or []
        if not result.get("success"):
            self._controller.log_message.emit(
                f"Refresh services failed: {result.get('error') or result.get('message')}"
            )
            self._service_list.addItem("frontend")
            self._service_list.item(0).setSelected(True)
            return
        for name in workloads:
            item = QListWidgetItem(name)
            self._service_list.addItem(item)
            if name in selected:
                item.setSelected(True)
        if self._service_list.count() and not self._service_list.selectedItems():
            self._service_list.item(0).setSelected(True)
        self._controller.log_message.emit(f"Loaded {len(workloads)} workload(s)")

    def _selected_services(self) -> list[str]:
        names = [i.text().strip() for i in self._service_list.selectedItems() if i.text().strip()]
        return names

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
                        loss_percentage=10.0 if ftype == "packet_loss" else None,
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

    def _on_submit(self):
        try:
            services = self._selected_services()
            if not services:
                raise ValueError("Select at least one microservice target")
            max_n = self._max_services_per_suite()
            if len(services) > max_n:
                raise ValueError(
                    f"Selected {len(services)} services exceeds max_services_per_suite={max_n}"
                )
            label_key = self._label_key.text().strip() or "app"
            ns = self._target_ns.text().strip() or "default"
            faults = self._build_faults()
            if not faults:
                raise ValueError("Add at least one fault")

            self._kubectl_module()
            base_name = self._name_input.text().strip() or "suite"
            desc = self._desc_input.text()

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
                    f"[STARTED] {label} → {', '.join(services)} ({len(faults)} fault(s))"
                ),
            )
            self._controller.log_message.emit(
                f"Experiment started: {label} targets={services} faults={len(faults)}"
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
        signal = QLineEdit("SIGKILL")
        self._style_input(signal)
        remove_btn = QPushButton("Remove")
        fl.addRow("Type:", ftype)
        fl.addRow("Duration:", duration)
        fl.addRow("Latency (net):", latency)
        fl.addRow("Signal (kill):", signal)
        fl.addRow("", remove_btn)
        entry = {
            "widget": row,
            "ftype": ftype,
            "duration": duration,
            "latency": latency,
            "signal": signal,
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
        header.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-weight: bold; font-size: {Fonts.SIZE_LARGE}px;"
        )
        card_layout.addWidget(header)
        return card

    def _style_input(self, widget):
        widget.setStyleSheet(
            f"background-color: {Colors.BG_INPUT}; color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 4px; padding: 6px;"
        )

    def _update_params(self, fault_type):
        # Kept for compatibility; multi-fault rows carry their own params.
        return

    def _connect_signals(self):
        self._controller.experiment_finished.connect(self._on_experiment_done)
        self._controller.experiment_started.connect(self._on_experiment_started)
        self._controller.state_changed.connect(self._on_state_changed)

    def _set_lifecycle_phase(self, phase: str) -> None:
        self._lifecycle_phase = phase
        active = (
            f"color: {Colors.TEXT_PRIMARY}; background-color: {Colors.BG_SELECTED}; "
            f"padding: 4px 10px; border: 1px solid {Colors.ACCENT}; border-radius: 4px; "
            f"font-weight: bold;"
        )
        idle = (
            f"color: {Colors.TEXT_MUTED}; padding: 4px 10px; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 4px;"
        )
        for name, lbl in self._phase_labels.items():
            lbl.setStyleSheet(active if name == phase else idle)

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
