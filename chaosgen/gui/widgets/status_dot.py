"""
StatusDot -- small painted circle indicating health state.
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget

from chaosgen.gui.theme import Colors


class StatusDot(QWidget):
    """8px painted circle: green / amber / red / grey."""

    COLOR_MAP = {
        "healthy": Colors.SUCCESS,
        "degraded": Colors.WARNING,
        "critical": Colors.DANGER,
        "unknown": Colors.TEXT_MUTED,
        "idle": Colors.TEXT_SECONDARY,
        "running": Colors.ACCENT,
    }

    def __init__(self, state: str = "unknown", size: int = 10, parent=None):
        super().__init__(parent)
        self._state = state
        self._dot_size = size
        self.setFixedSize(QSize(size + 4, size + 4))

    def set_state(self, state: str):
        self._state = state
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color_hex = self.COLOR_MAP.get(self._state, Colors.TEXT_MUTED)
        color = QColor(color_hex)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        x = (self.width() - self._dot_size) // 2
        y = (self.height() - self._dot_size) // 2
        painter.drawEllipse(x, y, self._dot_size, self._dot_size)
        painter.end()
