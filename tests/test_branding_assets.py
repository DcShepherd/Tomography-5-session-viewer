from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QToolBar

import tomography_session_browser.ui.branding as branding
from tomography_session_browser.ui.branding import (
    TitleBarLockup,
    brand_asset_path,
    brand_pixmap,
    brand_window_icon,
    centered_pixmap_rect,
    draw_stack_mark,
    mark_asset_name,
)
from tomography_session_browser.ui.main_window import MainWindow
from tomography_session_browser.ui.theme import apply_theme, palette_for
from tomography_session_browser.ui.widgets.loading_overlay import LoadingOverlay


def test_branding_assets_exist_and_render() -> None:
    _app()
    assets = [
        "tomo_mark_dark.svg",
        "tomo_mark_light.svg",
        "tomo_lockup_dark.svg",
        "tomo_lockup_light.svg",
        "tomo_icon_slate.svg",
    ]

    for asset in assets:
        path = brand_asset_path(asset)
        assert path.exists()
        assert path.read_text(encoding="utf-8").count("<svg") == 1
        pixmap = brand_pixmap(asset, QSize(96, 48))
        assert not pixmap.isNull()
        assert pixmap.deviceIndependentSize().width() <= 96
        assert pixmap.deviceIndependentSize().height() <= 48


def test_brand_mark_assets_are_visually_centered() -> None:
    for asset in ("tomo_mark_dark.svg", "tomo_mark_light.svg"):
        text = brand_asset_path(asset).read_text(encoding="utf-8")
        assert 'viewBox="0 0.5 22 22"' in text

    for asset in ("tomo_lockup_dark.svg", "tomo_lockup_light.svg"):
        text = brand_asset_path(asset).read_text(encoding="utf-8")
        assert 'transform="translate(0, 14) scale(4)"' in text


def test_slate_app_icon_uses_visual_centering() -> None:
    text = brand_asset_path("tomo_icon_slate.svg").read_text(encoding="utf-8")
    match = re.search(
        r'transform="translate\(([-0-9.]+),\s*([-0-9.]+)\) scale\(([-0-9.]+)\)"',
        text,
    )
    assert match is not None

    x, y, scale = (float(value) for value in match.groups())
    mark_size = 22 * scale
    mark_offset = (1024 - mark_size) / 2
    assert x == pytest.approx(204.8)
    assert y == pytest.approx(mark_offset - 0.5 * scale, abs=0.01)


def test_direct_stack_mark_painting_centres_visible_bounds() -> None:
    _app()
    image = QImage(64, 64, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        draw_stack_mark(painter, QRectF(17, 17, 30, 30), "dark")
    finally:
        painter.end()

    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            if QColor(image.pixel(x, y)).alpha() > 0:
                xs.append(x)
                ys.append(y)

    assert xs
    assert (min(xs) + max(xs)) / 2 == pytest.approx(32, abs=0.75)
    assert (min(ys) + max(ys)) / 2 == pytest.approx(32, abs=0.75)


def test_loading_overlay_centres_brand_mark_in_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    calls: list[QRectF] = []

    def fake_draw_stack_mark(painter: QPainter, rect: QRectF, theme_name: str) -> None:
        calls.append(QRectF(rect))

    monkeypatch.setattr(branding, "draw_stack_mark", fake_draw_stack_mark)
    overlay = LoadingOverlay()
    overlay.resize(800, 600)
    overlay.show_loading("Loading session...", "Preparing the session interface.")

    image = QImage(800, 600, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        overlay.render(painter, QPoint(0, 0))
    finally:
        painter.end()
        overlay.hide_loading(fade=False)

    assert len(calls) == 1
    brand_rect = calls[0]
    assert brand_rect.size().width() == pytest.approx(30)
    assert brand_rect.size().height() == pytest.approx(30)
    assert brand_rect.center().x() == pytest.approx(400)


def test_centered_pixmap_rect_uses_logical_hidpi_size() -> None:
    _app()
    pixmap = brand_pixmap("tomo_mark_dark.svg", QSize(30, 30))
    rect = centered_pixmap_rect(QPointF(100, 80), pixmap)

    assert rect.center().x() == pytest.approx(100)
    assert rect.center().y() == pytest.approx(80)
    assert rect.width() == pytest.approx(pixmap.deviceIndependentSize().width())
    assert rect.height() == pytest.approx(pixmap.deviceIndependentSize().height())


def test_main_window_uses_balanced_theme_aware_branding() -> None:
    app = _app()
    apply_theme(app, palette_for("dark"))
    window = MainWindow()

    assert not window.windowIcon().isNull()
    assert not brand_window_icon().isNull()
    assert not brand_window_icon().pixmap(QSize(64, 64)).isNull()
    assert isinstance(window.brand_label, TitleBarLockup)
    assert not window.brand_label.is_compact()
    # Sanity ceiling — well below any reasonable adapted toolbar height.
    assert window.brand_label.height() <= 56

    name_px, tag_px = window.brand_label.text_font_pixel_sizes()
    name_metrics, tag_metrics = window.brand_label.text_metrics()
    assert name_px > tag_px
    # Title-bar sizes mirror the asset sheet's in-context lockup (scale 0.85).
    assert name_px == 19
    assert tag_px == 9
    # Inline single-line lockup: the widget must fit the wordmark's
    # visible glyph height (ascent + descent) so the 'y' descender of
    # "Tomography" never clips. The tag shares the same optical centre
    # and is smaller, so it never drives the height.
    wordmark_visible = name_metrics.ascent() + name_metrics.descent()
    assert window.brand_label.height() >= wordmark_visible
    # Stays well under QToolBar's ~40 px centring threshold so the lockup
    # is properly centred on the toolbar row, aligned with action buttons.
    assert window.brand_label.height() < 40
    # Width: mark + gap + wordmark + inline gap + tag + trailing pad.
    expected_min_width = (
        19 + 9
        + name_metrics.horizontalAdvance("Tomography")
        + tag_metrics.horizontalAdvance("SESSION BROWSER")
    )
    assert window.brand_label.width() >= expected_min_width
    assert window.brand_label.width() < expected_min_width + 40

    toolbar = window.findChild(QToolBar, "mainToolbar")
    assert toolbar is not None
    # Toolbar targets the asset-sheet 50 px but adapts upward if a
    # platform's fallback font produces a taller lockup. It must always
    # be at least tall enough to contain the lockup plus QSS padding.
    assert toolbar.height() >= 50
    assert toolbar.height() >= window.brand_label.height() + 8
    assert window.brand_label.geometry().bottom() <= toolbar.height()

    apply_theme(app, palette_for("light"))
    window._refresh_branding()
    assert not window.brand_label.is_compact()
    assert mark_asset_name("dark") == "tomo_mark_dark.svg"
    assert mark_asset_name("light") == "tomo_mark_light.svg"

    window.resize(980, 700)
    window._refresh_branding()
    assert window.brand_label.is_compact()
    window.close()


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
