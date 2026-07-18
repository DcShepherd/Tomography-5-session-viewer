from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Sequence

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import BatchPosition, TiltSeries
from tomography_session_browser.services.batch_inference import inferred_batch_label_for_tilt


LABEL_FONT_SIZE_PX = 34.0
LABEL_MIN_SCREEN_FONT_SIZE_PX = 17.0
LABEL_MAX_SCREEN_FONT_SIZE_PX = 36.0
LABEL_GAP_PX = 22.0
LABEL_STROKE_WIDTH_PX = 3.2
LABEL_ESTIMATED_CHAR_WIDTH_PX = 18.5
LABEL_BOUNDS_MARGIN_PX = 4.0


@dataclass(frozen=True, slots=True)
class _LabelSeed:
    source: ImageMarker
    text: str
    anchor: tuple[float, float]
    geometry: tuple[float, float, float, float]
    size: tuple[float, float]


@dataclass(frozen=True, slots=True)
class _LabelCandidate:
    bbox: tuple[float, float, float, float]
    placement: str


def batch_label_screen_font_size_px(view_scale: float) -> float:
    """Return the screen-space font size for batch labels.

    The text is drawn in screen space in the Qt viewer. A very small low-zoom
    boost keeps labels readable at fit-to-view, while high zooms are gently
    capped so labels do not grow into inspection-sized annotations.
    """

    scale = max(float(view_scale), 0.01)
    size = LABEL_FONT_SIZE_PX * min(1.0, math.sqrt(scale))
    high_zoom_boost = min(2.0, max(0.0, scale - 1.5) * 0.8)
    return min(
        LABEL_MAX_SCREEN_FONT_SIZE_PX,
        max(
            LABEL_MIN_SCREEN_FONT_SIZE_PX,
            size + high_zoom_boost,
        ),
    )


def compact_batch_label_for_batch(batch: BatchPosition) -> str:
    """Return the short batch identifier used for overlay labels."""

    for value in (batch.name, batch.id):
        label = compact_batch_label(str(value) if value is not None else "")
        if label:
            return label
    return "?"


def compact_batch_label(value: str) -> str:
    text = re.sub(r"\.[^.]+$", "", value.strip())
    if not text:
        return ""
    position = re.search(r"(?:^|[_\-\s])position[_\-\s]*(\d+)$", text, flags=re.IGNORECASE)
    if position:
        return str(int(position.group(1)))
    trailing = re.search(r"(\d+)(?!.*\d)", text)
    if trailing:
        return str(int(trailing.group(1)))
    return text


def exposure_batch_label(marker: ImageMarker) -> str | None:
    if marker.marker_type != MarkerType.EXPOSURE_AREA:
        return None
    base = _as_nonempty_str(marker.metadata.get("batch_label"))
    if base is None:
        base = compact_batch_label(_as_nonempty_str(marker.metadata.get("batch_name")) or marker.linked_object_id or "")
    if not base:
        return None
    exposure_index = _as_int(marker.metadata.get("exposure_index"))
    if exposure_index is not None:
        return base if exposure_index <= 0 else f"{base}.{exposure_index + 1}"
    fallback_index = _fallback_exposure_index(marker)
    if fallback_index is not None:
        return base if fallback_index <= 0 else f"{base}.{fallback_index + 1}"
    return base


def compact_label_for_tilt(tilt: TiltSeries) -> str | None:
    inferred = inferred_batch_label_for_tilt(tilt)
    if inferred is None:
        return None
    base = compact_batch_label(inferred)
    if not base:
        return None
    text = re.sub(r"\.[^.]+$", "", (tilt.name or tilt.id or "").strip())
    match = re.fullmatch(r".+?_\d+_(\d+)", text)
    if match:
        return f"{base}.{int(match.group(1))}"
    return base


