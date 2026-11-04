"""The sample summary's card (design review B3.3, plan P7).

One Tilt series card: the recorded count, the outcomes that can open rows,
and a band with one block per series, kept together by batch position and
named under each group (when the names lose the prefix they share, the
heading row gives it once). Under the band, one problem row for each kind
with items that are not complete (its band and what is not complete), then
one inventory line of the rest: the atlas and each kind with nothing to say
but its count. The Tomograms aside sits beside it when there are tomograms.
A kind the sample does not hold is not drawn; the page says so on one line.

The card reads the existing count cards (``DashboardModel.counts``) and the
atlas rows; nothing here classifies a status.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from tomography_session_browser.presentation.dashboard.models import (
    TILE_STATUS_COMPLETE,
    AtlasRowModel,
    DashboardFilterModel,
    DashboardModel,
    StatCardModel,
    tile_status_word,
)
from tomography_session_browser.presentation.dashboard.summary import SampleSummary
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S, SPACE_XL, SPACE_XS
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.ui.dashboard.common import CardIcon, card_title, name_prefix_note, pill, show_name_prefix, status_dot, status_tone
from tomography_session_browser.ui.dashboard.counts import is_zero_value, zero_counts_line
from tomography_session_browser.ui.dashboard.outcome import outcome_headline
from tomography_session_browser.ui.dashboard.route_link import RouteLink
from tomography_session_browser.ui.dashboard.summary_card import SummaryCard, heading_row
from tomography_session_browser.ui.dashboard.tomograms_aside import TomogramsAside, tomograms_aside
from tomography_session_browser.ui.navigation_icons import TAB_ICONS
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.atlas_thumbnail import AtlasThumbnail
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.link_button import link_button
from tomography_session_browser.ui.widgets.status_tile_grid import BAND_HEIGHT_PX, StatusTileGrid

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard

#: Blocks of the tilt-series band: tall enough to be read as a series each.
HERO_BAND_HEIGHT_PX = 30
#: A problem row's columns: its link, then its band, then what is not complete.
PROBLEM_LINK_PX = 200
PROBLEM_CHIPS_PX = 190
ATLAS_THUMB_PX = 36
#: The kinds under the band, in the order their rows and entries are drawn.
_KINDS = (EntityKind.OVERVIEW, EntityKind.SEARCH_MAP, EntityKind.BATCH_POSITION)
REACQUIRED_TOOLTIP = "The atlas of this grid was recorded more than one time. This summary shows one of them."


class TiltSeriesCard(SummaryCard):
    """The sample's card: how the recorded series came out, then the rest of the sample."""


def build_sample_cards(dashboard: SessionDashboard, model: DashboardModel) -> QWidget:
    """The Tilt series card and the line naming what is absent."""

    cards = {card.kind: card for card in model.counts}
    assert isinstance(model.summary, SampleSummary)
    routes = {route.tab: route for route in model.summary.card_routes}
    wrap = QWidget()
    wrap.setObjectName("sampleCards")
    stack = QVBoxLayout(wrap)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.setSpacing(SPACE_M)
    absent: list[StatCardModel] = []
    problems: list[QWidget] = []
    inventory: list[QWidget] = []

    tilt_series = cards.get(EntityKind.TILT_SERIES)
    recorded = tilt_series is not None and not is_zero_value(tilt_series.value)
    if tilt_series is not None and not recorded:
        absent.append(tilt_series)
    atlas = cards.get(EntityKind.ATLAS)
    if atlas is not None:
        if is_zero_value(atlas.value):
            absent.append(atlas)
        else:
            inventory.append(_atlas_link(dashboard, model, routes[atlas.destination]))
    for kind in _KINDS:
        card = cards.get(kind)
        if card is None:
            continue
        if is_zero_value(card.value):
            absent.append(card)
            continue
        link = _count_link(dashboard, card, routes[card.destination])
        inferred = model.summary.inferred_batch_route if kind is EntityKind.BATCH_POSITION else None
        extra = [_inferred_link(dashboard, inferred)] if inferred is not None else []
        chips = _exceptions(card)
        if chips is not None:
            problems.append(_problem_row(link, _band(dashboard, card), chips, extra))
        else:
            inventory += [link, *extra]

    aside = tomograms_aside(dashboard, model.tomograms)
    if recorded or problems or inventory or aside is not None:
        stack.addWidget(_card(dashboard, model, tilt_series if recorded else None, problems, inventory, aside))
    if absent:
        stack.addWidget(zero_counts_line(absent))
    return wrap


def _card(dashboard: SessionDashboard, model: DashboardModel, tilt_series: StatCardModel | None,
          problems: list[QWidget], inventory: list[QWidget], aside: TomogramsAside | None) -> TiltSeriesCard:
    prefix_note = name_prefix_note("bandLabelPrefix")
    heading = heading_row(card_title("Tilt series", TabKey.TILT_SERIES), prefix_note)
    body: list[QWidget] = []
    headline = None
    if tilt_series is not None:
        headline = outcome_headline(dashboard, model, noun="recorded")
        band = _band(dashboard, tilt_series, hero=True)
        band.label_prefix_changed.connect(lambda prefix: _show_label_prefix(prefix_note, prefix))
        _show_label_prefix(prefix_note, band.shown_label_prefix())
        body.append(band)
    else:
        prefix_note.hide()
    body += problems
    if inventory:
        body.append(_inventory(inventory))
    return TiltSeriesCard(heading, headline, body, aside)


