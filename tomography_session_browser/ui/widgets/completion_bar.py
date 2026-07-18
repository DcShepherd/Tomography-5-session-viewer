"""SearchMap completion bar widget.

A compact, palette-aware row showing how many tiles a SearchMap has acquired
versus planned. Each row has three vertically-stacked elements:

* an elided **name** (left) that always remains legible,
* a numeric **ratio** label (right, e.g. "30 / 30"),
* a thin **progress track** beneath them with a fill proportional to the
  acquired-vs-planned fraction.

Earlier revisions wrapped every bar in a ``dashboardCard`` panel and used a
hard-coded light-grey track. With more than three search maps the nested
cards squeezed the labels behind the fills, and the track was invisible on
the dark theme. This rewrite drops the per-row card wrapper, fixes a
predictable per-row height, and pulls track / accent colours from the
active palette so the widget renders correctly in either theme.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.ui.theme import current_palette

# Per-row vertical footprint. With these numbers a card holding 6 bars
# (the dashboard's hard cap) settles around ~260 px — comfortable inside the
# parent card and predictable across rebuilds.
_ROW_TOTAL_HEIGHT = 36
_BAR_HEIGHT = 6


class _BarTrack(QWidget):
    """Custom-painted progress bar with palette-aware colours."""

    def __init__(self, fraction: float, accent: QColor | str, track: QColor | str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fraction = max(min(fraction, 1.0), 0.0)
        self._accent = QColor(accent)
        self._track = QColor(track)
        self.setFixedHeight(_BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt signature
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            rect = self.rect()
            painter.setBrush(self._track)
            painter.drawRoundedRect(rect, 3, 3)
            if self._fraction > 0:
                fill_w = max(int(rect.width() * self._fraction), _BAR_HEIGHT)  # always show at least one cap
                fill = rect.adjusted(0, 0, -(rect.width() - fill_w), 0)
                painter.setBrush(self._accent)
                painter.drawRoundedRect(fill, 3, 3)
        finally:
            painter.end()


class _ElidedTitleLabel(QLabel):
    """A QLabel that elides its text mid-string and shows the full text on hover."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setToolTip(text)
        self.setObjectName("metaValue")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self._apply_elision()

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt signature
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, max(self.width() - 4, 20))
        super().setText(elided)


class CompletionBar(QWidget):
    """Single search-map completion row used by the dashboard."""

    clicked = Signal(str)

    def __init__(
        self,
        identifier: str,
        label: str,
        acquired: int,
        planned: int,
        accent: QColor | str | None = None,
        failed_count: int = 0,
        failed_batch_count: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Predictable footprint regardless of label length / theme.
        self.setMinimumHeight(_ROW_TOTAL_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._identifier = identifier

        theme = current_palette()
        track_color = QColor(theme.surface_hi)
        accent_color = QColor(accent) if accent is not None else QColor(theme.chart_green)
        failed_color = QColor(theme.chart_red)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._title = _ElidedTitleLabel(label)
        header.addWidget(self._title, stretch=1)
        # When failed tilt series are linked to this search map, surface the
        # count as a small red chip beside the ratio label so the user
        # doesn't read "30 / 30" and assume everything succeeded.
        if failed_count > 0:
            exposure_noun = "exposure area" if failed_count == 1 else "exposure areas"
            batch_suffix = ""
            if failed_batch_count:
                batch_noun = "batch position group" if failed_batch_count == 1 else "batch position groups"
                batch_suffix = f" · {failed_batch_count} {batch_noun}"
            failed_chip = QLabel(
                f'<span style="color: {failed_color.name()}">● {failed_count} failed {exposure_noun}{batch_suffix}</span>'
            )
            failed_chip.setTextFormat(Qt.TextFormat.RichText)
            failed_chip.setStyleSheet("font-size: 9pt;")
            failed_chip.setToolTip(
                f"{failed_count} exposure area{'s' if failed_count != 1 else ''} associated with this search map "
                "failed validation"
                + (
                    f" across {failed_batch_count} inferred failed batch position group"
                    f"{'s' if failed_batch_count != 1 else ''}"
                    if failed_batch_count
                    else ""
                )
                + "."
            )
            header.addWidget(failed_chip, stretch=0)
        ratio = QLabel(f"{acquired} / {planned}" if planned else f"{acquired}")
        ratio.setObjectName("metaKey")
        ratio.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(ratio, stretch=0)
        layout.addLayout(header)

        fraction = (acquired / planned) if planned else 0.0
        layout.addWidget(_BarTrack(fraction, accent_color, track_color))

    # ------- click forwarding ------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._identifier)
        super().mousePressEvent(event)

    # ------- accessors used by tests ----------------------------------------

    @property
    def identifier(self) -> str:
        return self._identifier

    def title_text(self) -> str:
        return self._title._full_text  # noqa: SLF001 — test-only accessor
