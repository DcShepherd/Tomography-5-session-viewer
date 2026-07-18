"""Brand asset helpers for the Stack tomography mark and title-bar lockup."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QSizePolicy, QWidget

from tomography_session_browser.ui.theme import MONO_FONT_NAME, SANS_FONT_NAME, current_palette, palette_for


_BRAND_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "assets" / "branding"
_MARK_GEOMETRY: Final[tuple[tuple[float, float, float, bool], ...]] = (
    (3.0, 0.42, 0.55, False),
    (6.0, 0.65, 0.75, False),
    (9.0, 0.84, 0.90, False),
    (11.6, 1.00, 1.00, True),
    (14.2, 0.84, 0.90, False),
    (17.2, 0.65, 0.70, False),
    (20.0, 0.42, 0.50, False),
)
_MARK_CENTER: Final[float] = 11.0
_MARK_MAX_WIDTH: Final[float] = 18.0
# The asset-sheet geometry is intentionally preserved, but its visible stroke
# envelope is centred at y=11.5 inside the 22-unit viewBox. UI containers need
# the visible mark, not the nominal viewBox, centred in their local frame.
_MARK_VISUAL_OFFSET_Y: Final[float] = -0.5

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


def mark_asset_name(theme_name: str) -> str:
    """Return the theme-appropriate mark-only asset."""

    return "tomo_mark_light.svg" if theme_name == "light" else "tomo_mark_dark.svg"


class StackMarkWidget(QWidget):
    """Native vector-painted version of the approved 7-ray Stack mark."""

    def __init__(self, parent: QWidget | None = None, *, size: int = _TITLEBAR_MARK_SIZE) -> None:
        super().__init__(parent)
        self._theme_name = current_palette().name
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_theme(self, theme_name: str) -> None:
        if self._theme_name == theme_name:
            return
        self._theme_name = theme_name
        self.update()

    def set_mark_size(self, size: int) -> None:
        self.setFixedSize(size, size)
        self.updateGeometry()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            draw_stack_mark(painter, QRectF(self.rect()), self._theme_name)
        finally:
            painter.end()


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


def brand_pixmap(asset_name: str, logical_size: QSize) -> QPixmap:
    """Rasterise a branding SVG at the current device-pixel ratio."""

    renderer = _renderer(asset_name)
    source = renderer.defaultSize()
    if source.isEmpty():
        source = QSize(22, 22)
    scale = min(logical_size.width() / source.width(), logical_size.height() / source.height())
    target = QSize(max(1, int(source.width() * scale)), max(1, int(source.height() * scale)))
    dpr = _device_pixel_ratio()
    physical = QSize(max(1, round(target.width() * dpr)), max(1, round(target.height() * dpr)))
    pixmap = QPixmap(physical)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        renderer.render(painter, QRectF(0, 0, physical.width(), physical.height()))
    finally:
        painter.end()
    return pixmap


def draw_stack_mark(painter: QPainter, rect: QRectF, theme_name: str) -> None:
    """Paint the Stack mark centred in ``rect`` using asset-sheet geometry."""

    palette = palette_for(theme_name)
    accent = palette.accent
    mid = palette.text_strong
    scale = min(rect.width(), rect.height()) / 22.0
    painter.save()
    painter.translate(
        rect.left() + (rect.width() - 22 * scale) / 2,
        rect.top() + (rect.height() - 22 * scale) / 2 + _MARK_VISUAL_OFFSET_Y * scale,
    )
    painter.scale(scale, scale)
    try:
        for y, half_width, opacity, is_mid in _MARK_GEOMETRY:
            length = _MARK_MAX_WIDTH * half_width
            pen = QPen(QColor(mid if is_mid else accent), 1.6 if is_mid else 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setOpacity(1.0 if is_mid else opacity)
            painter.drawLine(
                QPointF(_MARK_CENTER - length / 2, y),
                QPointF(_MARK_CENTER + length / 2, y),
            )
    finally:
        painter.restore()


def centered_pixmap_rect(center: QPointF, pixmap: QPixmap) -> QRectF:
    """Return a logical target rect that centres a HiDPI pixmap at ``center``."""

    size = pixmap.deviceIndependentSize()
    return QRectF(center.x() - size.width() / 2, center.y() - size.height() / 2, size.width(), size.height())


def brand_window_icon() -> QIcon:
    """Return the slate app icon from the centered source SVG."""

    return QIcon(str(brand_asset_path("tomo_icon_slate.svg")))


def _device_pixel_ratio() -> float:
    app = QGuiApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    return max(1.0, float(screen.devicePixelRatio()) if screen is not None else 1.0)


@lru_cache(maxsize=8)
def _renderer(asset_name: str) -> QSvgRenderer:
    path = brand_asset_path(asset_name)
    raw = path.read_bytes()
    return QSvgRenderer(QByteArray(raw))
