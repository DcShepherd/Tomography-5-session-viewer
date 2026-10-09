"""Common components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QToolTip, QVBoxLayout, QWidget
from tomography_session_browser.presentation.dashboard.models import BatchPositionProgressModel, SearchMapOverviewRowModel
from tomography_session_browser.ui.widgets.dashboard_card import DASHBOARD_CARD_MARGINS, DASHBOARD_CARD_SPACING, DASHBOARD_HEADER_SPACING
from tomography_session_browser.presentation.design_tokens import SPACE_XS
from tomography_session_browser.ui.theme import TIER_PRIMARY, TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.dashboard.constants import ROW_LABEL_MIN_WIDTH, ROW_LABEL_MIN_WIDTH_COMPACT, STATUS_TONES
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def status_colors() -> dict[str, str]:
    """Palette-aware dashboard status colours from the Slate & Sage guide."""

    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()
    return {
        # New tilt-series outcome labels (driven from
        # ``services.tilt_series_validation``).
        "Complete": theme.chart_green,
        "Incomplete": theme.chart_amber,
        "Failed": theme.chart_red,
        "Unknown": theme.unknown,
        # Header completeness pill / warning row severities.
        "complete": theme.chart_green,
        "warning": theme.chart_amber,
        "error": theme.chart_red,
        "failed": theme.chart_red,
        "missing": theme.chart_red,
        "neutral": theme.unknown,
    }


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


def progress_row_name(text: str, *, compact: bool) -> ElidedLabel:
    """The entity name on a dashboard progress row: primary content."""

    name = ElidedLabel(text)
    name.setObjectName("progressRowName")
    name.setProperty("compact", bool(compact))
    apply_tier(name, TIER_PRIMARY)
    name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    name.setMinimumWidth(ROW_LABEL_MIN_WIDTH_COMPACT if compact else ROW_LABEL_MIN_WIDTH)
    return name


def status_color(key: str | None, fallback: str | None = None) -> str:
    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()
    if not key:
        return fallback or theme.unknown
    return status_colors().get(key, fallback or theme.unknown)


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


class ProgressStrip(QWidget):
    """Small multi-segment progress bar used by compact summary cards."""

    def __init__(
        self,
        row: BatchPositionProgressModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._row = row
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        colors = [
            (self._row.complete, QColor(theme.chart_green)),
            (self._row.warning, QColor(theme.chart_amber)),
            (self._row.failed, QColor(theme.chart_red)),
            (self._row.queued, QColor(theme.border_strong)),
        ]
        total = max(self._row.total, 1)
        rect = QRectF(self.rect())
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.surface_hi))
            painter.drawRoundedRect(rect, 4, 4)
            x = rect.left()
            for count, color in colors:
                if count <= 0:
                    continue
                width = rect.width() * (count / total)
                segment = QRectF(x, rect.top(), width, rect.height())
                painter.setBrush(color)
                painter.drawRoundedRect(segment, 4, 4)
                x += width
        finally:
            painter.end()


def status_label(status: str) -> str:
    labels = {
        "complete": "complete",
        "warning": "incomplete",
        "failed": "failed",
        "missing": "missing",
        "neutral": "unknown",
    }
    return labels.get(status, status or "unknown")


def search_map_status_label(row: SearchMapOverviewRowModel) -> str:
    if not row.acquisition_associated:
        return "N/A"
    return status_label(row.status)


def batch_outcome_chip_parts(row_model: BatchPositionProgressModel) -> list[str]:
    """Return the non-zero outcome phrases for a batch-position row chip.

    Always yields at least one phrase so a row with no recorded outcomes
    still reads as "0 complete" rather than showing an empty chip.
    """

    parts: list[str] = []
    if row_model.complete or not (row_model.failed or row_model.warning):
        parts.append(f"{row_model.complete} complete")
    if row_model.failed:
        parts.append(f"{row_model.failed} failed")
    if row_model.warning:
        parts.append(f"{row_model.warning} partial")
    return parts


def search_map_overview_tooltip(row: SearchMapOverviewRowModel) -> str:
    lines = [row.label, f"Status: {search_map_status_label(row)}", row.summary]
    if not row.acquisition_associated:
        lines.append("No batch positions or tilt series are associated with this search map.")
    return "\n".join(lines)


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


def card_with_title(title: str) -> tuple[CardBox, QVBoxLayout]:
    card = CardBox()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*DASHBOARD_CARD_MARGINS)
    layout.setSpacing(DASHBOARD_CARD_SPACING)
    title_label = QLabel(title.upper())
    title_label.setObjectName("cardTitle")
    apply_tier(title_label, TIER_SUPPORTING)
    title_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    layout.addWidget(title_label)
    return card, layout


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


def more_button(dashboard: SessionDashboard, label: str, destination: str, tooltip: str) -> QToolButton:
    """Return a "+N more ..." control that opens the owning tab.

    These counts used to be plain muted labels, so the only way to reach
    the hidden rows was to guess which tab held them.
    """

    button = QToolButton()
    button.setObjectName("dashboardFilterButton")
    button.setText(f"+{label}")
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(lambda: dashboard.navigate_requested.emit(destination))
    return button


def copy_text(text: str, *, source: QToolButton | None = None) -> None:
    from PySide6.QtGui import QGuiApplication

    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)
        if source is not None:
            original_tooltip = source.toolTip()
            source.setToolTip("Copied")
            QToolTip.showText(
                source.mapToGlobal(source.rect().bottomLeft()),
                "Copied",
                source,
            )
            QTimer.singleShot(
                1200,
                lambda button=source, tooltip=original_tooltip: button.setToolTip(tooltip),
            )
