"""Strict scalar review state codec; never opens a source."""
from dataclasses import fields
import math
from tomography_session_browser.presentation.tomogram_state import PaneGeometry, ReviewSegment, TomogramViewState
from tomography_session_browser.presentation.tomogram_tabs import restored_sort
from tomography_session_browser.services.volume.scale_bar import decode_style


def decode_state(data: dict) -> TomogramViewState:
    if not isinstance(data, dict):
        raise ValueError("Saved state must be an object")
    allowed = {field.name for field in fields(TomogramViewState)}
    values = {key: value for key, value in data.items() if key in allowed}
    values["segment"] = ReviewSegment(values.get("segment", "all"))
    values["sort"], _ = restored_sort(values.get("sort", "Name"))
    values["scale_bar"] = decode_style(values.get("scale_bar", {}))
    for key, size in (("centre", 2), ("volume_signature", 2), ("cross_xyz", 3), ("capture_region", 4), ("centre_xyz", 3), ("dividers", 4)):
        value = values.get(key)
        if value is not None:
            if not isinstance(value, (list, tuple)) or len(value) != size or any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
                raise ValueError("Invalid saved coordinates")
            values[key] = tuple(value)
    for key in ("plane", "slices"):
        value = values.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("Invalid saved plane/count")
    for key in ("zoom", "black", "white", "spacing_override", "lowpass", "highpass", "linked_scale", "brightness", "contrast"):
        value = values.get(key)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("Invalid saved scalar")
    for key in ("row_key", "volume_id", "query"):
        if key in values and values[key] is not None and not isinstance(values[key], str):
            raise ValueError("Invalid saved identity")
    if "query" in values and not isinstance(values["query"], str):
        raise ValueError("Invalid saved query")
    for key in ("show_bar", "smooth", "invert", "linked", "ortho_lines"):
        if key in values and type(values[key]) is not bool:
            raise ValueError("Invalid saved flag")
    panes = values.get("pane_views", [])
    if not isinstance(panes, (list, tuple)) or len(panes) > 3:
        raise ValueError("Invalid saved pane views")
    decoded = []
    for pane in panes:
        if not isinstance(pane, dict) or pane.get("axis") not in ("XY", "XZ", "YZ"):
            raise ValueError("Invalid saved pane")
        scalar = decode_state({key: value for key, value in pane.items() if key in ("zoom", "centre")})
        decoded.append(PaneGeometry(pane["axis"], scalar.zoom, scalar.centre))
    if len({pane.axis for pane in decoded}) != len(decoded):
        raise ValueError("Duplicate saved pane")
    values["pane_views"] = tuple(decoded)
    anchor = values.get("levels_anchor")
    if anchor is not None:
        if not isinstance(anchor, (list, tuple)) or len(anchor) != 2 or anchor[0] not in ("XY", "XZ", "YZ") or type(anchor[1]) is not int or anchor[1] < 0:
            raise ValueError("Invalid levels anchor")
        values["levels_anchor"] = tuple(anchor)
    state = TomogramViewState(**values)
    from dataclasses import replace
    from tomography_session_browser.services.volume.oblique import coordinates
    rotation = coordinates(state.rotation)
    pivot = coordinates(state.pivot_xyz, optional=True)
    grid_pivot = coordinates(state.pivot_grid_xyz, optional=True)
    if (pivot is None) != (grid_pivot is None) or any(type(v) is not bool for v in (state.pivot_locked, state.orientation_visible)):
        raise ValueError("Invalid saved oblique geometry")
    assert rotation is not None
    if type(state.rotation_axis) is not int or state.rotation_axis not in (0, 1, 2):
        raise ValueError("Invalid active rotation axis")
    state = replace(state, rotation=rotation, pivot_xyz=pivot, pivot_grid_xyz=grid_pivot)
    if state.levels_mode not in ("Auto", "±3σ", "Manual") or not -1 <= state.brightness <= 1 or not .1 <= state.contrast <= 10:
        raise ValueError("Invalid saved contrast")
    if state.linked_scale is not None and state.linked_scale <= 0:
        raise ValueError("Invalid linked scale")
    if state.dividers is not None and any(type(v) is not int or not 0 <= v <= 100000 for v in state.dividers):
        raise ValueError("Invalid divider sizes")
    if state.axis not in ("XY", "XZ", "YZ") or state.layout not in ("Single", "Ortho") or not 1 <= state.slices <= 101 or state.slices % 2 != 1:
        raise ValueError("Unsupported saved layout or averaging")
    if not 0 <= state.lowpass <= .5 or not 0 <= state.highpass <= 10000:
        raise ValueError("Invalid saved filters")
    if state.zoom is not None and not .01 <= state.zoom <= 100:
        raise ValueError("Invalid saved zoom")
    if state.spacing_override is not None and state.spacing_override <= 0:
        raise ValueError("Invalid saved calibration")
    if state.volume_signature is not None and any(type(v) is not int or v < 0 for v in state.volume_signature):
        raise ValueError("Invalid saved source signature")
    if state.cross_xyz is not None and any(type(v) is not int or v < 0 for v in state.cross_xyz):
        raise ValueError("Invalid saved intersection")
    if state.capture_region is not None:
        x0, y0, x1, y1 = state.capture_region
        if not (0 <= x0 < x1 and 0 <= y0 < y1):
            raise ValueError("Invalid captured region")
    return state

