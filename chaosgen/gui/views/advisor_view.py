"""
Telemetry & AI Advisor — live stack, offline export, anomaly review, scenario approval.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt, Slot, QTimer, QSettings
from PySide6.QtGui import QColor, QShowEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QSpinBox, QDoubleSpinBox, QSlider, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QSplitter, QMessageBox, QFileDialog, QRadioButton,
    QButtonGroup, QCheckBox, QComboBox, QTabWidget, QSizePolicy,
    QDialog, QDialogButtonBox, QPlainTextEdit, QScrollArea, QFrame,
    QTreeWidget, QTreeWidgetItem,
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
from chaosgen.gui.theme import Colors, Fonts, Spacing, set_semantic_role
from chaosgen.schemas.scenarios import (
    AdvisorReport,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)
from chaosgen.telemetry.guided_discovery import DiscoveredQuery, GuidedCatalog

logger = logging.getLogger(__name__)

_VERDICT_COLORS = {
    "REAL": Colors.SUCCESS,
    "CHRONIC": Colors.ACCENT,
    "TRANSIENT": Colors.WARNING,
    "NOISE": Colors.TEXT_MUTED,
}

_EMPTY_SCENARIOS_MSG = (
    "No scenarios generated — see Triage (Gatekeeper / Descriptions) "
    "(fallback incidents are not fed to chaos generation)."
)

_DEFAULT_EXPORT = Path("./exports")
_COMPOSITE_WIDTH_BREAKPOINT = 1100
_SETTINGS_ORG = "ChaosGen"
_SETTINGS_APP = "AdvisorView"

# Default (Enterprise Packs) — static form-first stack for Step 1 Default mode
_ENTERPRISE_PROFILE = "boutique"
_ENTERPRISE_EXTRAS = ["cadvisor", "loki_system"]
_BUCKET_ORDER = ("traffic", "errors", "latency", "saturation", "logs")
_BUCKET_LABELS = {
    "traffic": "Traffic",
    "errors": "Errors",
    "latency": "Latency",
    "saturation": "Saturation",
    "logs": "Logs",
}


def _shorten_feature(name: str, max_len: int = 42) -> str:
    """Layer-1 signal label; full PromQL stays in the detail pane."""
    if len(name) <= max_len:
        return name
    # Prefer a readable tail (metric/label fragment) over head truncation.
    return "…" + name[-(max_len - 1) :]


class AdvisorView(QWidget):
    """Wizard: configure source → analyze → review anomalies & scenarios."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._current_report: AdvisorReport | None = None
        self._current_result: AnalysisResult | None = None
        self._current_descriptions: list[UnknownScenarioDescription] = []
        self._syncing_selection = False
        self._composite_narrow: bool | None = None
        self._resize_layout_timer = QTimer(self)
        self._resize_layout_timer.setSingleShot(True)
        self._resize_layout_timer.setInterval(120)
        self._resize_layout_timer.timeout.connect(self._apply_composite_layout)
        # --- START MODIFICATION ---
        # Guided Custom Discovery session cache (Ping prefetch; never written to disk)
        self._guided_catalog: GuidedCatalog | None = None
        self._guided_prefetch_pending = False
        self._guided_prefetch_error: str | None = None
        self._guided_prefetch_gen = 0
        self._guided_prefetch_waiting_gen: int | None = None
        self._telemetry_connected = False
        self._resolved_scope_ns = "default"
        # --- END MODIFICATION ---
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
        self._step_label.setObjectName("stepWizard")
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

        # --- START MODIFICATION ---
        # Guided Custom Discovery: Default | Custom replaces pack combo + Target namespace
        ingest_mode_row = QHBoxLayout()
        self._ingest_mode_combo = QComboBox()
        self._ingest_mode_combo.addItem("Default (Enterprise Packs)", "default")
        self._ingest_mode_combo.addItem("Custom (Guided Discovery)", "custom")
        self._ingest_mode_combo.setEnabled(False)
        self._style_input(self._ingest_mode_combo)
        self._ingest_beta_badge = QLabel("Beta / Experimental")
        self._ingest_beta_badge.setObjectName("betaBadge")
        self._ingest_beta_badge.setVisible(False)
        ingest_mode_row.addWidget(self._ingest_mode_combo)
        ingest_mode_row.addWidget(self._ingest_beta_badge)
        ingest_mode_row.addStretch()
        live_form.addRow("Ingest mode:", ingest_mode_row)

        self._custom_panel = QWidget()
        custom_l = QVBoxLayout(self._custom_panel)
        custom_l.setContentsMargins(0, 4, 0, 0)
        custom_l.setSpacing(6)

        self._custom_status_label = QLabel("")
        self._custom_status_label.setWordWrap(True)
        self._custom_status_label.setObjectName("textSecondary")
        custom_l.addWidget(self._custom_status_label)

        toolbar = QHBoxLayout()
        self._btn_suggested = QPushButton("Suggested")
        self._btn_select_all = QPushButton("Select all")
        self._btn_deselect_all = QPushButton("Deselect all")
        for b in (self._btn_suggested, self._btn_select_all, self._btn_deselect_all):
            b.setEnabled(False)
            toolbar.addWidget(b)
        toolbar.addStretch()
        custom_l.addLayout(toolbar)

        self._guided_tree = QTreeWidget()
        self._guided_tree.setHeaderLabels(["Signal", "Query preview"])
        self._guided_tree.setColumnCount(2)
        self._guided_tree.setRootIsDecorated(True)
        self._guided_tree.setUniformRowHeights(True)
        self._guided_tree.setMinimumHeight(180)
        self._guided_tree.setMaximumHeight(320)
        self._guided_tree.setObjectName("treeDark")
        self._guided_tree.header().setStretchLastSection(True)
        self._guided_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        custom_l.addWidget(self._guided_tree)

        self._custom_zero_hint = QLabel("Select at least one metric to proceed")
        self._custom_zero_hint.setObjectName("dangerSmall")
        self._custom_zero_hint.setVisible(False)
        custom_l.addWidget(self._custom_zero_hint)

        self._custom_panel.setVisible(False)
        live_form.addRow("", self._custom_panel)

        self._ingest_mode_combo.currentIndexChanged.connect(self._on_ingest_mode_changed)
        self._btn_suggested.clicked.connect(self._on_guided_suggested)
        self._btn_select_all.clicked.connect(lambda: self._set_all_guided_checks(True))
        self._btn_deselect_all.clicked.connect(lambda: self._set_all_guided_checks(False))
        self._guided_tree.itemChanged.connect(self._on_guided_item_changed)
        # --- END MODIFICATION ---

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
        self._mode_live.toggled.connect(lambda _on: self._refresh_analyze_guard())
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
        self._zscore_spin.setValue(5.0)
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
            lbl.setObjectName("textSecondaryWide")

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
        self._credential_status.setObjectName("textSecondary")
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

        # --- START MODIFICATION ---
        # P0: status strip above Run analysis
        self._ingest_scope_frame = QFrame()
        self._ingest_scope_frame.setObjectName("ingestScopeStrip")
        strip_l = QVBoxLayout(self._ingest_scope_frame)
        strip_l.setContentsMargins(12, 8, 12, 8)
        self._ingest_status_label = QLabel("")
        self._ingest_status_label.setWordWrap(True)
        self._ingest_status_label.setObjectName("textSecondary")
        strip_l.addWidget(self._ingest_status_label)
        s1.addWidget(self._ingest_scope_frame)
        # --- END MODIFICATION ---

        self._analyze_btn = QPushButton("Run analysis")
        self._analyze_btn.setObjectName("btnPrimary")
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
        self._progress_label.setObjectName("textPrimaryLarge")
        s2.addWidget(self._progress_label)
        s2.addStretch()
        self._stack.addWidget(step2)

        # --- Step 3 ---
        # --- START MODIFICATION ---
        # Union peer tabs into Evidence / Triage / Scenarios composites
        # --- END MODIFICATION ---
        step3 = QWidget()
        s3 = QVBoxLayout(step3)
        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        self._summary_label.setObjectName("textPrimary")
        s3.addWidget(self._summary_label)

        # --- START MODIFICATION ---
        # Honest Approve context: never invent a green env when unset.
        self._exec_context_label = QLabel()
        self._exec_context_label.setWordWrap(True)
        self._exec_context_label.setObjectName("textMuted")
        set_semantic_role(self._exec_context_label, "muted")
        s3.addWidget(self._exec_context_label)

        # On-page Approve outcome (do not rely only on bottom OUTPUT log).
        self._approve_outcome_label = QLabel("")
        self._approve_outcome_label.setWordWrap(True)
        self._approve_outcome_label.setVisible(False)
        s3.addWidget(self._approve_outcome_label)
        # --- END MODIFICATION ---

        self._outer_splitter = QSplitter(Qt.Vertical)
        self._tabs = QTabWidget()

        # --- Evidence = Anomalies + Timeline ---
        self._anomaly_table = QTableWidget()
        self._anomaly_table.setColumnCount(4)
        self._anomaly_table.setHorizontalHeaderLabels(
            ["Service", "Severity", "Top signals", "Window"]
        )
        self._anomaly_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._anomaly_table.verticalHeader().setVisible(False)
        self._anomaly_table.setMinimumHeight(160)
        self._anomaly_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._style_table(self._anomaly_table)

        self._timeline_widget = InteractiveTimelineWidget(self)
        self._timeline_widget.setMinimumHeight(140)
        self._timeline_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        evidence_page = QWidget()
        evidence_layout = QVBoxLayout(evidence_page)
        evidence_layout.setContentsMargins(0, 0, 0, 0)
        evidence_layout.setSpacing(Spacing.SM)
        evidence_toolbar = QHBoxLayout()
        evidence_toolbar.addWidget(QLabel("Evidence — anomalies + timeline"))
        evidence_toolbar.addStretch()
        self._toggle_timeline_btn = QPushButton("Hide timeline")
        self._toggle_timeline_btn.setFlat(True)
        self._toggle_timeline_btn.clicked.connect(self._toggle_timeline_pane)
        evidence_toolbar.addWidget(self._toggle_timeline_btn)
        evidence_layout.addLayout(evidence_toolbar)
        self._evidence_splitter = QSplitter(Qt.Vertical)
        self._evidence_splitter.setChildrenCollapsible(True)
        self._evidence_splitter.addWidget(self._anomaly_table)
        self._evidence_splitter.addWidget(self._timeline_widget)
        self._evidence_splitter.setStretchFactor(0, 2)
        self._evidence_splitter.setStretchFactor(1, 1)
        evidence_layout.addWidget(self._evidence_splitter)
        self._tabs.addTab(evidence_page, "Evidence")

        # --- Triage = Gatekeeper + Descriptions ---
        self._gatekeeper_table = QTableWidget()
        self._gatekeeper_table.setColumnCount(8)
        self._gatekeeper_table.setHorizontalHeaderLabels(
            ["Cluster", "Verdict", "Freq/h", "Severity", "Log", "Service", "Describe", "Downstream"]
        )
        self._gatekeeper_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self._gatekeeper_table.verticalHeader().setVisible(False)
        self._gatekeeper_table.setMinimumWidth(280)
        self._gatekeeper_table.setMinimumHeight(160)
        self._gatekeeper_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._style_table(self._gatekeeper_table)
        self._gatekeeper_table.currentCellChanged.connect(self._on_gatekeeper_selected)

        descriptions_panel = QWidget()
        descriptions_panel.setMinimumWidth(280)
        descriptions_panel.setMinimumHeight(160)
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
        self._promote_btn.setObjectName("btnPrimary")
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

        triage_page = QWidget()
        triage_layout = QVBoxLayout(triage_page)
        triage_layout.setContentsMargins(0, 0, 0, 0)
        triage_layout.setSpacing(Spacing.SM)
        triage_toolbar = QHBoxLayout()
        triage_toolbar.addWidget(QLabel("Triage — gatekeeper + descriptions"))
        triage_toolbar.addStretch()
        self._toggle_descriptions_btn = QPushButton("Hide descriptions")
        self._toggle_descriptions_btn.setFlat(True)
        self._toggle_descriptions_btn.clicked.connect(self._toggle_descriptions_pane)
        triage_toolbar.addWidget(self._toggle_descriptions_btn)
        triage_layout.addLayout(triage_toolbar)
        self._triage_splitter = QSplitter(Qt.Horizontal)
        self._triage_splitter.setChildrenCollapsible(True)
        self._triage_splitter.addWidget(self._gatekeeper_table)
        self._triage_splitter.addWidget(descriptions_panel)
        self._triage_splitter.setStretchFactor(0, 1)
        self._triage_splitter.setStretchFactor(1, 1)
        triage_layout.addWidget(self._triage_splitter)
        self._tabs.addTab(triage_page, "Triage")

        # --- Scenarios ---
        self._results_table = QTableWidget()
        self._results_table.setColumnCount(4)
        self._results_table.setHorizontalHeaderLabels(["Scenario", "SCI", "Confidence", "Action"])
        self._results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._results_table.verticalHeader().setVisible(False)
        self._style_table(self._results_table)
        self._results_table.currentCellChanged.connect(self._on_scenario_selected)
        self._scenario_tab_index = self._tabs.addTab(self._results_table, "Scenarios")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        self._outer_splitter.addWidget(self._tabs)

        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setMinimumHeight(100)
        self._detail_text.setObjectName("detailMono")
        self._outer_splitter.addWidget(self._detail_text)
        self._outer_splitter.setStretchFactor(0, 2)
        self._outer_splitter.setStretchFactor(1, 1)
        self._outer_splitter.setChildrenCollapsible(True)
        s3.addWidget(self._outer_splitter)

        controls = QHBoxLayout()
        self._export_btn = QPushButton("Export report…")
        self._export_btn.clicked.connect(self._on_export_report)
        self._reject_btn = QPushButton("Reject all scenarios")
        self._reject_btn.setObjectName("btnDanger")
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
        self._restore_splitter_sizes()
        QTimer.singleShot(0, self._apply_composite_layout)

    def _style_table(self, table: QTableWidget):
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        # MODIFIED: dataTable objectName from theme
        table.setObjectName("dataTable")

    def _make_card(self, title: str) -> QWidget:
        card = QWidget()
        card.setObjectName("cardWidget")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        header = QLabel(title)
        header.setObjectName("cardTitle")
        cl.addWidget(header)
        return card

    def _style_input(self, widget):
        # MODIFIED: formInput objectName from theme
        widget.setObjectName("formInput")

    def _connect_signals(self):
        self._controller.advisor_finished.connect(self._on_analysis_finished)
        self._controller.advisor_error.connect(self._on_advisor_error)
        self._controller.telemetry_check_finished.connect(self._on_check_finished)
        self._controller.telemetry_progress.connect(self._on_progress)
        self._controller.guided_catalog_finished.connect(self._on_guided_catalog_finished)
        self._anomaly_table.currentCellChanged.connect(self._on_anomaly_selected)
        self._provider_combo.currentTextChanged.connect(self._refresh_credential_status)
        self._controller.llm_check_finished.connect(self._on_llm_test_finished)
        # MODIFIED: surface Approve PASS/FAIL on Step 3, not only bottom log
        self._controller.experiment_finished.connect(self._on_approve_experiment_finished)

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
        # MODIFIED: refresh resolved namespace strip after Settings save
        self._sync_ingest_controls_from_settings()
        # --- START MODIFICATION ---
        # Re-apply Prom/Loki from disk so Settings Save is not a lie on Step 1.
        self._reload_observability_urls_from_settings()
        self._refresh_execution_context()
        # --- END MODIFICATION ---

    def _reload_observability_urls_from_settings(self) -> None:
        try:
            from chaosgen.config.settings import load_settings
            from chaosgen.schemas.discovery import ObservabilityTool

            settings = load_settings()
            for hint in settings.hints.observability:
                if hint.tool == ObservabilityTool.PROMETHEUS and hint.url:
                    self._prom_url.setText(hint.url)
                elif hint.tool == ObservabilityTool.LOKI and hint.url:
                    self._loki_url.setText(hint.url)
        except Exception as exc:
            logger.warning("Could not reload observability URLs: %s", exc)

    def _resolved_namespace(self) -> str:
        from chaosgen.config.settings import load_settings
        from chaosgen.telemetry.guided_discovery import resolve_scope_namespace

        try:
            return resolve_scope_namespace(load_settings())
        except Exception:
            return self._resolved_scope_ns or "default"

    def _ingest_mode(self) -> str:
        data = self._ingest_mode_combo.currentData()
        return str(data or "default")

    def _update_ingest_status_strip(self) -> None:
        from chaosgen.telemetry.pack_loader import format_pack_status_line

        ns = self._resolved_scope_ns or self._resolved_namespace()
        if self._ingest_mode() == "custom":
            n = len(self._selected_guided_queries())
            pending = " · discovering…" if self._guided_prefetch_pending else ""
            err = (
                f" · prefetch error: {self._guided_prefetch_error}"
                if self._guided_prefetch_error
                else ""
            )
            self._ingest_status_label.setText(
                f"Mode: Custom (Guided Discovery Beta) — {n} selected · ns={ns}{pending}{err}"
            )
        else:
            base = format_pack_status_line(
                _ENTERPRISE_PROFILE, list(_ENTERPRISE_EXTRAS), ns
            )
            self._ingest_status_label.setText(f"Mode: Default · {base}")

    def _sync_ingest_controls_from_settings(self) -> None:
        """Refresh read-only namespace resolution from settings (no Step 1 editor)."""
        self._resolved_scope_ns = self._resolved_namespace()
        self._update_ingest_status_strip()
        self._refresh_analyze_guard()

    def _on_ingest_mode_changed(self) -> None:
        is_custom = self._ingest_mode() == "custom"
        self._custom_panel.setVisible(is_custom)
        self._ingest_beta_badge.setVisible(is_custom)
        if is_custom:
            self._render_guided_panel()
        self._update_ingest_status_strip()
        self._refresh_analyze_guard()

    def _render_guided_panel(self) -> None:
        """Refresh Custom checklist from prefetch cache / in-flight / error state."""
        if self._guided_prefetch_pending and self._guided_catalog is None:
            self._custom_status_label.setText("Discovering live signals…")
            set_semantic_role(self._custom_status_label, "secondary")
            self._guided_tree.clear()
            self._set_guided_toolbar_enabled(False)
            return

        if self._guided_prefetch_error and self._guided_catalog is None:
            self._custom_status_label.setText(
                f"Discovery failed: {self._guided_prefetch_error}. "
                "Default mode remains available."
            )
            set_semantic_role(self._custom_status_label, "warning")
            self._guided_tree.clear()
            self._set_guided_toolbar_enabled(False)
            return

        if self._guided_catalog is None:
            self._custom_status_label.setText(
                "Check connection to discover live signals for Custom mode."
            )
            set_semantic_role(self._custom_status_label, "secondary")
            self._guided_tree.clear()
            self._set_guided_toolbar_enabled(False)
            return

        warnings = list(self._guided_catalog.warnings or [])
        stack = self._guided_catalog.stack_summary or "live"
        n = len(self._guided_catalog.queries)
        sug = len(self._guided_catalog.suggested)
        msg = f"Discovered {n} candidates ({stack}) · {sug} suggested"
        if warnings:
            msg += " · " + "; ".join(warnings[:2])
        self._custom_status_label.setText(msg)
        set_semantic_role(self._custom_status_label, "secondary")
        self._populate_guided_tree(self._guided_catalog)
        self._set_guided_toolbar_enabled(True)
        # Default action UX: apply Suggested once when first populated empty
        if not self._any_guided_checked():
            self._on_guided_suggested()

    def _set_guided_toolbar_enabled(self, enabled: bool) -> None:
        for b in (self._btn_suggested, self._btn_select_all, self._btn_deselect_all):
            b.setEnabled(enabled)

    def _populate_guided_tree(self, catalog: GuidedCatalog) -> None:
        self._guided_tree.blockSignals(True)
        self._guided_tree.clear()
        by_bucket: dict[str, list[DiscoveredQuery]] = {b: [] for b in _BUCKET_ORDER}
        for q in catalog.queries:
            by_bucket.setdefault(q.bucket, []).append(q)

        for bucket in _BUCKET_ORDER:
            items = by_bucket.get(bucket) or []
            if not items:
                continue
            parent = QTreeWidgetItem(
                [f"{_BUCKET_LABELS.get(bucket, bucket)} ({len(items)})", ""]
            )
            parent.setFlags(parent.flags() & ~Qt.ItemIsUserCheckable)
            parent.setExpanded(True)
            self._guided_tree.addTopLevelItem(parent)
            for dq in items:
                preview = dq.query if len(dq.query) <= 72 else dq.query[:71] + "…"
                label = dq.metric
                if dq.suggested:
                    label = f"{dq.metric}  ★ Suggested"
                child = QTreeWidgetItem([label, preview])
                child.setFlags(
                    child.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                )
                child.setCheckState(0, Qt.Unchecked)
                child.setData(0, Qt.UserRole, dq)
                child.setToolTip(0, dq.query)
                child.setToolTip(1, dq.query)
                parent.addChild(child)
        self._guided_tree.blockSignals(False)

    def _iter_guided_leaf_items(self):
        root = self._guided_tree.invisibleRootItem()
        for i in range(root.childCount()):
            parent = root.child(i)
            for j in range(parent.childCount()):
                yield parent.child(j)

    def _any_guided_checked(self) -> bool:
        return any(
            item.checkState(0) == Qt.Checked for item in self._iter_guided_leaf_items()
        )

    def _selected_guided_queries(self) -> list[DiscoveredQuery]:
        out: list[DiscoveredQuery] = []
        for item in self._iter_guided_leaf_items():
            if item.checkState(0) != Qt.Checked:
                continue
            dq = item.data(0, Qt.UserRole)
            if isinstance(dq, DiscoveredQuery):
                out.append(dq)
        return out

    def _set_all_guided_checks(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        self._guided_tree.blockSignals(True)
        for item in self._iter_guided_leaf_items():
            item.setCheckState(0, state)
        self._guided_tree.blockSignals(False)
        self._refresh_analyze_guard()
        self._update_ingest_status_strip()

    def _on_guided_suggested(self) -> None:
        self._guided_tree.blockSignals(True)
        for item in self._iter_guided_leaf_items():
            dq = item.data(0, Qt.UserRole)
            want = isinstance(dq, DiscoveredQuery) and dq.suggested
            item.setCheckState(0, Qt.Checked if want else Qt.Unchecked)
        self._guided_tree.blockSignals(False)
        self._refresh_analyze_guard()
        self._update_ingest_status_strip()

    def _on_guided_item_changed(self, _item, _column) -> None:
        self._refresh_analyze_guard()
        self._update_ingest_status_strip()

    def _refresh_analyze_guard(self) -> None:
        """Zero-selection guard for Custom mode; Default always runnable."""
        if not hasattr(self, "_analyze_btn"):
            return
        live = self._mode_live.isChecked()
        custom = live and self._ingest_mode() == "custom"
        ok = True
        if custom:
            ok = len(self._selected_guided_queries()) > 0
            self._custom_zero_hint.setVisible(not ok)
        else:
            self._custom_zero_hint.setVisible(False)
        # Keep disabled while analysis is running (stack index 1)
        analyzing = self._stack.currentIndex() == 1
        self._analyze_btn.setEnabled(ok and not analyzing)

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
        set_semantic_role(self._credential_status, "secondary")

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
        # MODIFIED: P0 — seed pack/namespace after widgets exist
        self._sync_ingest_controls_from_settings()

    def _on_browse_export(self):
        path = QFileDialog.getExistingDirectory(self, "Select export bundle or exports folder")
        if path:
            self._export_path.setText(path)

    def _on_check_connection(self):
        self._check_status.setText("Checking…")
        self._check_btn.setEnabled(False)
        # Invalidate prior guided cache until Ping succeeds
        self._guided_prefetch_gen += 1
        self._guided_prefetch_waiting_gen = None
        self._guided_catalog = None
        self._guided_prefetch_error = None
        self._guided_prefetch_pending = False
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
        set_semantic_role(self._check_status, "success" if all_ok else "warning")

        self._telemetry_connected = all_ok
        self._ingest_mode_combo.setEnabled(all_ok)
        if not all_ok:
            # Keep Default usable; Custom needs a healthy stack for discovery
            self._ingest_mode_combo.blockSignals(True)
            self._ingest_mode_combo.setCurrentIndex(0)
            self._ingest_mode_combo.blockSignals(False)
            self._custom_panel.setVisible(False)
            self._ingest_beta_badge.setVisible(False)
            self._update_ingest_status_strip()
            self._refresh_analyze_guard()
            return

        # Prefetch guided catalog in background (warm Custom panel)
        self._resolved_scope_ns = self._resolved_namespace()
        self._guided_prefetch_pending = True
        self._guided_prefetch_error = None
        self._guided_prefetch_gen += 1
        gen = self._guided_prefetch_gen
        self._guided_prefetch_waiting_gen = gen
        if self._ingest_mode() == "custom":
            self._render_guided_panel()
        self._update_ingest_status_strip()
        self._controller.prefetch_guided_catalog_async(
            self._prom_url.text().strip(),
            self._loki_url.text().strip(),
            self._resolved_scope_ns,
        )

    @Slot(object)
    def _on_guided_catalog_finished(self, payload):
        waiting = self._guided_prefetch_waiting_gen
        if waiting is not None and waiting != self._guided_prefetch_gen:
            return
        self._guided_prefetch_pending = False
        if not isinstance(payload, dict):
            self._guided_prefetch_error = "Unexpected discovery payload"
            self._guided_catalog = None
        elif not payload.get("ok"):
            self._guided_prefetch_error = str(payload.get("error") or "discovery failed")
            self._guided_catalog = None
        else:
            self._guided_prefetch_error = None
            catalog = payload.get("catalog")
            self._guided_catalog = catalog if isinstance(catalog, GuidedCatalog) else None
            if self._guided_catalog is None:
                self._guided_prefetch_error = "Empty discovery catalog"
        if self._ingest_mode() == "custom":
            self._render_guided_panel()
        self._update_ingest_status_strip()
        self._refresh_analyze_guard()

    def _on_test_llm(self):
        self._test_llm_btn.setEnabled(False)
        self._llm_test_status.setText("Testing…")
        set_semantic_role(self._llm_test_status, "secondary")
        self._controller.test_llm_async(
            self._provider_combo.currentText(),
            self._model.text().strip() or None,
        )

    @Slot(bool, str)
    def _on_llm_test_finished(self, ok: bool, message: str):
        self._test_llm_btn.setEnabled(True)
        self._llm_test_status.setText(message)
        set_semantic_role(self._llm_test_status, "success" if ok else "danger")
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

            # --- START MODIFICATION ---
            # Persist normalized weights (same as UI %) so YAML sum≈1.0 and
            # load_settings does not warn on every Analyze during demo.
            raw_w = [
                self._w_conf_slider.value() / 100.0,
                self._w_hist_slider.value() / 100.0,
                self._w_cov_slider.value() / 100.0,
                self._w_safe_slider.value() / 100.0,
            ]
            total_w = sum(raw_w) or 1.0
            settings.ranking.weight_confidence = raw_w[0] / total_w
            settings.ranking.weight_historical = raw_w[1] / total_w
            settings.ranking.weight_coverage = raw_w[2] / total_w
            settings.ranking.weight_safety = raw_w[3] / total_w
            # --- END MODIFICATION ---

            settings.safety.max_affected_nodes = self._max_affected_nodes.value()
            settings.safety.blocked_namespaces = [
                ns.strip() for ns in self._blocked_namespaces.text().split(",") if ns.strip()
            ]
            # MODIFIED: do not persist Guided Custom ticks / ingest mode to disk
            save_settings(settings)
        except Exception as e:
            logger.warning("Failed to save tuning settings before analysis: %s", e)

        ns = self._resolved_namespace()
        ingest_mode = self._ingest_mode() if source == "live" else "default"
        custom_queries = None
        if ingest_mode == "custom":
            selected = self._selected_guided_queries()
            if not selected:
                QMessageBox.warning(
                    self,
                    "No metrics selected",
                    "Select at least one metric to proceed.",
                )
                return
            custom_queries = [asdict(q) for q in selected]

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
            telemetry_profile=_ENTERPRISE_PROFILE,
            extra_packs=list(_ENTERPRISE_EXTRAS),
            scope_namespace=ns,
            ingest_mode=ingest_mode,  # type: ignore[arg-type]
            custom_queries=custom_queries,
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
        self._check_btn.setEnabled(True)
        self._dev_options_toggle.setEnabled(True)
        self._dev_options_container.setEnabled(True)
        self._stack.setCurrentIndex(2)
        self._refresh_analyze_guard()
        self._step_label.setText("Step 3 of 3 — Review results")
        self._refresh_execution_context()

        if result:
            window_txt = ""
            if result.window_start and result.window_end:
                window_txt = (
                    f"Window: {result.window_start.strftime('%Y-%m-%d %H:%M')} → "
                    f"{result.window_end.strftime('%Y-%m-%d %H:%M')} UTC "
                    f"({result.lookback_hours:.1f}h)  |  "
                )
            clusters_txt = (
                f"Clusters: {len(result.clusters)}"
                + (f" ({result.clustering_label})" if result.clustering_label else "")
            )
            self._summary_label.setText(
                f"{window_txt}"
                f"Source: {result.source_label}  |  "
                f"Series: {result.metric_series}  |  "
                f"Samples: {result.total_samples}  |  "
                f"Feature windows: {result.feature_rows}  |  "
                f"{clusters_txt}  |  "
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
            # MODIFIED: Layer-1 short labels; full names in detail pane
            feats = ", ".join(
                f"{_shorten_feature(n)} ({v:.2f})" for n, v in s.top_features[:2]
            )
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
            approve_btn.setObjectName("btnSuccess")
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
        if self._syncing_selection or not self._current_report or row < 0:
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
        self._sync_select_description(data["cluster"])
        self._sync_select_anomaly_by_cluster(data["cluster"])

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
        if self._syncing_selection:
            return
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
        cluster = str(desc.source_incident_id)
        self._sync_select_gatekeeper(cluster)
        self._sync_select_anomaly_by_cluster(cluster)

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
            from chaosgen.advisor.report_store import load_verdict_report
            from chaosgen.schemas.scenarios import ExperimentVerdict

            try:
                last_verdict = load_verdict_report().verdict
            except Exception:
                last_verdict = None
            if last_verdict is None:
                QMessageBox.warning(
                    self,
                    "Cannot promote",
                    "No ExpectationVerdictReport found. Run inject/verdict first "
                    "(promote requires PASS).",
                )
                return
            if last_verdict != ExperimentVerdict.PASS:
                QMessageBox.warning(
                    self,
                    "Cannot promote",
                    f"Expectation Verdict is {last_verdict.value}; only PASS can promote.",
                )
                return

            from chaosgen.config.connect_routing import architecture_from_settings
            from chaosgen.config.settings import load_settings

            promote_arch = architecture_from_settings(load_settings())
            entry = CatalogPromoter(history_store=get_default_history_store()).promote(
                desc,
                dialog.selected_experiment(),
                approved_by=dialog.approved_by(),
                verdict=last_verdict,
                acceptance_criteria=dialog.acceptance_criteria(),
                name=dialog.catalog_name(),
                description_row_id=description_row_id,
                architecture=promote_arch,
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
        self._check_btn.setEnabled(True)
        self._dev_options_toggle.setEnabled(True)
        self._dev_options_container.setEnabled(True)
        self._go_step1()
        self._refresh_analyze_guard()
        QMessageBox.critical(self, "Analysis error", error_msg)

    def _on_anomaly_selected(self, row, _col, _prev_row, _prev_col):
        if self._syncing_selection or not self._current_result or row < 0:
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
        if s.source_cluster_id is not None:
            cluster = str(s.source_cluster_id)
            self._sync_select_gatekeeper(cluster)
            self._sync_select_description(cluster)

    def _sync_select_gatekeeper(self, cluster: str) -> None:
        if not self._current_report:
            return
        rows = gatekeeper_rows(self._current_report)
        for i, row in enumerate(rows):
            if row["cluster"] == cluster:
                self._syncing_selection = True
                try:
                    self._gatekeeper_table.selectRow(i)
                finally:
                    self._syncing_selection = False
                return

    def _sync_select_description(self, cluster: str) -> None:
        for i, desc in enumerate(self._current_descriptions):
            if str(desc.source_incident_id) == cluster:
                self._syncing_selection = True
                try:
                    self._descriptions_table.selectRow(i)
                    block = promote_blocked_reason(desc)
                    self._promote_btn.setEnabled(block is None)
                    self._promote_btn.setToolTip(block or "Promote this described incident to the catalog")
                finally:
                    self._syncing_selection = False
                return

    def _sync_select_anomaly_by_cluster(self, cluster: str) -> None:
        if not self._current_result:
            return
        for i, s in enumerate(self._current_result.summaries):
            if s.source_cluster_id is not None and str(s.source_cluster_id) == cluster:
                self._syncing_selection = True
                try:
                    self._anomaly_table.selectRow(i)
                finally:
                    self._syncing_selection = False
                return

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self._resize_layout_timer.start()

    def hideEvent(self, event):
        self._persist_splitter_sizes()
        super().hideEvent(event)

    def _settings(self) -> QSettings:
        return QSettings(_SETTINGS_ORG, _SETTINGS_APP)

    def _persist_splitter_sizes(self) -> None:
        s = self._settings()
        s.setValue("evidence_sizes", self._evidence_splitter.sizes())
        s.setValue("triage_sizes", self._triage_splitter.sizes())
        s.setValue("outer_sizes", self._outer_splitter.sizes())

    def _restore_splitter_sizes(self) -> None:
        s = self._settings()
        for key, splitter in (
            ("evidence_sizes", self._evidence_splitter),
            ("triage_sizes", self._triage_splitter),
            ("outer_sizes", self._outer_splitter),
        ):
            sizes = s.value(key)
            if isinstance(sizes, list) and sizes and all(int(x) > 0 for x in sizes):
                splitter.setSizes([int(x) for x in sizes])

    def _apply_composite_layout(self) -> None:
        """Orientation switch for laptop/demo widths — one axis per composite."""
        width = self._tabs.width() if self._tabs.width() > 0 else self.width()
        narrow = width < _COMPOSITE_WIDTH_BREAKPOINT
        if self._composite_narrow is not None and narrow == self._composite_narrow:
            return
        self._composite_narrow = narrow
        # Evidence: always vertical (table above timeline)
        if self._evidence_splitter.orientation() != Qt.Vertical:
            self._evidence_splitter.setOrientation(Qt.Vertical)
            self._evidence_splitter.setStretchFactor(0, 2)
            self._evidence_splitter.setStretchFactor(1, 1)
        # Triage: horizontal when wide, vertical when narrow
        desired = Qt.Vertical if narrow else Qt.Horizontal
        if self._triage_splitter.orientation() != desired:
            self._triage_splitter.setOrientation(desired)
            if narrow:
                self._triage_splitter.setStretchFactor(0, 3)
                self._triage_splitter.setStretchFactor(1, 2)
            else:
                self._triage_splitter.setStretchFactor(0, 1)
                self._triage_splitter.setStretchFactor(1, 1)
        # Very small shell: collapse secondary panes by default once
        if width <= 1000 and self._timeline_widget.isVisible():
            self._timeline_widget.setVisible(False)
            self._toggle_timeline_btn.setText("Show timeline")

    def _toggle_timeline_pane(self) -> None:
        visible = self._timeline_widget.isVisible()
        self._timeline_widget.setVisible(not visible)
        self._toggle_timeline_btn.setText("Show timeline" if visible else "Hide timeline")

    def _toggle_descriptions_pane(self) -> None:
        pane = self._descriptions_table.parentWidget()
        if pane is None:
            return
        visible = pane.isVisible()
        pane.setVisible(not visible)
        self._toggle_descriptions_btn.setText(
            "Show descriptions" if visible else "Hide descriptions"
        )

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

    def _refresh_execution_context(self) -> None:
        """Show inject/connect facts Approve will use — or admit undetermined."""
        # --- START MODIFICATION ---
        from chaosgen.config.settings import load_settings
        from chaosgen.gui.execution_context import format_execution_context

        try:
            text = format_execution_context(load_settings())
        except Exception as exc:
            text = f"Execution environment not determined ({exc})."
        self._exec_context_label.setText(text)
        lowered = text.lower()
        if "not determined" in lowered:
            set_semantic_role(self._exec_context_label, "warning")
        else:
            set_semantic_role(self._exec_context_label, "muted")
        # --- END MODIFICATION ---

    def _set_approve_buttons_enabled(self, enabled: bool) -> None:
        for row in range(self._results_table.rowCount()):
            widget = self._results_table.cellWidget(row, 3)
            if widget is not None:
                widget.setEnabled(enabled)

    def _on_approve(self, index: int):
        self._refresh_execution_context()
        # MODIFIED: clear prior outcome so demo does not show stale PASS/FAIL
        self._approve_outcome_label.setVisible(False)
        self._approve_outcome_label.setText("")
        self._set_approve_buttons_enabled(False)
        if not self._controller.approve_and_run(index):
            self._set_approve_buttons_enabled(True)
            return
        self._controller.log_message.emit(f"Approved scenario index={index}")

    def _on_approve_experiment_finished(self, ok: bool, message: str) -> None:
        """Surface Approve PASS/FAIL on Step 3 (toast + bottom log alone are easy to miss)."""
        self._set_approve_buttons_enabled(True)
        if self._stack.currentIndex() != 2:
            return
        verdict = "PASS" if ok else "FAIL"
        self._approve_outcome_label.setText(f"Last Approve: {verdict} — {message}")
        self._approve_outcome_label.setVisible(True)
        set_semantic_role(self._approve_outcome_label, "success" if ok else "danger")

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
