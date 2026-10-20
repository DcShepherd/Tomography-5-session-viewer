"""The application style sheet's QSS template, in the order it applies.

``QSS_TEMPLATE`` is the fragments below, joined. Order matters: a later rule
overrides an earlier one of the same specificity, so do not reorder these
without checking the built style sheet.
"""

from __future__ import annotations

from tomography_session_browser.ui.stylesheets import (
    shell,
    canvas_chrome,
    viewer_controls,
    cards_and_chips,
    type_tiers,
    intake_and_empty_states,
    interactive_controls,
)

_FRAGMENTS = (
    shell.TEMPLATE,
    canvas_chrome.TEMPLATE,
    viewer_controls.TEMPLATE,
    cards_and_chips.TEMPLATE,
    type_tiers.TEMPLATE,
    intake_and_empty_states.TEMPLATE,
    interactive_controls.TEMPLATE,
)

QSS_TEMPLATE = "".join(_FRAGMENTS)
