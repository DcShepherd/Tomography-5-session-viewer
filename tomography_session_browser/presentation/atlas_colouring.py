"""Colour the Atlas batch positions by a value of their tomograms. Display only.

A position's members are its status members (``AtlasMarkerSet.outcome_by_marker_id``),
so the colouring adds no membership, link or status rule. A value is given only
when it is one value without doubt; anything else is No value, with the reason
in ``explanation_for``:

- a position with no members, or a linked failed position (decision 7), has no value;
- every placed tomogram under the members counts, from every processing source
  and version; they must each record one metric of the definition, and all
  ``(unit, value)`` pairs must be equal, else No value naming the sources
  (decision 1);
- a member named by a tomogram that is not placed (``TomogramPlacement.exclusion``)
  gives No value with that reason;
- a position with no unique destination (Unresolved link) and a failed tilt
  series with no batch link keep their status glyph in every mode (decision 8);
- Tags mode (batch 6, decision 13): a position is Tagged when any placed
  tomogram under it has a saved tag (counted or not, also when its
  reconstruction changed since tagged); Not tagged when every placed tomogram
  is in a tag project with no tag; else No value. ``TagsOf`` gives the tags.

Qt-free; ``ui/`` draws what this module decides.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.domain.recorded_metrics import RecordedMetric
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram
from tomography_session_browser.presentation.dashboard.index_chart import ChartBin, distribution
from tomography_session_browser.presentation.recorded_metrics import Ambiguous, display_value, single_metric, value_text
from tomography_session_browser.presentation.tomogram_placement import ExclusionKind, TomogramPlacement
from tomography_session_browser.services.atlas_lod import UNLINKED_FAILED_KEY, UNRESOLVED_LINK_KEY, atlas_glyph_key
from tomography_session_browser.services.markers.builders import AtlasMarkerSet
from tomography_session_browser.services.tomogram_links import TomogramLink


# -- keys -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatusKey:
    """The status glyph's own colour: ``word`` is an ``atlas_glyph_key``."""
    word: str


@dataclass(frozen=True, slots=True)
class ValueBin:
    lower: float
    upper: float
    #: Where the bin's centre sits in the observed range, 0 to 1; 0.5 when the range is one value.
    fraction: float


@dataclass(frozen=True, slots=True)
class NoValueKey:
    """One visual key for every reason a position has no value."""


NO_VALUE = NoValueKey()


class Presence(StrEnum):
    """A category of a category mode: Tomogram (placed or none linked) or Tags (tagged or not)."""
    PLACED = "placed"
    NONE_LINKED = "none_linked"
    TAGGED = "tagged"
    NOT_TAGGED = "not_tagged"


@dataclass(frozen=True, slots=True)
class TomogramPresence:
    presence: Presence


ColourKey = StatusKey | ValueBin | NoValueKey | TomogramPresence

PRESENCE_LABELS = {Presence.PLACED: "Placed tomogram", Presence.NONE_LINKED: "No tomogram linked",
                   Presence.TAGGED: "Tagged", Presence.NOT_TAGGED: "Not tagged"}
#: A category in a few characters, for a cluster popup row.
PRESENCE_SHORT = {Presence.PLACED: "Placed", Presence.NONE_LINKED: "None linked",
                  Presence.TAGGED: "Tagged", Presence.NOT_TAGGED: "Not tagged"}
#: The first category of each pair draws in the ramp's high end, the second in its low end.
FIRST_CATEGORIES = frozenset((Presence.PLACED, Presence.TAGGED))


# -- modes ----------------------------------------------------------------------------


class SimpleMode(StrEnum):
    STATUS = "status"
    TOMOGRAM = "tomogram"
    TAGS = "tags"


@dataclass(frozen=True, slots=True)
class Metric:
    definition: str
    name: str
    #: The name on the card's Colour by button, which has little room.
    short: str = ""


ColourMode = SimpleMode | Metric

