"""Brand asset helpers for the Stack tomography mark and title-bar lockup."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from tomography_session_browser.presentation.design_tokens import MONO_FONT_NAME, SANS_FONT_NAME, palette_for
from tomography_session_browser.ui.stack_mark import draw_stack_mark
from tomography_session_browser.ui.theme import current_palette


_BRAND_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "assets" / "branding"

# Sizes mirror the asset sheet's in-context title bar, which renders the
# Stack lockup at scale 0.85 (mark 18.7, wordmark 18.7, tag 8.075). We use
# integers close to those values so the hierarchy reads correctly without
# the lockup ballooning the toolbar or clipping descenders.
_TITLEBAR_MARK_SIZE: Final[int] = 19
_TITLEBAR_GAP: Final[int] = 9
_TITLEBAR_NAME_PX: Final[int] = 19
_TITLEBAR_TAG_PX: Final[int] = 9
# Horizontal gap between "Tomography" and "SESSION BROWSER" in the inline
# (single-line) lockup. The two-line stacked layout that the asset sheet
# uses cannot align with the action buttons inside a QToolBar (Qt centres
# widgets only up to ~40 px tall), so we render the tag inline. The
# wordmark and tag share an optical-centre baseline so both glyphs sit at
# the widget's vertical centre, which QToolBar then centres on the row.
_TITLEBAR_INLINE_GAP: Final[int] = 8
# Vertical safety margin above/below the wordmark glyph. Kept at 0 so the
# widget height equals the visible wordmark height — which on Windows
# matches the action buttons' rendered height, letting QToolBar's top-
# aligned layout put both at the same vertical centre.
_TITLEBAR_VERTICAL_PAD: Final[int] = 0
# Trailing horizontal slack so anti-aliased glyph edges of "Tomography"
# and "SESSION BROWSER" are never clipped at the widget's right edge.
_TITLEBAR_TEXT_PAD: Final[int] = 4
# Compact-mode mark size when the window is too narrow for the full lockup.
_TITLEBAR_COMPACT_SIZE: Final[int] = 26


def brand_asset_path(name: str) -> Path:
    """Return an absolute path to a branding SVG asset."""

    return _BRAND_DIR / name


class TitleBarLockup(QWidget):
    """Compact title-bar lockup matching the asset sheet without clipping text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("brandLockup")
        self._theme_name = current_palette().name
        self._compact = False

        self._name_font = QFont(SANS_FONT_NAME, weight=QFont.Weight.DemiBold)
        self._name_font.setPixelSize(_TITLEBAR_NAME_PX)
        self._name_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 98.5)

        self._tag_font = QFont(MONO_FONT_NAME, weight=QFont.Weight.Medium)
        self._tag_font.setPixelSize(_TITLEBAR_TAG_PX)
        self._tag_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 116)

        name_metrics = QFontMetrics(self._name_font)
        tag_metrics = QFontMetrics(self._tag_font)
        # Visible (ascent + descent) glyph height, excluding leading.
        self._name_visible = name_metrics.ascent() + name_metrics.descent()
        self._tag_visible = tag_metrics.ascent() + tag_metrics.descent()
        # Inline lockup: height tracks the dominant wordmark glyph (the
        # tag is smaller and shares the same optical centre, so it does
        # not push the height). Stays comfortably under QToolBar's ~40 px
        # centring threshold so the widget centres cleanly on the row.
        self._expanded_height = max(
            self._name_visible + 2 * _TITLEBAR_VERTICAL_PAD,
            _TITLEBAR_MARK_SIZE + 2,
        )
        # Width: mark + gap + wordmark + inline gap + tag + trailing pad.
        self._expanded_width = (
            _TITLEBAR_MARK_SIZE
            + _TITLEBAR_GAP
            + name_metrics.horizontalAdvance("Tomography")
            + _TITLEBAR_INLINE_GAP
            + tag_metrics.horizontalAdvance("SESSION BROWSER")
            + _TITLEBAR_TEXT_PAD
        )
        self._text_colour = current_palette().text_strong
        self._tag_colour = current_palette().text_muted
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._refresh_colours()
        self.set_compact(False)

    def set_theme(self, theme_name: str) -> None:
        self._theme_name = theme_name
        self._refresh_colours()

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        if compact:
            self.setFixedSize(_TITLEBAR_COMPACT_SIZE, _TITLEBAR_COMPACT_SIZE)
        else:
            self.setFixedSize(self._expanded_width, self._expanded_height)
        self.updateGeometry()
        self.update()

    def is_compact(self) -> bool:
        return self._compact

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt signature
        if self._compact:
            return QSize(_TITLEBAR_COMPACT_SIZE, _TITLEBAR_COMPACT_SIZE)
        return QSize(self._expanded_width, self._expanded_height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt signature
        return self.sizeHint()

    def text_font_pixel_sizes(self) -> tuple[int, int]:
        """Expose wordmark/tagline sizes for clipping and hierarchy tests."""

        return self._name_font.pixelSize(), self._tag_font.pixelSize()

    def text_metrics(self) -> tuple[QFontMetrics, QFontMetrics]:
        """Expose current metrics for layout regression tests."""

        return QFontMetrics(self._name_font), QFontMetrics(self._tag_font)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        try:
            if self._compact:
                draw_stack_mark(painter, QRectF(self.rect()), self._theme_name)
                return

            mark_rect = QRectF(
                0,
                (self.height() - _TITLEBAR_MARK_SIZE) / 2,
                _TITLEBAR_MARK_SIZE,
                _TITLEBAR_MARK_SIZE,
            )
            draw_stack_mark(painter, mark_rect, self._theme_name)

            name_metrics, tag_metrics = self.text_metrics()
            centre_y = self.height() / 2
            # Optical-centre alignment: each glyph's visible block
            # (ascent + descent) is centred on the widget's vertical
            # centre. Baseline = centre + (ascent - descent) / 2.
            name_baseline = centre_y + (name_metrics.ascent() - name_metrics.descent()) / 2
            tag_baseline = centre_y + (tag_metrics.ascent() - tag_metrics.descent()) / 2

            wordmark_x = _TITLEBAR_MARK_SIZE + _TITLEBAR_GAP
            tag_x = (
                wordmark_x
                + name_metrics.horizontalAdvance("Tomography")
                + _TITLEBAR_INLINE_GAP
            )

            painter.setFont(self._name_font)
            painter.setPen(QColor(self._text_colour))
            painter.drawText(QPointF(wordmark_x, name_baseline), "Tomography")

            painter.setFont(self._tag_font)
            painter.setPen(QColor(self._tag_colour))
            painter.drawText(QPointF(tag_x, tag_baseline), "SESSION BROWSER")
        finally:
            painter.end()

    def _refresh_colours(self) -> None:
        palette = palette_for(self._theme_name)
        self._text_colour = palette.text_strong
        self._tag_colour = palette.text_muted
        self.update()


def brand_window_icon() -> QIcon:
    """Return the slate app icon from the centered source SVG."""

    return QIcon(str(brand_asset_path("tomo_icon_slate.svg")))
