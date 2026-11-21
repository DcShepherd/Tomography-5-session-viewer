"""The Sources of a report's recorded values (Q3 C, 19 Nov 2026): one line per column, then exceptions.

Each table column gets one line under its own heading: the recorded fields,
the file as a pattern (``warp_tiltseries/<series>.xml``) and each caveat
once. Columns from the same file and the same tomograms share a line. A
tomogram that differs from the rest (another unit, file or caveat) gets one
short exception line. The numbers behind each value go to the PDF metadata
(``recorded_detail``), not the page. The split comes from the parser's
``caveats``, ``detail`` and ``pattern`` fields; no display text is parsed.
No path from this computer is printed (ruling R1, ``print_source``).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from reportlab.platypus import Paragraph, Spacer, Table

from tomography_session_browser.domain.recorded_metrics import RecordedMetric
from tomography_session_browser.presentation.recorded_metrics import METRIC_COLUMNS, metric_number
from tomography_session_browser.presentation.report_tomograms import ReportRow, ScopedTomograms, print_source
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W

DETAIL_NOTE = "Detail for each tomogram is in the PDF metadata."
LABEL_SHARE = 0.3
CELL_PAD = 4.0

#: ``(printed source, caveats, unit)``: the tomograms of one column that agree on all three.
_Key = tuple[str, tuple[str, ...], str]


@dataclass
class _Line:
    """One printed line: the headings, fields and caveats of columns with the same file and tomograms."""
    headings: list[str]
    fields: list[str]
    source: str | None
    caveats: list[str]
    keys: frozenset[str] = field(default_factory=frozenset)


def caveats_of(metric: RecordedMetric) -> tuple[str, ...]:
    return metric.caveats or (metric.qualification,)


def _ordered_union(*parts: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(item for part in parts for item in part))


def _quoted(fields: Sequence[str]) -> list[str]:
    return [f'"{text}"' for text in fields]


def _names(members: Sequence[tuple[ReportRow, RecordedMetric]], rows: Sequence[ReportRow],
           scoped: ScopedTomograms) -> str:
    """Tomogram names; the folder too when two printed rows share a name."""
    labels = {source.source.id: source.label for source in scoped.sources}
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.tomogram.name] = counts.get(row.tomogram.name, 0) + 1
    return ", ".join(row.tomogram.name if counts[row.tomogram.name] == 1
                     else f"{row.tomogram.name} ({labels[row.tomogram.source_id]})" for row, _ in members)


def _column(heading: str, members: list[tuple[ReportRow, RecordedMetric]], rows: Sequence[ReportRow],
            scoped: ScopedTomograms) -> tuple[_Line | None, list[tuple[str, str, str]]]:
    """The line of one column and its exceptions as ``(names, heading, tail)``.

    The usual case is the group that holds more than half of the column's
    tomograms. With no such group, the line keeps only what every tomogram
    shares and each group gets an exception line, so no tomogram's own
    caveat reads as the rule.
    """
    groups: dict[_Key, list[tuple[ReportRow, RecordedMetric]]] = {}
    for row, metric in members:
        key = (print_source(metric.pattern or metric.source, scoped), caveats_of(metric), metric.unit)
        groups.setdefault(key, []).append((row, metric))

    def source_of(key: _Key, group: list) -> str:
        return print_source(group[0][1].source, scoped) if len(group) == 1 else key[0]

    ordered = list(groups.items())
    usual_key, usual = max(ordered, key=lambda item: len(item[1]))
    if 2 * len(usual) > len(members):
        source, caveats, unit = source_of(usual_key, usual), list(usual_key[1]), usual_key[2]
        others = [(key, group) for key, group in ordered if key != usual_key]
        line = _Line([heading], _quoted(dict.fromkeys(metric.field for _, metric in usual)), source, caveats,
                     frozenset(row.key for row, _ in usual))
    else:
        sources, units = {key[0] for key, _ in ordered}, {key[2] for key, _ in ordered}
        source = next(iter(sources)) if len(sources) == 1 else None
        unit = next(iter(units)) if len(units) == 1 else ""
        caveats = [text for text in ordered[0][0][1] if all(text in key[1] for key, _ in ordered)]
        others = ordered
        line = _Line([heading], _quoted(dict.fromkeys(metric.field for _, metric in members)), source, caveats)
    exceptions = []
    for key, group in others:
        tail = f" in {key[2]}" if key[2] != unit else ""
        if key[0] != source:
            fields = ", ".join(_quoted(dict.fromkeys(metric.field for _, metric in group)))
            tail += f", from {fields} in {source_of(key, group)}"
        extra = [text for text in key[1] if text not in caveats]
        exceptions.append((_names(group, rows, scoped), heading, f"{tail}." + "".join(f" {text}" for text in extra)))
    # With no shared file or caveat, the exception lines say it all.
    return (line if line.source is not None or line.caveats else None), exceptions


def _merge(lines: list[_Line], line: _Line) -> None:
    """Join a column to the line with the same file and the same tomograms, or start a new line."""
    for existing in lines:
        if line.keys and existing.keys == line.keys and existing.source == line.source:
            existing.headings += line.headings
            existing.fields = _ordered_union(existing.fields, line.fields)
            existing.caveats = _ordered_union(existing.caveats, line.caveats)
            return
    lines.append(line)


def _line_text(line: _Line) -> str:
    """Escaped markup: the fields, the file in mono, then each caveat once."""
    fields = graphics.html_escape(", ".join(line.fields))
    where = (f' in <font name="{styles.MONO}">{graphics.html_escape(line.source)}</font>'
             if line.source is not None else "")
    caveats = "".join(f" {graphics.html_escape(text)}" for text in line.caveats)
    return f"{fields}{where}.{caveats}"


def _table(lines: Sequence[_Line]) -> Table:
    width = PORTRAIT_USABLE_W
    label = min(max(styles.string_width(heading, styles.SANS_BOLD, styles.TYPE_SMALL)
                    for line in lines for heading in line.headings) + 2 * CELL_PAD, LABEL_SHARE * width)
    cells = [[Paragraph(f"<b>{graphics.html_escape(', '.join(line.headings))}</b>", styles.SMALL_STYLE),
              Paragraph(_line_text(line), styles.MUTED_STYLE)] for line in lines]
    return Table(cells, colWidths=[label, width - label], hAlign="LEFT", style=[
        ("LINEABOVE", (0, 0), (-1, -1), 0.4, styles.BORDER),
        ("LINEBELOW", (0, -1), (-1, -1), 0.4, styles.BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), CELL_PAD),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
    ])


def sources_block(rows: Sequence[ReportRow], scoped: ScopedTomograms) -> list:
    """The Sources lines, exception lines, ambiguous values and the metadata note, as flowables."""
    lines: list[_Line] = []
    exceptions: list[tuple[str, str, str]] = []
    ambiguous: list[str] = []
    has_detail = False
    for definition, heading in METRIC_COLUMNS:
        members = []
        for row in rows:
            candidates = [metric for metric in row.tomogram.processing.metrics if metric.definition == definition]
            if len(candidates) > 1:
                values = ", ".join(f"{metric_number(metric)} {metric.unit}" for metric in candidates)
                sources = ", ".join(dict.fromkeys(print_source(metric.source, scoped) for metric in candidates))
                ambiguous.append(f"{row.tomogram.name}: {heading} has {len(candidates)} values ({values}), "
                                 f"so the report shows no value. Sources: {sources}.")
            elif candidates:
                members.append((row, candidates[0]))
                has_detail = has_detail or bool(candidates[0].detail)
        if members:
            line, found = _column(heading, members, rows, scoped)
            if line is not None:
                _merge(lines, line)
            exceptions += found
    joined: dict[tuple[str, str], list[str]] = {}
    for names, heading, tail in exceptions:
        joined.setdefault((names, tail), []).append(heading)
    notes = [f"{names} · {', '.join(headings)}{tail}" for (names, tail), headings in joined.items()]
    notes += ambiguous
    if has_detail:
        notes.append(DETAIL_NOTE)
    return [*([_table(lines), Spacer(1, 3)] if lines else []),
            *(Paragraph(graphics.html_escape(text), styles.MUTED_STYLE) for text in notes)]


def recorded_detail(rows: Sequence[ReportRow], scoped: ScopedTomograms) -> list[dict[str, str]]:
    """The numbers behind each printed value, for the PDF keywords: no path from this computer, no row key."""
    labels = {source.source.id: source.label for source in scoped.sources}
    headings = dict(METRIC_COLUMNS)
    records = []
    for row in {row.key: row for row in rows}.values():
        for metric in row.tomogram.processing.metrics:
            if metric.detail and metric.definition in headings:
                records.append({"name": row.tomogram.name, "folder": labels[row.tomogram.source_id],
                                "column": headings[metric.definition],
                                "value": f"{metric_number(metric)} {metric.unit}",
                                "source": print_source(metric.source, scoped), "detail": metric.detail})
    return records
