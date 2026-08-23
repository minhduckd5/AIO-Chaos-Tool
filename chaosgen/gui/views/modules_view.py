"""
Modules view -- table of chaos modules with status, actions, detail pane.
Phase 4 placeholder with functional table.
"""

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QTextEdit, QSizePolicy,
    QScrollArea, QFrame, QPushButton, QSplitter,
)

from chaosgen.gui.theme import Colors, Fonts, Spacing
from chaosgen.gui.widgets.status_dot import StatusDot


class ModulesView(QWidget):
    """Table-driven view of all registered chaos modules."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._init_ui()
        self._populate()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.XL, Spacing.XL, Spacing.XL, Spacing.XL)
        layout.setSpacing(Spacing.LG)

        title = QLabel("Modules")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel("Registered chaos engineering tool integrations")
        subtitle.setObjectName("sectionSubtitle")
        layout.addWidget(subtitle)

        hint = QLabel(
            "Inventory of injectors — use Experiments to run, Evaluation to review outcomes."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: {Fonts.SIZE_SMALL}px;")
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Vertical)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Status", "Module Name", "Available Actions"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            f"QTableWidget {{ background-color: {Colors.BG_CARD}; "
            f"border: 1px solid {Colors.BORDER}; gridline-color: {Colors.BORDER}; }}"
            f"QTableWidget::item {{ padding: 6px; }}"
            f"QTableWidget::item:selected {{ background-color: {Colors.BG_SELECTED}; }}"
            f"QHeaderView::section {{ background-color: {Colors.BG_SECONDARY}; "
            f"color: {Colors.TEXT_SECONDARY}; border: none; "
            f"border-bottom: 1px solid {Colors.BORDER}; padding: 6px; font-weight: bold; }}"
        )
        self._table.currentCellChanged.connect(self._on_row_selected)
        splitter.addWidget(self._table)

        # Detail pane
        detail_container = QWidget()
        detail_container.setObjectName("cardWidget")
        detail_layout = QVBoxLayout(detail_container)
        detail_layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)

        detail_header = QLabel("Module Detail")
        detail_header.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: 11px; "
            f"font-weight: bold; letter-spacing: 1px;"
        )
        detail_layout.addWidget(detail_header)

        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setStyleSheet(
            f"background-color: {Colors.BG_INPUT}; color: {Colors.TEXT_PRIMARY}; "
            f"font-family: {Fonts.FAMILY_MONO}; font-size: {Fonts.SIZE_SMALL}px; "
            f"border: none; padding: {Spacing.SM}px;"
        )
        detail_layout.addWidget(self._detail_text)

        splitter.addWidget(detail_container)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter)

    def _populate(self):
        modules = self._controller.list_modules()
        self._table.setRowCount(len(modules))
        for row, name in enumerate(modules):
            dot = StatusDot("idle", size=8)
            dot_container = QWidget()
            dl = QHBoxLayout(dot_container)
            dl.setContentsMargins(8, 0, 8, 0)
            dl.setAlignment(Qt.AlignCenter)
            dl.addWidget(dot)
            self._table.setCellWidget(row, 0, dot_container)

            self._table.setItem(row, 1, QTableWidgetItem(name))

            actions = self._controller.get_module_actions(name)
            self._table.setItem(row, 2, QTableWidgetItem(", ".join(actions) if actions else "—"))

    def _on_row_selected(self, row, col, prev_row, prev_col):
        if row < 0:
            return
        name_item = self._table.item(row, 1)
        if not name_item:
            return
        name = name_item.text()
        actions = self._controller.get_module_actions(name)
        lines = [
            f"Module: {name}",
            f"Actions ({len(actions)}):",
        ]
        for a in actions:
            lines.append(f"  - {a}")
        self._detail_text.setPlainText("\n".join(lines))
