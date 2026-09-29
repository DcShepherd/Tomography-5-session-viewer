"""The native window frame follows the app theme (plan F5.8a).

Windows draws the title bar itself, so the app's style sheet never reached it:
a light app kept a dark title bar, or the reverse, whatever the system theme.
Windows 10 (20H1 and later) and 11 accept a per-window dark-mode attribute.
Elsewhere, and whenever the call fails, the frame is simply left as it is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import sys

from PySide6.QtWidgets import QWidget

LOGGER = logging.getLogger(__name__)

#: ``DWMWA_USE_IMMERSIVE_DARK_MODE``, and its number before Windows 10 20H1.
DWM_DARK_MODE_ATTRIBUTE = 20
DWM_DARK_MODE_ATTRIBUTE_BEFORE_20H1 = 19
_RDW_INVALIDATE = 0x0001
_RDW_FRAME = 0x0400


@dataclass(frozen=True)
class WindowFrameApi:
    """The two calls this needs: set a DWM attribute, and redraw a frame."""

    set_attribute: Callable[[int, int, int], int]  # (hwnd, attribute, value) -> HRESULT
    redraw_frame: Callable[[int], None]


def _windows_api() -> WindowFrameApi | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None

    def set_attribute(hwnd: int, attribute: int, value: int) -> int:
        data = ctypes.c_int(value)
        return int(
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), ctypes.c_uint(attribute), ctypes.byref(data), ctypes.sizeof(data)
            )
        )

    def redraw_frame(hwnd: int) -> None:
        # Otherwise the new colour shows only at the next activation.
        user32.RedrawWindow(ctypes.c_void_p(hwnd), None, None, _RDW_FRAME | _RDW_INVALIDATE)

    return WindowFrameApi(set_attribute, redraw_frame)


def set_title_bar_dark(window: QWidget, dark: bool, *, api: WindowFrameApi | None = None) -> bool:
    """Draw ``window``'s native title bar dark or light; True when applied.

    Only for a shown window: asking an unshown one for its native handle
    would create the platform window early.
    """

    if not window.isWindow() or not window.isVisible():
        return False
    frame_api = api if api is not None else _windows_api()
    if frame_api is None:
        return False
    try:
        hwnd = int(window.winId())
        value = 1 if dark else 0
        result = frame_api.set_attribute(hwnd, DWM_DARK_MODE_ATTRIBUTE, value)
        if result != 0:
            result = frame_api.set_attribute(hwnd, DWM_DARK_MODE_ATTRIBUTE_BEFORE_20H1, value)
        if result != 0:
            return False
        frame_api.redraw_frame(hwnd)
        return True
    except (OSError, ValueError, TypeError):
        LOGGER.debug("Could not theme the native title bar", exc_info=True)
        return False
