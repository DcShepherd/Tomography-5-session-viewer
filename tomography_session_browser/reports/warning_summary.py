"""Warning categorisation for the PDF report cover page.

Sessions can produce hundreds of low-level warnings (one per NaN field, one
per missing per-tilt mdoc, etc.). The on-screen view groups them loosely by
the high-level bucket; the PDF cover page wants a tighter view: one row per
*category* with a count and how many distinct sources it touched.

The categoriser is a list of ``WarningPattern`` rules, walked in order.
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
class _Rule:
    category: str
    severity: str
    explanation: str
    # ``patterns`` are case-insensitive regex fragments. Match if *any* fires.
    patterns: tuple[str, ...]


# Ordered: more specific rules go first so that e.g. "wide drift" matches
# the defocus-drift rule rather than the generic "drift" catch-all.
_RULES: tuple[_Rule, ...] = (
    _Rule(
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
    _Rule(
        category="Wide defocus drift",
        severity="warning",
        explanation="Defocus across the tilt series spans more than the typical range.",
        patterns=(r"wide.*drift", r"defocus drift"),
    ),
    _Rule(
        category="Failed tilt series collection",
        severity="error",
        explanation="Acquisition stopped before enough tilt images were captured.",
        patterns=(
            r"fewer than \d+ images",
            r"likely aborted",
            r"only \d+ tilt images? present",
            r"no tilt images on disk",
            r"failed",
        ),
    ),
    _Rule(
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
    _Rule(
        category="Missing MDOC file",
        severity="warning",
        explanation="The .mdoc sidecar is absent; per-tilt metadata is unavailable.",
        patterns=(
            r"no matching mdoc",
            r"mdoc.*missing",
            r"missing.*mdoc",
            r"mdoc has no sections",
            r"could not read mdoc file",
        ),
    ),
    _Rule(
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
        ),
    ),
    _Rule(
        category="Search-tile link used a fallback",
        severity="info",
        explanation="The search tile was matched by projection, timestamp or "
                    "nearest-tile position rather than an explicit link.",
        patterns=(
            r"tile association used fallback",
            r"fallback matching",
        ),
    ),
    _Rule(
        category="Missing image files",
        severity="warning",
        explanation="One or more search/overview/exposure JPEG/MRC files were not found.",
        patterns=(
            r"no search image",
            r"no tracking image",
            r"no exposure image",
            r"no searchmap\.(jpg|mrc)",
            r"missing.*image",
        ),
    ),
    # NB: this rule is more specific than "Atlas files not found" and must
    # come first — the message "Atlas markers unavailable: missing …"
    # would otherwise match the broader "atlas.*missing" pattern below.
    _Rule(
        category="Atlas markers unavailable",
        severity="info",
        explanation="Stage/pixel calibration was insufficient to project Atlas overlays.",
        patterns=(r"atlas markers unavailable",),
    ),
    _Rule(
        category="Atlas files not found",
        severity="warning",
        explanation="The Atlas folder or Atlas.dm/atlas.xml file is missing.",
        patterns=(
            r"atlas.*(not found|missing|no parseable)",
            r"no atlas folder",
        ),
    ),
    _Rule(
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
    _Rule(
        category="Unrecognised folder layout",
        severity="warning",
        explanation="A folder did not match a known Tomography 5 session layout.",
        patterns=(
            r"did not match",
            r"unsupported",
            r"not a folder",
            r"unrecognised",
            r"unrecognized",
        ),
    ),
)


_OTHER_CATEGORY = "Other"
_OTHER_RULE = _Rule(
    category=_OTHER_CATEGORY,
    severity="info",
    explanation="Warnings that did not match a recognised category.",
    patterns=(),
)


def _matches(rule: _Rule, message: str) -> bool:
    return any(re.search(p, message, re.IGNORECASE) for p in rule.patterns)


def _classify_one(message: str) -> _Rule:
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


def _split_prefix(warning: str) -> tuple[str, str]:
    """Compatibility alias for existing internal callers and tests."""

    return split_warning_scope(warning)


def summarise_warnings(warnings: Iterable[str]) -> list[WarningSummary]:
    """Group ``warnings`` into ordered category summaries.

    Output order:
    1. severity (error → warning → info)
    2. count, descending
    3. category name (alphabetical, for stability)
    """

    counts: Counter[str] = Counter()
    affected: defaultdict[str, set[str]] = defaultdict(set)
    examples: defaultdict[str, list[str]] = defaultdict(list)
    rules: dict[str, _Rule] = {}

    for raw in warnings:
        rule = _classify_one(raw)
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
    rows.sort(key=lambda r: (severity_rank.get(r.severity, 3), -r.count, r.category))
    return rows


def severity_counts(rows: Sequence[WarningSummary]) -> dict[str, int]:
    """Tally warnings by severity for the cover-page chip strip."""

    counts = {"error": 0, "warning": 0, "info": 0}
    for row in rows:
        counts[row.severity] = counts.get(row.severity, 0) + row.count
    return counts


__all__ = [
    "WarningSummary",
    "split_warning_scope",
    "summarise_warnings",
    "severity_counts",
]
