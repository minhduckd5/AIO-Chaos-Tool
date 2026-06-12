"""
Dashboard view -- overview cards, module health grid, recent history.
Placeholder implementation for Phase 3 build-out.
"""

from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QSizePolicy, QScrollArea, QFrame,
)

from chaosgen.gui.theme import Colors, Fonts, Spacing, Radius
from chaosgen.gui.widgets.status_dot import StatusDot


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
        self._value_label.setObjectName("cardValue")
        self._value_label.setStyleSheet(f"color: {color};")
        layout.addWidget(self._value_label)

        self._text_label = QLabel(label)
        self._text_label.setObjectName("cardLabel")
        layout.addWidget(self._text_label)

    def set_value(self, value: str):
        self._value_label.setText(value)


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
        label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_NORMAL}px;")
        layout.addWidget(label)
        layout.addStretch()


class DashboardView(QWidget):
    """Main dashboard with summary cards and module health overview."""

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
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(Spacing.XL, Spacing.XL, Spacing.XL, Spacing.XL)
        layout.setSpacing(Spacing.LG)

        # Page header
        title = QLabel("Dashboard")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Microservices-focused pipeline — use Telemetry for live/export analysis"
        )
        subtitle.setObjectName("sectionSubtitle")
        layout.addWidget(subtitle)

        # Summary cards row
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(Spacing.LG)

        self._state_card = SummaryCard("Orchestrator State", "IDLE", Colors.SUCCESS)
        self._modules_card = SummaryCard("Active Modules", "0", Colors.ACCENT)
        self._experiments_card = SummaryCard("Experiments Run", "0", Colors.WARNING)
        self._pending_card = SummaryCard("Pending Approval", "0", Colors.DANGER)

        for card in (self._state_card, self._modules_card, self._experiments_card, self._pending_card):
            cards_layout.addWidget(card)

        layout.addLayout(cards_layout)

        # Module health section
        health_header = QLabel("Module Health")
        health_header.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_LARGE}px; "
            f"font-weight: bold; margin-top: {Spacing.MD}px;"
        )
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

        self._rebuild_health_grid(modules)

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
            empty.setStyleSheet(f"color: {Colors.TEXT_MUTED}; padding: {Spacing.MD}px;")
            self._health_layout.addWidget(empty)

    @Slot(str)
    def _on_state_changed(self, state):
        self._state_card.set_value(state.upper())

    @Slot(dict)
    def _on_status_refreshed(self, data):
        for name, status in data.items():
            pass  # Phase 3: update individual module dots