def _show_label_prefix(note: ElidedLabel, prefix: str) -> None:
    show_name_prefix(note, prefix, heading="Batch positions", names="names under the band", member="batch position")


def _band(dashboard: SessionDashboard, card: StatCardModel, *, hero: bool = False) -> StatusTileGrid:
    band = StatusTileGrid(
        card.items,
        band_height=HERO_BAND_HEIGHT_PX if hero else BAND_HEIGHT_PX,
        group_labels=hero,
    )
    band.tile_clicked.connect(dashboard._on_stat_tile_clicked)
    return band


# ---------------------------------------------------------------------- rows


def _problem_row(link: RouteLink, band: StatusTileGrid, chips: QWidget, extra: list[QWidget]) -> QWidget:
    """The kind's link, its band and what is not complete, in fixed columns."""

    row_widget = QWidget()
    row_widget.setObjectName("problemRow")
    row = QHBoxLayout(row_widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_L)
    link.setFixedWidth(PROBLEM_LINK_PX)
    chips.setMinimumWidth(PROBLEM_CHIPS_PX)
    row.addWidget(link, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(band, 1, Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(chips, 0, Qt.AlignmentFlag.AlignVCenter)
    for widget in extra:
        row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
    return row_widget


def _inventory(entries: list[QWidget]) -> QWidget:
    """One line of the atlas and the kinds that need no row of their own."""

    line = QWidget()
    line.setObjectName("sampleInventory")
    line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # Its top border is style.
    line.setAccessibleName("Other acquisitions in this sample")
    row = QHBoxLayout(line)
    row.setContentsMargins(0, SPACE_S, 0, 0)
    row.setSpacing(SPACE_XL)
    for entry in entries:
        row.addWidget(entry, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    return line


def _link(route: DashboardFilterModel, dashboard: SessionDashboard, *, name: str, description: str) -> tuple[RouteLink, QHBoxLayout]:
    link = RouteLink(route, lambda: dashboard.filter_requested.emit(route), name=name, description=description)
    row = QHBoxLayout(link)
    row.setContentsMargins(SPACE_S, SPACE_XS, SPACE_S, SPACE_XS)
    row.setSpacing(SPACE_S)
    return link, row


def _count_link(dashboard: SessionDashboard, card: StatCardModel, route: DashboardFilterModel) -> RouteLink:
    """Icon, label and count; opens the kind's rows in its tab."""

    link, row = _link(route, dashboard, name=f"Open {card.label}",
                      description=f"{route.count} {route.label.lower()} in this scope.")
    row.addWidget(CardIcon(TAB_ICONS[route.tab], link))
    row.addWidget(QLabel(card.label))
    count = QLabel(f"{route.count:,}")
    count.setObjectName("routeLinkCount")
    row.addWidget(count)
    row.addStretch(1)
    return link


def _atlas_link(dashboard: SessionDashboard, model: DashboardModel, route: DashboardFilterModel) -> RouteLink:
    """The atlas's picture and name, with no count (plan D6, D13)."""

    row_model = _atlas_row(model)
    name = row_model.sample_label if row_model is not None else "Atlas"
    reacquired = row_model is not None and row_model.reacquired
    link, row = _link(route, dashboard, name="Open Atlas",
                      description=f"The atlas {name}." + (" It was re-acquired." if reacquired else ""))
    row.addWidget(AtlasThumbnail(row_model.image_path if row_model is not None else None,
                                 QSize(ATLAS_THUMB_PX, ATLAS_THUMB_PX), description=f"{name} atlas picture",
                                 placeholder="", parent=link))
    row.addWidget(QLabel("Atlas"))
    title = QLabel(name)
    title.setObjectName("cardName")
    apply_tier(title, TIER_SUPPORTING)
    row.addWidget(title)
    if reacquired:
        mark = pill("Re-acquired", "warn")
        mark.setToolTip(REACQUIRED_TOOLTIP)
        row.addWidget(mark)
    return link


def _atlas_row(model: DashboardModel) -> AtlasRowModel | None:
    """The atlas the entry shows: the first whose files were found."""

    rows = model.atlas_summary.rows if model.atlas_summary is not None else []
    return next((row for row in rows if not row.missing), rows[0] if rows else None)


def _inferred_link(dashboard: SessionDashboard, inferred: DashboardFilterModel) -> QWidget:
    """Failed series with no batch position metadata: their own exact rows, never a batch."""

    evidence = link_button(style="table")
    evidence.setText("View failed series")
    evidence.setToolTip(f"{inferred.label}. Batch position metadata is absent. {inferred.tooltip}")
    evidence.setAccessibleDescription(f"{inferred.label}. {inferred.tooltip}")
    evidence.clicked.connect(lambda: dashboard.filter_requested.emit(inferred))
    return evidence


def _exceptions(card: StatCardModel) -> QWidget | None:
    """A chip for each status that is not complete ("1 failed"); None when all are.

    Complete is what the band's colour and the number already say, so it is
    stated only by its absence (say it once).
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
