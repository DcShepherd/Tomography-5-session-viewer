"""How the app names, ranks and counts a warning: the one classifier.

The dashboard, the context panel and the PDF all classify through this
module, so a warning has the same category, severity and count wherever it
appears. Sessions can produce hundreds of low-level warnings
(one per NaN field, one per missing per-tilt mdoc, etc.); the summary is one
row per *category* with a count and how many distinct sources it touched.

The categoriser is a list of ``WarningRule`` rules, walked in order.
Each rule has a substring/regex test and a normalised description. The
first matching rule wins; warnings that don't match any rule fall into
the catch-all ``Other`` bucket.

This is intentionally a flat lookup rather than something cleverer:
warning strings are emitted from many places in the parser/services
layer, so a small ordered table is much easier to extend than a richer
classifier when a new warning lands.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import re


# ----------------------------------------------------------------- public model


@dataclass(slots=True)
class WarningSummary:
    """One row of the cover-page warning table."""

    category: str
    count: int
    affected_items: int
    severity: str  # "error" | "warning" | "info"
    explanation: str = ""
    examples: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- categoriser


@dataclass(frozen=True, slots=True)
class WarningRule:
    """A warning category: its name, severity, explanation and patterns."""

    category: str
    severity: str
    explanation: str
    # ``patterns`` are case-insensitive regex fragments. Match if *any* fires.
    patterns: tuple[str, ...]


# Ordered: more specific rules go first so that e.g. "wide drift" matches
# the defocus-drift rule rather than the generic "drift" catch-all.
_RULES: tuple[WarningRule, ...] = (
    # First: the exception text quoted after "skipped:" is arbitrary and must
    # not decide the category (a "... failed ..." in it read as a failed
    # tilt series).
    WarningRule(
        category="Unreadable files or folders",
        severity="warning",
        explanation="A file or folder could not be listed or parsed and was skipped; "
                    "anything it held is missing from this review.",
        patterns=(
            r"could not be parsed and was skipped",
            r"could not list",
        ),
    ),
    WarningRule(
        category="NaN values in frame-dose metadata",
        severity="warning",
        explanation="One or more numeric MDOC fields contained NaN; "
                    "those values are excluded from summaries.",
        patterns=(
            r"\bnan\b",
            r"non-?finite",
            r"not a number",
        ),
    ),
    WarningRule(
        category="Wide defocus drift",
        severity="warning",
        explanation="Defocus across the tilt series spans more than the typical range.",
        patterns=(r"wide.*drift", r"defocus drift"),
    ),
    WarningRule(
        category="Failed tilt series collection",
        severity="error",
        explanation="Acquisition stopped before enough tilt images were captured.",
        patterns=(
            r"fewer than \d+ images",
            r"likely aborted",
            r"only \d+ tilt images? present",
            r"no tilt images on disk",
            # The dashboard's own note about an inferred failed batch. A bare
            # "failed" used to sit here and claimed any warning containing it.
            r"^failed batch\b",
        ),
    ),
    WarningRule(
        category="Incomplete MRC data",
        severity="error",
        explanation="The .mrc file is shorter than its header declares, so some "
                    "frames are not on disk.",
        patterns=(r"mrc data block is incomplete",),
    ),
    # Before the missing-file rules: "MDOC Binning is missing; using x.mrc
    # pixel size" is a settings conflict, not a missing MRC or MDOC. The
    # look-behind leaves "Tilt-angle conflict" to the tilt-angle rule.
    WarningRule(
        category="Acquisition metadata conflict",
        severity="warning",
        explanation="Two metadata sources disagree about an acquisition setting; "
                    "the warning names the value that was used.",
        patterns=(
            r"(?<!tilt-angle )\bconflict:",
            r"inconsistent \w+ values",
            r"binning is missing",
            r"disagrees with mrc nz",
        ),
    ),
    WarningRule(
        category="Missing MRC file",
        severity="error",
        explanation="The .mrc stack referenced by the session was not found on disk.",
        patterns=(
            r"no stack on disk",
            r"mrc.*missing",
            r"missing.*mrc",
            r"mrc file.*not found",
            r"has no matching mrc stack",
        ),
    ),
    WarningRule(
        category="Missing MDOC file",
        severity="warning",
        explanation="The .mdoc sidecar is absent; per-tilt metadata is unavailable.",
        patterns=(
            r"no matching mdoc",
            r"mdoc (file|sidecar) is missing",
            r"missing (an? )?(\.)?mdoc",
            r"mdoc has no sections",
            r"could not read mdoc file",
        ),
    ),
    WarningRule(
        category="Unreadable MDOC lines",
        severity="warning",
        explanation="Lines in an .mdoc file could not be read and were ignored; "
                    "the rest of the file was used.",
        patterns=(r"unrecognised mdoc line",),
    ),
    WarningRule(
        category="Tilt-angle metadata fallback",
        severity="warning",
        explanation="Per-frame tilt-angle metadata was missing, mismatched, or conflicted; "
                    "the viewer used the strongest valid source or frame order.",
        patterns=(
            # "tilt angles" with a space is what the MRC parser actually
            # emits; the hyphen-only form missed it and sent a real category
            # to "Other".
            r"tilt[-\s]?angles?",
            r"\btlt\b.*(count|invalid|ignored|disagree)",
            r"frame order without angle",
            r"alpha_tilt",
        ),
    ),
    WarningRule(
        category="MRC header issues",
        severity="warning",
        explanation="Part of an MRC header or extended header could not be interpreted; "
                    "metadata from it may be missing.",
        patterns=(
            r"mrc extended header is incomplete",
            r"extended header size is (not divisible|negative)",
            r"unsupported mrc extended header",
            r"extended header metadata block size was not recognised",
            r"extended header is smaller than",
            r"mrc header dimensions are not plausible",
            r"big-endian byte order",
            r"mrc mode \d+ is not supported",
        ),
    ),
    WarningRule(
        category="Search-tile link unresolved",
        severity="warning",
        explanation="No search-map tile could be matched to the exposure area, so the "
                    "Search tile row has no image.",
        patterns=(r"no matching search-map tile could be resolved",),
    ),
    WarningRule(
        category="Search-tile link used a fallback",
        severity="info",
        explanation="The search tile was matched by projection, timestamp or "
                    "nearest-tile position rather than an explicit link.",
        patterns=(
            r"tile association used fallback",
            r"fallback matching",
        ),
    ),
    WarningRule(
        category="Missing image files",
        severity="warning",
        explanation="One or more search/overview/exposure JPEG/MRC files were not found.",
        patterns=(
            r"no search image",
            r"no tracking image",
            r"no exposure image",
            r"no searchmap\.(jpg|mrc)",
            r"no displayable (mrc|jpg)",
            r"missing.*image",
        ),
    ),
    # NB: this rule is more specific than "Atlas files not found" and must
    # come first — the message "Atlas markers unavailable: missing …"
    # would otherwise match the broader "atlas.*missing" pattern below.
    WarningRule(
        category="Atlas markers unavailable",
        severity="info",
        explanation="Stage/pixel calibration was insufficient to project Atlas overlays.",
        patterns=(r"atlas markers unavailable",),
    ),
    WarningRule(
        category="Atlas re-acquired",
        severity="info",
        explanation="The Atlas folder holds more than one acquisition of this grid's atlas. "
                    "Only the one Atlas.dm names (or the latest) is shown, because the "
                    "overlay projection describes that acquisition.",
        patterns=(r"atlas was re-acquired",),
    ),
    WarningRule(
        category="Atlas files not found",
        severity="warning",
        explanation="The Atlas folder or Atlas.dm/atlas.xml file is missing.",
        patterns=(
            r"atlas.*(not found|missing|no parseable)",
            r"no atlas folder",
            r"no parseable atlas",
            r"no atlas_\S*\.(jpg|mrc)",
        ),
    ),
    WarningRule(
        category="Missing metadata files",
        severity="warning",
        explanation="An expected XML/JSON metadata file (Session.dm, SearchMap.xml, ...) is missing.",
        patterns=(
            r"no \.xml",
            r"missing metadata",
            r"\.dm.*not found",
            r"metadata.*(missing|absent|unavailable)",
            r"could not (parse|read).*metadata xml",
        ),
    ),
    WarningRule(
        category="Unrecognised folder layout",
        severity="warning",
        explanation="A folder did not match a known Tomography 5 session layout.",
        # About folders only: a bare "unsupported" or "unrecognised" claimed
        # MRC-header and MDOC-line warnings.
        patterns=(
            r"did not match a known",
            r"not a folder",
            r"unrecogni[sz]ed (folder|layout)",
            r"unsupported (folder|layout)",
        ),
    ),
)


_OTHER_CATEGORY = "Other"
_OTHER_RULE = WarningRule(
    category=_OTHER_CATEGORY,
    severity="info",
    explanation="Warnings that did not match a recognised category.",
    patterns=(),
)


def _matches(rule: WarningRule, message: str) -> bool:
    return any(re.search(p, message, re.IGNORECASE) for p in rule.patterns)


def classify_warning(message: str) -> WarningRule:
    """The first rule whose patterns match ``message``'s text (its scope
    prefix ignored), or the catch-all ``Other`` rule."""

    _prefix, message = split_warning_scope(message)
    for rule in _RULES:
        if _matches(rule, message):
            return rule
    return _OTHER_RULE


def split_warning_scope(warning: str) -> tuple[str, str]:
    """``"Sample / Tilt1: foo bar" -> ("Sample / Tilt1", "foo bar")``.

    Falls back to ``("", warning)`` if the warning has no obvious prefix.
    The prefix is what we count for "affected items" — it identifies the
    source object the warning came from.
    """

    # Only split on the first colon, and only if the prefix looks like a
    # path-style scope rather than e.g. "warning: foo bar".
    if ":" not in warning:
        return "", warning
    head, _, tail = warning.partition(":")
    head = head.strip()
    tail = tail.strip()
    if not head:
        return "", warning
    if len(head) == 1 and tail.startswith(("\\", "/")):
        return "", warning
    if " " in head and "/" not in head:
        return "", warning
    return head, tail


def summarise_warnings(warnings: Iterable[str]) -> list[WarningSummary]:
    """Group ``warnings`` into ordered category summaries.

    Output order:
    1. severity (error → warning → info)
    2. affected-object count, descending
    3. category name (alphabetical, for stability)
    """

    counts: Counter[str] = Counter()
    affected: defaultdict[str, set[str]] = defaultdict(set)
    examples: defaultdict[str, list[str]] = defaultdict(list)
    rules: dict[str, WarningRule] = {}

    for raw in warnings:
        rule = classify_warning(raw)
        prefix, _body = split_warning_scope(raw)
        rules[rule.category] = rule
        counts[rule.category] += 1
        affected[rule.category].add(prefix or raw)  # fall back to whole message
        if len(examples[rule.category]) < 5:
            examples[rule.category].append(raw)

    severity_rank = {"error": 0, "warning": 1, "info": 2}
    rows: list[WarningSummary] = []
    for category, count in counts.items():
        rule = rules[category]
        rows.append(
            WarningSummary(
                category=category,
                count=count,
                affected_items=len(affected[category]),
                severity=rule.severity,
                explanation=rule.explanation,
                examples=list(examples[category]),
            )
        )
    rows.sort(key=lambda r: (severity_rank.get(r.severity, 3), -r.affected_items, r.category))
    return rows


def severity_counts(rows: Sequence[WarningSummary]) -> dict[str, int]:
    """Tally affected objects per category, matching dashboard presentations."""

    counts = {"error": 0, "warning": 0, "info": 0}
    for row in rows:
        counts[row.severity] = counts.get(row.severity, 0) + row.affected_items
    return counts


__all__ = [
    "WarningRule",
    "WarningSummary",
    "classify_warning",
    "split_warning_scope",
    "summarise_warnings",
    "severity_counts",
]
