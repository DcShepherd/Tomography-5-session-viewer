"""The atlas session summary page (design review B3.4).

The name and its badge, a contact sheet with a tile for every atlas, and Needs
attention. A tile is the atlas's picture, its name, and only what informs: a
linked collection sample (a tinted border and its name over the picture, and
its tilt series when that collection is loaded), a Missing pill when the
atlas's files were not found, a Re-acquired pill. An atlas with nothing to say
says nothing: there is no "No collection data" text.

The sheet paces its own picture requests. Tiles in view ask first and the rest
follow in sheet order as the thumbnail lane has room (``thumbnail_room``); a
tile scrolled into view moves to the front; a rebuilt page cancels them all.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.presentation.dashboard.models import AtlasRowModel, DashboardModel
from tomography_session_browser.presentation.dashboard.sections.atlas import AtlasFilter, AtlasSheetModel
from tomography_session_browser.presentation.dashboard.summary import AtlasSessionSummary
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.ui.dashboard.attention import build_needs_attention
from tomography_session_browser.ui.dashboard.header import build_summary_header
from tomography_session_browser.ui.theme import TIER_LANDMARK, TIER_PRIMARY, TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.viewer.preview_scheduler import preview_scheduler
from tomography_session_browser.ui.widgets.atlas_thumbnail import AtlasThumbnail
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard

#: A tile is at least this wide; packed, the sheet fits as many columns as that allows.
TILE_MIN_WIDTH_PX = 176
#: And at most this wide, however much room there is.
TILE_MAX_WIDTH_PX = 320
TILE_GAP_PX = SPACE_M
#: A picture's proportions (the atlas mosaics are nearly square).
THUMB_WIDTH, THUMB_HEIGHT = 360, 327
#: The tile's own outline, inside which the picture sits.
BORDER_PX = 1
#: What the decoder is asked for, in logical pixels (times the screen ratio, capped):
#: the widest a tile gets, so a large tile is never an enlarged small picture.
SHEET_REQUEST_SIDE = TILE_MAX_WIDTH_PX
#: Below the sheet, room is kept for this much of what follows (Needs attention).
RESERVE_BELOW_MAX_FRACTION = 0.4
#: How long a scroll settles before the tiles in view are asked for first.
SCROLL_SETTLE_MS = 40


def _picture_size(tile_width: int) -> QSize:
    inner = max(1, tile_width - 2 * BORDER_PX)
    return QSize(inner, round(inner * THUMB_HEIGHT / THUMB_WIDTH))


def _column_width(width: int, columns: int) -> int:
    return (width - TILE_GAP_PX * (columns - 1)) // columns


def fit_tiles(count: int, width: int, height_budget: int | None, tile_chrome: int) -> tuple[int, int]:
    """The sheet's columns and tile width: the largest tiles for which every row
    fits in ``height_budget``. For each column count a tile is as wide as the
    column, the height (its rows) or ``TILE_MAX_WIDTH_PX`` allows, whichever
    is least; the widest wins, and of equals the one with more columns (fewer
    rows). When no arrangement of tiles at least ``TILE_MIN_WIDTH_PX`` wide fits,
    or the budget is unknown, the sheet is packed (as many columns as the width
    allows, filling it) and the page scrolls. ``tile_chrome`` is a tile's height
    less its picture."""

    packed = max(1, (width + TILE_GAP_PX) // (TILE_MIN_WIDTH_PX + TILE_GAP_PX))
    packed_fit = (packed, _column_width(width, packed))
    if count <= 0 or height_budget is None:
        return packed_fit
    best: tuple[int, int] | None = None
    for columns in range(1, packed + 1):
        rows = -(-count // columns)
        picture_room = (height_budget - TILE_GAP_PX * (rows - 1)) // rows - tile_chrome
        if picture_room <= 0:
            continue
        tile_width = min(
            _column_width(width, columns),
            TILE_MAX_WIDTH_PX,
            picture_room * THUMB_WIDTH // THUMB_HEIGHT + 2 * BORDER_PX,
        )
        while tile_width >= TILE_MIN_WIDTH_PX and _picture_size(tile_width).height() > picture_room:
            tile_width -= 1  # rounding in the picture's proportions
        if tile_width < TILE_MIN_WIDTH_PX:
            continue
        if best is None or tile_width > best[1] or (tile_width == best[1] and columns > best[0]):
            best = (columns, tile_width)
    return best or packed_fit


def _pill(text: str, tone: str) -> QLabel:
    pill = QLabel(text)
    pill.setObjectName("statePill")
    pill.setProperty("tone", tone)
    return pill


class AtlasTile(QFrame):
    """One atlas: its picture, name and what is worth saying about it."""

    clicked = Signal(str)  # the atlas's id

    def __init__(self, row: AtlasRowModel) -> None:
        super().__init__()
        self.row = row
        self.setObjectName("atlasTile")
        self.setProperty("linked", row.linked_sample is not None)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Open atlas {row.sample_label}")
        self.setAccessibleDescription(self._spoken())
        self.setToolTip(self._spoken())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(BORDER_PX, BORDER_PX, BORDER_PX, 0)
        layout.setSpacing(0)
        self.picture = AtlasThumbnail(
            row.image_path,
            _picture_size(TILE_MIN_WIDTH_PX),
            request_side=SHEET_REQUEST_SIDE,
            description=f"{row.sample_label} atlas",
            placeholder="Image file not found" if row.missing else "No image",
            auto_request=False,
        )
        layout.addWidget(self.picture)
        if row.linked_sample is not None:
            link = QLabel(row.linked_sample, self.picture)
            link.setObjectName("tileLink")
            link.setToolTip(f"Used by the collection sample {row.linked_sample}")
            link.move(SPACE_S, SPACE_S)
            link.adjustSize()

        # Every tile has the same shape: the name, then one detail line (the
        # count and any pills). The sheet gives every caption the height of the
        # tallest (``ContactSheet._equalise_captions``), so an atlas with
        # nothing to say keeps an empty line rather than a shorter outline.
        self.caption = QWidget()
        caption = QVBoxLayout(self.caption)
        caption.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_M)
        caption.setSpacing(SPACE_S)
        name = ElidedLabel(row.sample_label)
        name.setObjectName("cardName")
        apply_tier(name, TIER_PRIMARY)
        caption.addWidget(name)
        detail = QHBoxLayout()
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(SPACE_XS)
        if row.tilt_series_count is not None:
            # Only a loaded collection can say it (K2): an atlas session opened
            # alone does not know, and says nothing.
            count = ElidedLabel(f"{row.tilt_series_count:,} tilt series")
            count.setObjectName("tileCount")
            detail.addWidget(count, 1)
        if row.missing:
            detail.addWidget(_pill("Missing", "bad"))
        if row.reacquired:
            detail.addWidget(_pill("Re-acquired", "warn"))
        if detail.count():
            if row.tilt_series_count is None:
                detail.addStretch(1)
            caption.addLayout(detail)
        caption.addStretch(1)
        layout.addWidget(self.caption)

    def _spoken(self) -> str:
        row = self.row
        parts = [row.sample_label]
        if row.missing:
            parts.append("image file not found")
        if row.reacquired:
            parts.append("re-acquired")
        if row.linked_sample is not None:
            parts.append(f"used by collection sample {row.linked_sample}")
            if row.tilt_series_count is not None:
                parts.append(f"{row.tilt_series_count} tilt series")
        return ", ".join(parts)

    def in_view(self) -> bool:
        """Any of the picture is on screen (a scrolled-away tile is clipped to nothing)."""

        return not self.picture.visibleRegion().isEmpty()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.row.atlas_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt signature
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.clicked.emit(self.row.atlas_id)
            event.accept()
            return
        super().keyPressEvent(event)


class ContactSheet(QWidget):
    """The tiles, filtered and paced, as large as the visible page allows
    (``fit_columns``): on a large display the sheet fills the space above Needs
    attention instead of packing small tiles into its top half."""

    opened = Signal(str)  # an atlas id

    def __init__(self, sheet: AtlasSheetModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("contactSheet")
        self.setAccessibleName("Atlas contact sheet")
        self._sheet = sheet
        self._tiles = [AtlasTile(row) for row in sheet.tiles]
        for tile in self._tiles:
            tile.clicked.connect(self.opened)
            tile.setParent(self)
        self._shown: list[AtlasTile] = list(self._tiles)
        self._columns = 0
        self._tile_width = 0
        self._width = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(TILE_GAP_PX)
        self._grid.setVerticalSpacing(TILE_GAP_PX)
        self._filling = False
        self._area: QScrollArea | None = None
        self._reserve_below: QWidget | None = None
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SCROLL_SETTLE_MS)
        self._settle.timeout.connect(self.fill)
        preview_scheduler().thumbnail_room.connect(self.fill)
        self._equalise_captions()
        self._reflow(max(self.width(), TILE_MIN_WIDTH_PX))

    # ------------------------------------------------------------------ state

    def column_count(self) -> int:
        return self._columns

    def tiles(self) -> list[AtlasTile]:
        return list(self._tiles)

    def shown_tiles(self) -> list[AtlasTile]:
        """The tiles the filter lets through, in sheet order."""

        return list(self._shown)

    def keep_room_for(self, widget: QWidget) -> None:
        """Size the tiles so ``widget`` (what follows the sheet) still shows, up
        to ``RESERVE_BELOW_MAX_FRACTION`` of the visible page."""

        self._reserve_below = widget

    def set_filter(self, name: AtlasFilter) -> None:
        wanted = {row.atlas_id for row in self._sheet.tiles_for(name)}
        self._shown = [tile for tile in self._tiles if tile.row.atlas_id in wanted]
        self._reflow(self._width, force=True)
        QTimer.singleShot(0, self.fill)

    # ----------------------------------------------------------------- layout

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        self._reflow(event.size().width())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt signature
        # The scroll area's viewport: a taller or shorter window changes the
        # height the tiles may take without changing the sheet's width.
        if event.type() == QEvent.Type.Resize and self._area is not None and watched is self._area.viewport():
            self._reflow(self._width)
        return super().eventFilter(watched, event)

    def _height_budget(self) -> int | None:
        """The visible height the sheet may fill, or ``None`` before it is in a
        shown scroll area (the sheet then packs, as it is built under the
        loading overlay)."""

        area = self._area
        if area is None or area.widget() is None or not self.isVisible():
            return None
        visible = area.viewport().height()
        top = self.mapTo(area.widget(), QPoint(0, 0)).y()
        reserve = 0
        if self._reserve_below is not None:
            reserve = min(self._reserve_below.sizeHint().height() + TILE_GAP_PX, round(visible * RESERVE_BELOW_MAX_FRACTION))
        return visible - top - reserve

    def _tile_chrome(self) -> int:
        if not self._tiles:
            return 0
        tile = self._tiles[0]
        return tile.sizeHint().height() - tile.picture.height()

    def _reflow(self, width: int, *, force: bool = False) -> None:
        columns, tile_width = fit_tiles(len(self._shown), width, self._height_budget(), self._tile_chrome())
        self._width = width
        if not force and (columns, tile_width) == (self._columns, self._tile_width):
            return
        self._tile_width = tile_width
        if columns == self._columns and not force:
            self._fit_pictures(tile_width)
            return
        # One owned grid throughout filtering/resizing. Reset the spare-width
        # column before reusing it; never delete a layout while showing tiles
        # can trigger another resize.
        grid = self._grid
        while grid.count():
            grid.takeAt(0)
        for column in range(grid.columnCount()):
            grid.setColumnStretch(column, 0)
        # The tiles keep their width (``_fit_pictures``); when the height, not
        # the width, limits them, the spare width gathers at the right.
        grid.setColumnStretch(columns, 1)
        for index, tile in enumerate(self._shown):
            grid.addWidget(tile, index // columns, index % columns, Qt.AlignmentFlag.AlignTop)
        self._columns = columns
        self._fit_pictures(tile_width)
        shown = {id(tile) for tile in self._shown}
        for tile in self._tiles:
            tile.setVisible(id(tile) in shown)

    def _equalise_captions(self) -> None:
        """Every caption as tall as the tallest, so every tile is one height
        whatever it has to say (the pictures already share one size). Over all
        tiles, not the shown ones, so a filter does not change the shape."""

        for tile in self._tiles:
            tile.caption.setMinimumHeight(0)
            tile.caption.ensurePolished()
        tallest = max((tile.caption.sizeHint().height() for tile in self._tiles), default=0)
        for tile in self._tiles:
            tile.caption.setMinimumHeight(tallest)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt signature
        super().changeEvent(event)
        if event.type() in {QEvent.Type.StyleChange, QEvent.Type.FontChange}:
            self._equalise_captions()

    def _fit_pictures(self, tile_width: int) -> None:
        size = _picture_size(tile_width)
        for tile in self._tiles:
            tile.picture.set_display_size(size)
            tile.setFixedWidth(tile_width)
        self.updateGeometry()

    # ----------------------------------------------------------------- pictures

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt signature
        super().showEvent(event)
        self._equalise_captions()
        if self._area is None:
            area = self.parentWidget()
            while area is not None and not isinstance(area, QScrollArea):
                area = area.parentWidget()
            if isinstance(area, QScrollArea):
                # A method of this widget, not a lambda: the dashboard's scroll
                # area outlives the sheet, and only a connection with this
                # widget as its receiver is dropped when the sheet is deleted.
                # The event filter goes with the sheet the same way.
                self._area = area
                area.verticalScrollBar().valueChanged.connect(self._scrolled)
                area.viewport().installEventFilter(self)
        # Once the page is laid out, so the sheet's place on it is known.
        QTimer.singleShot(0, self._refit)

    def _refit(self) -> None:
        self._reflow(self._width)
        self.fill()

    def _scrolled(self, _value: int) -> None:
        self._settle.start()

    def fill(self) -> None:
        """Ask for the pictures: tiles in view first, then the rest in sheet order,
        until the lane's queue is full. It continues when ``thumbnail_room`` is
        emitted. A tile in view that finds the queue full takes the place of one
        out of view, which asks again later: what is on screen is what waits least."""

        if self._filling or not self.isVisible():
            return
        self._filling = True
        try:
            waiting = [
                tile for tile in self._shown
                if tile.isVisible() and not tile.picture.is_loaded() and not tile.picture.has_failed()
            ]
            in_view = [tile for tile in waiting if tile.in_view()]
            # Asked in reverse, each to the front: the first in view ends up first.
            for tile in reversed(in_view):
                while not tile.picture.request(urgent=True):
                    if not self._withdraw_one_out_of_view():
                        break
            for tile in waiting:
                if tile not in in_view and tile.picture.needs_request() and not tile.picture.request():
                    break
        finally:
            self._filling = False

    def _withdraw_one_out_of_view(self) -> bool:
        """Give up the request of the last tile that is waiting out of view."""

        for tile in reversed(self._shown):
            if tile.picture.is_requested() and not tile.in_view():
                tile.picture.cancel()
                return True
        return False


# ---------------------------------------------------------------------- the page


def _filter_control(sheet: ContactSheet, model: AtlasSheetModel) -> tuple[QFrame, dict[AtlasFilter, QToolButton]]:
    """All · Missing · With collection, each with its count; a segment with nothing
    behind it is dimmed and cannot be chosen."""

    control = QFrame()
    control.setObjectName("segmentedControl")
    control.setAccessibleName("Atlas filter")
    layout = QHBoxLayout(control)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    group = QButtonGroup(control)
    group.setExclusive(True)
    buttons: dict[AtlasFilter, QToolButton] = {}
    for name in AtlasFilter:
        count = model.count(name)
        button = QToolButton(control)
        button.setObjectName("segmentButton")
        button.setText(f"{name.value} {count}")
        button.setCheckable(True)
        button.setChecked(name is AtlasFilter.ALL)
        button.setEnabled(count > 0)
        button.setToolTip(
            {
                AtlasFilter.ALL: "Every atlas in this scope",
                AtlasFilter.MISSING: "Atlases whose image files were not found",
                AtlasFilter.WITH_COLLECTION: "Atlases used by a loaded collection sample",
            }[name]
        )
        button.clicked.connect(lambda _checked=False, selected=name: sheet.set_filter(selected))
        group.addButton(button)
        layout.addWidget(button)
        buttons[name] = button
    return control, buttons


class AtlasSection(QWidget):
    """The "Atlases" heading, its filters and the sheet; a link elsewhere on the
    page (an outcome row) can narrow it through ``show_filter``."""

    def __init__(self, model: AtlasSheetModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("atlasSheetSection")
        stack = QVBoxLayout(self)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(SPACE_M)
        head = QHBoxLayout()
        head.setSpacing(SPACE_M)
        title = QLabel("Atlases")
        apply_tier(title, TIER_LANDMARK)
        head.addWidget(title)
        head.addStretch(1)
        self.sheet = ContactSheet(model)
        control, self._buttons = _filter_control(self.sheet, model)
        head.addWidget(control)
        stack.addLayout(head)
        stack.addWidget(self.sheet)

    @Slot(str)
    def show_filter(self, name: str) -> None:
        try:
            chosen = AtlasFilter(name)
        except ValueError:
            return
        button = self._buttons[chosen]
        if button.isEnabled():
            button.setChecked(True)
            self.sheet.set_filter(chosen)


def _kind_line(dashboard: SessionDashboard, model: DashboardModel, summary: AtlasSessionSummary) -> QWidget | None:
    """"Atlas screening · collection data in <session>": what this session is, and
    where its grids were collected when that is loaded. For a session only; a
    sample of one has its own kind."""

    if model.kind != SessionKind.ATLAS_SCREENING.value:
        return None
    line = QWidget()
    line.setObjectName("atlasKindLine")
    row = QHBoxLayout(line)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)

    def plain(text: str) -> QLabel:
        label = QLabel(text)
        apply_tier(label, TIER_SUPPORTING)
        return label

    row.addWidget(plain("Atlas screening"))
    sessions = summary.collection_sessions
    if sessions:
        row.addWidget(plain(" · collection data in "))
        for index, session in enumerate(sessions):
            if index:
                row.addWidget(plain(", "))
            link = QToolButton()
            link.setObjectName("tableLink")
            link.setText(session.name)
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.setToolTip(f"Open {session.name}'s summary")
            link.setAccessibleName(f"Open {session.name} summary")
            link.clicked.connect(lambda _checked=False, session_id=session.id: dashboard.scope_requested.emit(session_id))
            row.addWidget(link)
    return line


def build_atlas_session_summary_page(
    dashboard: SessionDashboard,
    model: DashboardModel,
    summary: AtlasSessionSummary,
) -> list[QWidget]:
    """The page's sections, top to bottom."""

    section = AtlasSection(summary.sheet)
    section.sheet.opened.connect(lambda atlas_id: dashboard._on_stat_tile_clicked(atlas_id, TabKey.ATLAS))  # noqa: SLF001
    dashboard.page_filter_requested.connect(section.show_filter)
    attention = build_needs_attention(dashboard, summary.needs_attention)
    section.sheet.keep_room_for(attention)
    return [
        build_summary_header(dashboard, model, extra=_kind_line(dashboard, model, summary)),
        section,
        attention,
    ]
