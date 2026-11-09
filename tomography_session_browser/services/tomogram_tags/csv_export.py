"""Atomic spreadsheet-safe export of one immutable project revision."""
import csv
import io
from pathlib import Path
from collections.abc import Mapping

from tomography_session_browser.domain.tomogram_tags import TagProject
from tomography_session_browser.services.atomic_files import atomic_write
from tomography_session_browser.services.tomogram_tags.codec import thaw
from tomography_session_browser.services.tomogram_tags.store import validate_target

COLUMNS = ("processing folder", "tomogram", "tag", "count", "counting complete", "version", "updated",
           "linked image files", "note", "binding status", "source changed", "files accepted")


def spreadsheet_text(value: str) -> str:
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def export_csv(project: TagProject, destination: Path, protected_roots: tuple[Path, ...], *,
               binding_status: Mapping[tuple[str, str], tuple[str, bool | None]], unsaved: bool = False) -> str:
    sources, tags = {s.id: s for s in project.sources}, {t.id: t for t in project.catalogue}
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    for row in project.tomograms:
        status, changed = binding_status.get((row.ref.source, row.ref.relative), ("Not in loaded folders", None))
        change_status = {True: "Yes", False: "No", None: "Not checked"}[changed]
        acceptances = "; ".join(f"{event.accepted_at} ({event.kind.value})" for event in row.acceptances)
        captures = {c.key: thaw(c.record)["output"] for c in row.captures}
        assignments = row.tags or ((None,) if row.counting_complete or row.note else ())
        for assignment in assignments:
            linked = ";".join(captures[l.capture_key] for l in row.links if assignment and l.tag_id == assignment.tag_id)
            values = [str(sources[row.ref.source].path), row.ref.relative,
                      tags[assignment.tag_id].name if assignment else "", assignment.count if assignment and assignment.count is not None else "",
                      "Yes" if row.counting_complete else "No", assignment.version_label if assignment else "",
                      assignment.recorded_at if assignment else "", linked, row.note, status, change_status, acceptances]
            writer.writerow([spreadsheet_text(v) if isinstance(v, str) else v for v in values])
    validate_target(destination, protected_roots)
    atomic_write(destination, output.getvalue().encode("utf-8-sig"), protected_roots=protected_roots)
    return f"Exported {'unsaved ' if unsaved else ''}revision {project.revision}."
