"""Source evidence wording shared by tag review surfaces."""
from dataclasses import dataclass
from datetime import datetime, timezone

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.tomogram_tags import ChangeKind, TagAssignment, TagProject, TomogramTags
from tomography_session_browser.domain.tomograms import ProcessingSource
from tomography_session_browser.presentation.volume_formatting import mode_text
from tomography_session_browser.services.tomogram_tags.binding import AcceptanceIssue, AcceptanceProposal, AcceptanceRow, RowBinding, bind_rows
from tomography_session_browser.services.tomogram_tags.changes import MissingEvidence, VolumeChange, established, worst_kind


@dataclass(frozen=True, slots=True)
class ChangeNotice:
    title: str
    body: str
    warn: bool
    details: str = ""


@dataclass(frozen=True, slots=True)
class ChangedField:
    label: str
    recorded: str
    current: str
    warn: bool = True


def changed_fields(change: VolumeChange) -> tuple[ChangedField, ...]:
    before, after = change.saved, change.live
    if after is None:
        return ()
    if change.kind is ChangeKind.DATE:
        def modified(ns: int) -> str:
            seconds, fraction = divmod(ns, 1_000_000_000)
            try:
                date = datetime.fromtimestamp(seconds, timezone.utc)
            except (OverflowError, OSError, ValueError):
                return f"{ns} ns since the Unix epoch"
            return f"{date:%Y-%m-%d %H:%M:%S}.{fraction:09d} UTC"

        return (ChangedField("Modified date", modified(before.mtime_ns), modified(after.mtime_ns), warn=False),)

    fields = (
        ("Grid shape", before.grid.shape_xyz, after.grid.shape_xyz, ""),
        ("Voxel spacing", before.grid.spacing_xyz, after.grid.spacing_xyz, " Å"),
        ("File size", before.size, after.size, " bytes"),
        ("Data type", before.dtype, after.dtype, ""),
        ("Axis order", before.grid.axes_crs, after.grid.axes_crs, ""),
        ("Origin", before.grid.origin_xyz, after.grid.origin_xyz, ""),
        ("Grid starts", before.grid.starts_crs, after.grid.starts_crs, ""),
    )

    def display(label: str, value: object) -> str:
        if isinstance(value, tuple):
            return " × ".join("Unknown" if item is None else str(item) for item in value)
        return mode_text(value) if label == "Data type" and isinstance(value, str) else str(value)

    return tuple(ChangedField(label, display(label, old) + unit, display(label, new) + unit)
                 for label, old, new, unit in fields if old != new)


def volume_detail(change: VolumeChange) -> str:
    before, after = change.saved, change.live
    label = before.version
    if after is None:
        assert change.missing is not None
        return {
            MissingEvidence.ABSENT: f"{label} is not in the folder.",
            MissingEvidence.UNREADABLE: f"The header of {label} cannot be read.",
            MissingEvidence.UNKNOWN: f"{label} could not be checked when the folder loaded. It may still be there.",
        }[change.missing]
    if change.kind is ChangeKind.DATE:
        return f"{label}: the modified date differs. Size and geometry match."
    if before.grid.shape_xyz != after.grid.shape_xyz:
        old = " × ".join(map(str, before.grid.shape_xyz))
        new = " × ".join(map(str, after.grid.shape_xyz))
        return f"{label}: recorded {old}, now {new}."
    if before.grid.spacing_xyz != after.grid.spacing_xyz:
        return f"{label}: the voxel spacing differs."
    if before.size != after.size:
        return f"{label}: the file size differs."
    if before.dtype != after.dtype:
        return f"{label}: the recorded data type was {before.dtype}. It is now {after.dtype}."
    return f"{label}: the header geometry differs."


def review_versions(saved: TomogramTags, changes: tuple[VolumeChange, ...]) -> tuple[tuple[VolumeChange, str], ...]:
    counts = {change.saved.relative: sum(tag.version_relative == change.saved.relative for tag in saved.tags)
              for change in changes}
    ordered = sorted(changes, key=lambda change: (not counts[change.saved.relative], change.saved.version.casefold()))
    lines = []
    for change in ordered:
        count = counts[change.saved.relative]
        provenance = (f"{count_phrase(count, 'tag')} {'was' if count == 1 else 'were'} recorded on this version."
                      if count else "No tag was recorded on this version.")
        lines.append((change, provenance))
    return tuple(lines)


def volume_lines(saved: TomogramTags, changes: tuple[VolumeChange, ...]) -> tuple[str, ...]:
    return tuple(f"{volume_detail(change)} {provenance}" for change, provenance in review_versions(saved, changes))


