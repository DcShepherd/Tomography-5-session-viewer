"""What a parsed session or sample holds, asked one way everywhere.

``has_collection_data`` is the rule for "data collection happened here": any
overview, search map, search tile, batch position or tilt series. The PDF
cover's totals used to leave search tiles out; divergence-register rule 3
gave them this rule too (``tests/test_divergence_register.py``).
"""

from __future__ import annotations

from tomography_session_browser.domain.models import Sample, Session


def has_collection_data(holder: Session | Sample) -> bool:
    """Any overview, search map, search tile, batch position or tilt series.

    An empty Batch/BatchPositionsList.xml does not count on its own: the
    parser produces no batch positions for it.
    """

    return bool(
        holder.overviews
        or holder.search_maps
        or holder.search_tiles
        or holder.batch_positions
        or holder.tilt_series
    )


def is_single_atlas_placeholder(sample: Sample) -> bool:
    """A "Single Atlas" sample folder with no collection data: it only carries
    an atlas, and is neither a grid nor a second atlas."""

    return "single atlas" in sample.name.lower() and not has_collection_data(sample)
