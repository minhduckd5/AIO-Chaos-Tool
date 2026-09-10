"""
Lens-inspired dark theme — single source of truth for ChaosGen GUI colors & QSS.

Presentation layer only. Do not put audit/HITL/FSM logic here.

Semantic rules:
- Success / warning / danger meanings always use STATUS_* or SUCCESS/WARNING/DANGER —
  never ACCENT (blue) for those meanings.
- Chart multi-series colors live in CHART_SERIES; do not reuse ACCENT for cluster IDs.
"""

from __future__ import annotations

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

    # Canonical semantic status (buttons, badges, list verdicts)
    SUCCESS = "#22c55e"
    WARNING = "#f59e0b"
    DANGER = "#ef4444"
    INFO = "#3b82f6"

    # --- START MODIFICATION ---
    # Brighter status-on-dark tokens for inline labels on cards / settings.
    # Keep separate from ACCENT so "ok/error" never silently become blue.
    STATUS_OK = "#4ade80"
    STATUS_ERROR = "#f87171"
    STATUS_WARN = "#fbbf24"
    DANGER_PRESSED = "#b91c1c"
    # Chart baseline (normal score line) — slate, not muted text and not accent.
    CHART_BASELINE = "#475569"
    # Distinct cluster colors (cycle); intentional multi-hue set for scatter series.
    CHART_SERIES = (
        "#FF5E5B",
        "#00ADFF",
        "#00E676",
        "#FFA500",
        "#D500F9",
        "#FFD700",
        "#00CED1",
        "#FF1493",
        "#9B59B6",
        "#1ABC9C",
    )
    # --- END MODIFICATION ---

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


# ---------------------------------------------------------------------------
# QSS helpers — phase (a) foundation; Advisor/Experiments apply heavily in (b)
# ---------------------------------------------------------------------------


def qss_color(color: str, *, extra: str = "") -> str:
    """Inline label/status color. ``extra`` is appended (e.g. font-size)."""
    base = f"color: {color};"
    return f"{base} {extra}".strip() if extra else base


def muted_hint_qss() -> str:
    return qss_color(Colors.TEXT_MUTED, extra="font-size: 11px;")


def secondary_label_qss(*, min_width: int | None = None) -> str:
    parts = [f"color: {Colors.TEXT_SECONDARY};"]
    if min_width is not None:
        parts.append(f"min-width: {min_width}px;")
    return " ".join(parts)


def scroll_transparent_qss() -> str:
    return "QScrollArea { border: none; background: transparent; }"


def button_primary_qss() -> str:
    return f"""
    QPushButton {{
        background-color: {Colors.ACCENT};
        color: {Colors.TEXT_PRIMARY};
        border: none;
        border-radius: {Radius.MD}px;
        padding: 8px 16px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: {Colors.ACCENT_HOVER};
    }}
    QPushButton:pressed {{
        background-color: {Colors.ACCENT_MUTED};
    }}
    QPushButton:disabled {{
        background-color: {Colors.BG_HOVER};
        color: {Colors.TEXT_MUTED};
    }}
    """


def button_danger_qss() -> str:
    return f"""
    QPushButton {{
        background-color: {Colors.DANGER};
        color: {Colors.TEXT_PRIMARY};
        border: none;
        border-radius: {Radius.MD}px;
        padding: 8px 16px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: {Colors.STATUS_ERROR};
    }}
    QPushButton:pressed {{
        background-color: {Colors.DANGER_PRESSED};
    }}
    """


def button_secondary_qss() -> str:
    return f"""
    QPushButton {{
        background-color: {Colors.BG_SECONDARY};
        color: {Colors.TEXT_PRIMARY};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.MD}px;
        padding: 8px 16px;
    }}
    QPushButton:hover {{
        background-color: {Colors.BG_HOVER};
        border-color: {Colors.ACCENT};
    }}
    QPushButton:pressed {{
        background-color: {Colors.BG_SELECTED};
    }}
    """


