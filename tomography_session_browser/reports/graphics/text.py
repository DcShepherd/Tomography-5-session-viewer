"""Text report components."""
from __future__ import annotations
from reportlab.lib import colors
from tomography_session_browser.reports import styles


def ellipsis() -> str:
    return "…" if styles.has_unicode_fonts() else "..."


def fit_text(text: str, font: str, size: float, width: float) -> str:
    """``text``, cut with an ellipsis so it fits ``width``."""

    if styles.string_width(text, font, size) <= width:
        return text
    mark = ellipsis()
    while text and styles.string_width(text + mark, font, size) > width:
        text = text[:-1]
    return text.rstrip() + mark


def caption_width(text: str, size: float = styles.TYPE_CAPTION, font: str = styles.SANS_SEMIBOLD) -> float:
    text = text.upper()
    return styles.string_width(text, font, size) + styles.CAPTION_TRACKING * max(len(text) - 1, 0)


def draw_caption(
    canvas,
    x: float,
    y: float,
    text: str,
    *,
    colour: colors.Color = styles.TEXT_MUTED,
    size: float = styles.TYPE_CAPTION,
    font: str = styles.SANS_SEMIBOLD,
) -> None:
    """An uppercase, lightly tracked caption: the app's card-title tier.

    Character spacing is PDF graphics state and outlives the text object, so
    the caption draws inside its own save/restore; otherwise every string
    drawn after it (a card's value, a page-number form) would be tracked too.
    """

    styles.ensure_fonts()
    canvas.saveState()
    text_object = canvas.beginText(x, y)
    text_object.setFont(font, size)
    text_object.setCharSpace(styles.CAPTION_TRACKING)
    text_object.setFillColor(colour)
    text_object.textOut(text.upper())
    canvas.drawText(text_object)
    canvas.restoreState()


def html_escape(text: str | None) -> str:
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

