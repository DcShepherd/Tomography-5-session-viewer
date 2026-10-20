"""Common components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from tomography_session_browser.ui.widgets.dashboard_card import DASHBOARD_CARD_MARGINS, DASHBOARD_CARD_SPACING, DASHBOARD_HEADER_SPACING
from tomography_session_browser.presentation.design_tokens import SPACE_XS
from tomography_session_browser.ui.dashboard.constants import STATUS_TONES


def status_tone(key: str | None, fallback: str = "unknown") -> str:
    """The tone a status key is shown in; ``fallback`` for unknown keys."""

    if not key:
        return fallback
    return STATUS_TONES.get(key, fallback)


def severity_tone(severity: str) -> str:
    """The tone the dashboard draws a warning of ``severity`` in. The
    context panel's warning callouts use it too (harmonisation C1)."""

    return status_tone(severity)


def status_dot(tone: str, *, compact: bool = False, width: int = 12) -> QLabel:
    """A status glyph whose colour is data and whose size is style.

    The colour varies per row, so the dot carries its ``tone`` and the style
    sheet colours it: an inline colour baked the palette in, and a theme
    switch had to rebuild the dashboard to recolour it.
    """

    dot = QLabel("●")
    dot.setObjectName("statusDot")
    dot.setProperty("compact", bool(compact))
    dot.setProperty("tone", tone)
    dot.setFixedWidth(width)
    return dot


class CardBox(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dashboardCard")
        # Cards expand horizontally to fill the dashboard column but keep
        # their content-driven height. ``MinimumExpanding`` together with the
        # caller-set minimum height keeps a card from shrinking below its
        # content-needed size while still letting the layout balance widths
        # consistently across rebuilds.
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)


def format_dose(value: float | None) -> str:
    if value is None:
        return "n/a"
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def dose_metric_widget(label: str, value: str, *, stacked: bool = False) -> QWidget:
    wrap = QWidget()
    wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    if not stacked:
        wrap.setMinimumWidth(170)
        wrap.setMaximumWidth(240)
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_XS)

    title = QLabel(label)
    title.setObjectName("metaKey")
    title.setWordWrap(False)
    layout.addWidget(title)

    number = QLabel(value)
    number.setObjectName("healthValue")
    number.setToolTip(value)
    number.setWordWrap(False)
    number.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    number.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
    layout.addWidget(number)
    return wrap


def card_layout(card: CardBox) -> QVBoxLayout:
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*DASHBOARD_CARD_MARGINS)
    layout.setSpacing(DASHBOARD_CARD_SPACING)
    return layout


def card_header(title: str, badge: str | None = None, tooltip: str | None = None) -> tuple[QHBoxLayout, QLabel | None]:
    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(DASHBOARD_HEADER_SPACING)
    title_label = QLabel(title.upper())
    title_label.setObjectName("cardTitle")
    title_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    header.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignTop)
    header.addStretch(1)
    badge_label: QLabel | None = None
    if badge is not None:
        badge_label = QLabel(badge)
        badge_label.setObjectName("sampleCount")
        if tooltip:
            badge_label.setToolTip(tooltip)
        header.addWidget(badge_label)
    return header, badge_label
