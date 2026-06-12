"""
MainWindow -- ChaosGen application shell using qfluentwidgets NavigationInterface.
"""

import sys
import logging

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QIcon, QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QLabel, QSizePolicy,
)

from qfluentwidgets import (
    NavigationInterface, NavigationItemPosition, NavigationWidget,
    FluentIcon, Theme, setTheme, setThemeColor, isDarkTheme,
)
from qframelesswindow import FramelessWindow

from chaosgen.gui.controller import AppController
from chaosgen.gui.theme import Colors, Fonts, Spacing, build_global_stylesheet
from chaosgen.gui.widgets.log_console import LogConsole
from chaosgen.gui.widgets.status_dot import StatusDot

from chaosgen.gui.views.dashboard import DashboardView
from chaosgen.gui.views.modules_view import ModulesView
from chaosgen.gui.views.experiments_view import ExperimentsView
from chaosgen.gui.views.advisor_view import AdvisorView
from chaosgen.gui.views.evaluation_view import EvaluationView
from chaosgen.config.scope import DISCOVERY_ENABLED
from chaosgen.gui.views.discovery_view import DiscoveryView
from chaosgen.gui.views.scenario_catalog_view import ScenarioCatalogView
from chaosgen.gui.views.settings_view import SettingsView

# Page index constants
_PAGE_DASHBOARD = 0
_PAGE_MODULES = 1
_PAGE_EXPERIMENTS = 2
_PAGE_ADVISOR = 3
_PAGE_EVALUATION = 4
_PAGE_DISCOVERY = 5
_PAGE_CATALOG = 6
_PAGE_SETTINGS = 7


