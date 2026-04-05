"""
AI Advisor view -- wizard-step layout for telemetry analysis and scenario approval.
Phase 6 placeholder with full functional wiring.
"""

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QSpinBox, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QSplitter, QScrollArea, QFrame, QMessageBox, QSizePolicy,
)

from chaosgen.gui.theme import Colors, Fonts, Spacing
from chaosgen.schemas.scenarios import AdvisorReport


class AdvisorView(QWidget):
    """Wizard-style AI Advisor panel with step progression."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._current_report = None
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.XL, Spacing.XL, Spacing.XL, Spacing.XL)
        layout.setSpacing(Spacing.LG)

        title = QLabel("AI Advisor")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel("ML-driven anomaly detection and chaos scenario generation")
        subtitle.setObjectName("sectionSubtitle")
        layout.addWidget(subtitle)

        # Step indicator
        self._step_label = QLabel("Step 1 of 3 — Configure Telemetry Sources")
        self._step_label.setStyleSheet(
            f"color: {Colors.ACCENT}; font-size: {Fonts.SIZE_NORMAL}px; font-weight: bold;"
        )
        layout.addWidget(self._step_label)

        self._stack = QStackedWidget()

        # --- Step 1: Connection Config ---
        step1 = QWidget()
        s1_layout = QVBoxLayout(step1)
        s1_layout.setContentsMargins(0, 0, 0, 0)

        config_card = self._make_card("Telemetry Connection")
        config_fl = QFormLayout()
        self._prom_url = QLineEdit("http://localhost:9090")
        self._loki_url = QLineEdit("http://localhost:3100")
        self._ollama_url = QLineEdit("http://localhost:11434")
        self._model = QLineEdit("llama3")
        self._lookback = QSpinBox()
        self._lookback.setRange(1, 720)
        self._lookback.setValue(24)
        self._lookback.setSuffix(" hours")

        for w in (self._prom_url, self._loki_url, self._ollama_url, self._model):
            self._style_input(w)

        config_fl.addRow("Prometheus URL:", self._prom_url)
        config_fl.addRow("Loki URL:", self._loki_url)
        config_fl.addRow("Ollama URL:", self._ollama_url)
        config_fl.addRow("Model:", self._model)
        config_fl.addRow("Lookback:", self._lookback)
        config_card.layout().addLayout(config_fl)
        s1_layout.addWidget(config_card)

        self._analyze_btn = QPushButton("Analyze & Recommend")
        self._analyze_btn.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.ACCENT}; color: white; "
            f"border: none; border-radius: 6px; padding: 10px 24px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {Colors.ACCENT_HOVER}; }}"
        )
        self._analyze_btn.clicked.connect(self._on_analyze)
        s1_layout.addWidget(self._analyze_btn, alignment=Qt.AlignLeft)
        s1_layout.addStretch()
        self._stack.addWidget(step1)

        # --- Step 2: Progress ---
        step2 = QWidget()
        s2_layout = QVBoxLayout(step2)
        s2_layout.setContentsMargins(0, 0, 0, 0)
        self._progress_label = QLabel("Running analysis pipeline...")
        self._progress_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_LARGE}px;")
        s2_layout.addWidget(self._progress_label)
        s2_layout.addStretch()
        self._stack.addWidget(step2)

        # --- Step 3: Results ---
        step3 = QWidget()
        s3_layout = QVBoxLayout(step3)
        s3_layout.setContentsMargins(0, 0, 0, 0)

        self._summary_label = QLabel()
        self._summary_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_NORMAL}px;")
        s3_layout.addWidget(self._summary_label)

        splitter = QSplitter(Qt.Vertical)

        # Results table
        self._results_table = QTableWidget()
        self._results_table.setColumnCount(4)
        self._results_table.setHorizontalHeaderLabels(["Scenario", "SCI Score", "Confidence", "Action"])
        self._results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._results_table.verticalHeader().setVisible(False)
        self._results_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._results_table.setStyleSheet(
            f"QTableWidget {{ background-color: {Colors.BG_CARD}; border: 1px solid {Colors.BORDER}; "
            f"gridline-color: {Colors.BORDER}; }}"
            f"QTableWidget::item {{ padding: 6px; }}"
            f"QTableWidget::item:selected {{ background-color: {Colors.BG_SELECTED}; }}"
            f"QHeaderView::section {{ background-color: {Colors.BG_SECONDARY}; "
            f"color: {Colors.TEXT_SECONDARY}; border: none; "
            f"border-bottom: 1px solid {Colors.BORDER}; padding: 6px; font-weight: bold; }}"
        )
        self._results_table.currentCellChanged.connect(self._on_scenario_selected)
        splitter.addWidget(self._results_table)

        # Detail view
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setStyleSheet(
            f"background-color: {Colors.BG_INPUT}; color: {Colors.TEXT_PRIMARY}; "
            f"font-family: {Fonts.FAMILY_MONO}; font-size: {Fonts.SIZE_SMALL}px; "
            f"border: 1px solid {Colors.BORDER}; padding: {Spacing.SM}px;"
        )
        splitter.addWidget(self._detail_text)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        s3_layout.addWidget(splitter)

        # Reject all button
        controls = QHBoxLayout()
        self._reject_btn = QPushButton("Reject All")
        self._reject_btn.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.DANGER}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 20px; font-weight: bold; }}"
        )
        self._reject_btn.clicked.connect(self._on_reject_all)
        controls.addStretch()

        self._back_btn = QPushButton("New Analysis")
        self._back_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Colors.ACCENT}; "
            f"border: 1px solid {Colors.ACCENT}; border-radius: 6px; padding: 8px 20px; }}"
        )
        self._back_btn.clicked.connect(self._go_step1)
        controls.addWidget(self._back_btn)
        controls.addWidget(self._reject_btn)
        s3_layout.addLayout(controls)
        self._stack.addWidget(step3)

        layout.addWidget(self._stack)

    def _make_card(self, title: str) -> QWidget:
        card = QWidget()
        card.setObjectName("cardWidget")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        cl.setSpacing(Spacing.SM)
        header = QLabel(title)
        header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-weight: bold; font-size: {Fonts.SIZE_LARGE}px;")
        cl.addWidget(header)
        return card

    def _style_input(self, widget):
        widget.setStyleSheet(
            f"background-color: {Colors.BG_INPUT}; color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 4px; padding: 6px;"
        )

    def _connect_signals(self):
        self._controller.advisor_finished.connect(self._on_advisor_finished)
        self._controller.advisor_error.connect(self._on_advisor_error)

    def _on_analyze(self):
        from chaosgen.ingestion.prometheus_client import PrometheusClient
        from chaosgen.ingestion.loki_client import LokiClient
        from chaosgen.ingestion.collector import TelemetryCollector
        from chaosgen.advisor import ChaosAdvisor

        prom = PrometheusClient(self._prom_url.text())
        loki = LokiClient(self._loki_url.text())
        collector = TelemetryCollector(prom, loki)

        advisor = ChaosAdvisor(
            collector=collector,
            model_name=self._model.text(),
            ollama_url=self._ollama_url.text(),
        )

        self._analyze_btn.setEnabled(False)
        self._stack.setCurrentIndex(1)
        self._step_label.setText("Step 2 of 3 — Analyzing...")

        self._controller.run_advisor_async(advisor, self._lookback.value())

    @Slot(object)
    def _on_advisor_finished(self, report: AdvisorReport):
        self._current_report = report
        self._stack.setCurrentIndex(2)
        self._step_label.setText("Step 3 of 3 — Review Scenarios")
        self._analyze_btn.setEnabled(True)

        self._summary_label.setText(
            f"Found {report.anomalies_found} anomalies  |  "
            f"{len(report.hypotheses)} hypotheses  |  "
            f"{len(report.generated_experiments)} experiments  |  "
            f"{report.dropped_hypotheses} dropped"
        )

        self._results_table.setRowCount(len(report.generated_experiments))
        for i, exp in enumerate(report.generated_experiments):
            self._results_table.setItem(i, 0, QTableWidgetItem(exp.name))
            sci = report.sci_scores[i] if i < len(report.sci_scores) else None
            self._results_table.setItem(i, 1, QTableWidgetItem(f"{sci.weighted_score:.3f}" if sci else "—"))
            hyp = report.hypotheses[i] if i < len(report.hypotheses) else None
            self._results_table.setItem(i, 2, QTableWidgetItem(f"{hyp.confidence:.2f}" if hyp else "—"))

            approve_btn = QPushButton("Approve")
            approve_btn.setStyleSheet(
                f"QPushButton {{ background-color: {Colors.SUCCESS}; color: white; "
                f"border: none; border-radius: 4px; padding: 4px 12px; font-size: 11px; }}"
            )
            approve_btn.clicked.connect(lambda _, idx=i: self._on_approve(idx))
            self._results_table.setCellWidget(i, 3, approve_btn)

    @Slot(str)
    def _on_advisor_error(self, error_msg):
        self._analyze_btn.setEnabled(True)
        self._go_step1()
        QMessageBox.critical(self, "Advisor Error", error_msg)

    def _on_scenario_selected(self, row, col, prev_row, prev_col):
        if not self._current_report or row < 0:
            return
        experiments = self._current_report.generated_experiments
        if row >= len(experiments):
            return

        exp = experiments[row]
        hyp = self._current_report.hypotheses[row] if row < len(self._current_report.hypotheses) else None
        sci = self._current_report.sci_scores[row] if row < len(self._current_report.sci_scores) else None

        lines = [
            f"=== {exp.name} ===",
            f"Description: {exp.description}",
            f"Target: {exp.target.name} ({exp.target.type.value}) @ {exp.target.namespace}",
            f"Faults: {len(exp.faults)}",
        ]
        for f in exp.faults:
            lines.append(f"  - {f.fault_type.value} (duration={f.duration})")
        if hyp:
            lines += ["", "--- LLM Hypothesis ---", f"Confidence: {hyp.confidence:.2f}", f"Rationale: {hyp.rationale}"]
        if sci:
            lines += ["", "--- Complexity Index ---", f"Weighted SCI: {sci.weighted_score:.4f}",
                       f"Blast radius: {sci.blast_radius_percent:.1f}%"]
        self._detail_text.setPlainText("\n".join(lines))

    def _on_approve(self, index):
        self._controller.approve_and_run(index)
        self._controller.log_message.emit(f"Approved scenario index={index}")

    def _on_reject_all(self):
        self._controller.reject_all()
        self._go_step1()

    def _go_step1(self):
        self._stack.setCurrentIndex(0)
        self._step_label.setText("Step 1 of 3 — Configure Telemetry Sources")
        self._results_table.setRowCount(0)
        self._detail_text.clear()
        self._current_report = None
