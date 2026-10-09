"""Counts components for the dashboard."""
from __future__ import annotations
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from tomography_session_browser.presentation.dashboard.models import DashboardModel, StatCardModel
from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.stat_card import StatCard, is_zero_value
from tomography_session_browser.ui.dashboard.constants import TAB_BY_LABEL
from tomography_session_browser.ui.dashboard.layout import ResponsiveCardGrid
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def build_counts_row(dashboard: SessionDashboard, model: DashboardModel) -> QWidget:
    # Palette slots, not colours, so a card can recolour in place.
    accents = ("accent", "chart_green", "chart_amber", "chart_blue")
    cards: list[QWidget] = []
    empty: list[StatCardModel] = []
    for index, card_model in enumerate(model.counts):
        # Always populate the destination from the model when present;
        # fall back to a label-driven map so older models still navigate.
        if not card_model.destination:
            card_model.destination = TAB_BY_LABEL.get(card_model.label, card_model.label)
        if is_zero_value(card_model.value):
            empty.append(card_model)
            continue
        # The accent follows the card's place in the model, so a card keeps
        # its colour whether or not an earlier card was folded away.
        card = dashboard._build_stat_card(card_model, accents[index % len(accents)])
        cards.append(card)
    if not empty:
        return ResponsiveCardGrid(cards)
    # Plan C8: a full card saying "0 · No entries in this scope" three
    # times over is noise. Zero counts are still stated, on one line.
    wrap = QWidget()
    stack = QVBoxLayout(wrap)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.setSpacing(SPACE_S)
    if cards:
        stack.addWidget(ResponsiveCardGrid(cards))
    stack.addWidget(dashboard._zero_counts_line(empty))
    return wrap


def zero_counts_line(empty: list[StatCardModel]) -> QLabel:
    # U+00A0 inside each entry, so a wrap falls between entries only.
    entries = (f"{card.label} {card.value}".replace(" ", " ") for card in empty)
    text = " · ".join(entries) + " — none in this scope"
    line = QLabel(text)
    line.setObjectName("dashboardZeroCounts")
    apply_tier(line, TIER_SUPPORTING)
    line.setWordWrap(True)
    line.setAccessibleName(text)
    return line


def build_stat_card(dashboard: SessionDashboard, card_model: StatCardModel, accent_role: str) -> StatCard:
    card = StatCard(model=card_model, accent_role=accent_role)
    card.clicked.connect(dashboard.navigate_requested.emit)
    # Tile clicks navigate AND select the underlying item.
    card.tile_clicked.connect(dashboard._on_stat_tile_clicked)
    return card
