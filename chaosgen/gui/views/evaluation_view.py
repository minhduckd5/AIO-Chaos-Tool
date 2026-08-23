"""
Evaluation view — operational outcome for business stakeholders.

A/B thesis KPIs remain available via CLI: ``chaosgen evaluate --ab``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QTextEdit, QGroupBox,
)

from chaosgen.gui.theme import Colors, Fonts, Spacing
from chaosgen.schemas.scenarios import ExperimentVerdict


class EvaluationView(QWidget):
    """Stakeholder operational verdict: claim → outcome → where to improve."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._init_ui()
        self._refresh_operational()

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

        subtitle = QLabel(
            "After a chaos test: did the system meet the business claim, "
            "and where should we improve?"
        )
        subtitle.setObjectName("sectionSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # --- START MODIFICATION ---
        # Operational outcome only — A/B research appendix removed from GUI
        # --- END MODIFICATION ---
        ops_box = QGroupBox("Operational outcome (for business stakeholders)")
        ops_box.setStyleSheet(
            f"QGroupBox {{ color: {Colors.TEXT_PRIMARY}; font-weight: bold; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 8px; margin-top: 12px; "
            f"padding-top: 12px; background-color: {Colors.BG_CARD}; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}"
        )
        ops_layout = QVBoxLayout(ops_box)
        ops_layout.setSpacing(Spacing.MD)

        status_row = QHBoxLayout()
        self._ops_status = QLabel("No result yet")
        self._ops_status.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: {Fonts.SIZE_TITLE}px; font-weight: bold;"
        )
        status_row.addWidget(self._ops_status)
        status_row.addStretch()
        refresh_ops = QPushButton("Refresh outcome")
        refresh_ops.setStyleSheet(
            f"QPushButton {{ background-color: {Colors.ACCENT}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {Colors.ACCENT_HOVER}; }}"
        )
        refresh_ops.clicked.connect(self._refresh_operational)
        status_row.addWidget(refresh_ops)
        ops_layout.addLayout(status_row)

        self._ops_headline = QLabel("")
        self._ops_headline.setWordWrap(True)
        self._ops_headline.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_LARGE}px;"
        )
        ops_layout.addWidget(self._ops_headline)

        claim_lbl = QLabel("What we claimed")
        claim_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-weight: bold;")
        ops_layout.addWidget(claim_lbl)
        self._ops_claim = QTextEdit()
        self._ops_claim.setReadOnly(True)
        self._ops_claim.setMaximumHeight(80)
        self._ops_claim.setStyleSheet(
            f"QTextEdit {{ background: {Colors.BG_SECONDARY}; color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 6px; padding: 8px; }}"
        )
        ops_layout.addWidget(self._ops_claim)

        why_lbl = QLabel("Plain-language summary")
        why_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-weight: bold;")
        ops_layout.addWidget(why_lbl)
        self._ops_rationale = QTextEdit()
        self._ops_rationale.setReadOnly(True)
        self._ops_rationale.setMaximumHeight(100)
        self._ops_rationale.setStyleSheet(
            f"QTextEdit {{ background: {Colors.BG_SECONDARY}; color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 6px; padding: 8px; }}"
        )
        ops_layout.addWidget(self._ops_rationale)

        improve_lbl = QLabel("Where to improve")
        improve_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-weight: bold;")
        ops_layout.addWidget(improve_lbl)
        self._ops_improve = QTextEdit()
        self._ops_improve.setReadOnly(True)
        self._ops_improve.setMinimumHeight(100)
        self._ops_improve.setStyleSheet(
            f"QTextEdit {{ background: {Colors.BG_SECONDARY}; color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 6px; padding: 8px; }}"
        )
        ops_layout.addWidget(self._ops_improve)

        self._ops_meta = QLabel("")
        self._ops_meta.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: {Fonts.SIZE_SMALL}px;"
        )
        self._ops_meta.setWordWrap(True)
        ops_layout.addWidget(self._ops_meta)

        layout.addWidget(ops_box)

        cli_hint = QLabel(
            "Thesis A/B authoring KPIs: use CLI  chaosgen evaluate --ab  "
            "(not shown here — keeps this screen operational)."
        )
        cli_hint.setWordWrap(True)
        cli_hint.setStyleSheet(
            f"color: {Colors.TEXT_MUTED}; font-size: {Fonts.SIZE_SMALL}px;"
        )
        layout.addWidget(cli_hint)
        layout.addStretch()
        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_operational()

    def _refresh_operational(self):
        """Load last ExpectationVerdictReport into stakeholder language."""
        try:
            from chaosgen.advisor.report_store import load_verdict_report

            report = load_verdict_report()
        except FileNotFoundError:
            self._ops_status.setText("No chaos outcome yet")
            self._ops_status.setStyleSheet(
                f"color: {Colors.TEXT_SECONDARY}; font-size: {Fonts.SIZE_TITLE}px; font-weight: bold;"
            )
            self._ops_headline.setText(
                "Run a chaos experiment with expectations (or chaosgen verdict), "
                "then refresh this panel."
            )
            self._ops_claim.setPlainText("")
            self._ops_rationale.setPlainText("")
            self._ops_improve.setPlainText(
                "Tip for demos: use examples/demo-expectation-criteria.yaml, "
                "then open Evaluation after the run."
            )
            self._ops_meta.setText("")
            return
        except Exception as exc:
            self._ops_status.setText("Could not load outcome")
            self._ops_headline.setText(str(exc))
            return

        color = {
            ExperimentVerdict.PASS: Colors.SUCCESS,
            ExperimentVerdict.PARTIAL: Colors.WARNING,
            ExperimentVerdict.FAIL: Colors.DANGER,
        }.get(report.verdict, Colors.TEXT_PRIMARY)

        self._ops_status.setText(report.stakeholder_status_label())
        self._ops_status.setStyleSheet(
            f"color: {color}; font-size: {Fonts.SIZE_TITLE}px; font-weight: bold;"
        )
        self._ops_headline.setText(report.stakeholder_headline())
        self._ops_claim.setPlainText(report.claim.strip())
        self._ops_rationale.setPlainText(report.rationale.strip())
        bullets = report.improvement_notes()
        self._ops_improve.setPlainText("\n".join(f"• {b}" for b in bullets))
        meta_bits = []
        if report.experiment_name:
            meta_bits.append(f"Experiment: {report.experiment_name}")
        if report.evaluated_at:
            meta_bits.append(f"Evaluated: {report.evaluated_at}")
        failed = sum(1 for c in report.checks if not c.passed)
        meta_bits.append(f"Checks: {len(report.checks) - failed} ok / {failed} missed")
        self._ops_meta.setText("  ·  ".join(meta_bits))
