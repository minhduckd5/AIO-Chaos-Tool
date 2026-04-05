"""
Evaluation view -- KPI summary cards and A/B comparison.
Phase 7 placeholder with functional KPI display.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QScrollArea, QFrame, QMessageBox,
)

from chaosgen.gui.theme import Colors, Fonts, Spacing
from chaosgen.gui.views.dashboard import SummaryCard
from chaosgen.evaluation.kpi_tracker import KPITracker
from chaosgen.evaluation.ab_comparator import ABComparator


class EvaluationView(QWidget):
    """KPI dashboard with A/B comparison and export capabilities."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._kpi_tracker = KPITracker()
        self._comparator = ABComparator(self._kpi_tracker)
        self._init_ui()

    def _init_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(Spacing.XL, Spacing.XL, Spacing.XL, Spacing.XL)
        layout.setSpacing(Spacing.LG)

        title = QLabel("Evaluation")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel("KPI tracking and A/B comparison of AI vs human scenarios")
        subtitle.setObjectName("sectionSubtitle")
        layout.addWidget(subtitle)

        # KPI cards
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(Spacing.LG)
        self._actionability_card = SummaryCard("Actionability Rate", "—", Colors.SUCCESS)
        self._discovery_card = SummaryCard("Discovery Rate", "—", Colors.ACCENT)
        self._time_card = SummaryCard("Avg Design Time", "—", Colors.WARNING)
        self._total_card = SummaryCard("Total Experiments", "0", Colors.TEXT_SECONDARY)
        for c in (self._actionability_card, self._discovery_card, self._time_card, self._total_card):
            cards_layout.addWidget(c)
        layout.addLayout(cards_layout)

        # Comparison table
        comp_header = QLabel("A/B Comparison")
        comp_header.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_LARGE}px; font-weight: bold;"
        )
        layout.addWidget(comp_header)

        self._comp_table = QTableWidget()
        self._comp_table.setColumnCount(3)
        self._comp_table.setHorizontalHeaderLabels(["Metric", "AI", "Human"])
        self._comp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._comp_table.verticalHeader().setVisible(False)
        self._comp_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._comp_table.setStyleSheet(
            f"QTableWidget {{ background-color: {Colors.BG_CARD}; border: 1px solid {Colors.BORDER}; "
            f"gridline-color: {Colors.BORDER}; }}"
            f"QTableWidget::item {{ padding: 6px; }}"
            f"QHeaderView::section {{ background-color: {Colors.BG_SECONDARY}; "
            f"color: {Colors.TEXT_SECONDARY}; border: none; "
            f"border-bottom: 1px solid {Colors.BORDER}; padding: 6px; font-weight: bold; }}"
        )
        layout.addWidget(self._comp_table)

        # Controls
        controls = QHBoxLayout()
        refresh_btn = QPushButton("Refresh KPIs")
        refresh_btn.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.ACCENT}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 20px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {Colors.ACCENT_HOVER}; }}"
        )
        refresh_btn.clicked.connect(self._refresh)
        controls.addWidget(refresh_btn)

        export_btn = QPushButton("Export JSON")
        export_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Colors.ACCENT}; "
            f"border: 1px solid {Colors.ACCENT}; border-radius: 6px; padding: 8px 20px; }}"
        )
        export_btn.clicked.connect(self._export)
        controls.addWidget(export_btn)
        controls.addStretch()
        layout.addLayout(controls)

        layout.addStretch()
        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _refresh(self):
        kpis = self._kpi_tracker.compute_all_kpis()

        ai = kpis.get("ai", {})
        human = kpis.get("human", {})

        self._actionability_card.set_value(f"{ai.get('actionability_rate', 0):.1f}%")
        self._discovery_card.set_value(f"{ai.get('discovery_rate', 0):.2f}")
        self._time_card.set_value(f"{ai.get('time_to_design_minutes', 0):.1f}m")
        self._total_card.set_value(str(ai.get("total_experiments", 0)))

        metrics = [
            ("Actionability Rate", f"{ai.get('actionability_rate', 0):.1f}%", f"{human.get('actionability_rate', 0):.1f}%"),
            ("Discovery Rate", f"{ai.get('discovery_rate', 0):.2f}", f"{human.get('discovery_rate', 0):.2f}"),
            ("Time to Design", f"{ai.get('time_to_design_minutes', 0):.1f}m", f"{human.get('time_to_design_minutes', 0):.1f}m"),
            ("Total Experiments", str(ai.get("total_experiments", 0)), str(human.get("total_experiments", 0))),
        ]

        self._comp_table.setRowCount(len(metrics))
        for i, (name, ai_val, human_val) in enumerate(metrics):
            self._comp_table.setItem(i, 0, QTableWidgetItem(name))
            self._comp_table.setItem(i, 1, QTableWidgetItem(ai_val))
            self._comp_table.setItem(i, 2, QTableWidgetItem(human_val))

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export KPI Report", "kpi_report.json", "JSON (*.json)")
        if path:
            try:
                self._kpi_tracker.export_report(path, format="json")
                QMessageBox.information(self, "Export", f"Report exported to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))
