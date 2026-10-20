"""The sample summary's cards (design review B3.3).

One full-width Tilt series card: the recorded count, the outcomes that can
open rows, and a band with one block per series, kept together by batch
position and named under each group (when the names lose the prefix they
share, the card's heading row gives it once). Under it the smaller cards, one per
kind the sample holds: Batch positions, Search maps, Overviews, and the Atlas
with its picture. A card for a kind the sample does not hold is not drawn; the
page says so on one line.

The cards read the existing count cards (``DashboardModel.counts``) and the
atlas rows; nothing here classifies a status.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from tomography_session_browser.presentation.dashboard.models import (
    TILE_STATUS_COMPLETE,
    AtlasRowModel,
    DashboardModel,
    DashboardFilterModel,
    StatCardModel,
    tile_status_word,
)
from tomography_session_browser.presentation.dashboard.summary import SampleSummary
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.ui.dashboard.common import CardBox, card_layout, status_dot, status_tone
from tomography_session_browser.ui.dashboard.counts import is_zero_value, zero_counts_line
from tomography_session_browser.ui.dashboard.layout import ResponsiveCardGrid
from tomography_session_browser.ui.dashboard.outcome import outcome_headline
from tomography_session_browser.ui.theme import TIER_PRIMARY, TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.growing_atlas_preview import GrowingAtlasPreview
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.status_tile_grid import BAND_HEIGHT_PX, StatusTileGrid

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard

#: Blocks of the tilt-series band: tall enough to be read as a series each.
HERO_BAND_HEIGHT_PX = 30

#: Up to four small cards in a row once each has room for its number and band.
SMALL_CARD_BREAKPOINTS = ((860, 4), (440, 2))
#: The cards under the Tilt series card, in the order they are drawn.
_SMALL_KINDS = (EntityKind.BATCH_POSITION, EntityKind.SEARCH_MAP, EntityKind.OVERVIEW, EntityKind.ATLAS)


def build_sample_cards(dashboard: SessionDashboard, model: DashboardModel) -> QWidget:
    """The Tilt series card, the smaller cards, and the line naming what is absent."""

    cards = {card.kind: card for card in model.counts}
    assert isinstance(model.summary, SampleSummary)
    routes = {route.tab: route for route in model.summary.card_routes}
    wrap = QWidget()
    wrap.setObjectName("sampleCards")
    stack = QVBoxLayout(wrap)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.setSpacing(SPACE_M)
    absent: list[StatCardModel] = []

    tilt_series = cards.get(EntityKind.TILT_SERIES)
    if tilt_series is not None:
        if is_zero_value(tilt_series.value):
            absent.append(tilt_series)
        else:
            stack.addWidget(_tilt_series_card(dashboard, model, tilt_series))

    small: list[QWidget] = []
    for kind in _SMALL_KINDS:
        card = cards.get(kind)
        if card is None:
            continue
        if is_zero_value(card.value):
            absent.append(card)
        elif kind is EntityKind.ATLAS:
            small.append(_atlas_card(dashboard, model, card, routes[card.destination]))
        else:
            small.append(_count_card(dashboard, card, routes[card.destination],
                                     model.summary.inferred_batch_route if kind is EntityKind.BATCH_POSITION else None))
    if small:
        stack.addWidget(ResponsiveCardGrid(small, breakpoints=SMALL_CARD_BREAKPOINTS))
    if absent:
        stack.addWidget(zero_counts_line(absent))
    return wrap


# ------------------------------------------------------------------ card base


class NavigationCard(CardBox):
    """A card that opens exactly its advertised population.

    A click on a status block opens the block's item instead: the band takes
    that press, and only a press beside it reaches the card.
    """

    def __init__(self, dashboard: SessionDashboard, label: str, route: DashboardFilterModel, summary: str) -> None:
        super().__init__()
        self._dashboard = dashboard
        self._route = route
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Open {label}")
        self.setAccessibleDescription(f"{summary} Press Enter or Space to open the list.")
        self.setProperty("cardEmpty", False)
        if not route.count:
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def destination(self) -> str:
        return self._route.tab

    def activate(self) -> None:
        if self._route.count:
            self._dashboard.filter_requested.emit(self._route)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            self.activate()
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt signature
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.activate()
            event.accept()
            return
        super().keyPressEvent(event)


class InferredBatchButton(QToolButton):
    """Return activates the evidence, rather than bubbling to the parent card."""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt signature
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)


def _card_title(text: str) -> QLabel:
    # The card title's rule gives it the muted caption style; the text is as
    # the page names the kind, not capitals.
    title = QLabel(text)
    title.setObjectName("cardTitle")
    title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return title


def _band(dashboard: SessionDashboard, card: StatCardModel, *, hero: bool = False) -> StatusTileGrid:
    band = StatusTileGrid(
        card.items,
        band_height=HERO_BAND_HEIGHT_PX if hero else BAND_HEIGHT_PX,
        group_labels=hero,
    )
    band.tile_clicked.connect(dashboard._on_stat_tile_clicked)
    return band


# ----------------------------------------------------------------------- cards


class TiltSeriesCard(CardBox):
    """The full-width card: how the recorded series came out, and each series' block."""