class MainWindow(FramelessWindow):
    """ChaosGen frameless window with sidebar navigation."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChaosGen — AI-Driven Chaos Scenario Generator")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 600)

        # Apply qfluentwidgets dark theme with Lens accent
        setTheme(Theme.DARK)
        setThemeColor(QColor(Colors.ACCENT))

        # Core controller
        self.controller = AppController()

        self._init_ui()
        self._connect_signals()
        self._init_status_polling()

    def _init_ui(self):
        # Root layout: full window
        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Navigation sidebar (qfluentwidgets)
        self._nav = NavigationInterface(self, showMenuButton=False, showReturnButton=False)
        self._nav.setFixedWidth(48)
        self._nav.setExpandWidth(200)
        self._nav.setStyleSheet(
            f"NavigationInterface {{ background-color: {Colors.ACTIVITY_BAR}; "
            f"border-right: 1px solid {Colors.BORDER}; }}"
        )

        # Right side: content + log + status
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Title bar area (for frameless window drag)
        self._title_bar = self._build_title_bar()
        right_layout.addWidget(self._title_bar)

        # Stacked content area
        self._stack = QStackedWidget()
        self._stack.setObjectName("mainContent")

        self._dashboard = DashboardView(self.controller)
        self._modules_view = ModulesView(self.controller)
        self._experiments_view = ExperimentsView(self.controller)
        self._advisor_view = AdvisorView(self.controller)
        self._evaluation_view = EvaluationView(self.controller)
        self._discovery_view = DiscoveryView()
        self._catalog_view = ScenarioCatalogView()
        self._settings_view = SettingsView()

        self._stack.addWidget(self._dashboard)       # 0
        self._stack.addWidget(self._modules_view)    # 1
        self._stack.addWidget(self._experiments_view)  # 2
        self._stack.addWidget(self._advisor_view)    # 3
        self._stack.addWidget(self._evaluation_view) # 4
        self._stack.addWidget(self._discovery_view)  # 5
        self._stack.addWidget(self._catalog_view)    # 6
        self._stack.addWidget(self._settings_view)   # 7

        # Wire catalog "Add to Queue" → orchestrator pending queue
        self._catalog_view.scenario_queued.connect(self._on_catalog_scenario_queued)

        right_layout.addWidget(self._stack, stretch=1)

        # Log console (collapsible)
        self._log_console = LogConsole()
        self._log_console.setFixedHeight(200)
        right_layout.addWidget(self._log_console)

        # Status bar
        self._status_bar = self._build_status_bar()
        right_layout.addWidget(self._status_bar)

        root.addWidget(self._nav)
        root.addWidget(right_panel)

        # Set central widget
        central = QWidget()
        central.setLayout(root)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(central)

        # Register navigation items
        self._setup_navigation()

        # Apply global stylesheet overlay
        self.setStyleSheet(build_global_stylesheet())

    def _build_title_bar(self) -> QWidget:
        bar = _DraggableTitleBar(self)
        bar.setObjectName("breadcrumbBar")
        bar.setFixedHeight(36)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Spacing.LG, 0, Spacing.LG, 0)

        self._page_title = QLabel("Dashboard")
        self._page_title.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_NORMAL}px; font-weight: bold;"
        )
        layout.addWidget(self._page_title)
        layout.addStretch()

        # Window controls (minimize, maximize, close) for frameless
        for text, slot in [("—", self.showMinimized), ("□", self._toggle_max), ("✕", self.close)]:
            btn = QLabel(text)
            btn.setObjectName("windowControl")
            btn.setFixedSize(36, 36)
            btn.setAlignment(Qt.AlignCenter)
            btn.setStyleSheet(
                f"QLabel {{ color: {Colors.TEXT_SECONDARY}; font-size: 14px; }}"
                f"QLabel:hover {{ color: {Colors.TEXT_PRIMARY}; background-color: {Colors.BG_HOVER}; }}"
            )
            btn.mousePressEvent = lambda _, s=slot: s()
            layout.addWidget(btn)

        return bar

    def _toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("statusBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Spacing.SM, 0, Spacing.SM, 0)

        self._state_dot = StatusDot("idle", size=8)
        layout.addWidget(self._state_dot)

        self._state_label = QLabel("IDLE")
        self._state_label.setObjectName("statusText")
        layout.addWidget(self._state_label)

        sep = QLabel("|")
        sep.setStyleSheet(f"color: {Colors.BORDER}; padding: 0 4px;")
        layout.addWidget(sep)

        self._module_count_label = QLabel("0 modules")
        self._module_count_label.setObjectName("statusText")
        layout.addWidget(self._module_count_label)

        layout.addStretch()

        version_label = QLabel("ChaosGen v0.2.0")
        version_label.setObjectName("statusText")
        layout.addWidget(version_label)

        return bar

    def _setup_navigation(self):
        """Register views in the qfluentwidgets NavigationInterface."""
        self._nav.addItem(
            routeKey="dashboard",
            icon=FluentIcon.HOME,
            text="Dashboard",
            onClick=lambda: self._switch_page(_PAGE_DASHBOARD, "Dashboard"),
        )
        # MODIFIED: Discovery nav hidden while scope is microservices-only.
        if DISCOVERY_ENABLED:
            self._nav.addItem(
                routeKey="discovery",
                icon=FluentIcon.SEARCH,
                text="Discovery",
                onClick=lambda: self._switch_page(_PAGE_DISCOVERY, "System Discovery"),
            )
        self._nav.addItem(
            routeKey="catalog",
            icon=FluentIcon.LIBRARY,
            text="Scenario Catalog",
            onClick=lambda: self._switch_page(_PAGE_CATALOG, "Scenario Catalog"),
        )
        self._nav.addItem(
            routeKey="advisor",
            icon=FluentIcon.ROBOT,
            text="Telemetry",
            onClick=lambda: self._switch_page(_PAGE_ADVISOR, "Telemetry & Advisor"),
        )
        self._nav.addItem(
            routeKey="experiments",
            icon=FluentIcon.PLAY,
            text="Experiments",
            onClick=lambda: self._switch_page(_PAGE_EXPERIMENTS, "Experiments"),
        )
        self._nav.addItem(
            routeKey="modules",
            icon=FluentIcon.DEVELOPER_TOOLS,
            text="Modules",
            onClick=lambda: self._switch_page(_PAGE_MODULES, "Modules"),
        )
        self._nav.addItem(
            routeKey="evaluation",
            icon=FluentIcon.PIE_SINGLE,
            text="Evaluation",
            onClick=lambda: self._switch_page(_PAGE_EVALUATION, "Evaluation"),
            position=NavigationItemPosition.BOTTOM,
        )
        self._nav.addItem(
            routeKey="settings",
            icon=FluentIcon.SETTING,
            text="Settings",
            onClick=lambda: self._switch_page(_PAGE_SETTINGS, "Settings"),
            position=NavigationItemPosition.BOTTOM,
        )

        # Set default
        self._nav.setCurrentItem("dashboard")

    def _switch_page(self, index: int, title: str):
        self._stack.setCurrentIndex(index)
        self._page_title.setText(title)

    def _connect_signals(self):
        self._settings_view.settings_saved.connect(self._advisor_view.refresh_credentials)
        self.controller.log_message.connect(self._log_console.append_log)
        self.controller.state_changed.connect(self._update_state_bar)
        self.controller.experiment_started.connect(
            lambda name: self._log_console.append_log(f"Experiment started: {name}")
        )
        self.controller.experiment_finished.connect(
            lambda ok, msg: self._log_console.append_log(f"Experiment {'passed' if ok else 'failed'}: {msg}")
        )

    def _init_status_polling(self):
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_state)
        self._poll_timer.start(1000)

    def _poll_state(self):
        state = self.controller.state
        self._update_state_bar(state)
        module_count = len(self.controller.list_modules())
        self._module_count_label.setText(f"{module_count} modules")

    def _update_state_bar(self, state: str):
        state_upper = state.upper()
        self._state_label.setText(state_upper)
        dot_map = {
            "idle": "idle",
            "pending_approval": "degraded",
            "steady_state_check": "running",
            "injecting": "running",
            "verifying": "running",
            "rollback": "critical",
        }
        self._state_dot.set_state(dot_map.get(state, "unknown"))


    def _on_catalog_scenario_queued(self, experiment) -> None:
        """Push a catalog scenario into the orchestrator's pending approval queue."""
        try:
            self.controller.submit_catalog_experiment(experiment)
            self._switch_page(_PAGE_EXPERIMENTS, "Experiments")
            self._log_console.append_log(f"Catalog scenario queued: {experiment.name}")
        except Exception as exc:
            self._log_console.append_log(f"Error queuing scenario: {exc}")


def run_gui():
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("ChaosGen")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


class _DraggableTitleBar(QWidget):
    """
    Draggable title bar region for FramelessWindow.

    We intentionally implement manual dragging rather than using qframelesswindow.TitleBar,
    because this title bar is laid out inside the main UI (to the right of the nav rail).
    """

    def __init__(self, window: FramelessWindow):
        super().__init__(window)
        self._window = window
        self._dragging = False
        self._drag_offset = None

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)

        child = self.childAt(event.pos())
        if isinstance(child, QWidget) and child.objectName() == "windowControl":
            return super().mousePressEvent(event)

        # Start dragging
        self._dragging = True
        self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging or self._drag_offset is None:
            return super().mouseMoveEvent(event)

        if event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._drag_offset = None
        return super().mouseReleaseEvent(event)


if __name__ == "__main__":
    run_gui()
