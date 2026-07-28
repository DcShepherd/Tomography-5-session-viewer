"""Regression tests for user-facing unit spelling and count agreement.

Covers audit items 4 (Å / ° / µm were spelled three different ways across
adjacent panels) and 8 ("1 groups", "1 frames", "1 batch positions").
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.models import MdocSection, MrcMetadata, SearchMap, TiltSeries
from tomography_session_browser.domain.units import (
    ANGSTROM,
    ANGSTROM_PER_PIXEL,
    DEGREE,
    MICROMETRE,
)
from tomography_session_browser.services.item_status import build_item_status_context, item_list_status
from tomography_session_browser.ui import image_viewer, session_presenter


REPO_ROOT = Path(__file__).resolve().parents[1]

#: Modules whose strings reach the interface directly.
USER_FACING_SOURCES = (
    "tomography_session_browser/services/item_status.py",
    "tomography_session_browser/ui/image_viewer.py",
    "tomography_session_browser/ui/session_presenter.py",
    "tomography_session_browser/ui/main_window.py",
    "tomography_session_browser/ui/widgets/session_dashboard.py",
)


# --------------------------------------------------------------- count_phrase


@pytest.mark.parametrize(
    ("count", "singular", "plural", "expected"),
    [
        (1, "group", None, "1 group"),
        (0, "group", None, "0 groups"),
        (2, "group", None, "2 groups"),
        (1, "frame", None, "1 frame"),
        (1, "batch position", None, "1 batch position"),
        (3, "batch position", None, "3 batch positions"),
        (1, "atlas", "atlases", "1 atlas"),
        (4, "atlas", "atlases", "4 atlases"),
        # Invariant nouns must be passed explicitly so a single one does not
        # become "1 tilt serie".
        (1, "tilt series", "tilt series", "1 tilt series"),
        (9, "tilt series", "tilt series", "9 tilt series"),
        (2805, "warning", None, "2,805 warnings"),
    ],
)
def test_count_phrase_agrees_with_its_count(count, singular, plural, expected) -> None:
    assert count_phrase(count, singular, plural) == expected


def test_count_phrase_groups_thousands() -> None:
    assert count_phrase(12345, "tile") == "12,345 tiles"


# ------------------------------------------------------------- unit constants


def test_unit_constants_use_typographic_characters() -> None:
    # Guards against someone "fixing" an encoding problem by substituting
    # ASCII look-alikes, which is how the drift started.
    assert ANGSTROM == "Å"
    assert MICROMETRE == "µm"
    assert DEGREE == "°"
    assert ANGSTROM_PER_PIXEL == f"{ANGSTROM}/px"
    assert "A/px" not in ANGSTROM_PER_PIXEL.replace(ANGSTROM, "")


@pytest.mark.parametrize("relative_path", USER_FACING_SOURCES)
def test_user_facing_modules_do_not_hardcode_ascii_unit_spellings(relative_path: str) -> None:
    """No literal ``A/px``, `` um`` or `` deg`` in strings the user reads.

    The tilt-series header chip and the badge painted over the same image
    disagreed (``6.78 A/px`` vs ``6.78 Å/px``) because each layer spelled the
    unit inline. Units now come from ``domain.units``.
    """

    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    offenders = []
    for pattern, label in (
        (r"\bA/px\b", "A/px"),
        (r"\bA/pixel\b", "A/pixel"),
        (r"\{[^{}]*\}\s+um\b", "um"),
        (r"\{[^{}]*\}\s*deg\b", "deg"),
        (r"['\"]deg['\"]", "'deg'"),
    ):
        for match in re.finditer(pattern, source):
            line = source.count("\n", 0, match.start()) + 1
            offenders.append(f"{relative_path}:{line} uses {label}")
    assert offenders == []


# -------------------------------------------------- rendered strings end-to-end


def _tilt_series(**overrides) -> TiltSeries:
    defaults = dict(
        id="tilt-1",
        name="vellio_1",
        mrc_path=Path("vellio_1.mrc"),
        tilt_count=1,
        tilt_range=(-0.02, -0.02),
        pixel_size=6.78,
        original_pixel_size=3.39,
        binning=2,
        sections=[MdocSection(z_value=0, metadata={"TiltAngle": -0.02})],
        mrc_metadata=MrcMetadata(path=Path("vellio_1.mrc"), size_bytes=0, nx=4096, ny=4096, nz=1),
    )
    defaults.update(overrides)
    return TiltSeries(**defaults)


def test_viewer_chip_and_context_panel_agree_on_the_pixel_size_unit() -> None:
    """The header chip and the Summary block used to disagree on Å vs A."""

    tilt = _tilt_series()
    chips = image_viewer._chips_for_item(tilt)
    pixel_chip = next(chip for chip in chips if "px" in chip)
    assert pixel_chip == f"6.78 {ANGSTROM_PER_PIXEL}"

    summary = session_presenter.describe_object(tilt)
    assert f"6.78 {ANGSTROM_PER_PIXEL}" in summary
    assert f"original 3.39 {ANGSTROM_PER_PIXEL}, binning 2×" in summary
    assert "6.78 A " not in summary
    assert " A/px" not in summary


def test_context_panel_summary_and_validation_use_the_same_degree_notation() -> None:
    summary = session_presenter.describe_object(_tilt_series())
    assert f"Tilt range: -0.02{DEGREE} to -0.02{DEGREE}" in summary
    assert "deg" not in summary


def test_single_frame_tilt_series_reads_as_one_frame() -> None:
    summary = image_viewer._summary_for_item(_tilt_series(tilt_count=1))
    assert summary.startswith("1 frame ")
    assert "1 frames" not in summary


def test_single_linked_batch_position_reads_as_singular() -> None:
    search_map = SearchMap(
        id="sm-1",
        name="SearchMap_20260320_021641",
        tile_paths=[Path("Tile_1.mrc")],
        linked_batch_position_ids=["bp-1"],
    )
    chips = image_viewer._chips_for_item(search_map)
    assert "1 batch position" in chips
    assert "1 batch positions" not in chips
    assert "1 tile" in chips


def test_viewer_row_status_uses_singular_nouns_and_angstrom() -> None:
    tilt = _tilt_series()
    status = item_list_status(tilt, build_item_status_context(tilt_series=[tilt]))
    assert "1 frames" not in status.summary
    assert f"6.78 {ANGSTROM_PER_PIXEL}" in status.summary
    assert " A/px" not in status.summary