STATUS = SimpleMode.STATUS
TOMOGRAM = SimpleMode.TOMOGRAM
TAGS = SimpleMode.TAGS
#: The categories a category mode shows in its key, in key order.
MODE_CATEGORIES: Mapping[ColourMode, tuple[Presence, Presence]] = MappingProxyType({
    TOMOGRAM: (Presence.PLACED, Presence.NONE_LINKED),
    TAGS: (Presence.TAGGED, Presence.NOT_TAGGED),
})
#: The value modes, in menu order. IMOD positioning thickness is its own mode (decision 6).
METRIC_MODES: tuple[Metric, ...] = (
    Metric("warp.ctf_specimen_thickness", "Specimen thickness", "Thickness"),
    Metric("warp.ctf_resolution", "CTF fit", "CTF fit"),
    Metric("imod.tomopitch.specimen_thickness", "IMOD positioning thickness", "IMOD thickness"),
)


def mode_name(mode: ColourMode) -> str:
    return mode.name if isinstance(mode, Metric) else {"status": "Status", "tomogram": "Tomogram", "tags": "Tags"}[mode.value]


def mode_short_name(mode: ColourMode) -> str:
    return (mode.short or mode.name) if isinstance(mode, Metric) else mode_name(mode)


@dataclass(frozen=True, slots=True)
class Unavailable:
    reason: str


# -- tags -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TomogramTagState:
    """One tomogram's saved tags in its tag project (batch 6).

    ``tags`` is ``(name, count)`` per tag, the count ``None`` when not counted;
    ``change`` is the "changed since tagged" notice, empty when none.
    """

    tags: tuple[tuple[str, int | None], ...] = ()
    change: str = ""


#: A placed tomogram's tags, or ``None`` when no open tag project lists it.
TagsOf = Callable[[Tomogram], TomogramTagState | None]


# -- sources --------------------------------------------------------------------------

EXCLUSION_TEXT: Mapping[ExclusionKind, str] = MappingProxyType({
    'ambiguous': "A tomogram matches more than one tilt series. The app did not select one.",
    'unconfirmed_name': "A tomogram has the same name, not confirmed.",
    'declined_name': "A tomogram has the same name. The reviewer declined the link.",
    'revoked_name': "A tomogram has the same name. The reviewer removed the link.",
    'nonunique_owner': "A tilt series is in more than one dataset. The app did not select one.",
    'unrepresented_owner': "A tilt series is in the session, not in a grid.",
})


@dataclass(frozen=True, slots=True)
class ColouringSources:
    """What the colouring reads, built once per workspace."""

    placement: TomogramPlacement
    #: Every (tomogram, link) whose link names a tilt series key, as target or candidate.
    links_by_key: Mapping[str, tuple[tuple[Tomogram, TomogramLink], ...]]
    source_names: Mapping[str, str]

    @classmethod
    def build(cls, placement: TomogramPlacement, links: Mapping[str, TomogramLink],
              sources: Iterable[ProcessingSource]) -> ColouringSources:
        rows = {row.id: row for row in placement.rows}
        index: dict[str, list[tuple[Tomogram, TomogramLink]]] = {}
        for row_id, link in links.items():
            row = rows.get(row_id)
            if row is None:
                continue
            for key in dict.fromkeys((link.target_key, *link.candidates)):
                if key:
                    index.setdefault(key, []).append((row, link))
        return cls(placement, MappingProxyType({key: tuple(value) for key, value in index.items()}),
                   MappingProxyType({source.id: source.name for source in sources}))


# -- one position ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Finding:
    """What one position's members have: placed tomograms, an exclusion, any link that names them."""

    placed: tuple[Tomogram, ...]
    exclusion: str | None
    named: bool


NO_MEMBERS = Finding((), None, False)
Findings = Mapping[str, Finding]


