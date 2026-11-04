"""User-entered text shown exactly as typed in tooltips, buttons and menus."""
from html import escape


def literal_tooltip(text: str) -> str:
    """Rich text that renders ``text`` literally, line breaks included.

    Qt shows a tooltip as plain text unless it looks like markup, so escaped
    text alone would display entities such as ``&amp;``.
    """
    if not text:
        return ""
    return f"<p style='white-space:pre-wrap'>{escape(text, quote=False)}</p>"


def literal_mnemonic(text: str) -> str:
    """Button and menu text in which ``&`` is a character, not a mnemonic."""
    return text.replace("&", "&&")