def change_notice(binding: RowBinding) -> ChangeNotice | None:
    if binding.saved is None or not binding.needs_review:
        return None
    kind = worst_kind(established(binding.changes))
    if kind is None:
        title = ("Recorded version could not be checked"
                 if any(change.missing is MissingEvidence.UNKNOWN for change in binding.changes)
                 else "Recorded version could not be read")
    else:
        title = {
            ChangeKind.DATE: "Files may have changed since tagged",
            ChangeKind.FILE_FACTS: "Reconstruction changed since tagged",
            ChangeKind.MISSING: "Recorded version not found",
        }[kind]
    verified = established(binding.changes)
    unverified = tuple(change for change in binding.changes if change not in verified)
    if len(verified) > 1:
        primary = tuple(change for change in verified if change.kind is kind)
        lead = volume_lines(binding.saved, primary)[0]
        body = " ".join((f"{lead} +{len(verified) - 1} more", *volume_lines(binding.saved, unverified)))
        details = "\n".join(volume_lines(binding.saved, binding.changes))
    else:
        body = " ".join(volume_lines(binding.saved, binding.changes))
        details = ""
    return ChangeNotice(title, body, any(change.kind is not ChangeKind.DATE for change in binding.changes), details)


def issue_text(row: AcceptanceRow) -> str:
    return {
        None: "",
        AcceptanceIssue.UNREADABLE: "Reload the processing folder when the recorded header can be read.",
        AcceptanceIssue.INCOMPLETE_DISCOVERY: "Reload the processing folder when it is available. Nothing has been accepted.",
        AcceptanceIssue.NO_READABLE_VERSION: "There is no readable current version to accept.",
    }[row.issue]


def unmatched_count(project: TagProject, folders: tuple[ProcessingSource, ...]) -> int:
    matched = {(item.binding.ref.source, item.binding.ref.relative)
               for item in bind_rows(project, folders) if item.binding.ref is not None}
    return sum((row.ref.source, row.ref.relative) not in matched for row in project.tomograms)


def review_total(rows: tuple[AcceptanceRow, ...]) -> str:
    changed = sum(bool(established(row.changes)) for row in rows)
    unverified = len(rows) - changed
    parts = []
    if changed:
        parts.append(f"{count_phrase(changed, 'tomogram')} changed since tagged")
    if unverified:
        subject = str(unverified) if changed else count_phrase(unverified, "tomogram")
        parts.append(f"{subject} could not be checked")
    return " · ".join((*parts, "Review…")) if parts else ""


@dataclass(frozen=True, slots=True)
class ShowAction:
    keys: frozenset[str]
    scope_label: str


def show_action(proposal: AcceptanceProposal, in_view: frozenset[str]) -> ShowAction:
    routed = tuple(row for row in proposal.rows if row.row_id in in_view)
    states = {bool(established(row.changes)) for row in routed}
    label = {frozenset({True}): "changed since tagged", frozenset({False}): "unverified tag sources",
             frozenset({True, False}): "tag source review"}.get(frozenset(states), "")
    return ShowAction(frozenset(row.row_id for row in routed), label)


KIND_LABELS = {ChangeKind.DATE: "Date only", ChangeKind.FILE_FACTS: "File facts differ",
               ChangeKind.MISSING: "Recorded version gone"}


def change_review(proposal: AcceptanceProposal) -> tuple[tuple[str, tuple[AcceptanceRow, ...]], ...]:
    groups: dict[ChangeKind | None, list[AcceptanceRow]] = {kind: [] for kind in (*KIND_LABELS, None)}
    for row in proposal.rows:
        kind = worst_kind(established(row.changes)) if row.issue is None else None
        groups[kind].append(row)
    return tuple((KIND_LABELS[kind] if kind is not None else "Cannot be accepted yet", tuple(rows))
                 for kind, rows in groups.items() if rows)


def count_tooltip(assignment: TagAssignment, row: TomogramTags) -> str:
    recorded = datetime.fromisoformat(assignment.recorded_at).astimezone()
    lines = [f"Recorded on {assignment.version_label} · {recorded:%d %b %Y %H:%M %Z}"]
    for event in row.acceptances:
        accepted = datetime.fromisoformat(event.accepted_at).astimezone()
        lines.append(f"Acceptance history · {accepted:%d %b %Y %H:%M %Z} · {KIND_LABELS[event.kind]}")
    return "\n".join(lines)