def find_positions(marker_set: AtlasMarkerSet, sources: ColouringSources) -> Findings:
    """Each colourable position's ``Finding``, by marker id: worked out once for every mode."""

    findings: dict[str, Finding] = {}
    for marker in _counted(marker_set):
        if _status_only(marker) is not None:
            continue
        outcome = marker_set.outcome_by_marker_id.get(marker.id)
        findings[marker.id] = _find(outcome.members, sources) if outcome is not None and outcome.members else NO_MEMBERS
    return MappingProxyType(findings)


def _find(members: Sequence[object], sources: ColouringSources) -> Finding:
    placed: dict[str, Tomogram] = {}
    exclusion: str | None = None
    named = False
    for member in members:
        key = entity_key_text(member)
        for row in sources.placement.by_parent.get(key, ()):
            placed[row.id] = row
        for row, link in sources.links_by_key.get(key, ()):
            named = True
            kind = sources.placement.exclusion(row, link)
            if kind is not None and exclusion is None:
                exclusion = EXCLUSION_TEXT.get(kind, "A tomogram is not placed.")
    return Finding(tuple(placed.values()), exclusion, named or bool(placed))


def _metric_value(finding: Finding, definition: str, sources: ColouringSources) -> tuple[tuple[str, float] | None, str]:
    if finding.exclusion is not None:
        return None, finding.exclusion
    if not finding.placed:
        return None, "No tomogram linked." if not finding.named else "No placed tomogram."
    pairs: dict[tuple[str, float], list[str]] = {}
    for row in finding.placed:
        found = single_metric(row, definition)
        if found is None:
            return None, f"Not recorded for {row.name}."
        if isinstance(found, Ambiguous):
            return None, f"{row.name} records {found.count} values. The app did not select one."
        assert isinstance(found, RecordedMetric)
        pairs.setdefault(display_value(found), []).append(sources.source_names.get(row.source_id, row.source_id))
    if len(pairs) > 1:
        named = "; ".join(f"{value_text(definition, value)} {unit} ({', '.join(dict.fromkeys(names))})"
                          for (unit, value), names in pairs.items())
        return None, f"The tomograms do not agree: {named}."
    pair = next(iter(pairs))
    return pair, ""


# -- the colouring --------------------------------------------------------------------


def _counted(marker_set: AtlasMarkerSet) -> list[ImageMarker]:
    return [marker for marker in marker_set.markers if marker.metadata.get("atlas_lod_role") == "batch_position"]


def _status_only(marker: ImageMarker) -> str | None:
    key = atlas_glyph_key(marker)
    return key if key in (UNLINKED_FAILED_KEY, UNRESOLVED_LINK_KEY) else None


