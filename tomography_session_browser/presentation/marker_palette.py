"""The colour an overlay marker is drawn in, on screen and in the PDF.

One rule for both (harmonisation F1, decision 2 of the 2026-10-06 review). The
viewer used to colour batch-position and tilt-series markers by their status
while the PDF coloured every marker by its type, overridden only by an exact
``"failed"``: an incomplete tilt series was amber on screen and printed in the
report's "complete" green, and every batch that had not failed printed in the
report's "incomplete" amber. Nine of eleven marker types also changed hue
between the two.

Qt-free and display only. Nothing here touches projection, scaling, anchoring
or any scientific value; it maps a marker's type and its already-derived
status to a colour, an opacity and the reason for the colour (which the PDF's
overlay key names).

The colours are bright on purpose: they sit on grey microscope images, in
both themes and in print, so the same values serve every surface. The theme's
``marker_*`` and ``overlay_*`` slots are these constants.

What carries colour (adopted from the review's draft on 2026-10-06, with the
camera field kept cyan at the user's choice):

* targets take their status: batch positions, tilt series and exposure
  areas, so "complete" is one green wherever it appears, and a target with no
  status is grey ("unattributed") rather than a hue another type means;
* frames are neutral: a search map's and an overview's extent is a light
  outline with a dark halo, solid and dashed. The camera field keeps its cyan
  and is dotted, so it stays easy to find over an exposure;
* areas keep the Tomography 5 hues: exposure (unacquired), tracking, focus,
  template, condition.

Print draws the neutral frames as one thin dark-slate line with no halo
(``reports.graphics.PRINT_FRAME``): a light frame on a halo read as a heavy
double rule at print scale, and its key ring vanished on white paper. Every
other marker prints in its screen colour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tomography_session_browser.domain.markers import ImageMarker, MarkerType

# --- marker types -----------------------------------------------------------

MARKER_EXPOSURE: Final[str] = "#16a34a"
MARKER_FOCUS: Final[str] = "#2563eb"
MARKER_TRACKING: Final[str] = "#facc15"
MARKER_TEMPLATE: Final[str] = "#84cc16"
MARKER_CAMERA: Final[str] = "#38bdf8"
MARKER_CONDITION: Final[str] = "#f97316"
MARKER_LINK: Final[str] = "#7c3aed"
#: A camera field's fill: deliberately not its outline's colour, and faint,
#: because a camera field can cover much of the image.
CAMERA_FILL: Final[str] = "#14b8a6"
#: Frames (a search map's and an overview's extent) are one neutral outline
#: with a dark halo, told apart by their line: solid, dashed. Colour is left to
#: what carries meaning: a target's status and the Tomography 5 area hues.
FRAME: Final[str] = "#f1f5f9"
FRAME_HALO: Final[str] = "#020617"

# --- states -----------------------------------------------------------------

SELECTED: Final[str] = "#fbbf24"
UNRESOLVED: Final[str] = "#f87171"
STATUS_FAILED: Final[str] = "#f85149"
STATUS_WARNING: Final[str] = "#d29922"
STATUS_PENDING: Final[str] = "#38bdf8"
STATUS_COMPLETE: Final[str] = "#3fb950"
STATUS_UNATTRIBUTED: Final[str] = "#9ca3af"

#: Outline and fill opacity (of 255) per marker type, when the type sets the colour.
_TYPE_STYLES: Final[dict[str, tuple[str, str | None, int]]] = {
    MarkerType.SEARCH_MAP: (FRAME, None, 0),
    MarkerType.OVERVIEW: (FRAME, None, 0),
    # A target with no status of its own is "unattributed", not a colour that
    # another marker type already means.
    MarkerType.BATCH_POSITION: (STATUS_UNATTRIBUTED, None, 70),
    MarkerType.TEMPLATE_AREA: (MARKER_TEMPLATE, None, 35),
    MarkerType.EXPOSURE_AREA: (MARKER_EXPOSURE, None, 65),
    # Cyan and dotted: a frame by its line, but kept easy to find.
    MarkerType.CAMERA_FOV: (MARKER_CAMERA, CAMERA_FILL, 34),
    MarkerType.BATCH_LABEL: (MARKER_EXPOSURE, None, 0),
    MarkerType.TRACKING_AREA: (MARKER_TRACKING, None, 70),
    MarkerType.FOCUS_AREA: (MARKER_FOCUS, None, 70),
    MarkerType.CONDITION_AREA: (MARKER_CONDITION, None, 70),
    MarkerType.TILT_SERIES: (STATUS_UNATTRIBUTED, None, 70),
    MarkerType.LINK_LINE: (MARKER_LINK, None, 0),
}

#: Marker types a failed acquisition recolours: a failed batch propagates its
#: status to every marker it spawns, so failures read in red on every tab.
_FAILURE_TYPES: Final[frozenset[str]] = frozenset(
    {
        MarkerType.BATCH_POSITION,
        MarkerType.TILT_SERIES,
        MarkerType.TEMPLATE_AREA,
        MarkerType.EXPOSURE_AREA,
        MarkerType.TRACKING_AREA,
        MarkerType.FOCUS_AREA,
    }
)
#: Marker types whose colour is their status whenever they have one: the
#: targets. An exposure area is where an image was (or was to be) taken.
_STATUS_TYPES: Final[frozenset[str]] = frozenset(
    {MarkerType.BATCH_POSITION, MarkerType.TILT_SERIES, MarkerType.EXPOSURE_AREA}
)
#: How each frame's outline is drawn; everything else is solid.
FRAME_LINES: Final[dict[str, str]] = {
    MarkerType.SEARCH_MAP: "solid",
    MarkerType.OVERVIEW: "dash",
    MarkerType.CAMERA_FOV: "dot",
}
_QUEUED_TYPES: Final[frozenset[str]] = frozenset({MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV})
_STATUS_FILL_ALPHA: Final[int] = 70
_UNRESOLVED_FILL_ALPHA: Final[int] = 60
_FALLBACK_FILL_ALPHA: Final[int] = 60


@dataclass(frozen=True, slots=True)
class MarkerStyle:
    """How one marker is drawn: outline, fill, and why it has that colour.

    ``reason`` is ``"type"`` when the marker type chose the colour, else the
    state that overrode it: ``"unresolved"``, ``"queued"``, or a status
    (``"failed"``, ``"missing"``, ``"incomplete"``, ``"pending"``,
    ``"complete"``). The PDF's overlay key names it.
    """

    stroke: str
    fill: str
    fill_alpha: int
    reason: str
    #: "solid", "dash" or "dot"; frames only are not solid.
    line: str = "solid"
    #: Draw a dark halo under the outline (the neutral frames).
    halo: bool = False


def status_reason(status: str | None) -> str | None:
    """The status a marker's colour stands for, from its recorded status.

    The viewer's long-standing substring reading of status words, kept
    exactly: "Partial" and "warning" read as incomplete, "done" and
    "acquired" as complete, "error" and "aborted" as failed.
    """

    if not status:
        return None
    normalized = status.lower()
    if any(value in normalized for value in ("fail", "error", "abort")):
        return "failed"
    if "missing" in normalized:
        return "missing"
    if any(value in normalized for value in ("partial", "incomplete", "warn")):
        return "incomplete"
    if any(value in normalized for value in ("queue", "pending", "selected")):
        return "pending"
    if any(value in normalized for value in ("acquired", "done", "complete", "ok", "available")):
        return "complete"
    if any(value in normalized for value in ("refined", "initial")):
        return "complete"
    return None


#: The colour each status reason is drawn in.
STATUS_STROKES: Final[dict[str, str]] = {
    "failed": STATUS_FAILED,
    "missing": STATUS_FAILED,
    "incomplete": STATUS_WARNING,
    "pending": STATUS_PENDING,
    "complete": STATUS_COMPLETE,
}


def marker_style(marker: ImageMarker, *, queued_stroke: str, fallback_stroke: str) -> MarkerStyle:
    """The style ``marker`` is drawn in, everywhere but its selected state.

    ``queued_stroke`` is the queued-position blue of the surface (the Atlas
    marker palette differs between the themes and print); ``fallback_stroke``
    colours marker types with no colour of their own (the surface's text).
    Selection is the viewer's alone and is resolved before this is asked.
    """

    if marker.unresolved:
        return MarkerStyle(UNRESOLVED, UNRESOLVED, _UNRESOLVED_FILL_ALPHA, "unresolved")
    if marker.metadata.get("queued_position") and marker.marker_type in _QUEUED_TYPES:
        # The Atlas queued glyph's hue, with an empty centre that keeps it
        # apart from a filled blue focus area near the same target.
        return MarkerStyle(queued_stroke, queued_stroke, 0, "queued")
    reason = status_reason(marker.status)
    if (
        reason is not None
        and marker.marker_type in _FAILURE_TYPES
        and "fail" in (marker.status or "").lower()
    ):
        return MarkerStyle(STATUS_FAILED, STATUS_FAILED, _STATUS_FILL_ALPHA, "failed")
    if reason is not None and marker.marker_type in _STATUS_TYPES:
        stroke = STATUS_STROKES[reason]
        return MarkerStyle(stroke, stroke, _STATUS_FILL_ALPHA, reason)
    style = _TYPE_STYLES.get(marker.marker_type)
    if style is None:
        return MarkerStyle(fallback_stroke, fallback_stroke, _FALLBACK_FILL_ALPHA, "type")
    stroke, fill, alpha = style
    line = FRAME_LINES.get(marker.marker_type, "solid")
    return MarkerStyle(stroke, fill or stroke, alpha, "type", line=line, halo=stroke == FRAME)