def batch_label_markers(
    markers: Sequence[ImageMarker],
    *,
    image_size: tuple[int, int] | None = None,
    exposure_indices: set[int] | None = None,
) -> list[ImageMarker]:
    """Build display-only label markers from exposure and inferred-failed markers.

    The input markers are already in the target image coordinate system. This
    helper only adds text labels and chooses deterministic, in-bounds positions.
    """

    size = image_size or _image_size_from_markers(markers)
    if size is None:
        return []
    width, height = size
    if width <= 0 or height <= 0:
        return []

    seeds: list[_LabelSeed] = []
    for marker in markers:
        if marker.marker_type == MarkerType.EXPOSURE_AREA:
            if exposure_indices is not None:
                exposure_index = _exposure_index(marker)
                if exposure_index not in exposure_indices:
                    continue
            text = exposure_batch_label(marker)
        elif marker.marker_type == MarkerType.TILT_SERIES and marker.metadata.get("inferred_from_stage"):
            text = _as_nonempty_str(marker.metadata.get("batch_label"))
        else:
            text = None
        if not text:
            continue
        geometry = _marker_bounds(marker)
        anchor = _marker_center(marker)
        if geometry is None or anchor is None:
            continue
        seeds.append(
            _LabelSeed(
                source=marker,
                text=text,
                anchor=anchor,
                geometry=geometry,
                size=_estimated_label_size(text),
            )
        )

    if not seeds:
        return []

    obstacles = [
        bounds
        for marker in markers
        if marker.marker_type != MarkerType.BATCH_LABEL
        for bounds in [_marker_bounds(marker)]
        if bounds is not None
    ]
    placed: list[tuple[float, float, float, float]] = []
    labels: list[ImageMarker] = []
    for seed in seeds:
        bbox, placement = _best_label_bbox(seed, image_size=size, obstacles=obstacles, placed=placed)
        placed.append(bbox)
        label_metadata = {
            "batch_id": seed.source.metadata.get("batch_id") or seed.source.linked_object_id,
            "batch_label": seed.text,
            "image_size": size,
            "label_for_marker_id": seed.source.id,
            "label_anchor": seed.anchor,
            "label_source_geometry": seed.geometry,
            "label_placement": placement,
            "label_gap_px": LABEL_GAP_PX,
            "label_font_size_px": LABEL_FONT_SIZE_PX,
            "label_source_type": seed.source.marker_type,
            "label_colour_role": "exposure",
        }
        if seed.source.marker_type == MarkerType.TILT_SERIES:
            label_metadata["label_colour_role"] = "default"
            label_metadata["inferred_from_stage"] = seed.source.metadata.get("inferred_from_stage")
        labels.append(
            ImageMarker(
                id=f"{seed.source.id}:{MarkerType.BATCH_LABEL}",
                marker_type=MarkerType.BATCH_LABEL,
                linked_object_id=seed.source.linked_object_id,
                source_object_id=seed.source.source_object_id,
                x=bbox[0],
                y=bbox[1],
                bbox=bbox,
                label=seed.text,
                tooltip=f"Batch/exposure label: {seed.text}",
                status=seed.source.status,
                unresolved=False,
                metadata=label_metadata,
            )
        )
    return labels


def _best_label_bbox(
    seed: _LabelSeed,
    *,
    image_size: tuple[int, int],
    obstacles: Sequence[tuple[float, float, float, float]],
    placed: Sequence[tuple[float, float, float, float]],
) -> tuple[tuple[float, float, float, float], str]:
    width, height = image_size
    label_w, label_h = seed.size
    candidates = _candidate_bboxes(seed, image_size=image_size)
    if not candidates:
        x = min(max(seed.anchor[0] + LABEL_GAP_PX, 0.0), max(0.0, width - label_w))
        y = min(max(seed.anchor[1] - label_h / 2, 0.0), max(0.0, height - label_h))
        candidates = [_LabelCandidate((x, y, label_w, label_h), "right")]

    def score(candidate: _LabelCandidate) -> tuple[float, float, float]:
        clip_penalty = _clip_penalty(candidate.bbox, image_size)
        clipped = _clamp_bbox(candidate.bbox, image_size)
        label_overlap = sum(_intersection_area(clipped, other) for other in placed)
        geometry_overlap = sum(_intersection_area(clipped, other) for other in obstacles)
        cx = clipped[0] + clipped[2] / 2
        cy = clipped[1] + clipped[3] / 2
        distance = math.hypot(cx - seed.anchor[0], cy - seed.anchor[1])
        return (
            clip_penalty * 1_000_000 + label_overlap * 1_000 + geometry_overlap * 20 + distance,
            label_overlap,
            geometry_overlap,
        )

    best = min(candidates, key=score)
    return _clamp_bbox(best.bbox, image_size), best.placement


