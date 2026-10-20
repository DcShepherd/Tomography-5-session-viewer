"""What a page says about the kinds it does not hold (design review B3.5)."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel

from tomography_session_browser.presentation.dashboard.models import StatCardModel
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier


def is_zero_value(value: object) -> bool:
    """Whether a card's headline value means "there is nothing here".

    Tolerant of the string values some callers pass ("0", "—", "").
    The sample cards use the same test to fold zero-count cards into one line,
    so a card and the line can never disagree about what counts as empty.
    """

    if value is None:
        return True
    if isinstance(value, (int, float)):
        return value == 0
    text = str(value).strip()
    return text in {"", "0", "-", "—", "–", "n/a", "N/A"}


def zero_counts_line(empty: list[StatCardModel]) -> QLabel:
    """"Atlases 0 · Overviews 0 — none in this scope": the kinds the scope lacks, on one line."""

    # U+00A0 inside each entry, so a wrap falls between entries only.
    entries = (f"{card.label} {card.value}".replace(" ", "\u00a0") for card in empty)
    text = " · ".join(entries) + " — none in this scope"
    line = QLabel(text)
    line.setObjectName("dashboardZeroCounts")
    apply_tier(line, TIER_SUPPORTING)
    line.setWordWrap(True)
    line.setAccessibleName(text)
    return line
