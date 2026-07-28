"""Single statistic card for the Session dashboard.

Layout (top to bottom):

    LABEL                   ← small uppercase title
    VALUE                   ← large count (e.g. ``21``)
    ✓ N complete · ⚠ N warnings    ← status chip strip
    [tile][tile][tile][...]        ← StatusTileGrid, OR grouped fallback
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.ui.session_presenter import (
    TILE_STATUS_COMPLETE,
    TILE_STATUS_FAILED,
    TILE_STATUS_MISSING,
    TILE_STATUS_NEUTRAL,
    TILE_STATUS_WARNING,
    StatCardModel,
    StatusTileModel,
)
from tomography_session_browser.ui.widgets.dashboard_card import (
    DASHBOARD_CARD_MARGINS,
    DASHBOARD_CARD_SPACING,
)
from tomography_session_browser.ui.widgets.status_tile_grid import StatusTileGrid


# Chip text and label for each status state (in display order). Keep
# definitions close to the rendering so designer-style tweaks don't need to
# chase the constants across modules.
_STATUS_CHIP_ORDER = (
    TILE_STATUS_COMPLETE,
    TILE_STATUS_WARNING,
    TILE_STATUS_FAILED,
    TILE_STATUS_MISSING,
    TILE_STATUS_NEUTRAL,
)
# U+FE0E (VARIATION SELECTOR-15) forces text presentation. Without it Windows
# resolves U+26A0 through Segoe UI Emoji, so the warning chip rendered as a
# full-colour emoji triangle beside monochrome, theme-tinted check and cross
# glyphs — and ignored the status colour applied to it.
_TEXT_PRESENTATION = "︎"
_STATUS_GLYPHS = {
    TILE_STATUS_COMPLETE: "✓",
    TILE_STATUS_WARNING: f"⚠{_TEXT_PRESENTATION}",
    TILE_STATUS_FAILED: "✕",
    TILE_STATUS_MISSING: "✕",
    TILE_STATUS_NEUTRAL: "•",
}
#: What each count card actually measures. The Search maps card and the
#: "Search maps overview" panel lower down the dashboard classify the same
#: maps by different questions — one asks whether the map's own tiles were
#: acquired, the other whether its batch positions produced tilt series — so
#: both have to say which question they answered.
_CARD_HELP = {
    "Atlases": "Atlas images available in this scope.",
    "Overviews": "Overview images available in this scope.",
    "Search maps": (
        "Search maps in this scope, classified by whether the map's own tiles were acquired.\n"
        "The Search maps overview panel lower down classifies the same maps by whether their "
        "batch positions produced tilt series, so the two breakdowns differ by design."
    ),
    "Batch positions": "Acquisition targets parsed from BatchPositionsList.xml.",
    "Tilt series": "Acquired MRC stacks, classified by tilt-series validation.",
}
_STATUS_LABELS = {
    TILE_STATUS_COMPLETE: "complete",
    TILE_STATUS_WARNING: "warnings",
    TILE_STATUS_FAILED: "failed",
    TILE_STATUS_MISSING: "missing",
    TILE_STATUS_NEUTRAL: "loaded",
}


class StatCard(QFrame):
    """One block in the dashboard's "counts" row.

    Two outgoing signals:

    * ``clicked(destination)`` — the user clicked the card chrome (header /
      number); the parent navigates to that tab.
    * ``tile_clicked(item_id, destination)`` — re-emitted from the embedded
      tile grid; the parent navigates to the tab AND selects that item.
    """

    clicked = Signal(str)
    tile_clicked = Signal(str, str)

    def __init__(
        self,
        model: StatCardModel,
        *,
        accent: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = model
        self._destination = model.destination or model.label
        from tomography_session_browser.ui.theme import current_palette

        self._accent = accent or current_palette().accent
        self.setObjectName("dashboardCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Open {model.label}")
        self.setAccessibleDescription(
            f"{model.value} {model.label.lower()} in this scope. Press Enter or Space to open the list."
        )
        # Stable footprint: we deliberately do NOT grow taller for cards with
        # many items — the tile grid manages its own internal sizing and
        # falls back to grouped chips when needed.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(134)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*DASHBOARD_CARD_MARGINS)
        layout.setSpacing(DASHBOARD_CARD_SPACING)

        title = QLabel(model.label.upper())
        title.setObjectName("cardTitle")
        title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        help_text = _CARD_HELP.get(model.label)
        if help_text:
            title.setToolTip(help_text)
            self.setToolTip(help_text)
        layout.addWidget(title)

        value_label = QLabel(str(model.value))
        value_label.setObjectName("cardValue")
        layout.addWidget(value_label)

        chip_strip = self._build_chip_strip(model)
        layout.addWidget(chip_strip)

        # Tile grid sits in the remaining vertical space. The ``visible_grid``
        # check happens after the first paint when we know the actual width;
        # until then we always include the grid widget. If the grid hides
        # itself it becomes a zero-height invisible row, and the grouped
        # fallback inside the chip strip already covers the information.
        if model.items:
            self._grid = StatusTileGrid(model.items, accent=self._accent, parent=self)
            self._grid.setMinimumHeight(22)
            self._grid.tile_clicked.connect(self.tile_clicked.emit)
            layout.addWidget(self._grid, stretch=1)
        else:
            self._grid = None
            placeholder = QLabel("No entries in this scope.")
            placeholder.setObjectName("metaPlain")
            placeholder.setWordWrap(True)
            layout.addWidget(placeholder, stretch=1)

    # ----- chip strip --------------------------------------------------------

    def _build_chip_strip(self, model: StatCardModel) -> QWidget:
        """Return a horizontal strip of "✓ 21 complete · ⚠ 0 warnings" chips."""

        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        # Build the displayable chips in a stable order so the same status
        # always sits in the same position from one rebuild to the next.
        status_summary = model.status_summary or {}
        any_chip = False
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        color_map = {
            TILE_STATUS_COMPLETE: theme.chart_green,
            TILE_STATUS_WARNING: theme.chart_amber,
            TILE_STATUS_FAILED: theme.chart_red,
            TILE_STATUS_MISSING: theme.chart_red,
            TILE_STATUS_NEUTRAL: self._accent,
        }
        for status in _STATUS_CHIP_ORDER:
            count = status_summary.get(status, 0)
            # Always show the "complete" chip even when zero so the user
            # immediately sees that nothing succeeded; suppress the others
            # when they have no items to avoid noise.
            if count == 0 and status != TILE_STATUS_COMPLETE:
                # If nothing else has fired and this is a card with items,
                # we need at least one chip — fall through to the neutral
                # chip below. Otherwise skip.
                continue
            any_chip = True
            chip = QLabel(
                f'<span style="color: {color_map[status]}">'
                f"{_STATUS_GLYPHS[status]}</span> "
                f'<span style="color: {theme.text}">{count} {_STATUS_LABELS[status]}</span>'
            )
            chip.setTextFormat(Qt.TextFormat.RichText)
            chip.setStyleSheet("font-size: 9pt;")
            row.addWidget(chip)

        if not any_chip:
            # Fallback if the summary is empty (no items at all).
            empty = QLabel("no items")
            empty.setObjectName("cardSubvalue")
            row.addWidget(empty)

        row.addStretch(1)
        return wrap

    # ----- click handling ----------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            # Only fire the card-level signal when the click missed the tile
            # grid; the grid emits its own ``tile_clicked`` for in-grid hits.
            self.clicked.emit(self._destination)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt signature
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.clicked.emit(self._destination)
            event.accept()
            return
        super().keyPressEvent(event)