def _tilt_series_card(dashboard: SessionDashboard, model: DashboardModel, card: StatCardModel) -> QWidget:
    box = TiltSeriesCard()
    layout = card_layout(box)
    layout.setSpacing(SPACE_S)
    head = QHBoxLayout()
    head.setSpacing(SPACE_M)
    head.addWidget(_card_title(card.label))
    head.addStretch(1)
    prefix_note = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle, prefer_full_width=True)
    prefix_note.setObjectName("bandLabelPrefix")
    prefix_note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    apply_tier(prefix_note, TIER_SUPPORTING)
    head.addWidget(prefix_note)
    layout.addLayout(head)
    layout.addWidget(outcome_headline(dashboard, model, noun="recorded"))
    band = _band(dashboard, card, hero=True)
    band.label_prefix_changed.connect(lambda prefix: _show_label_prefix(prefix_note, prefix))
    _show_label_prefix(prefix_note, band.shown_label_prefix())
    layout.addWidget(band)
    return box


def _show_label_prefix(note: ElidedLabel, prefix: str) -> None:
    """Say once what the shortened group names under the band leave out."""

    note.setText(f"Batch positions: {prefix}…" if prefix else "")
    note.setToolTip(f"The names under the band leave out {prefix}, which every batch position here starts with."
                    if prefix else "")
    note.setVisible(bool(prefix))


def _count_card(dashboard: SessionDashboard, card: StatCardModel, route: DashboardFilterModel,
                inferred: DashboardFilterModel | None) -> QWidget:
    summary = f"{route.count} {route.label.lower()} in this scope."
    box = NavigationCard(dashboard, card.label, route, summary)
    layout = card_layout(box)
    layout.addWidget(_card_title(route.label))
    value = QLabel(f"{route.count:,}")
    value.setObjectName("cardValue")
    apply_tier(value, TIER_PRIMARY)
    layout.addWidget(value)
    if inferred is not None:
        detail = QLabel(f"{inferred.label}\nBatch position metadata is absent.")
        detail.setObjectName("cardSubvalue")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        evidence = InferredBatchButton()
        evidence.setObjectName("tableLink")
        evidence.setText("View failed series")
        evidence.setToolTip(inferred.tooltip)
        evidence.setAccessibleDescription(f"{inferred.label}. {inferred.tooltip}")
        evidence.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        evidence.clicked.connect(lambda: dashboard.filter_requested.emit(inferred))
        layout.addWidget(evidence, alignment=Qt.AlignmentFlag.AlignLeft)
        box.setAccessibleDescription(f"{summary} {inferred.label}. {inferred.tooltip} Press Enter or Space to open recorded positions.")
    exceptions = _exceptions(card)
    if exceptions is not None:
        layout.addWidget(exceptions)
    layout.addStretch(1)
    layout.addWidget(_band(dashboard, card))
    return box


def _exceptions(card: StatCardModel) -> QWidget | None:
    """A chip for each status that is not complete ("1 failed"); None when all are.

    Complete is what the band's colour and the card's number already say, so
    it is stated only by its absence (say it once).
    """

    chips = [
        (status, count)
        for status, count in card.status_summary.items()
        if count and status != TILE_STATUS_COMPLETE
    ]
    if not chips:
        return None
    strip = QWidget()
    row = QHBoxLayout(strip)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_S)
    for status, count in chips:
        row.addWidget(status_dot(status_tone(status), compact=True, width=10))
        text = QLabel(f"{count} {tile_status_word(card.label, status)}")
        apply_tier(text, TIER_SUPPORTING)
        row.addWidget(text)
        row.addSpacing(SPACE_XS)
    row.addStretch(1)
    return strip


def _atlas_card(dashboard: SessionDashboard, model: DashboardModel, card: StatCardModel, route: DashboardFilterModel) -> QWidget:
    row = _atlas_row(model)
    name = row.sample_label if row is not None else card.label
    box = NavigationCard(dashboard, "Atlas", route, f"{route.count} atlases in this scope. The atlas {name}.")
    layout = card_layout(box)
    layout.addWidget(_card_title("Atlas"))
    picture = GrowingAtlasPreview(
        row.image_path if row is not None else None,
        description=f"{name} atlas picture",
    )
    layout.addWidget(picture, stretch=1)
    text = QVBoxLayout()
    text.setSpacing(SPACE_XS)
    title = QLabel(name)
    title.setObjectName("cardName")
    apply_tier(title, TIER_PRIMARY)
    title.setWordWrap(True)
    text.addWidget(title)
    if route.count != 1:
        more = QLabel(f"{route.count} atlases")
        more.setObjectName("cardSubvalue")
        text.addWidget(more)
    layout.addLayout(text)
    return box


def _atlas_row(model: DashboardModel) -> AtlasRowModel | None:
    """The atlas the card shows: the first whose files were found."""

    rows = model.atlas_summary.rows if model.atlas_summary is not None else []
    return next((row for row in rows if not row.missing), rows[0] if rows else None)
