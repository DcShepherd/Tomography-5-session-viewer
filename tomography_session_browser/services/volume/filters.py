"""Display-only full-plane Fourier filters, independent of viewport crops."""
from __future__ import annotations

import math
import numpy as np

from tomography_session_browser.services.volume.memory import VOLUME_MEMORY, BudgetExceeded, MIB

TRANSFER_KIND = "filter-transfer"
#: Room for one transfer function per Ortho pane at typical plane sizes.
TRANSFER_LIMIT = 32 * MIB


def transfer_function(height: int, width: int, lowpass: float, highpass: float) -> np.ndarray:
    fy, fx = np.fft.fftfreq(height)[:, None], np.fft.rfftfreq(width)[None, :]
    transfer = np.ones((height, width // 2 + 1), dtype=np.float64)
    if lowpass:
        transfer *= np.exp(-.5 * (fx * fx + fy * fy) / lowpass ** 2)
    if highpass:
        transfer *= -np.expm1(-.5 * ((fx * width) ** 2 + (fy * height) ** 2) / highpass ** 2)
    return transfer


def remember_transfer(key: tuple, transfer: np.ndarray) -> None:
    """Keep a transfer function in the evictable budget when there is room; never required.

    Building one costs about as much as half the filter's FFTs, and a rotation
    burst filters planes of the same shape and settings on every frame.
    """
    for attempt in range(2):
        try:
            lease = VOLUME_MEMORY.reserve(transfer.nbytes, TRANSFER_KIND, evict=False, group_limit=TRANSFER_LIMIT)
        except BudgetExceeded:
            if attempt:
                return
            VOLUME_MEMORY.retire_group(TRANSFER_KIND)  # A new shape or setting replaces the old ones.
            continue
        VOLUME_MEMORY.publish(key, transfer, lease)
        return


def filter_plane(plane: np.ndarray, lowpass: float, highpass: float) -> np.ndarray:
    if not (math.isfinite(lowpass) and math.isfinite(highpass) and 0 <= lowpass <= .5 and 0 <= highpass <= 10000):
        raise ValueError("Filter sigma must be 0–0.5 cycles/px and radius 0–10000 Fourier pixels.")
    if not lowpass and not highpass:
        return plane
    finite = np.isfinite(plane)
    if not finite.any():
        raise ValueError("This plane contains no finite image values.")
    values = np.where(finite, plane, np.mean(plane[finite], dtype=np.float64))
    height, width = values.shape
    key = (TRANSFER_KIND, height, width, lowpass, highpass)
    with VOLUME_MEMORY.borrow(key) as transfer:
        if transfer is None:
            transfer = transfer_function(height, width, lowpass, highpass)
            remember_transfer(key, transfer)
        result = np.fft.irfft2(np.fft.rfft2(values) * transfer, s=values.shape).astype(np.float32)
    result[~finite] = np.nan
    return result
