"""
Lens-inspired dark theme constants and palette overrides for qfluentwidgets.
"""

from enum import Enum
from PySide6.QtGui import QColor


class Colors:
    """Central color palette -- Lens-inspired dark theme."""
    BG_PRIMARY = "#1e1f28"
    BG_SECONDARY = "#252631"
    BG_CARD = "#2a2b3d"
    BG_INPUT = "#1a1b26"
    BG_HOVER = "#32334a"
    BG_SELECTED = "#3b3c55"

    ACCENT = "#3b82f6"
    ACCENT_HOVER = "#60a5fa"
    ACCENT_MUTED = "#2563eb"

    TEXT_PRIMARY = "#e2e8f0"
    TEXT_SECONDARY = "#94a3b8"
    TEXT_MUTED = "#64748b"

    BORDER = "#363748"
    BORDER_FOCUS = "#3b82f6"

    SUCCESS = "#22c55e"
    WARNING = "#f59e0b"
    DANGER = "#ef4444"
    INFO = "#3b82f6"

    ACTIVITY_BAR = "#1a1b24"
    SIDEBAR = "#21222d"
    STATUS_BAR = "#191a23"


class Fonts:
    """Typography constants."""
    FAMILY_UI = "'Segoe UI', 'SF Pro Display', sans-serif"
    FAMILY_MONO = "'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace"
    SIZE_SMALL = 12
    SIZE_NORMAL = 13
    SIZE_LARGE = 15
    SIZE_TITLE = 18
    SIZE_HEADER = 22


class Spacing:
    """Layout spacing constants."""
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32


class Radius:
    """Border radius constants."""
    SM = 4
    MD = 6
    LG = 8
    XL = 12


def build_global_stylesheet() -> str:
    """Build the global QSS overlay applied on top of qfluentwidgets theme."""
    return f"""
    QMainWindow {{
        background-color: {Colors.BG_PRIMARY};
    }}

    QWidget {{
        color: {Colors.TEXT_PRIMARY};
        font-family: {Fonts.FAMILY_UI};
        font-size: {Fonts.SIZE_NORMAL}px;
    }}

    QWidget#activityBar {{
        background-color: {Colors.ACTIVITY_BAR};
        border-right: 1px solid {Colors.BORDER};
    }}

    QWidget#sidebar {{
        background-color: {Colors.SIDEBAR};
        border-right: 1px solid {Colors.BORDER};
    }}

    QWidget#mainContent {{
        background-color: {Colors.BG_PRIMARY};
    }}

    QWidget#statusBar {{
        background-color: {Colors.STATUS_BAR};
        border-top: 1px solid {Colors.BORDER};
        min-height: 28px;
        max-height: 28px;
    }}

    QWidget#breadcrumbBar {{
        background-color: {Colors.BG_SECONDARY};
        border-bottom: 1px solid {Colors.BORDER};
        min-height: 36px;
        max-height: 36px;
    }}

    QWidget#logConsole {{
        background-color: {Colors.BG_INPUT};
        border-top: 1px solid {Colors.BORDER};
    }}

    QWidget#cardWidget {{
        background-color: {Colors.BG_CARD};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.LG}px;
    }}

    QTextEdit#logTextEdit {{
        background-color: {Colors.BG_INPUT};
        color: {Colors.TEXT_PRIMARY};
        font-family: {Fonts.FAMILY_MONO};
        font-size: {Fonts.SIZE_SMALL}px;
        border: none;
        padding: {Spacing.SM}px;
    }}

    QLabel#sectionTitle {{
        color: {Colors.TEXT_PRIMARY};
        font-size: {Fonts.SIZE_TITLE}px;
        font-weight: bold;
    }}

    QLabel#sectionSubtitle {{
        color: {Colors.TEXT_SECONDARY};
        font-size: {Fonts.SIZE_NORMAL}px;
    }}

    QLabel#cardValue {{
        color: {Colors.TEXT_PRIMARY};
        font-size: {Fonts.SIZE_HEADER}px;
        font-weight: bold;
    }}

    QLabel#cardLabel {{
        color: {Colors.TEXT_SECONDARY};
        font-size: {Fonts.SIZE_SMALL}px;
    }}

    QLabel#statusText {{
        color: {Colors.TEXT_SECONDARY};
        font-size: {Fonts.SIZE_SMALL}px;
        padding: 0 {Spacing.SM}px;
    }}
    """