@dataclass(frozen=True, slots=True, eq=False)
class AtlasColouring:
    mode: ColourMode
    keys: Mapping[str, ColourKey]
    explanations: Mapping[str, str]
    bins: tuple[ChartBin, ...] = ()
    unit: str = ""
    range: tuple[float, float] | None = None
    #: How many positions read each key, unresolved links included.
    counts: Mapping[ColourKey, int] = field(default_factory=dict)
    #: A value with its unit, by marker id, for a cluster popup row.
    values: Mapping[str, str] = field(default_factory=dict)

    @property
    def no_value_count(self) -> int:
        return self.counts.get(NO_VALUE, 0)

    def key_for(self, marker: ImageMarker) -> ColourKey:
        status = _status_only(marker)
        if status is not None:
            return StatusKey(status)
        return self.keys.get(marker.id, NO_VALUE)

    def explanation_for(self, marker: ImageMarker) -> str:
        return self.explanations.get(marker.id, "")

    def short_text(self, marker: ImageMarker) -> str:
        """The value in a few characters: ``"120 nm"``, ``"Placed"``, ``"No value"``; empty for a status key."""

        key = self.key_for(marker)
        if isinstance(key, ValueBin):
            return self.values.get(marker.id, "")
        if isinstance(key, TomogramPresence):
            return PRESENCE_SHORT[key.presence]
        return "" if isinstance(key, StatusKey) else "No value"

    def members_of(self, key: ColourKey) -> frozenset[str]:
        """The ids of the markers that read ``key``: what a colour key entry highlights (batch 5)."""
        return frozenset(marker_id for marker_id, marker_key in self.keys.items() if marker_key == key)

    def ring_counts(self, members: Iterable[ImageMarker]) -> tuple[tuple[ColourKey, int], ...]:
        """``(key, count)`` for a cluster's members, in key order; every member once."""
        counts = Counter(self.key_for(member) for member in members)
        return tuple(sorted(counts.items(), key=lambda item: key_order(item[0])))

    @classmethod
    def build(cls, marker_set: AtlasMarkerSet, sources: ColouringSources, mode: ColourMode,
              findings: Findings | None = None, tags_of: TagsOf | None = None) -> AtlasColouring | Unavailable:
        """The colouring of ``marker_set`` in ``mode``; ``findings`` from ``find_positions``, if known.

        ``tags_of`` gives the tags of a tomogram for Tags mode; without it Tags is unavailable.
        """

        if mode == STATUS:
            return Unavailable("Status colours need no colouring.")
        markers = _counted(marker_set)
        found = findings if findings is not None else find_positions(marker_set, sources)
        keys: dict[str, ColourKey] = {}
        explanations: dict[str, str] = {}
        if mode == TOMOGRAM:
            for marker in markers:
                if _status_only(marker) is not None:
                    continue
                finding = found.get(marker.id, NO_MEMBERS)
                if finding.exclusion is not None:
                    keys[marker.id], explanations[marker.id] = NO_VALUE, finding.exclusion
                elif finding.placed:
                    keys[marker.id] = TomogramPresence(Presence.PLACED)
                    explanations[marker.id] = f"Placed tomograms: {len(finding.placed)}"
                elif finding.named:
                    keys[marker.id], explanations[marker.id] = NO_VALUE, "No placed tomogram."
                else:
                    keys[marker.id] = TomogramPresence(Presence.NONE_LINKED)
                    explanations[marker.id] = "No tomogram linked."
            if not any(isinstance(key, TomogramPresence) and key.presence == Presence.PLACED for key in keys.values()):
                return Unavailable("No tomogram is placed on this atlas.")
            return cls(mode, MappingProxyType(keys), MappingProxyType(explanations),
                       counts=MappingProxyType(Counter(_all_keys(markers, keys))))
        if mode == TAGS:
            any_placed = False
            for marker in markers:
                if _status_only(marker) is not None:
                    continue
                finding = found.get(marker.id, NO_MEMBERS)
                any_placed = any_placed or bool(finding.placed)
                keys[marker.id], explanations[marker.id] = _tag_reading(finding, tags_of)
            if not any(isinstance(key, TomogramPresence) for key in keys.values()):
                return Unavailable("No tomogram is placed on this atlas." if not any_placed
                                   else "No open tag project lists a placed tomogram.")
            return cls(mode, MappingProxyType(keys), MappingProxyType(explanations),
                       counts=MappingProxyType(Counter(_all_keys(markers, keys))))
        assert isinstance(mode, Metric)
        values: dict[str, tuple[str, float]] = {}
        for marker in markers:
            if _status_only(marker) is not None:
                continue
            outcome = marker_set.outcome_by_marker_id.get(marker.id)
            if outcome is None or not outcome.members:
                explanations[marker.id] = "No tilt series is linked to this position."
                continue
            if (marker.status or "").lower() == "failed":
                explanations[marker.id] = "Failed acquisition: no tomogram value."
                continue
            pair, reason = _metric_value(found.get(marker.id, NO_MEMBERS), mode.definition, sources)
            if pair is None:
                explanations[marker.id] = reason
            else:
                values[marker.id] = pair
        if not values:
            return Unavailable("No batch position has one agreed value.")
        units = {unit for unit, _ in values.values()}
        if len(units) > 1:
            return Unavailable(f"The values have more than one unit ({', '.join(sorted(units))}).")
        unit = units.pop()
        ids = tuple(values)
        numbers = tuple(values[marker_id][1] for marker_id in ids)
        low, high = min(numbers), max(numbers)
        bins = distribution(numbers, ids, min_count=1)
        texts = {marker_id: f"{value_text(mode.definition, value)} {unit}" for marker_id, (_, value) in values.items()}
        for chart_bin in bins:
            centre = (chart_bin.lower + chart_bin.upper) / 2.0
            fraction = 0.5 if high == low else min(max((centre - low) / (high - low), 0.0), 1.0)
            key = ValueBin(chart_bin.lower, chart_bin.upper, fraction)
            for marker_id in chart_bin.row_keys:
                keys[marker_id] = key
                explanations[marker_id] = f"{mode.name}: {texts[marker_id]}"
        for marker in markers:
            if marker.id not in keys and _status_only(marker) is None:
                keys[marker.id] = NO_VALUE
        return cls(mode, MappingProxyType(keys), MappingProxyType(explanations), bins, unit, (low, high),
                   MappingProxyType(Counter(_all_keys(markers, keys))), MappingProxyType(texts))