def button_toolbar_qss() -> str:
    """Compact toolbar buttons (chart zoom, etc.)."""
    return f"""
    QPushButton {{
        background-color: {Colors.BG_SECONDARY};
        color: {Colors.TEXT_PRIMARY};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.SM}px;
        padding: 4px 12px;
        font-size: 14px;
        font-weight: bold;
        min-width: 32px;
    }}
    QPushButton:hover {{
        background-color: {Colors.BG_HOVER};
        border-color: {Colors.ACCENT};
    }}
    QPushButton:pressed {{
        background-color: {Colors.BG_SELECTED};
    }}
    """


def chart_tooltip_qss(*, locked: bool = False) -> str:
    border = Colors.ACCENT if locked else Colors.BORDER
    return f"""
    QLabel {{
        background-color: {Colors.BG_SECONDARY};
        color: {Colors.TEXT_PRIMARY};
        border: 1px solid {border};
        border-radius: {Radius.MD}px;
        padding: 8px;
        font-size: 11px;
    }}
    """


def section_header_qss() -> str:
    return (
        f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_TITLE}px; "
        f"font-weight: bold;"
    )


def set_semantic_role(widget, role: str) -> None:
    """
    Apply a named text/status role and force Qt to re-evaluate QSS.

    Roles: muted, secondary, primary, ok, error, warn, success, danger, warning.

    Always sets the Qt dynamic property ``semanticRole`` (used by chrome widgets
    that keep a stable objectName, e.g. ``cardValue`` / ``alignMetrics`` /
    ``opsStatus``). For plain labels without chrome, also sets objectName to the
    matching status/text token so existing QSS selectors keep working.
    """
    names = {
        "muted": "textMuted",
        "secondary": "textSecondary",
        "primary": "textPrimary",
        "ok": "statusOk",
        "error": "statusError",
        "warn": "statusWarn",
        "success": "statusSuccess",
        "danger": "statusDanger",
        "warning": "statusWarning",
        "accent": "statusAccent",
    }
    if role not in names:
        raise ValueError(f"unknown semantic role: {role!r}")

    # --- START MODIFICATION ---
    # Chrome widgets keep a fixed objectName; color comes from [semanticRole="…"].
    chrome = frozenset({"cardValue", "alignMetrics", "opsStatus"})
    widget.setProperty("semanticRole", role)
    current = widget.objectName() or ""
    if current not in chrome:
        widget.setObjectName(names[role])
    # --- END MODIFICATION ---
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


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
    QLabel#cardValue[semanticRole="success"] {{ color: {Colors.SUCCESS}; }}
    QLabel#cardValue[semanticRole="warning"] {{ color: {Colors.WARNING}; }}
    QLabel#cardValue[semanticRole="danger"] {{ color: {Colors.DANGER}; }}
    QLabel#cardValue[semanticRole="secondary"] {{ color: {Colors.TEXT_SECONDARY}; }}
    QLabel#cardValue[semanticRole="primary"] {{ color: {Colors.TEXT_PRIMARY}; }}
    QLabel#cardValue[semanticRole="accent"] {{ color: {Colors.ACCENT}; }}

    QLabel#alignMetrics {{
        font-size: {Fonts.SIZE_NORMAL}px;
        padding: 8px;
        background: {Colors.BG_SECONDARY};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.MD}px;
        color: {Colors.TEXT_PRIMARY};
    }}
    QLabel#alignMetrics[semanticRole="muted"] {{ color: {Colors.TEXT_MUTED}; }}
    QLabel#alignMetrics[semanticRole="success"] {{ color: {Colors.SUCCESS}; }}
    QLabel#alignMetrics[semanticRole="warning"] {{ color: {Colors.WARNING}; }}
    QLabel#alignMetrics[semanticRole="primary"] {{ color: {Colors.TEXT_PRIMARY}; }}

    QLabel#opsStatus {{
        font-size: {Fonts.SIZE_TITLE}px;
        font-weight: bold;
        color: {Colors.TEXT_SECONDARY};
    }}
    QLabel#opsStatus[semanticRole="success"] {{ color: {Colors.SUCCESS}; }}
    QLabel#opsStatus[semanticRole="warning"] {{ color: {Colors.WARNING}; }}
    QLabel#opsStatus[semanticRole="danger"] {{ color: {Colors.DANGER}; }}
    QLabel#opsStatus[semanticRole="secondary"] {{ color: {Colors.TEXT_SECONDARY}; }}
    QLabel#opsStatus[semanticRole="primary"] {{ color: {Colors.TEXT_PRIMARY}; }}

    QLabel#subsectionHeader {{
        color: {Colors.TEXT_PRIMARY};
        font-size: {Fonts.SIZE_LARGE}px;
        font-weight: bold;
        margin-top: {Spacing.MD}px;
    }}

    QLabel#emptyStateHint {{
        color: {Colors.TEXT_MUTED};
        padding: {Spacing.MD}px;
    }}

    QLabel#moduleHealthName {{
        color: {Colors.TEXT_PRIMARY};
        font-size: {Fonts.SIZE_NORMAL}px;
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

    QLabel#statusOk {{
        color: {Colors.STATUS_OK};
    }}

    QLabel#statusError {{
        color: {Colors.STATUS_ERROR};
    }}

    QLabel#statusWarn {{
        color: {Colors.STATUS_WARN};
    }}

    QPushButton#btnPrimary {{
        background-color: {Colors.ACCENT};
        color: {Colors.TEXT_PRIMARY};
        border: none;
        border-radius: {Radius.MD}px;
        padding: 8px 16px;
        font-weight: bold;
    }}
    QPushButton#btnPrimary:hover {{
        background-color: {Colors.ACCENT_HOVER};
    }}

    QPushButton#btnDanger {{
        background-color: {Colors.DANGER};
        color: {Colors.TEXT_PRIMARY};
        border: none;
        border-radius: {Radius.MD}px;
        padding: 8px 16px;
        font-weight: bold;
    }}
    QPushButton#btnDanger:disabled {{
        background-color: {Colors.BG_HOVER};
        color: {Colors.TEXT_MUTED};
    }}

    QPushButton#btnSecondary {{
        background-color: {Colors.BG_SECONDARY};
        color: {Colors.TEXT_PRIMARY};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.MD}px;
        padding: 8px 16px;
    }}
    QPushButton#btnSecondary:hover {{
        background-color: {Colors.BG_HOVER};
        border-color: {Colors.ACCENT};
    }}

    QPushButton#btnPrimary:disabled {{
        background-color: {Colors.BG_HOVER};
        color: {Colors.TEXT_MUTED};
    }}

    QPushButton#btnLink {{
        background: transparent;
        color: {Colors.ACCENT};
        border: 1px solid {Colors.ACCENT};
        border-radius: {Radius.MD}px;
        padding: 10px 16px;
    }}

    QPushButton#btnGhost {{
        background: transparent;
        color: {Colors.TEXT_MUTED};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.SM}px;
        padding: 8px 12px;
    }}

    QPushButton#btnSuccess {{
        background-color: {Colors.SUCCESS};
        color: {Colors.TEXT_PRIMARY};
        border: none;
        border-radius: {Radius.SM}px;
        padding: 4px 12px;
    }}

    QLabel#stepWizard {{
        color: {Colors.ACCENT};
        font-size: {Fonts.SIZE_NORMAL}px;
        font-weight: bold;
    }}

    QLabel#phaseIdle {{
        color: {Colors.TEXT_MUTED};
        padding: 4px 10px;
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.SM}px;
    }}

    QLabel#phaseActive {{
        color: {Colors.TEXT_PRIMARY};
        background-color: {Colors.BG_SELECTED};
        padding: 4px 10px;
        border: 1px solid {Colors.ACCENT};
        border-radius: {Radius.SM}px;
        font-weight: bold;
    }}

    QLabel#listSectionHeader {{
        color: {Colors.TEXT_SECONDARY};
        font-size: 11px;
        font-weight: bold;
        letter-spacing: 1px;
    }}

    QLabel#mutedHint {{
        color: {Colors.TEXT_MUTED};
        font-size: 11px;
    }}

    QLabel#textMuted {{
        color: {Colors.TEXT_MUTED};
    }}

    QLabel#textSecondary {{
        color: {Colors.TEXT_SECONDARY};
        font-size: {Fonts.SIZE_SMALL}px;
    }}

    QLabel#textSecondaryWide {{
        color: {Colors.TEXT_SECONDARY};
        min-width: 90px;
    }}

    QLabel#textPrimary {{
        color: {Colors.TEXT_PRIMARY};
    }}

    QLabel#textPrimaryLarge {{
        color: {Colors.TEXT_PRIMARY};
        font-size: {Fonts.SIZE_LARGE}px;
    }}

    QLabel#cardTitle {{
        color: {Colors.TEXT_PRIMARY};
        font-weight: bold;
        font-size: {Fonts.SIZE_LARGE}px;
    }}

    QLabel#bodyPrimary {{
        color: {Colors.TEXT_PRIMARY};
        font-size: 12px;
    }}

    QLabel#dangerSmall {{
        color: {Colors.DANGER};
        font-size: {Fonts.SIZE_SMALL}px;
    }}

    QLabel#betaBadge {{
        color: {Colors.WARNING};
        font-size: {Fonts.SIZE_SMALL}px;
        border: 1px solid {Colors.WARNING};
        border-radius: {Radius.SM}px;
        padding: 2px 6px;
    }}

    QLabel#statusSuccess {{
        color: {Colors.SUCCESS};
    }}

    QLabel#statusDanger {{
        color: {Colors.DANGER};
    }}

    QLabel#statusWarning {{
        color: {Colors.WARNING};
    }}

    QLabel#statusAccent {{
        color: {Colors.ACCENT};
    }}

    QListWidget#historyList {{
        background-color: {Colors.BG_CARD};
        border: 1px solid {Colors.BORDER};
    }}
    QListWidget#historyList::item {{
        padding: 8px;
    }}
    QListWidget#historyList::item:selected {{
        background-color: {Colors.BG_SELECTED};
    }}

    QTreeWidget#treeDark {{
        background-color: {Colors.BG_INPUT};
        color: {Colors.TEXT_PRIMARY};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.SM}px;
    }}

    QWidget#cardAccent {{
        background-color: {Colors.BG_CARD};
        border: 1px solid {Colors.ACCENT};
        border-radius: {Radius.MD}px;
    }}

    QFrame#ingestScopeStrip {{
        background-color: {Colors.BG_CARD};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.MD}px;
    }}

    QTextEdit#detailMono {{
        background-color: {Colors.BG_INPUT};
        color: {Colors.TEXT_PRIMARY};
        font-family: {Fonts.FAMILY_MONO};
        font-size: {Fonts.SIZE_SMALL}px;
        border: 1px solid {Colors.BORDER};
        padding: {Spacing.SM}px;
    }}

    QTableWidget#dataTable {{
        background-color: {Colors.BG_CARD};
        border: 1px solid {Colors.BORDER};
    }}
    QHeaderView::section {{
        background-color: {Colors.BG_SECONDARY};
        padding: 6px;
    }}

    QScrollArea#transparentScroll {{
        border: none;
        background: transparent;
    }}

    QLineEdit#formInput, QComboBox#formInput, QSpinBox#formInput, QDoubleSpinBox#formInput {{
        background-color: {Colors.BG_INPUT};
        color: {Colors.TEXT_PRIMARY};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.SM}px;
        padding: 6px;
    }}
    """
