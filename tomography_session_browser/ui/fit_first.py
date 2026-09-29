"""Fit-first degradation for the window chrome (plan F3, D1).

Each piece of chrome lists its presentations from richest to plainest and
takes the first one that fits the room it actually has, measured from real
text widths. This replaces a single workspace breakpoint (1420 px) that turned
every label off at once: with both panels open the window had to be about
1975 px wide before any label appeared, so a maximised laptop or a 1080p
monitor showed seven unlabelled tab icons beside 750 px of empty tab strip.

Pure and Qt-free, so the choice itself is unit-testable. The widths are
measured by the widgets that own them. Like the breakpoint it replaces, every
choice here is a transient observation of the current size, never a setting.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Tab presentations, richest first. The count is information, so it outlives
#: the name: the last step keeps only the icon, with everything in the tooltip.
TAB_STEP_FULL = 0
TAB_STEP_SHORT = 1
TAB_STEP_COUNT = 2
TAB_STEP_ICON = 3
TAB_STEPS = (TAB_STEP_FULL, TAB_STEP_SHORT, TAB_STEP_COUNT, TAB_STEP_ICON)

#: Shorter names for the long tab labels, used before names are dropped.
TAB_SHORT_LABELS = {
    "Search map": "Maps",
    "Search": "Tiles",
    "Batch position": "Positions",
}


def clamped_share(minimum: int, share: float, maximum: int, total: int) -> int:
    """``share`` of ``total``, kept between ``minimum`` and ``maximum`` (plan D2).

    Used for the side panels, which had fixed widths at every size: slivers
    that still wrapped values at 4K, and a workspace crushed to about 420 px
    at the 960 px minimum window.
    """

    return max(minimum, min(maximum, round(total * share)))


def balanced_rows(count: int, columns: int) -> list[int]:
    """Split ``count`` cards into rows of at most ``columns``, as evenly as
    possible, fuller rows first (plan C4).

    Five cards in three columns became 3 + 2 with an empty slot; balanced,
    they are 3 + 2 with the second row stretched. Four in three are 2 + 2
    rather than 3 + 1.
    """

    if count <= 0:
        return []
    columns = max(1, min(columns, count))
    rows = -(-count // columns)  # ceiling division
    base, extra = divmod(count, rows)
    return [base + 1] * extra + [base] * (rows - extra)


def first_fitting_step(required_widths: Sequence[int], available: int) -> int:
    """Index of the first (richest) step whose width fits ``available``.

    The steps are ordered richest first, so their widths only shrink. When
    nothing fits, the plainest step is still the best there is.
    """

    for index, required in enumerate(required_widths):
        if required <= available:
            return index
    return max(len(required_widths) - 1, 0)


def tab_text(label: str, display_label: str, count: int | None, step: int) -> str:
    """The text a tab shows at ``step``; its tooltip always has the full text.

    ``count`` is None for a tab with nothing to count (Session) and for any
    tab before the first count is known; such a tab shows only its name.
    """

    name = TAB_SHORT_LABELS.get(label, display_label) if step == TAB_STEP_SHORT else display_label
    if step in (TAB_STEP_FULL, TAB_STEP_SHORT):
        return name if count is None else f"{name}  {count}"
    if step == TAB_STEP_COUNT and count is not None:
        return str(count)
    return ""
