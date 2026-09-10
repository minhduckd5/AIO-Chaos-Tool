"""
Dashboard view — Layer-1 health: what needs attention now?
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QScrollArea, QFrame,
)

from chaosgen.gui.theme import Colors, Spacing, set_semantic_role
from chaosgen.gui.widgets.status_dot import StatusDot

# Map Colors.* hex (legacy call sites) → semantic roles for SummaryCard.
_COLOR_TO_ROLE = {
    Colors.SUCCESS: "success",
    Colors.WARNING: "warning",
    Colors.DANGER: "danger",
    Colors.TEXT_SECONDARY: "secondary",
    Colors.TEXT_PRIMARY: "primary",
    Colors.ACCENT: "accent",
}


class SummaryCard(QWidget):
    """Single metric card with value + label."""

    def __init__(self, label: str, value: str = "—", color: str = Colors.ACCENT, parent=None):
        super().__init__(parent)
        self.setObjectName("cardWidget")
        self.setMinimumSize(180, 100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.XS)

        self._value_label = QLabel(value)
        # MODIFIED: chrome objectName + semanticRole (set_semantic_role does unpolish/polish)
        self._value_label.setObjectName("cardValue")
        layout.addWidget(self._value_label)
        self.set_color(color)

        self._text_label = QLabel(label)
        self._text_label.setObjectName("cardLabel")
        layout.addWidget(self._text_label)

    def set_value(self, value: str):
        self._value_label.setText(value)

    def set_color(self, color: str):
        role = _COLOR_TO_ROLE.get(color, "primary")
        set_semantic_role(self._value_label, role)


class ModuleHealthRow(QWidget):
    """Single row in the module health grid."""

    def __init__(self, name: str, status: str = "unknown", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.SM, Spacing.XS, Spacing.SM, Spacing.XS)
        layout.setSpacing(Spacing.SM)

        self.dot = StatusDot(status, size=8)
        layout.addWidget(self.dot)

        label = QLabel(name)
        label.setObjectName("moduleHealthName")
        layout.addWidget(label)
        layout.addStretch()


class DashboardView(QWidget):
    """Main dashboard: hero KPIs, next-action deep-links, module health."""

    # Emitted with route keys: advisor | experiments | evaluation
    navigate_requested = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._init_ui()
        self._connect_signals()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh)
        self._poll_timer.start(2000)

    def _init_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("transparentScroll")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(Spacing.XL, Spacing.XL, Spacing.XL, Spacing.XL)
        layout.setSpacing(Spacing.LG)

        title = QLabel("Dashboard")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "What needs attention now? — Advise → Run → Review"
        )
        subtitle.setObjectName("sectionSubtitle")
        layout.addWidget(subtitle)

        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(Spacing.LG)

        self._state_card = SummaryCard("Orchestrator State", "IDLE", Colors.SUCCESS)
        self._modules_card = SummaryCard("Active Modules", "0", Colors.ACCENT)
        self._pending_card = SummaryCard("Pending Approval", "0", Colors.DANGER)
        self._verdict_card = SummaryCard("Last Outcome", "—", Colors.TEXT_SECONDARY)

        for card in (
            self._state_card,
            self._modules_card,
            self._pending_card,
            self._verdict_card,
        ):
            cards_layout.addWidget(card)

        layout.addLayout(cards_layout)

        # --- START MODIFICATION ---
        # Next-action deep-links (one page = one decision)
        # --- END MODIFICATION ---
        actions_header = QLabel("Next actions")
        actions_header.setObjectName("subsectionHeader")
        layout.addWidget(actions_header)

        actions = QHBoxLayout()
        actions.setSpacing(Spacing.MD)
        open_telem = QPushButton("Open Telemetry")
        open_telem.setObjectName("btnPrimary")
        open_telem.clicked.connect(lambda: self.navigate_requested.emit("advisor"))
        open_exp = QPushButton("Open Experiments")
        open_exp.setObjectName("btnLink")
        open_exp.clicked.connect(lambda: self.navigate_requested.emit("experiments"))
        open_eval = QPushButton("View Outcome")
        open_eval.setObjectName("btnLink")
        open_eval.clicked.connect(lambda: self.navigate_requested.emit("evaluation"))
        actions.addWidget(open_telem)
        actions.addWidget(open_exp)
        actions.addWidget(open_eval)
        actions.addStretch()
        layout.addLayout(actions)

        health_header = QLabel("Module Health")
        health_header.setObjectName("subsectionHeader")
        layout.addWidget(health_header)

        self._health_container = QWidget()
        self._health_container.setObjectName("cardWidget")
        self._health_layout = QVBoxLayout(self._health_container)
        self._health_layout.setContentsMargins(Spacing.SM, Spacing.SM, Spacing.SM, Spacing.SM)
        self._health_layout.setSpacing(0)
        layout.addWidget(self._health_container)

        layout.addStretch()

        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _connect_signals(self):
        self._controller.state_changed.connect(self._on_state_changed)
        self._controller.status_refreshed.connect(self._on_status_refreshed)

    def _refresh(self):
        state = self._controller.state
        self._state_card.set_value(state.upper())

        modules = self._controller.list_modules()
        self._modules_card.set_value(str(len(modules)))

        pending = self._controller.get_pending_experiments()
        self._pending_card.set_value(str(len(pending)))

        self._refresh_verdict_card()
        self._rebuild_health_grid(modules)

    def _refresh_verdict_card(self):
        try:
            from chaosgen.advisor.report_store import load_verdict_report
            from chaosgen.schemas.scenarios import ExperimentVerdict

            report = load_verdict_report()
            label = report.stakeholder_status_label()
            color = {
                ExperimentVerdict.PASS: Colors.SUCCESS,
                ExperimentVerdict.PARTIAL: Colors.WARNING,
                ExperimentVerdict.FAIL: Colors.DANGER,
            }.get(report.verdict, Colors.TEXT_SECONDARY)
            self._verdict_card.set_value(label)
            self._verdict_card.set_color(color)
        except Exception:
            self._verdict_card.set_value("—")
            self._verdict_card.set_color(Colors.TEXT_SECONDARY)

    def _rebuild_health_grid(self, modules):
        while self._health_layout.count():
            item = self._health_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for name in modules:
            row = ModuleHealthRow(name, "idle")
            self._health_layout.addWidget(row)
        if not modules:
            empty = QLabel("No modules loaded")
            empty.setObjectName("emptyStateHint")
            self._health_layout.addWidget(empty)

    @Slot(str)
    def _on_state_changed(self, state):
        self._state_card.set_value(state.upper())

    @Slot(dict)
    def _on_status_refreshed(self, data):
        pass
