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


