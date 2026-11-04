"""Unit-safe display text shared by plain metadata and typed Context views."""
import re

# Display-only glue for values. A number stays with its unit, and a unit such
# as "Å/px" never breaks at its slash (Qt's line breaker allows a break after
# "/"). Both characters are removed again when a value is copied.
_WORD_JOINER = "⁠"
_NO_BREAK_SPACE = " "
_UNIT_TOKENS = frozenset(
    {
        "Å", "Å/px", "nm", "nm/px", "µm", "μm", "µm/px", "μm/px", "um", "mm", "pm",
        "px", "kV", "V", "eV", "keV", "s", "ms", "min", "h", "°", "%", "×", "Hz",
        "fps", "e/Å²", "e⁻/Å²", "e-/Å²", "e/Å^2", "MB", "GB", "KB",
    }
)
_NUMBER_RE = re.compile(r"^[-+±~≈]?\d[\d.,]*$")


def display_value(value: str) -> str:
    """Return ``value`` with display-only glue that keeps units intact."""

    words = value.split(" ")
    glued: list[str] = []
    for word in words:
        if "/" in word.strip("/") and not word.startswith("/"):
            word = word.replace("/", f"{_WORD_JOINER}/{_WORD_JOINER}")
        glued.append(word)
    if not glued:
        return value
    result = glued[0]
    for previous, raw, word in zip(words, words[1:], glued[1:]):
        unit = raw.rstrip(",;:)")
        number = previous.lstrip("(")
        separator = _NO_BREAK_SPACE if unit in _UNIT_TOKENS and _NUMBER_RE.match(number) else " "
        result += separator + word
    return result


def clean_copied_text(text: str) -> str:
    """Undo :func:`display_value` so copied values are the source text."""

    return text.replace(_WORD_JOINER, "").replace(_NO_BREAK_SPACE, " ")


