"""
LogConsole -- collapsible bottom panel for execution logs.
"""

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, QPushButton,
    QSizePolicy,
)

from chaosgen.gui.theme import Colors, Fonts, Spacing


class LogConsole(QWidget):
    """Collapsible log console panel with monospace output."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logConsole")
        self._collapsed = False
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setFixedHeight(28)
        header.setStyleSheet(
            f"background-color: {Colors.BG_SECONDARY}; "
            f"border-top: 1px solid {Colors.BORDER}; "
            f"border-bottom: 1px solid {Colors.BORDER};"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(Spacing.SM, 0, Spacing.SM, 0)

        title = QLabel("OUTPUT")
        title.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: 11px; "
            f"font-weight: bold; letter-spacing: 1px;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedHeight(20)
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Colors.TEXT_SECONDARY}; "
            f"border: none; font-size: 11px; padding: 0 6px; }}"
            f"QPushButton:hover {{ color: {Colors.TEXT_PRIMARY}; }}"
        )
        self._clear_btn.clicked.connect(self.clear)
        header_layout.addWidget(self._clear_btn)

        self._toggle_btn = QPushButton("—")
        self._toggle_btn.setFixedSize(20, 20)
        self._toggle_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Colors.TEXT_SECONDARY}; "
            f"border: none; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {Colors.TEXT_PRIMARY}; }}"
        )
        self._toggle_btn.clicked.connect(self.toggle)
        header_layout.addWidget(self._toggle_btn)

        layout.addWidget(header)

        # Text area
        self._text_edit = QTextEdit()
        self._text_edit.setObjectName("logTextEdit")
        self._text_edit.setReadOnly(True)
        self._text_edit.setMinimumHeight(100)
        self._text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._text_edit)

    @Slot(str)
    def append_log(self, message: str):
        self._text_edit.append(message)
        cursor = self._text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._text_edit.setTextCursor(cursor)

    def clear(self):
        self._text_edit.clear()

    def toggle(self):
        self._collapsed = not self._collapsed
        self._text_edit.setVisible(not self._collapsed)
        self._toggle_btn.setText("+" if self._collapsed else "—")
