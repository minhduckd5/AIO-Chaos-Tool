"""
Experiments view -- experiment list + creation form.
Phase 5 placeholder with functional form wired through AppController.
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


class ExperimentsView(QWidget):
    """Split view: experiment history list (left) + creation form (right)."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.XL, Spacing.XL, Spacing.XL, Spacing.XL)
        layout.setSpacing(Spacing.LG)

        title = QLabel("Experiments")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

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

        # Card: Target
        target_card = self._make_card("Target Configuration")
        target_fl = QFormLayout()
        self._target_type = QComboBox()
        for t in TargetType:
            self._target_type.addItem(t.value)
        self._target_name = QLineEdit("frontend")
        self._target_ns = QLineEdit("default")
        self._style_input(self._target_name)
        self._style_input(self._target_ns)
        target_fl.addRow("Type:", self._target_type)
        target_fl.addRow("Name / Label:", self._target_name)
        target_fl.addRow("Namespace:", self._target_ns)
        target_card.layout().addLayout(target_fl)
        form_layout.addWidget(target_card)

        # Card: Fault
        fault_card = self._make_card("Fault Injection")
        fault_fl = QFormLayout()
        self._fault_type = QComboBox()
        for f in FaultType:
            self._fault_type.addItem(f.value)
        self._duration = QLineEdit("30s")
        self._style_input(self._duration)
        fault_fl.addRow("Fault Type:", self._fault_type)
        fault_fl.addRow("Duration:", self._duration)

        # Stacked params
        self._params_stack = QStackedWidget()

        # Network page
        net_page = QWidget()
        net_fl = QFormLayout(net_page)
        self._latency = QLineEdit("100ms")
        self._jitter = QLineEdit("10ms")
        self._style_input(self._latency)
        self._style_input(self._jitter)
        net_fl.addRow("Latency:", self._latency)
        net_fl.addRow("Jitter:", self._jitter)
        self._params_stack.addWidget(net_page)

        # Process page
        proc_page = QWidget()
        proc_fl = QFormLayout(proc_page)
        self._signal = QLineEdit("SIGKILL")
        self._style_input(self._signal)
        proc_fl.addRow("Signal:", self._signal)
        self._params_stack.addWidget(proc_page)

        # Resource page
        res_page = QWidget()
        res_fl = QFormLayout(res_page)
        self._cpu = QSpinBox()
        self._cpu.setRange(0, 100)
        self._cpu.setValue(80)
        res_fl.addRow("CPU %:", self._cpu)
        self._params_stack.addWidget(res_page)

        # Empty page
        self._params_stack.addWidget(QWidget())

        fault_fl.addRow("Parameters:", self._params_stack)
        fault_card.layout().addLayout(fault_fl)
        form_layout.addWidget(fault_card)

        self._fault_type.currentTextChanged.connect(self._update_params)

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
        if fault_type in ("network_latency", "packet_loss"):
            self._params_stack.setCurrentIndex(0)
        elif fault_type == "process_kill":
            self._params_stack.setCurrentIndex(1)
        elif fault_type == "resource_exhaustion":
            self._params_stack.setCurrentIndex(2)
        else:
            self._params_stack.setCurrentIndex(3)

    def _on_submit(self):
        try:
            target = TargetSpec(
                type=TargetType(self._target_type.currentText()),
                name=self._target_name.text(),
                namespace=self._target_ns.text() or None,
            )
            ftype = self._fault_type.currentText()
            common = {"fault_type": FaultType(ftype), "duration": self._duration.text()}

            if ftype in ("network_latency", "packet_loss"):
                fault = NetworkFaultSpec(**common, latency=self._latency.text(), jitter=self._jitter.text())
            elif ftype == "process_kill":
                fault = ProcessFaultSpec(**common, signal=self._signal.text())
            elif ftype == "resource_exhaustion":
                fault = ResourceFaultSpec(**common, cpu_percent=self._cpu.value())
            else:
                fault = FaultSpec(**common)

            experiment = ChaosExperiment(
                name=self._name_input.text(),
                description=self._desc_input.text(),
                target=target,
                faults=[fault],
                rollback=self._rollback.isChecked(),
                steady_state_check={"http_health": "http://localhost:8080/health"},
            )

            self._controller.run_experiment_async(experiment)
            self._history_list.insertItem(0, QListWidgetItem(f"[STARTED] {experiment.name}"))
            self._controller.log_message.emit(f"Experiment created: {experiment.name}")

        except Exception as e:
            QMessageBox.critical(self, "Validation Error", str(e))

    def _connect_signals(self):
        self._controller.experiment_finished.connect(self._on_experiment_done)

    @Slot(bool, str)
    def _on_experiment_done(self, success, msg):
        status = "PASSED" if success else "FAILED"
        self._history_list.insertItem(0, QListWidgetItem(f"[{status}] {msg}"))
