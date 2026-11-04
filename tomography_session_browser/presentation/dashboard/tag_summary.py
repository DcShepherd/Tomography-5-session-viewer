"""Content-only tag summaries reuse the shared indexed chart model."""
from dataclasses import dataclass
from statistics import median

from tomography_session_browser.presentation.dashboard.index_chart import IndexSeries
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey


@dataclass(frozen=True, slots=True)
class TagValue:
    id: str
    name: str
    count: int | None


@dataclass(frozen=True, slots=True)
class TagContent:
    project: ProjectKey
    project_name: str
    tags: tuple[TagValue, ...] = ()
    counting_complete: bool = False


@dataclass(frozen=True, slots=True)
class TagSummaryRow:
    tag_id: str
    name: str
    row_keys: tuple[str, ...]
    counted: int
    zero: int
    total: int | None
    median: float | None

    @property
    def tomograms(self) -> int:
        return len(self.row_keys)


@dataclass(frozen=True, slots=True)
class TagSummary:
    project: ProjectKey | None
    rows: tuple[TagSummaryRow, ...]
    series: tuple[IndexSeries, ...]
    notice: str = ""
    row_keys: tuple[str, ...] = ()
    completed: int = 0


def tag_summary(row_keys: tuple[str, ...], content: tuple[TagContent | None, ...], *,
                folder_projects: tuple[ProjectKey | None, ...]) -> TagSummary:
    if len(row_keys) != len(content):
        raise ValueError("Tag summary content must share the chart's row vector.")
    projects = {p for p in folder_projects if p is not None}
    if len(projects) > 1:
        return TagSummary(None, (), (), f"Tags: these folders belong to {len(projects)} projects. Open a folder's Summary to see its tags.")
    if not projects:
        return TagSummary(None, (), ())
    project = next(iter(projects))
    eligible = tuple((key, c) for key, c in zip(row_keys, content, strict=True) if c and c.project == project)
    labels = {t.id: t.name for _, c in eligible for t in c.tags}
    rows, series = [], []
    for tag_id, label in labels.items():
        keys, values, missing = [], [], []
        for key, c in eligible:
            tag = next((t for t in c.tags if t.id == tag_id), None)
            if tag:
                keys.append(key)
            values.append(float(tag.count) if tag and tag.count is not None else None)
            missing.append("" if tag and tag.count is not None else "Tagged, not counted" if tag else "Not tagged")
        numeric = [int(v) for v in values if v is not None]
        rows.append(TagSummaryRow(tag_id, label, tuple(keys), len(numeric), numeric.count(0),
                                  sum(numeric) if numeric else None, median(numeric) if numeric else None))
        series.append(IndexSeries(tag_id, label, "count", tuple(values), tuple(missing), missing_lane="Tagged, not counted"))
    rows.sort(key=lambda r: (-r.tomograms, r.name.casefold()))
    by_id = {s.key: s for s in series}
    unattached = len(content) - len(eligible)
    notice = f"{unattached} {'tomogram is' if unattached == 1 else 'tomograms are'} in folders without a project." if unattached else ""
    return TagSummary(project, tuple(rows), tuple(by_id[r.tag_id] for r in rows), notice,
                      tuple(key for key, _ in eligible), sum(c.counting_complete for _, c in eligible))
