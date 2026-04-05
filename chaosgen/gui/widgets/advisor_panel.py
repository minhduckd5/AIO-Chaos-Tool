from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QGroupBox, QLabel, QListWidget, QListWidgetItem,
    QTextEdit, QMessageBox, QSpinBox,
)
from PySide6.QtCore import Signal, Qt, QThread

from chaosgen.schemas.scenarios import AdvisorReport


class AdvisorWorker(QThread):
    """Background thread for running the ChaosAdvisor pipeline."""
    finished = Signal(object)
    error = Signal(str)
    status = Signal(str)

    def __init__(self, advisor, lookback_hours: int):
        super().__init__()
        self.advisor = advisor
        self.lookback_hours = lookback_hours

    def run(self):
        try:
            self.status.emit("Collecting telemetry...")
            report = self.advisor.analyze_and_recommend(
                lookback_hours=self.lookback_hours
            )
            self.finished.emit(report)
        except Exception as e:
            self.error.emit(str(e))


class AdvisorPanel(QWidget):
    """
    AI Advisor tab for the GUI.
    Provides telemetry connection config, analysis trigger,
    scenario review with SCI scores, and approval workflow.
    """
    experiment_approved = Signal(int)
    all_rejected = Signal()

    def __init__(self):
        super().__init__()
        self._current_report: AdvisorReport | None = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # --- Connection Config ---
        conn_group = QGroupBox("Telemetry Connection")
        conn_layout = QFormLayout()
        self.prom_url_input = QLineEdit("http://localhost:9090")
        self.loki_url_input = QLineEdit("http://localhost:3100")
        self.ollama_url_input = QLineEdit("http://localhost:11434")
        self.model_input = QLineEdit("llama3")
        self.lookback_input = QSpinBox()
        self.lookback_input.setRange(1, 720)
        self.lookback_input.setValue(24)
        self.lookback_input.setSuffix(" hours")
        conn_layout.addRow("Prometheus URL:", self.prom_url_input)
        conn_layout.addRow("Loki URL:", self.loki_url_input)
        conn_layout.addRow("Ollama URL:", self.ollama_url_input)
        conn_layout.addRow("Model:", self.model_input)
        conn_layout.addRow("Lookback:", self.lookback_input)
        conn_group.setLayout(conn_layout)
        layout.addWidget(conn_group)

        # --- Analysis Controls ---
        controls = QHBoxLayout()
        self.analyze_btn = QPushButton("Analyze & Recommend")
        self.analyze_btn.setObjectName("analyzeButton")
        self.analyze_btn.clicked.connect(self._on_analyze_clicked)
        controls.addWidget(self.analyze_btn)

        self.export_btn = QPushButton("Export All YAML")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export_clicked)
        controls.addWidget(self.export_btn)
        layout.addLayout(controls)

        # --- Results Summary ---
        self.summary_label = QLabel("No analysis performed yet.")
        self.summary_label.setObjectName("headerLabel")
        layout.addWidget(self.summary_label)

        # --- Scenario List ---
        scenarios_group = QGroupBox("Recommended Scenarios")
        scenarios_layout = QVBoxLayout()
        self.scenario_list = QListWidget()
        self.scenario_list.currentRowChanged.connect(self._on_scenario_selected)
        scenarios_layout.addWidget(self.scenario_list)

        approval_controls = QHBoxLayout()
        self.approve_btn = QPushButton("Approve Selected")
        self.approve_btn.setEnabled(False)
        self.approve_btn.clicked.connect(self._on_approve_clicked)
        approval_controls.addWidget(self.approve_btn)

        self.reject_all_btn = QPushButton("Reject All")
        self.reject_all_btn.setObjectName("stopButton")
        self.reject_all_btn.setEnabled(False)
        self.reject_all_btn.clicked.connect(self._on_reject_all_clicked)
        approval_controls.addWidget(self.reject_all_btn)

        scenarios_layout.addLayout(approval_controls)
        scenarios_group.setLayout(scenarios_layout)
        layout.addWidget(scenarios_group)

        # --- Detail View ---
        detail_group = QGroupBox("Scenario Detail")
        detail_layout = QVBoxLayout()
        self.detail_view = QTextEdit()
        self.detail_view.setReadOnly(True)
        detail_layout.addWidget(self.detail_view)
        detail_group.setLayout(detail_layout)
        layout.addWidget(detail_group)

        self.setLayout(layout)

    def _on_analyze_clicked(self):
        """Emit signal -- actual advisor creation is handled by MainWindow."""
        self.analyze_btn.setEnabled(False)
        self.analyze_btn.setText("Analyzing...")
        self.summary_label.setText("Running AI analysis pipeline...")

    def display_report(self, report: AdvisorReport):
        """Populate the UI with results from an AdvisorReport."""
        self._current_report = report
        self.analyze_btn.setEnabled(True)
        self.analyze_btn.setText("Analyze & Recommend")

        self.summary_label.setText(
            f"Found {report.anomalies_found} anomalies | "
            f"{len(report.hypotheses)} hypotheses | "
            f"{len(report.generated_experiments)} experiments | "
            f"{report.dropped_hypotheses} dropped (low confidence)"
        )

        self.scenario_list.clear()
        for i, exp in enumerate(report.generated_experiments):
            sci = report.sci_scores[i] if i < len(report.sci_scores) else None
            sci_str = f" [SCI={sci.weighted_score:.3f}]" if sci else ""
            item_text = f"{exp.name}{sci_str}"
            item = QListWidgetItem(item_text)
            self.scenario_list.addItem(item)

        has_experiments = len(report.generated_experiments) > 0
        self.approve_btn.setEnabled(has_experiments)
        self.reject_all_btn.setEnabled(has_experiments)
        self.export_btn.setEnabled(len(report.manifest_paths) > 0)

    def display_error(self, error_msg: str):
        """Handle pipeline errors."""
        self.analyze_btn.setEnabled(True)
        self.analyze_btn.setText("Analyze & Recommend")
        self.summary_label.setText(f"Error: {error_msg}")
        QMessageBox.critical(self, "Advisor Error", error_msg)

    def _on_scenario_selected(self, index: int):
        """Show detail for the selected scenario."""
        if not self._current_report or index < 0:
            return

        experiments = self._current_report.generated_experiments
        if index >= len(experiments):
            return

        exp = experiments[index]
        hyp = None
        if index < len(self._current_report.hypotheses):
            hyp = self._current_report.hypotheses[index]
        sci = None
        if index < len(self._current_report.sci_scores):
            sci = self._current_report.sci_scores[index]

        lines = [
            f"=== {exp.name} ===",
            f"Description: {exp.description}",
            f"Target: {exp.target.name} ({exp.target.type.value}) @ {exp.target.namespace}",
            f"Faults: {len(exp.faults)}",
        ]
        for f in exp.faults:
            lines.append(f"  - {f.fault_type.value} (duration={f.duration})")
        lines.append(f"Rollback: {exp.rollback}")

        if hyp:
            lines.extend([
                "",
                "--- LLM Hypothesis ---",
                f"Confidence: {hyp.confidence:.2f}",
                f"Rationale: {hyp.rationale}",
                f"Suggested params: {hyp.suggested_parameters}",
            ])

        if sci:
            lines.extend([
                "",
                "--- Complexity Index ---",
                f"Fault cardinality: {sci.fault_cardinality}",
                f"Target diversity: {sci.target_diversity}",
                f"Temporal stages: {sci.temporal_stages}",
                f"Blast radius: {sci.blast_radius_percent:.1f}%",
                f"Causal chain depth: {sci.causal_chain_depth}",
                f"Weighted SCI: {sci.weighted_score:.4f}",
            ])

        self.detail_view.setPlainText("\n".join(lines))

    def _on_approve_clicked(self):
        index = self.scenario_list.currentRow()
        if index >= 0:
            self.experiment_approved.emit(index)

    def _on_reject_all_clicked(self):
        self.all_rejected.emit()
        self.scenario_list.clear()
        self.detail_view.clear()
        self.approve_btn.setEnabled(False)
        self.reject_all_btn.setEnabled(False)
        self.summary_label.setText("All scenarios rejected.")

    def _on_export_clicked(self):
        if self._current_report and self._current_report.manifest_paths:
            paths = "\n".join(self._current_report.manifest_paths)
            QMessageBox.information(
                self, "Manifests Exported",
                f"YAML manifests written to:\n\n{paths}"
            )
