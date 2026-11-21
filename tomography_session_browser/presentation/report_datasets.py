"""The report's datasets and their atlases, shared by the PDF, the tomogram selection and the dialog preview."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from tomography_session_browser.domain.models import Atlas, Sample, Session
from tomography_session_browser.services.session_linking import atlas_link_resolutions

AMBIGUOUS_ATLAS = "Atlas link is ambiguous; no atlas has been selected."
UNLOADED_ATLAS = "Linked atlas is unavailable. Load the matching atlas session to include it."


def has_collection_content(owner: Session | Sample) -> bool:
    """The report's dataset predicate: any data-collection content."""
    return bool(owner.overviews or owner.search_maps or owner.search_tiles or owner.batch_positions or owner.tilt_series)


def report_datasets(session: Session) -> list[Sample]:
    """A session's report datasets, in report order.

    The real samples with collection content, else one synthetic session-level
    sample when the session holds that content directly. The synthetic sample is
    a new object each call: match datasets by their content keys, never identity.
    """
    datasets = [sample for sample in session.samples if has_collection_content(sample)]
    if not datasets and has_collection_content(session):
        datasets.append(Sample(
            id=session.id, name=session.name, path=session.path, atlas=session.atlas,
            overviews=session.overviews, search_maps=session.search_maps, search_tiles=session.search_tiles,
            batch_positions=session.batch_positions, tilt_series=session.tilt_series,
            metadata=session.metadata_summary, warnings=list(session.warnings),
        ))
    return datasets


@dataclass(frozen=True, slots=True)
class DatasetAtlas:
    """The atlas a dataset shows in the report; display only, never a link."""
    atlas: Atlas | None = None
    #: A link lends the atlas: from this session and grid.
    linked: bool = False
    source_session: str | None = None
    source_sample: str | None = None
    #: Why a recorded link gives no atlas: ambiguous, or its session is not loaded.
    note: str | None = None

    @property
    def source(self) -> str | None:
        """"Session / Sample" for a linked atlas, as the PDF prints it."""
        if not self.linked:
            return None
        return " / ".join(name for name in (self.source_session, self.source_sample) if name)


def dataset_atlases(owners: Sequence[tuple[Session, Sequence[Sample]]],
                    sources: Sequence[Session]) -> dict[int, DatasetAtlas]:
    """Each dataset's atlas, keyed by ``id(dataset)``: its own, a linked one, or the session's.

    ``owners`` are the report's sessions with their ``report_datasets``;
    ``sources`` are every loaded session, so an ambiguity cannot disappear when
    the reviewer ticks a subset. An ambiguous or unloaded link gives a note and
    no atlas; it never falls back to the session's atlas.
    """
    lookup = list(sources) + [replace(session, samples=session.samples or list(datasets))
                              for session, datasets in owners]
    session_names = {id(sample): session.name for session in lookup for sample in session.samples}
    resolutions = atlas_link_resolutions(lookup)
    atlases: dict[int, DatasetAtlas] = {}
    for session, datasets in owners:
        for sample in datasets:
            resolution = resolutions.get(id(sample))
            if sample.atlas is None and resolution is not None:
                lender = resolution.sample
                if resolution.linked and lender is not None:     # ``linked`` implies a sample
                    atlases[id(sample)] = DatasetAtlas(lender.atlas, True, session_names.get(id(lender)) or None,
                                                       lender.name or None)
                else:
                    atlases[id(sample)] = DatasetAtlas(note=AMBIGUOUS_ATLAS if resolution.ambiguous else UNLOADED_ATLAS)
            else:
                atlases[id(sample)] = DatasetAtlas(sample.atlas if sample.atlas is not None else session.atlas)
    return atlases
