"""
Telemetry & AI Advisor — live stack, offline export, anomaly review, scenario approval.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QShowEvent, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QSpinBox, QDoubleSpinBox, QSlider, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QSplitter, QMessageBox, QFileDialog, QRadioButton,
    QButtonGroup, QCheckBox, QComboBox, QTabWidget,
    QDialog, QDialogButtonBox, QPlainTextEdit, QScrollArea,
)

from chaosgen.config.telemetry_endpoints import DEFAULT_LOKI_URL, DEFAULT_PROMETHEUS_URL
from chaosgen.gui.advisor_presenter import (
    description_rows,
    gatekeeper_rows,
    gatekeeper_summary,
    promote_blocked_reason,
)
from chaosgen.gui.analysis_pipeline import AnalysisRequest, AnalysisResult
from chaosgen.gui.widgets.interactive_timeline import InteractiveTimelineWidget
from chaosgen.gui.theme import Colors, Fonts, Spacing
from chaosgen.schemas.scenarios import (
    AdvisorReport,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)

_VERDICT_COLORS = {
    "REAL": Colors.SUCCESS,
    "CHRONIC": Colors.ACCENT,
    "TRANSIENT": Colors.WARNING,
    "NOISE": Colors.TEXT_MUTED,
}

_EMPTY_SCENARIOS_MSG = (
    "No scenarios generated — see Gatekeeper / Descriptions "
    "(fallback incidents are not fed to chaos generation)."
)

_DEFAULT_EXPORT = Path(r"H:\Project\microservices-demo-1\local\observability-fetch\exports")


class AdvisorView(QWidget):
    """Wizard: configure source → analyze → review anomalies & scenarios."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._current_report: AdvisorReport | None = None
        self._current_result: AnalysisResult | None = None
        self._current_descriptions: list[UnknownScenarioDescription] = []
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

        from PySide6.QtCore import QDateTime
        from PySide6.QtWidgets import QDateTimeEdit

        live_panel = QWidget()
        live_form = QFormLayout(live_panel)
        self._prom_url = QLineEdit(DEFAULT_PROMETHEUS_URL)
        self._loki_url = QLineEdit(DEFAULT_LOKI_URL)

        time_mode_row = QHBoxLayout()
        self._time_relative = QRadioButton("Relative (last N hours)")
        self._time_absolute = QRadioButton("Absolute range (UTC)")
        self._time_relative.setChecked(True)
        self._time_group = QButtonGroup(self)
        self._time_group.addButton(self._time_relative)
        self._time_group.addButton(self._time_absolute)
        time_mode_row.addWidget(self._time_relative)
        time_mode_row.addWidget(self._time_absolute)
        time_mode_row.addStretch()

        self._lookback = QSpinBox()
        self._lookback.setRange(1, 168)
        self._lookback.setValue(24)
        self._lookback.setSuffix(" h")

        now_dt = QDateTime.currentDateTime()
        self._start_dt_edit = QDateTimeEdit(now_dt.addDays(-1))
        self._start_dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._end_dt_edit = QDateTimeEdit(now_dt)
        self._end_dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        self._relative_widget = QWidget()
        rel_l = QHBoxLayout(self._relative_widget)
        rel_l.setContentsMargins(0, 0, 0, 0)
        rel_l.addWidget(self._lookback)
        rel_l.addStretch()

        self._absolute_widget = QWidget()
        abs_l = QFormLayout(self._absolute_widget)
        abs_l.setContentsMargins(0, 0, 0, 0)
        abs_l.addRow("Start:", self._start_dt_edit)
        abs_l.addRow("End:", self._end_dt_edit)
        self._absolute_widget.setVisible(False)

        for w in (self._prom_url, self._loki_url):
            self._style_input(w)
        live_form.addRow("Prometheus:", self._prom_url)
        live_form.addRow("Loki:", self._loki_url)
        live_form.addRow("Time Mode:", time_mode_row)
        live_form.addRow("Lookback:", self._relative_widget)
        live_form.addRow("Date Range:", self._absolute_widget)

        self._time_relative.toggled.connect(lambda rel: self._relative_widget.setVisible(rel))
        self._time_relative.toggled.connect(lambda rel: self._absolute_widget.setVisible(not rel))

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
        self._skip_gatekeeper_cb = QCheckBox("Skip gatekeeper (debug — bypass noise filter)")
        self._skip_gatekeeper_cb.setChecked(False)
        llm_form.addRow(self._skip_gatekeeper_cb)
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["ollama", "openai", "anthropic", "groq"])
        self._model = QLineEdit("llama3.2:3b")
        self._style_input(self._model)
        llm_form.addRow("LLM provider:", self._provider_combo)
        llm_form.addRow("Model:", self._model)
        self._ml_model_combo = QComboBox()
        self._style_input(self._ml_model_combo)
        llm_form.addRow("ML Model (optional):", self._ml_model_combo)

        # Collapsible Developer/Advanced Options
        self._dev_options_toggle = QCheckBox("Show Advanced/Developer Options")
        self._dev_options_toggle.setChecked(False)
        self._dev_options_toggle.setVisible(False)
        llm_form.addRow(self._dev_options_toggle)

        self._dev_options_container = QWidget()
        self._dev_options_container.setVisible(False)
        dev_l = QFormLayout(self._dev_options_container)
        dev_l.setContentsMargins(0, 8, 0, 0)
        dev_l.setSpacing(12)

        # A. Cluster Mode (K)
        k_mode_row = QHBoxLayout()
        self._k_mode_auto = QRadioButton("Auto K (Silhouette)")
        self._k_mode_custom = QRadioButton("Custom K:")
        self._k_mode_auto.setChecked(True)
        self._k_mode_group = QButtonGroup(self)
        self._k_mode_group.addButton(self._k_mode_auto)
        self._k_mode_group.addButton(self._k_mode_custom)

        self._custom_k_spin = QSpinBox()
        self._custom_k_spin.setRange(1, 20)
        self._custom_k_spin.setValue(5)
        self._custom_k_spin.setEnabled(False)

        k_mode_row.addWidget(self._k_mode_auto)
        k_mode_row.addWidget(self._k_mode_custom)
        k_mode_row.addWidget(self._custom_k_spin)
        k_mode_row.addStretch()
        self._k_mode_custom.toggled.connect(lambda custom: self._custom_k_spin.setEnabled(custom))
        dev_l.addRow("Cluster Mode (K):", k_mode_row)

        # B. Feature Tuning (Z-score, Contamination, Step, Window)
        self._zscore_spin = QDoubleSpinBox()
        self._zscore_spin.setRange(0.5, 10.0)
        self._zscore_spin.setSingleStep(0.1)
        self._zscore_spin.setValue(3.0)
        self._style_input(self._zscore_spin)

        self._contamination_spin = QDoubleSpinBox()
        self._contamination_spin.setRange(0.01, 0.49)
        self._contamination_spin.setSingleStep(0.01)
        self._contamination_spin.setValue(0.10)
        self._style_input(self._contamination_spin)

        self._window_spin = QSpinBox()
        self._window_spin.setRange(10, 7200)
        self._window_spin.setSuffix(" s")
        self._window_spin.setValue(300)
        self._style_input(self._window_spin)

        self._step_spin = QSpinBox()
        self._step_spin.setRange(10, 1800)
        self._step_spin.setSuffix(" s")
        self._step_spin.setValue(60)
        self._style_input(self._step_spin)

        dev_l.addRow("Z-Score Threshold:", self._zscore_spin)
        dev_l.addRow("IForest Contamination:", self._contamination_spin)
        dev_l.addRow("Rolling Window size:", self._window_spin)
        dev_l.addRow("Resample Step size:", self._step_spin)

        # C. Ranker Weights Sliders
        self._w_conf_slider = QSlider(Qt.Horizontal)
        self._w_conf_slider.setRange(0, 100)
        self._w_conf_slider.setValue(35)
        self._w_hist_slider = QSlider(Qt.Horizontal)
        self._w_hist_slider.setRange(0, 100)
        self._w_hist_slider.setValue(25)
        self._w_cov_slider = QSlider(Qt.Horizontal)
        self._w_cov_slider.setRange(0, 100)
        self._w_cov_slider.setValue(20)
        self._w_safe_slider = QSlider(Qt.Horizontal)
        self._w_safe_slider.setRange(0, 100)
        self._w_safe_slider.setValue(20)

        self._w_conf_label = QLabel("Raw: 35 (35%)")
        self._w_hist_label = QLabel("Raw: 25 (25%)")
        self._w_cov_label = QLabel("Raw: 20 (20%)")
        self._w_safe_label = QLabel("Raw: 20 (20%)")

        for lbl in (self._w_conf_label, self._w_hist_label, self._w_cov_label, self._w_safe_label):
            lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; min-width: 90px;")

        for slider in (self._w_conf_slider, self._w_hist_slider, self._w_cov_slider, self._w_safe_slider):
            slider.valueChanged.connect(self._update_normalized_labels)

        r_conf = QHBoxLayout()
        r_conf.addWidget(self._w_conf_slider, stretch=1)
        r_conf.addWidget(self._w_conf_label)
        dev_l.addRow("Weight LLM Confidence:", r_conf)

        r_hist = QHBoxLayout()
        r_hist.addWidget(self._w_hist_slider, stretch=1)
        r_hist.addWidget(self._w_hist_label)
        dev_l.addRow("Weight Hist Recurrence:", r_hist)

        r_cov = QHBoxLayout()
        r_cov.addWidget(self._w_cov_slider, stretch=1)
        r_cov.addWidget(self._w_cov_label)
        dev_l.addRow("Weight Metric Coverage:", r_cov)

        r_safe = QHBoxLayout()
        r_safe.addWidget(self._w_safe_slider, stretch=1)
        r_safe.addWidget(self._w_safe_label)
        dev_l.addRow("Weight Blast Radius:", r_safe)

        # D. Safety constraints
        self._max_affected_nodes = QSpinBox()
        self._max_affected_nodes.setRange(1, 100)
        self._max_affected_nodes.setValue(2)
        self._style_input(self._max_affected_nodes)

        self._blocked_namespaces = QLineEdit("kube-system, monitoring")
        self._style_input(self._blocked_namespaces)

        dev_l.addRow("Max Affected Nodes:", self._max_affected_nodes)
        dev_l.addRow("Blocked Namespaces:", self._blocked_namespaces)

        llm_form.addRow(self._dev_options_container)
        self._dev_options_toggle.toggled.connect(self._dev_options_container.setVisible)

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

        # Wrap Step 1 in a scroll area to make it scrollable when advanced options expand
        step1_scroll = QScrollArea()
        step1_scroll.setWidgetResizable(True)
        step1_scroll.setFrameShape(QScrollArea.NoFrame)
        step1_scroll.setWidget(step1)
        self._stack.addWidget(step1_scroll)

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

        self._timeline_widget = InteractiveTimelineWidget(self)
        self._tabs.addTab(self._timeline_widget, "Timeline")

        # --- Gatekeeper tab (P1) ---
        self._gatekeeper_table = QTableWidget()
        self._gatekeeper_table.setColumnCount(8)
        self._gatekeeper_table.setHorizontalHeaderLabels(
            ["Cluster", "Verdict", "Freq/h", "Severity", "Log", "Service", "Describe", "Downstream"]
        )
        self._gatekeeper_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self._gatekeeper_table.verticalHeader().setVisible(False)
        self._style_table(self._gatekeeper_table)
        self._gatekeeper_table.currentCellChanged.connect(self._on_gatekeeper_selected)
        self._tabs.addTab(self._gatekeeper_table, "Gatekeeper")

        # --- Descriptions tab (P2) ---
        descriptions_panel = QWidget()
        desc_layout = QVBoxLayout(descriptions_panel)
        desc_layout.setContentsMargins(0, 0, 0, 0)
        desc_toolbar = QHBoxLayout()
        desc_toolbar.addWidget(QLabel("Filter:"))
        self._desc_filter = QComboBox()
        self._desc_filter.addItems(["All", "Described", "Unknown", "Known"])
        self._desc_filter.currentTextChanged.connect(lambda _: self._fill_descriptions_table())
        desc_toolbar.addWidget(self._desc_filter)
        desc_toolbar.addStretch()
        self._promote_btn = QPushButton("Promote to catalog")
        self._promote_btn.setEnabled(False)
        self._promote_btn.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.ACCENT}; color: white; "
            f"border: none; border-radius: 4px; padding: 6px 16px; }}"
            f"QPushButton:disabled {{ background-color: {Colors.BG_HOVER}; color: {Colors.TEXT_MUTED}; }}"
        )
        self._promote_btn.clicked.connect(self._on_promote_clicked)
        desc_toolbar.addWidget(self._promote_btn)
        desc_layout.addLayout(desc_toolbar)

        self._descriptions_table = QTableWidget()
        self._descriptions_table.setColumnCount(5)
        self._descriptions_table.setHorizontalHeaderLabels(
            ["Incident", "State", "Title", "Confidence", "Fallback"]
        )
        self._descriptions_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._descriptions_table.verticalHeader().setVisible(False)
        self._style_table(self._descriptions_table)
        self._descriptions_table.currentCellChanged.connect(self._on_description_selected)
        desc_layout.addWidget(self._descriptions_table)
        self._tabs.addTab(descriptions_panel, "Descriptions")

        self._results_table = QTableWidget()
        self._results_table.setColumnCount(4)
        self._results_table.setHorizontalHeaderLabels(["Scenario", "SCI", "Confidence", "Action"])
        self._results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._results_table.verticalHeader().setVisible(False)
        self._style_table(self._results_table)
        self._results_table.currentCellChanged.connect(self._on_scenario_selected)
        self._scenario_tab_index = self._tabs.addTab(self._results_table, "Scenarios")
        self._tabs.currentChanged.connect(self._on_tab_changed)

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
        self._export_btn = QPushButton("Export report…")
        self._export_btn.clicked.connect(self._on_export_report)
        self._reject_btn = QPushButton("Reject all scenarios")
        self._reject_btn.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.DANGER}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 20px; }}"
        )
        self._reject_btn.clicked.connect(self._on_reject_all)
        self._back_btn = QPushButton("New analysis")
        self._back_btn.clicked.connect(self._go_step1)
        controls.addStretch()
        controls.addWidget(self._export_btn)
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
        self._refresh_ml_models()
        self._refresh_developer_mode()

    def _update_normalized_labels(self):
        raw = [
            self._w_conf_slider.value(),
            self._w_hist_slider.value(),
            self._w_cov_slider.value(),
            self._w_safe_slider.value(),
        ]
        total = sum(raw)
        if total > 0:
            p_conf = int(round(100 * raw[0] / total))
            p_hist = int(round(100 * raw[1] / total))
            p_cov = int(round(100 * raw[2] / total))
            p_safe = int(round(100 * raw[3] / total))
        else:
            p_conf, p_hist, p_cov, p_safe = 25, 25, 25, 25

        self._w_conf_label.setText(f"Raw: {raw[0]} ({p_conf}%)")
        self._w_hist_label.setText(f"Raw: {raw[1]} ({p_hist}%)")
        self._w_cov_label.setText(f"Raw: {raw[2]} ({p_cov}%)")
        self._w_safe_label.setText(f"Raw: {raw[3]} ({p_safe}%)")

    def _refresh_developer_mode(self):
        try:
            from chaosgen.config.settings import load_settings
            settings = load_settings()
            is_dev = settings.developer_mode
            self._dev_options_toggle.setVisible(is_dev)
            if not is_dev:
                self._dev_options_toggle.setChecked(False)
                self._dev_options_container.setVisible(False)
        except Exception:
            pass

    def refresh_credentials(self):
        """Called after Settings save so credential status picks up new keys."""
        self._refresh_credential_status()

    def _refresh_ml_models(self):
        self._ml_model_combo.clear()
        self._ml_model_combo.addItem("None (Dynamic Fit)", "")
        models_dir = Path("./models")
        if models_dir.exists() and models_dir.is_dir():
            for f in sorted(models_dir.glob("*.joblib")):
                self._ml_model_combo.addItem(f.name, str(f.resolve()))

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

            # Load advanced settings values
            if settings.anomaly:
                if settings.anomaly.clustering_mode == "fixed":
                    self._k_mode_custom.setChecked(True)
                else:
                    self._k_mode_auto.setChecked(True)
                self._custom_k_spin.setValue(settings.anomaly.n_clusters)
                self._contamination_spin.setValue(settings.anomaly.contamination)

            if settings.features:
                self._zscore_spin.setValue(settings.features.zscore_threshold)
                self._window_spin.setValue(settings.features.rolling_window_seconds)
                self._step_spin.setValue(settings.features.resample_step_seconds)

            if settings.ranking:
                self._w_conf_slider.setValue(int(settings.ranking.weight_confidence * 100))
                self._w_hist_slider.setValue(int(settings.ranking.weight_historical * 100))
                self._w_cov_slider.setValue(int(settings.ranking.weight_coverage * 100))
                self._w_safe_slider.setValue(int(settings.ranking.weight_safety * 100))
                self._update_normalized_labels()

            if settings.safety:
                self._max_affected_nodes.setValue(settings.safety.max_affected_nodes)
                self._blocked_namespaces.setText(", ".join(settings.safety.blocked_namespaces))

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
        model_path = self._ml_model_combo.currentData() or None

        start_datetime = None
        end_datetime = None
        if self._mode_live.isChecked() and self._time_absolute.isChecked():
            from datetime import timezone
            s_qdt = self._start_dt_edit.dateTime().toPython()
            e_qdt = self._end_dt_edit.dateTime().toPython()
            start_datetime = s_qdt.replace(tzinfo=timezone.utc)
            end_datetime = e_qdt.replace(tzinfo=timezone.utc)

        clustering_mode = "fixed" if self._k_mode_custom.isChecked() else "auto"
        n_clusters = self._custom_k_spin.value()

        # Persist advanced options to settings.yaml before running
        try:
            from chaosgen.config.settings import load_settings, save_settings
            settings = load_settings()
            settings.anomaly.clustering_mode = clustering_mode
            settings.anomaly.n_clusters = n_clusters
            settings.anomaly.contamination = self._contamination_spin.value()

            settings.features.zscore_threshold = self._zscore_spin.value()
            settings.features.rolling_window_seconds = self._window_spin.value()
            settings.features.resample_step_seconds = self._step_spin.value()

            settings.ranking.weight_confidence = self._w_conf_slider.value() / 100.0
            settings.ranking.weight_historical = self._w_hist_slider.value() / 100.0
            settings.ranking.weight_coverage = self._w_cov_slider.value() / 100.0
            settings.ranking.weight_safety = self._w_safe_slider.value() / 100.0

            settings.safety.max_affected_nodes = self._max_affected_nodes.value()
            settings.safety.blocked_namespaces = [
                ns.strip() for ns in self._blocked_namespaces.text().split(",") if ns.strip()
            ]

            save_settings(settings)
        except Exception as e:
            logger.warning("Failed to save tuning settings before analysis: %s", e)

        request = AnalysisRequest(
            source=source,
            prom_url=self._prom_url.text().strip(),
            loki_url=self._loki_url.text().strip(),
            lookback_hours=self._lookback.value(),
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            export_path=self._export_path.text().strip() or None,
            clustering_mode=clustering_mode,
            n_clusters=n_clusters,
            generate_scenarios=self._generate_cb.isChecked(),
            llm_provider=self._provider_combo.currentText(),
            llm_model=self._model.text().strip() or None,
            skip_gatekeeper=self._skip_gatekeeper_cb.isChecked(),
            model_path=model_path,
        )

        self._analyze_btn.setEnabled(False)
        self._check_btn.setEnabled(False)
        self._dev_options_toggle.setEnabled(False)
        self._dev_options_container.setEnabled(False)
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
        self._dev_options_toggle.setEnabled(True)
        self._dev_options_container.setEnabled(True)
        self._stack.setCurrentIndex(2)
        self._step_label.setText("Step 3 of 3 — Review results")

        if result:
            self._summary_label.setText(
                f"Source: {result.source_label}  |  "
                f"Series: {result.metric_series}  |  "
                f"Samples: {result.total_samples}  |  "
                f"Feature windows: {result.feature_rows}  |  "
                f"{gatekeeper_summary(report)}"
            )
            self._fill_anomaly_table(result.summaries)
            self._timeline_widget.update_plot(
                result.timeline_df,
                result.summaries,
                report.clusters
            )
            
            if result.metric_series == 0 or result.feature_rows == 0:
                self._detail_text.setPlainText(self._empty_metrics_help(result))
        else:
            self._summary_label.setText(
                f"Anomalies: {report.anomalies_found}  |  {gatekeeper_summary(report)}"
            )
            self._timeline_widget.clear()

        self._fill_gatekeeper_table(report)
        self._fill_descriptions_table()
        self._fill_scenario_table(report)
        self._persist_report(report)

    def _persist_report(self, report: AdvisorReport) -> None:
        """Auto-save the report so CLI incidents/promote can read the same artifact."""
        try:
            from chaosgen.advisor.report_store import save_report

            save_report(report)
        except Exception as exc:
            self._controller.log_message.emit(f"Could not save advisor report: {exc}")

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
        self._results_table.clearSpans()
        self._tabs.setTabEnabled(self._scenario_tab_index, True)
        if not exps:
            self._results_table.setRowCount(1)
            placeholder = QTableWidgetItem(_EMPTY_SCENARIOS_MSG)
            placeholder.setFlags(Qt.ItemIsEnabled)
            self._results_table.setItem(0, 0, placeholder)
            self._results_table.setSpan(0, 0, 1, 4)
            return

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

    def _fill_gatekeeper_table(self, report: AdvisorReport):
        rows = gatekeeper_rows(report)
        self._gatekeeper_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            cells = [
                row["cluster"], row["verdict"], row["frequency"], row["severity"],
                row["log"], row["service"], row["describe"], row["downstream"],
            ]
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if col == 1:
                    color = _VERDICT_COLORS.get(row["verdict"])
                    if color:
                        item.setForeground(QColor(color))
                self._gatekeeper_table.setItem(i, col, item)

    def _on_gatekeeper_selected(self, row, _col, _prev_row, _prev_col):
        if not self._current_report or row < 0:
            return
        rows = gatekeeper_rows(self._current_report)
        if row >= len(rows):
            return
        data = rows[row]
        lines = [
            f"Cluster {data['cluster']} — {data['verdict']}",
            f"Frequency: {data['frequency']}/h   Severity: {data['severity']}",
            f"Service: {data['service']}   Log correlated: {data['log']}",
            f"Describe: {data['describe']}   Downstream: {data['downstream']}",
            "",
            "Rationale:",
            data["rationale"],
        ]
        linked = next(
            (d for d in self._current_report.descriptions
             if str(d.source_incident_id) == data["cluster"]),
            None,
        )
        if linked:
            lines += [
                "",
                "Linked description:",
                f"  {linked.title}",
                f"  {linked.root_cause_hypothesis}",
            ]
        self._detail_text.setPlainText("\n".join(lines))

    def _fill_descriptions_table(self):
        if not self._current_report:
            self._descriptions_table.setRowCount(0)
            self._current_descriptions = []
            self._promote_btn.setEnabled(False)
            return
        state = self._desc_filter.currentText()
        descriptions = description_rows(self._current_report, state)
        self._current_descriptions = descriptions
        self._descriptions_table.setRowCount(len(descriptions))
        for i, desc in enumerate(descriptions):
            self._descriptions_table.setItem(i, 0, QTableWidgetItem(str(desc.source_incident_id)))
            self._descriptions_table.setItem(i, 1, QTableWidgetItem(desc.knowledge_state.value))
            self._descriptions_table.setItem(i, 2, QTableWidgetItem(desc.title))
            self._descriptions_table.setItem(i, 3, QTableWidgetItem(f"{desc.confidence:.2f}"))
            badge = "yes" if desc.metadata.get("describe_fallback") else ""
            fallback_item = QTableWidgetItem(badge)
            if badge:
                fallback_item.setForeground(QColor(Colors.WARNING))
            self._descriptions_table.setItem(i, 4, fallback_item)
        self._promote_btn.setEnabled(False)

    def _selected_description(self) -> UnknownScenarioDescription | None:
        row = self._descriptions_table.currentRow()
        if row < 0 or row >= len(self._current_descriptions):
            return None
        return self._current_descriptions[row]

    def _on_description_selected(self, row, _col, _prev_row, _prev_col):
        desc = self._selected_description()
        if not desc:
            self._promote_btn.setEnabled(False)
            return
        block = promote_blocked_reason(desc)
        self._promote_btn.setEnabled(block is None)
        self._promote_btn.setToolTip(block or "Promote this described incident to the catalog")
        steps = "\n".join(f"  - {s}" for s in desc.repro_steps)
        lines = [
            f"=== {desc.title} ===",
            f"State: {desc.knowledge_state.value}   Confidence: {desc.confidence:.2f}",
            f"Incident: {desc.source_incident_id}",
            "",
            "Root cause hypothesis:",
            desc.root_cause_hypothesis,
            "",
            "Repro steps:",
            steps,
            "",
            f"Blast radius: {desc.blast_radius_estimate}",
            f"Suggested fault type: {desc.suggested_fault_type.value}",
        ]
        if block:
            lines += ["", f"[Promote disabled] {block}"]
        self._detail_text.setPlainText("\n".join(lines))

    def _on_promote_clicked(self):
        desc = self._selected_description()
        if not desc or not self._current_report:
            return
        block = promote_blocked_reason(desc)
        if block:
            QMessageBox.warning(self, "Cannot promote", block)
            return
        dialog = _PromoteDialog(desc, self._current_report.generated_experiments, self)
        if dialog.exec() != QDialog.Accepted:
            return
        from chaosgen.advisor.catalog_promoter import CatalogPromoter, PromoteError
        from chaosgen.storage.history import get_default_history_store

        description_row_id = None
        if self._current_report.description_db_ids:
            description_row_id = self._current_report.description_db_ids.get(
                desc.source_incident_id
            )

        try:
            entry = CatalogPromoter(history_store=get_default_history_store()).promote(
                desc,
                dialog.selected_experiment(),
                approved_by=dialog.approved_by(),
                acceptance_criteria=dialog.acceptance_criteria(),
                name=dialog.catalog_name(),
                description_row_id=description_row_id,
            )
        except (PromoteError, ValueError) as exc:
            QMessageBox.critical(self, "Promote failed", str(exc))
            return
        QMessageBox.information(
            self, "Promoted",
            f"'{entry.name}' added to the dynamic catalog (incident {desc.source_incident_id} → KNOWN).",
        )
        self._controller.log_message.emit(f"Promoted scenario '{entry.name}' to catalog")
        self._fill_descriptions_table()

    def _on_export_report(self):
        if not self._current_report:
            QMessageBox.information(self, "No report", "Run an analysis first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export advisor report", "advisor_report.json", "JSON (*.json)"
        )
        if not path:
            return
        from chaosgen.advisor.report_store import save_report

        try:
            save_report(self._current_report, path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Exported", f"Report saved to {path}")

    @Slot(str)
    def _on_advisor_error(self, error_msg: str):
        self._analyze_btn.setEnabled(True)
        self._check_btn.setEnabled(True)
        self._dev_options_toggle.setEnabled(True)
        self._dev_options_container.setEnabled(True)
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

    def _on_tab_changed(self, index: int):
        if index != self._scenario_tab_index or not self._current_report:
            return
        if not self._current_report.generated_experiments:
            self._detail_text.setPlainText(_EMPTY_SCENARIOS_MSG)

    def _on_scenario_selected(self, row, _col, _prev_row, _prev_col):
        if not self._current_report or row < 0:
            return
        experiments = self._current_report.generated_experiments
        if not experiments or row >= len(experiments):
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
        self._gatekeeper_table.setRowCount(0)
        self._descriptions_table.setRowCount(0)
        self._results_table.setRowCount(0)
        self._timeline_widget.clear()
        self._detail_text.clear()
        self._current_report = None
        self._current_result = None
        self._current_descriptions = []
        self._promote_btn.setEnabled(False)


class _PromoteDialog(QDialog):
    """HITL promote dialog — mirrors the CLI `promote` inputs and P3 guards."""

    def __init__(self, description: UnknownScenarioDescription, experiments, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Promote to catalog")
        self.setMinimumWidth(480)
        self._experiments = list(experiments)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(description.title)
        form.addRow("Catalog name:", self._name_edit)

        self._approved_edit = QLineEdit()
        self._approved_edit.setPlaceholderText("operator name (required)")
        form.addRow("Approved by:", self._approved_edit)

        self._experiment_combo = QComboBox()
        for exp in self._experiments:
            self._experiment_combo.addItem(exp.name)
        form.addRow("Experiment:", self._experiment_combo)
        layout.addLayout(form)

        layout.addWidget(QLabel("Acceptance criteria (YAML — e.g. http_health / prometheus):"))
        self._criteria_edit = QPlainTextEdit()
        self._criteria_edit.setPlaceholderText(
            "http_health: https://payments/health\n"
            "# or:\n# prometheus:\n#   query: up{job=\"payments\"}"
        )
        self._criteria_edit.setMinimumHeight(120)
        layout.addWidget(self._criteria_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Promote")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self._approved_edit.text().strip():
            QMessageBox.warning(self, "Missing operator", "Approved by is required.")
            return
        if not self._experiments:
            QMessageBox.warning(self, "No experiment", "Report has no experiments to promote.")
            return
        try:
            self.acceptance_criteria()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid criteria", f"Could not parse criteria YAML: {exc}")
            return
        self.accept()

    def approved_by(self) -> str:
        return self._approved_edit.text().strip()

    def catalog_name(self) -> str:
        return self._name_edit.text().strip() or None

    def selected_experiment(self):
        return self._experiments[self._experiment_combo.currentIndex()]

    def acceptance_criteria(self) -> dict:
        import yaml

        text = self._criteria_edit.toPlainText().strip()
        if not text:
            return {}
        parsed = yaml.safe_load(text)
        if parsed is not None and not isinstance(parsed, dict):
            raise ValueError("criteria must be a YAML mapping")
        return parsed or {}
