"""
Discovery View — hybrid system environment and architecture scan.

Reads hints from settings.yaml, runs the hybrid discovery pipeline, and
displays results as status cards with probe outcome badges and mismatch warnings.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class _ScanWorker(QThread):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, config_path: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_path = config_path

    def run(self) -> None:
        try:
            from chaosgen.config.settings import load_settings
            from chaosgen.discovery import run_full_discovery

            settings = load_settings(self._config_path)
            report = run_full_discovery(settings=settings)
            self.finished.emit(report)
        except Exception as exc:
            logger.exception("Discovery scan failed")
            self.error.emit(str(exc))


class _BootstrapWorker(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, report, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._report = report

    def run(self) -> None:
        try:
            from chaosgen.bootstrap import ObservabilityInstaller
            installer = ObservabilityInstaller(
                env_profile=self._report.environment,
                obs_profile=self._report.observability,
            )
            actions = installer.install()
            self.finished.emit(actions)
        except Exception as exc:
            logger.exception("Bootstrap failed")
            self.error.emit(str(exc))


class DiscoveryView(QWidget):
    """
    Discovery & Bootstrap view.

    Sidebar nav item: "Discovery"
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._report = None
        self._worker: QThread | None = None
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("System Discovery")
        title.setObjectName("viewTitle")
        root.addWidget(title)

        subtitle = QLabel(
            "Hybrid discovery: reads hints from settings.yaml, probes endpoints "
            "with auth, and merges user overrides with auto-detection."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("viewSubtitle")
        root.addWidget(subtitle)

        # --- Action buttons ---
        btn_row = QHBoxLayout()
        self._scan_btn = QPushButton("Scan System")
        self._scan_btn.setObjectName("primaryButton")
        self._scan_btn.clicked.connect(self._on_scan)
        btn_row.addWidget(self._scan_btn)

        self._bootstrap_btn = QPushButton("Fix Missing Observability")
        self._bootstrap_btn.setObjectName("warningButton")
        self._bootstrap_btn.setEnabled(False)
        self._bootstrap_btn.clicked.connect(self._on_bootstrap)
        btn_row.addWidget(self._bootstrap_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # --- Result cards ---
        cards_row = QHBoxLayout()
        self._env_card = self._make_card("Environment", "-")
        self._arch_card = self._make_card("Architecture", "-")
        self._obs_card = self._make_card("Observability", "-")
        cards_row.addWidget(self._env_card)
        cards_row.addWidget(self._arch_card)
        cards_row.addWidget(self._obs_card)
        root.addLayout(cards_row)

        # --- Warnings panel ---
        self._warnings_group = QGroupBox("Warnings")
        self._warnings_layout = QVBoxLayout(self._warnings_group)
        self._warnings_label = QLabel("")
        self._warnings_label.setWordWrap(True)
        self._warnings_layout.addWidget(self._warnings_label)
        self._warnings_group.setVisible(False)
        root.addWidget(self._warnings_group)

        # --- Service map panel ---
        svc_group = QGroupBox("Service Map")
        svc_layout = QVBoxLayout(svc_group)
        self._svc_text = QTextEdit()
        self._svc_text.setReadOnly(True)
        self._svc_text.setPlaceholderText("Run a scan to view the service dependency map.")
        self._svc_text.setMaximumHeight(160)
        svc_layout.addWidget(self._svc_text)
        root.addWidget(svc_group)

        # --- Scan log ---
        log_group = QGroupBox("Scan Log")
        log_layout = QVBoxLayout(log_group)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        log_layout.addWidget(self._log)
        root.addWidget(log_group)

        root.addStretch()

    @staticmethod
    def _make_card(title: str, value: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("discoveryCard")
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(frame)
        label_title = QLabel(title)
        label_title.setObjectName("cardTitle")
        label_value = QLabel(value)
        label_value.setObjectName("cardValue")
        label_value.setWordWrap(True)
        layout.addWidget(label_title)
        layout.addWidget(label_value)
        frame.setProperty("_value_label", label_value)
        return frame

    @staticmethod
    def _set_card_value(card: QFrame, value: str, ok: bool = True) -> None:
        lbl: QLabel = card.property("_value_label")
        if lbl:
            lbl.setText(value)
            lbl.setStyleSheet("color: #4ade80;" if ok else "color: #f87171;")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("Scanning...")
        self._log.clear()
        self._log.append("Starting hybrid discovery scan...")
        self._warnings_group.setVisible(False)

        self._worker = _ScanWorker(parent=self)
        self._worker.finished.connect(self._on_scan_complete)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_scan_complete(self, report) -> None:
        from chaosgen.schemas.discovery import ProbeOutcome

        self._report = report
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("Scan System")

        env = report.environment
        arch = report.architecture
        obs = report.observability
        smap = report.service_map

        # --- Env card ---
        env_source = "auto"
        for s in report.signals:
            if s.key == "environment_type":
                env_source = s.source.value
                break
        self._set_card_value(
            self._env_card,
            f"{env.type.value} [{env_source}]\n"
            + (f"cloud={env.cloud_provider}\n" if env.cloud_provider else "")
            + (f"nodes={env.node_count}" if env.node_count else ""),
            ok=True,
        )

        # --- Arch card ---
        arch_source = "auto"
        for s in report.signals:
            if s.key == "architecture_type":
                arch_source = s.source.value
                break
        self._set_card_value(
            self._arch_card,
            f"{arch.type.value} [{arch_source}]\n"
            f"services={arch.service_count}\n"
            f"confidence={arch.confidence:.0%}\n"
            f"broker={'yes' if arch.has_message_broker else 'no'}",
            ok=arch.confidence >= 0.6,
        )

        # --- Obs card with probe outcomes ---
        obs_lines = []
        for tool_label, has, endpoint in [
            ("metrics", obs.has_metrics, obs.metrics_endpoint),
            ("logs", obs.has_logs, obs.logs_endpoint),
            ("traces", obs.has_traces, obs.traces_endpoint),
        ]:
            mark = "OK" if has else "MISSING"
            obs_lines.append(f"{tool_label}: {mark}")

        auth_issues = [
            s for s in report.signals if s.probe_outcome == ProbeOutcome.AUTH_REJECTED
        ]
        for s in auth_issues:
            obs_lines.append(f"{s.key}: AUTH REJECTED")

        missing_str = ", ".join(t.value for t in obs.missing) or "none"
        obs_lines.append(f"missing: {missing_str}")

        self._set_card_value(self._obs_card, "\n".join(obs_lines), ok=obs.telemetry_ready)

        # --- Warnings ---
        warning_texts = []
        mismatch = [s for s in report.signals if s.flag == "USER_HEURISTIC_MISMATCH"]
        for s in mismatch:
            warning_texts.append(f"MISMATCH: {s.message}")
        for s in auth_issues:
            warning_texts.append(f"AUTH: {s.key} - {s.message}")

        if warning_texts:
            self._warnings_label.setText("\n".join(warning_texts))
            self._warnings_label.setStyleSheet("color: #fbbf24;")
            self._warnings_group.setVisible(True)

        # --- Service map ---
        if smap.nodes:
            lines = [f"Nodes ({len(smap.nodes)}):"]
            for n in smap.nodes[:15]:
                lines.append(f"  - {n.name} [{n.node_type}]")
            if smap.edges:
                lines.append(f"Edges ({len(smap.edges)}):")
                for e in smap.edges[:10]:
                    lines.append(f"  {e.source} -> {e.target}")
            self._svc_text.setPlainText("\n".join(lines))
        else:
            self._svc_text.setPlainText("No service map data available.")

        for err in report.discovery_errors:
            self._log.append(f"WARNING: {err}")

        # Log signals summary
        for s in report.signals:
            self._log.append(f"  [{s.source.value}] {s.key}: {s.value}")

        self._log.append("Scan complete.")
        self._bootstrap_btn.setEnabled(bool(obs.missing))

    def _on_scan_error(self, msg: str) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("Scan System")
        self._log.append(f"ERROR: {msg}")

    def _on_bootstrap(self) -> None:
        if self._report is None:
            return
        self._bootstrap_btn.setEnabled(False)
        self._bootstrap_btn.setText("Installing...")
        self._log.append("Starting observability bootstrap...")

        worker = _BootstrapWorker(report=self._report, parent=self)
        worker.finished.connect(self._on_bootstrap_done)
        worker.error.connect(self._on_bootstrap_error)
        worker.start()

    def _on_bootstrap_done(self, actions: list) -> None:
        self._bootstrap_btn.setEnabled(True)
        self._bootstrap_btn.setText("Fix Missing Observability")
        for action in actions:
            self._log.append(f"OK: {action}")
        self._log.append("Bootstrap complete. Re-scan to verify.")

    def _on_bootstrap_error(self, msg: str) -> None:
        self._bootstrap_btn.setEnabled(True)
        self._bootstrap_btn.setText("Fix Missing Observability")
        self._log.append(f"Bootstrap ERROR: {msg}")

    def get_report(self):
        return self._report
