"""The report's dataset enumeration, shared by the PDF context and the tomogram selection."""
from __future__ import annotations

from tomography_session_browser.domain.models import Sample, Session


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