def _tag_reading(finding: Finding, tags_of: TagsOf | None) -> tuple[ColourKey, str]:
    """A position's Tags key and its explanation (decision 13: tagged if any)."""

    if finding.exclusion is not None:
        return NO_VALUE, finding.exclusion
    if not finding.placed:
        return NO_VALUE, "No tomogram linked." if not finding.named else "No placed tomogram."
    states = [(row, tags_of(row) if tags_of is not None else None) for row in finding.placed]
    tagged = [state for _, state in states if state is not None and state.tags]
    total = len(states)
    if not tagged:
        missing = [row.name for row, state in states if state is None]
        if missing:
            named = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
            return NO_VALUE, f"Not in an open tag project: {named}."
        return TomogramPresence(Presence.NOT_TAGGED), f"Tagged tomograms: 0 of {total}"
    counts: dict[str, int | None] = {}
    for state in tagged:
        for name, count in state.tags:
            if name not in counts:
                counts[name] = count
            elif count is not None:
                counts[name] = (counts[name] or 0) + count
    lines = [f"Tagged tomograms: {len(tagged)} of {total}",
             "Tags: " + ", ".join(name if count is None else f"{name} ({count})" for name, count in counts.items())]
    lines.extend(dict.fromkeys(f"{state.change}." for state in tagged if state.change))
    return TomogramPresence(Presence.TAGGED), "\n".join(lines)


def _all_keys(markers: Iterable[ImageMarker], keys: Mapping[str, ColourKey]) -> Iterable[ColourKey]:
    for marker in markers:
        status = _status_only(marker)
        yield StatusKey(status) if status is not None else keys.get(marker.id, NO_VALUE)


def key_order(key: ColourKey) -> tuple[int, float]:
    """Ring and key order: values low to high, then presence, No value, then status keys."""
    if isinstance(key, ValueBin):
        return (0, key.lower)
    if isinstance(key, TomogramPresence):
        return (1, 0.0 if key.presence in FIRST_CATEGORIES else 1.0)
    if key is NO_VALUE or isinstance(key, NoValueKey):
        return (2, 0.0)
    assert isinstance(key, StatusKey)
    return (3, float(key.word == UNRESOLVED_LINK_KEY))


def offered_modes(marker_set: AtlasMarkerSet, sources: ColouringSources, findings: Findings | None = None,
                  tags_of: TagsOf | None = None) -> tuple[tuple[ColourMode, str | None], ...]:
    """Every mode with the reason it is left out, or ``None`` when it is offered."""
    found = findings if findings is not None else find_positions(marker_set, sources)
    offered: list[tuple[ColourMode, str | None]] = [(STATUS, None)]
    for mode in (TOMOGRAM, TAGS, *METRIC_MODES):
        built = AtlasColouring.build(marker_set, sources, mode, found, tags_of)
        offered.append((mode, built.reason if isinstance(built, Unavailable) else None))
    return tuple(offered)