def _candidate_bboxes(
    seed: _LabelSeed,
    *,
    image_size: tuple[int, int],
) -> list[_LabelCandidate]:
    label_w, label_h = seed.size
    gx, gy, gw, gh = seed.geometry
    cx, cy = seed.anchor
    gap = LABEL_GAP_PX
    raw = [
        _LabelCandidate((gx + gw + gap, cy - label_h / 2, label_w, label_h), "right"),
        _LabelCandidate((gx - label_w - gap, cy - label_h / 2, label_w, label_h), "left"),
        _LabelCandidate((cx - label_w / 2, gy - label_h - gap, label_w, label_h), "above"),
        _LabelCandidate((cx - label_w / 2, gy + gh + gap, label_w, label_h), "below"),
        _LabelCandidate((gx + gw + gap, gy - label_h - gap, label_w, label_h), "above_right"),
        _LabelCandidate((gx - label_w - gap, gy - label_h - gap, label_w, label_h), "above_left"),
        _LabelCandidate((gx + gw + gap, gy + gh + gap, label_w, label_h), "below_right"),
        _LabelCandidate((gx - label_w - gap, gy + gh + gap, label_w, label_h), "below_left"),
    ]
    visible = [candidate for candidate in raw if _clip_penalty(candidate.bbox, image_size) == 0.0]
    return visible or raw


def _estimated_label_size(text: str) -> tuple[float, float]:
    width = max(30.0, len(text) * LABEL_ESTIMATED_CHAR_WIDTH_PX + LABEL_BOUNDS_MARGIN_PX * 2)
    height = LABEL_FONT_SIZE_PX + LABEL_BOUNDS_MARGIN_PX * 2
    return width, height


def _marker_bounds(marker: ImageMarker) -> tuple[float, float, float, float] | None:
    if marker.bbox is not None:
        x, y, width, height = marker.bbox
        if width > 0 and height > 0:
            return (float(x), float(y), float(width), float(height))
    if marker.polygon:
        xs = [point[0] for point in marker.polygon]
        ys = [point[1] for point in marker.polygon]
        if xs and ys:
            return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    center = _marker_center(marker)
    if center is None:
        return None
    radius = max(float(marker.radius or 8.0), 1.0)
    return (center[0] - radius, center[1] - radius, radius * 2, radius * 2)


def _marker_center(marker: ImageMarker) -> tuple[float, float] | None:
    if marker.x is not None and marker.y is not None:
        return float(marker.x), float(marker.y)
    if marker.bbox is not None:
        x, y, width, height = marker.bbox
        return float(x) + float(width) / 2, float(y) + float(height) / 2
    if marker.polygon:
        return (
            sum(point[0] for point in marker.polygon) / len(marker.polygon),
            sum(point[1] for point in marker.polygon) / len(marker.polygon),
        )
    return None


def _image_size_from_markers(markers: Sequence[ImageMarker]) -> tuple[int, int] | None:
    for marker in markers:
        size = marker.metadata.get("image_size")
        if (
            isinstance(size, tuple)
            and len(size) == 2
            and isinstance(size[0], int | float)
            and isinstance(size[1], int | float)
        ):
            return int(size[0]), int(size[1])
    return None


def _clamp_bbox(
    bbox: tuple[float, float, float, float],
    image_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    image_w, image_h = image_size
    x, y, width, height = bbox
    width = min(width, max(1.0, float(image_w)))
    height = min(height, max(1.0, float(image_h)))
    x = min(max(0.0, x), max(0.0, image_w - width))
    y = min(max(0.0, y), max(0.0, image_h - height))
    return x, y, width, height


def _clip_penalty(
    bbox: tuple[float, float, float, float],
    image_size: tuple[int, int],
) -> float:
    image_w, image_h = image_size
    x, y, width, height = bbox
    over_left = max(0.0, -x)
    over_top = max(0.0, -y)
    over_right = max(0.0, x + width - image_w)
    over_bottom = max(0.0, y + height - image_h)
    return over_left + over_top + over_right + over_bottom


def _intersection_area(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    overlap_w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    overlap_h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    return overlap_w * overlap_h


def _fallback_exposure_index(marker: ImageMarker) -> int | None:
    batch_name = _as_nonempty_str(marker.metadata.get("batch_name")) or marker.linked_object_id
    area_name = _as_nonempty_str(marker.metadata.get("area_name")) or marker.label
    if not area_name:
        return None
    if area_name.casefold() == "exposure":
        return 0
    batch_key = _normalise_key(batch_name or "")
    area_key = _normalise_key(area_name)
    if batch_key and area_key == batch_key:
        return 0
    if batch_key and area_key.startswith(f"{batch_key}_"):
        suffix = area_key.removeprefix(f"{batch_key}_")
        if suffix.isdigit():
            return max(0, int(suffix) - 1)
    match = re.fullmatch(r"exposure_?(\d+)", area_key)
    if match:
        return max(0, int(match.group(1)) - 1)
    return None


def _exposure_index(marker: ImageMarker) -> int | None:
    explicit = _as_int(marker.metadata.get("exposure_index"))
    if explicit is not None:
        return max(0, explicit)
    return _fallback_exposure_index(marker)


def _normalise_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_nonempty_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
