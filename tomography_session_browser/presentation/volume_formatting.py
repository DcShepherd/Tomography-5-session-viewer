"""Shared display formatting for volume metadata, never scientific derivation."""
import numpy as np

def file_size(size: int) -> str:
    megabytes = size / 1_000_000
    return f"{megabytes:,.1f} MB" if megabytes < 10 else f"{megabytes:,.0f} MB"


def mode_text(dtype: str) -> str:
    """``<f2`` reads as float16; an unknown code is shown as recorded."""
    try:
        return np.dtype(dtype).name if dtype else "Unknown"
    except TypeError:
        return dtype


def triple(values) -> str:
    return ", ".join(f"{value:g}" if isinstance(value, float) else str(value) for value in values)


def per_axis_text(values, form: str = "g", missing: str = "?") -> str:
    """One value when every axis reads the same, otherwise ``a × b × c``.

    Display text only: it compares the formatted texts, so float32 noise such as
    7.840000153 / 7.839999729 reads as one "7.84". Equal texts never establish
    isotropy for calibration (that is ``services.volume.render.uniform_spacing``).
    """
    texts = [missing if value is None else format(value, form) for value in values]
    return texts[0] if len(set(texts)) == 1 else " × ".join(texts)


