"""
Telemetry & AI Advisor — live stack, offline export, anomaly review, scenario approval.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QSpinBox, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QSplitter, QMessageBox, QFileDialog, QRadioButton,
    QButtonGroup, QCheckBox, QComboBox, QTabWidget,
)

from chaosgen.config.telemetry_endpoints import DEFAULT_LOKI_URL, DEFAULT_PROMETHEUS_URL
from chaosgen.gui.analysis_pipeline import AnalysisRequest, AnalysisResult
from chaosgen.gui.theme import Colors, Fonts, Spacing
from chaosgen.schemas.scenarios import AdvisorReport

_DEFAULT_EXPORT = Path(r"H:\Project\microservices-demo-1\local\observability-fetch\exports")


class AdvisorView(QWidget):
    """Wizard: configure source → analyze → review anomalies & scenarios."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._current_report: AdvisorReport | None = None
        self._current_result: AnalysisResult | None = None
        self._init_ui()
        self._connect_signals()
        self._load_defaults()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.XL, Spacing.XL, Spacing.XL, Spacing.XL)
        layout.setSpacing(Spacing.LG)

        title = QLabel("Telemetry & AI Advisor")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Way 1: live Prometheus/Loki on registry-vm  |  "
            "Way 2: offline export bundle  |  optional scenario generation"
        )
        subtitle.setObjectName("sectionSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._step_label = QLabel("Step 1 of 3 — Data source & endpoints")
        self._step_label.setStyleSheet(
            f"color: {Colors.ACCENT}; font-size: {Fonts.SIZE_NORMAL}px; font-weight: bold;"
        )
        layout.addWidget(self._step_label)

        self._stack = QStackedWidget()

        # --- Step 1 ---
        step1 = QWidget()
        s1 = QVBoxLayout(step1)
        s1.setContentsMargins(0, 0, 0, 0)

        source_card = self._make_card("Data Source")
        source_layout = QVBoxLayout()
        source_card.layout().addLayout(source_layout)

        mode_row = QHBoxLayout()
        self._mode_live = QRadioButton("Live stack (registry-vm)")
        self._mode_export = QRadioButton("Offline export")
        self._mode_live.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._mode_live)
        self._mode_group.addButton(self._mode_export)
        mode_row.addWidget(self._mode_live)
        mode_row.addWidget(self._mode_export)
        mode_row.addStretch()
        source_layout.addLayout(mode_row)

        self._source_stack = QStackedWidget()

        live_panel = QWidget()
        live_form = QFormLayout(live_panel)
        self._prom_url = QLineEdit(DEFAULT_PROMETHEUS_URL)
        self._loki_url = QLineEdit(DEFAULT_LOKI_URL)
        self._lookback = QSpinBox()
        self._lookback.setRange(1, 168)
        self._lookback.setValue(24)
        self._lookback.setSuffix(" h")
        for w in (self._prom_url, self._loki_url):
            self._style_input(w)
        live_form.addRow("Prometheus:", self._prom_url)
        live_form.addRow("Loki:", self._loki_url)
        live_form.addRow("Lookback:", self._lookback)

        check_row = QHBoxLayout()
        self._check_btn = QPushButton("Check connection")
        self._check_btn.clicked.connect(self._on_check_connection)
        self._check_status = QLabel("")
        check_row.addWidget(self._check_btn)
        check_row.addWidget(self._check_status)
        check_row.addStretch()
        live_form.addRow("", check_row)
        self._source_stack.addWidget(live_panel)

        export_panel = QWidget()
        export_form = QFormLayout(export_panel)
        export_row = QHBoxLayout()
        self._export_path = QLineEdit(str(_DEFAULT_EXPORT))
        self._style_input(self._export_path)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_export)
        export_row.addWidget(self._export_path)
        export_row.addWidget(browse_btn)
        export_form.addRow("Export folder:", export_row)
        self._source_stack.addWidget(export_panel)

        source_layout.addWidget(self._source_stack)
        self._mode_live.toggled.connect(lambda on: self._source_stack.setCurrentIndex(0 if on else 1))
        s1.addWidget(source_card)

        llm_card = self._make_card("Analysis options")
        llm_form = QFormLayout()
        llm_card.layout().addLayout(llm_form)
        self._generate_cb = QCheckBox("Generate chaos scenarios after anomaly detection")
        self._generate_cb.setChecked(True)
        llm_form.addRow(self._generate_cb)
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["ollama", "openai", "anthropic", "groq"])
        self._model = QLineEdit("llama3.2:3b")
        self._style_input(self._model)
        llm_form.addRow("LLM provider:", self._provider_combo)
        llm_form.addRow("Model:", self._model)
        self._credential_status = QLabel()
        self._credential_status.setWordWrap(True)
        self._credential_status.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: {Fonts.SIZE_SMALL}px;")
        llm_form.addRow("Credentials:", self._credential_status)
        llm_test_row = QHBoxLayout()
        self._test_llm_btn = QPushButton("Test LLM connection")
        self._test_llm_btn.clicked.connect(self._on_test_llm)
        self._llm_test_status = QLabel("")
        self._llm_test_status.setWordWrap(True)
        llm_test_row.addWidget(self._test_llm_btn)
        llm_test_row.addWidget(self._llm_test_status, stretch=1)
        llm_form.addRow("", llm_test_row)
        s1.addWidget(llm_card)

        self._analyze_btn = QPushButton("Run analysis")
        self._analyze_btn.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.ACCENT}; color: white; "
            f"border: none; border-radius: 6px; padding: 10px 24px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {Colors.ACCENT_HOVER}; }}"
        )
        self._analyze_btn.clicked.connect(self._on_analyze)
        s1.addWidget(self._analyze_btn, alignment=Qt.AlignLeft)
        s1.addStretch()
        self._stack.addWidget(step1)

        # --- Step 2 ---
        step2 = QWidget()
        s2 = QVBoxLayout(step2)
        self._progress_label = QLabel("Running telemetry pipeline…")
        self._progress_label.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_LARGE}px;"
        )
        s2.addWidget(self._progress_label)
        s2.addStretch()
        self._stack.addWidget(step2)

        # --- Step 3 ---
        step3 = QWidget()
        s3 = QVBoxLayout(step3)
        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        s3.addWidget(self._summary_label)

        splitter = QSplitter(Qt.Vertical)
        self._tabs = QTabWidget()

        self._anomaly_table = QTableWidget()
        self._anomaly_table.setColumnCount(4)
        self._anomaly_table.setHorizontalHeaderLabels(
            ["Service", "Severity", "Top signals", "Window"]
        )
        self._anomaly_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._anomaly_table.verticalHeader().setVisible(False)
        self._style_table(self._anomaly_table)
        self._tabs.addTab(self._anomaly_table, "Anomalies")

        self._results_table = QTableWidget()
        self._results_table.setColumnCount(4)
        self._results_table.setHorizontalHeaderLabels(["Scenario", "SCI", "Confidence", "Action"])
        self._results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._results_table.verticalHeader().setVisible(False)
        self._style_table(self._results_table)
        self._results_table.currentCellChanged.connect(self._on_scenario_selected)
        self._tabs.addTab(self._results_table, "Scenarios")

        splitter.addWidget(self._tabs)

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
        s3.addWidget(splitter)

        controls = QHBoxLayout()
        self._reject_btn = QPushButton("Reject all scenarios")
        self._reject_btn.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.DANGER}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 20px; }}"
        )
        self._reject_btn.clicked.connect(self._on_reject_all)
        self._back_btn = QPushButton("New analysis")
        self._back_btn.clicked.connect(self._go_step1)
        controls.addStretch()
        controls.addWidget(self._back_btn)
        controls.addWidget(self._reject_btn)
        s3.addLayout(controls)
        self._stack.addWidget(step3)

        layout.addWidget(self._stack)

    def _style_table(self, table: QTableWidget):
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setStyleSheet(
            f"QTableWidget {{ background-color: {Colors.BG_CARD}; border: 1px solid {Colors.BORDER}; }}"
            f"QHeaderView::section {{ background-color: {Colors.BG_SECONDARY}; padding: 6px; }}"
        )

    def _make_card(self, title: str) -> QWidget:
        card = QWidget()
        card.setObjectName("cardWidget")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        header = QLabel(title)
        header.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-weight: bold; font-size: {Fonts.SIZE_LARGE}px;"
        )
        cl.addWidget(header)
        return card

    def _style_input(self, widget):
        widget.setStyleSheet(
            f"background-color: {Colors.BG_INPUT}; color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 4px; padding: 6px;"
        )

    def _connect_signals(self):
        self._controller.advisor_finished.connect(self._on_analysis_finished)
        self._controller.advisor_error.connect(self._on_advisor_error)
        self._controller.telemetry_check_finished.connect(self._on_check_finished)
        self._controller.telemetry_progress.connect(self._on_progress)
        self._anomaly_table.currentCellChanged.connect(self._on_anomaly_selected)
        self._provider_combo.currentTextChanged.connect(self._refresh_credential_status)
        self._controller.llm_check_finished.connect(self._on_llm_test_finished)

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        self._refresh_credential_status()

    def refresh_credentials(self):
        """Called after Settings save so credential status picks up new keys."""
        self._refresh_credential_status()

    def _refresh_credential_status(self):
        from chaosgen.config.secrets import provider_credential_status

        provider = self._provider_combo.currentText()
        ready, message = provider_credential_status(provider)
        color = Colors.SUCCESS if ready else Colors.WARNING
        self._credential_status.setText(message)
        self._credential_status.setStyleSheet(
            f"color: {color}; font-size: {Fonts.SIZE_SMALL}px;"
        )

    def _load_defaults(self):
        try:
            from chaosgen.config.settings import load_settings
            from chaosgen.schemas.discovery import ObservabilityTool

            settings = load_settings()
            for hint in settings.hints.observability:
                if hint.tool == ObservabilityTool.PROMETHEUS:
                    self._prom_url.setText(hint.url)
                elif hint.tool == ObservabilityTool.LOKI:
                    self._loki_url.setText(hint.url)
            if settings.llm_provider:
                idx = self._provider_combo.findText(settings.llm_provider)
                if idx >= 0:
                    self._provider_combo.setCurrentIndex(idx)
            if settings.llm_model:
                self._model.setText(settings.llm_model)
        except Exception:
            pass
        self._refresh_credential_status()

    def _on_browse_export(self):
        path = QFileDialog.getExistingDirectory(self, "Select export bundle or exports folder")
        if path:
            self._export_path.setText(path)

    def _on_check_connection(self):
        self._check_status.setText("Checking…")
        self._check_btn.setEnabled(False)
        self._controller.check_telemetry_async(
            self._prom_url.text().strip(),
            self._loki_url.text().strip(),
        )

    @Slot(dict)
    def _on_check_finished(self, health: dict):
        self._check_btn.setEnabled(True)
        parts = [f"{name}: {msg}" for name, msg in health.items()]
        self._check_status.setText("  |  ".join(parts))
        all_ok = all(not str(msg).startswith("FAIL") for msg in health.values())
        color = Colors.SUCCESS if all_ok else Colors.WARNING
        self._check_status.setStyleSheet(f"color: {color};")

    def _on_test_llm(self):
        self._test_llm_btn.setEnabled(False)
        self._llm_test_status.setText("Testing…")
        self._llm_test_status.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        self._controller.test_llm_async(
            self._provider_combo.currentText(),
            self._model.text().strip() or None,
        )

    @Slot(bool, str)
    def _on_llm_test_finished(self, ok: bool, message: str):
        self._test_llm_btn.setEnabled(True)
        self._llm_test_status.setText(message)
        color = Colors.SUCCESS if ok else Colors.DANGER
        self._llm_test_status.setStyleSheet(f"color: {color};")
        if ok:
            self._refresh_credential_status()

    @Slot(str)
    def _on_progress(self, message: str):
        self._progress_label.setText(message)

    def _on_analyze(self):
        if self._generate_cb.isChecked():
            from chaosgen.config.secrets import provider_credential_status

            provider = self._provider_combo.currentText()
            ready, message = provider_credential_status(provider)
            if not ready:
                QMessageBox.warning(
                    self,
                    "LLM credentials required",
                    message + "\n\nScenario generation will fall back to Ollama if the key stays missing.",
                )

        source = "live" if self._mode_live.isChecked() else "export"
        request = AnalysisRequest(
            source=source,
            prom_url=self._prom_url.text().strip(),
            loki_url=self._loki_url.text().strip(),
            lookback_hours=self._lookback.value(),
            export_path=self._export_path.text().strip() or None,
            generate_scenarios=self._generate_cb.isChecked(),
            llm_provider=self._provider_combo.currentText(),
            llm_model=self._model.text().strip() or None,
        )

        self._analyze_btn.setEnabled(False)
        self._check_btn.setEnabled(False)
        self._stack.setCurrentIndex(1)
        self._step_label.setText("Step 2 of 3 — Analyzing telemetry")
        self._controller.run_telemetry_analysis_async(request)

    @Slot(object)
    def _on_analysis_finished(self, payload):
        result = payload if isinstance(payload, AnalysisResult) else None
        report = result.report if result else payload

        self._current_result = result
        self._current_report = report
        self._analyze_btn.setEnabled(True)
        self._check_btn.setEnabled(True)
        self._stack.setCurrentIndex(2)
        self._step_label.setText("Step 3 of 3 — Review results")

        if result:
            self._summary_label.setText(
                f"Source: {result.source_label}  |  "
                f"Series: {result.metric_series}  |  "
                f"Samples: {result.total_samples}  |  "
                f"Log streams: {result.log_streams}  |  "
                f"Feature windows: {result.feature_rows}  |  "
                f"Anomalies: {len(result.clusters)}  |  "
                f"Scenarios: {len(report.generated_experiments)}"
            )
            self._fill_anomaly_table(result.summaries)
            if result.metric_series == 0 or result.feature_rows == 0:
                self._detail_text.setPlainText(self._empty_metrics_help(result))
        else:
            self._summary_label.setText(
                f"Anomalies: {report.anomalies_found}  |  "
                f"Scenarios: {len(report.generated_experiments)}"
            )

        self._fill_scenario_table(report)

    @staticmethod
    def _empty_metrics_help(result: AnalysisResult) -> str:
        lines = [
            "No metric features could be extracted from telemetry.",
            "",
            f"Collected: {result.metric_series} series, {result.total_samples} samples, "
            f"{result.log_streams} log streams.",
            "",
            "This is usually NOT an LLM issue. Common causes:",
            "• Prometheus reachable (/healthy) but queries return empty — wrong metrics/labels",
            "• 401/403 on Prometheus API — set Prometheus Token in Settings → Observability Auth",
            "• Golden-signal PromQL does not match your stack (http_requests_total labels differ)",
            "• Loki missing only affects log features; metrics should still work without Loki",
            "",
            "Try: Check connection (Step 1) and confirm prometheus shows OK with series count.",
            "Or switch to Offline export mode with a recent export bundle.",
        ]
        return "\n".join(lines)

    def _fill_anomaly_table(self, summaries):
        self._anomaly_table.setRowCount(len(summaries))
        for i, s in enumerate(summaries):
            feats = ", ".join(f"{n} ({v:.2f})" for n, v in s.top_features[:3])
            self._anomaly_table.setItem(i, 0, QTableWidgetItem(s.service_name))
            self._anomaly_table.setItem(i, 1, QTableWidgetItem(f"{s.severity:.2f}"))
            self._anomaly_table.setItem(i, 2, QTableWidgetItem(feats))
            self._anomaly_table.setItem(i, 3, QTableWidgetItem(s.time_window))

    def _fill_scenario_table(self, report: AdvisorReport):
        exps = report.generated_experiments
        self._results_table.setRowCount(len(exps))
        for i, exp in enumerate(exps):
            self._results_table.setItem(i, 0, QTableWidgetItem(exp.name))
            sci = report.sci_scores[i] if i < len(report.sci_scores) else None
            self._results_table.setItem(i, 1, QTableWidgetItem(
                f"{sci.weighted_score:.3f}" if sci else "—"
            ))
            hyp = report.hypotheses[i] if i < len(report.hypotheses) else None
            self._results_table.setItem(i, 2, QTableWidgetItem(
                f"{hyp.confidence:.2f}" if hyp else "—"
            ))
            approve_btn = QPushButton("Approve")
            approve_btn.setStyleSheet(
                f"QPushButton {{ background-color: {Colors.SUCCESS}; color: white; "
                f"border: none; border-radius: 4px; padding: 4px 12px; }}"
            )
            approve_btn.clicked.connect(lambda _, idx=i: self._on_approve(idx))
            self._results_table.setCellWidget(i, 3, approve_btn)

        if not exps:
            self._tabs.setTabEnabled(1, False)
        else:
            self._tabs.setTabEnabled(1, True)

    @Slot(str)
    def _on_advisor_error(self, error_msg: str):
        self._analyze_btn.setEnabled(True)
        self._check_btn.setEnabled(True)
        self._go_step1()
        QMessageBox.critical(self, "Analysis error", error_msg)

    def _on_anomaly_selected(self, row, _col, _prev_row, _prev_col):
        if not self._current_result or row < 0:
            return
        summaries = self._current_result.summaries
        if row >= len(summaries):
            return
        s = summaries[row]
        lines = [
            f"Service: {s.service_name}",
            f"Severity: {s.severity:.2f}",
            f"Window: {s.time_window}",
            f"Cluster ID: {s.source_cluster_id}",
            "",
            "Top deviating metrics:",
        ]
        for name, score in s.top_features:
            lines.append(f"  - {name}: z={score:.3f}")
        if s.error_pattern:
            lines.append(f"\nError pattern: {s.error_pattern}")
        self._detail_text.setPlainText("\n".join(lines))

    def _on_scenario_selected(self, row, _col, _prev_row, _prev_col):
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
            exp.description or "",
            f"Target: {exp.target.name} ({exp.target.type.value})",
        ]
        if hyp:
            lines += ["", "Hypothesis:", hyp.rationale, f"Confidence: {hyp.confidence:.2f}"]
        if sci:
            lines.append(f"SCI: {sci.weighted_score:.4f}")
        self._detail_text.setPlainText("\n".join(lines))

    def _on_approve(self, index: int):
        self._controller.approve_and_run(index)
        self._controller.log_message.emit(f"Approved scenario index={index}")

    def _on_reject_all(self):
        self._controller.reject_all()
        self._go_step1()

    def _go_step1(self):
        self._stack.setCurrentIndex(0)
        self._step_label.setText("Step 1 of 3 — Data source & endpoints")
        self._anomaly_table.setRowCount(0)
        self._results_table.setRowCount(0)
        self._detail_text.clear()
        self._current_report = None
        self._current_result = None
        self._tabs.setTabEnabled(1, True)
