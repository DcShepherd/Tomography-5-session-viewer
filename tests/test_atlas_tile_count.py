"""Atlas tile counts count tiles, not the files that store them.

Tomography 5 saves every atlas tile as an ``.mrc`` and a ``.jpg`` (plus one
``.xml``), and ``Atlas.tile_paths`` keeps both images so a viewer can choose a
display source. Counting that list directly showed "24 tiles" for the 12-tile
SA_5J atlas on the Atlas tab chip, its list row, the context panel and the
dashboard atlas row. Search maps were fixed the same way earlier
(``test_search_map_tile_count_matches_the_chip_and_list_row``).
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Atlas, Sample, Session
from tomography_session_browser.services.item_status import atlas_tile_count
from tomography_session_browser.ui import image_viewer
from tomography_session_browser.ui.session_presenter import (
    EntityGroup,
    describe_object,
    session_dashboard_model,
)
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.session_dashboard import SessionDashboard


def _atlas(tiles: int, atlas_id: str = "24550202") -> Atlas:
    # One .mrc, one .jpg and one .xml per tile, as Tomography 5 writes them.
    stems = [f"Tile_{24550218 + index}_{index}_{atlas_id}" for index in range(tiles)]
    return Atlas(
        id=f"atlas-{atlas_id}",
        image_path=Path(f"Atlas_{atlas_id}.mrc"),
        tile_paths=[Path(f"{stem}{suffix}") for stem in stems for suffix in (".mrc", ".jpg")],
        tile_metadata_paths=[Path(f"{stem}.xml") for stem in stems],
    )


def test_atlas_tile_count_counts_each_tile_once() -> None:
    atlas = _atlas(12)
    assert len(atlas.tile_paths) == 24
    assert atlas_tile_count(atlas) == 12

    # Without tile XML the unique image stems still count each tile once.
    atlas.tile_metadata_paths = []
    assert atlas_tile_count(atlas) == 12

    assert atlas_tile_count(Atlas(id="empty")) == 0


def test_atlas_tile_count_prefers_one_xml_per_acquired_tile() -> None:
    atlas = _atlas(12)
    # A tile whose images are not on disk is still an acquired tile.
    atlas.tile_paths = atlas.tile_paths[:-2]
    assert atlas_tile_count(atlas) == 12


def test_atlas_tab_chip_and_list_row_count_each_tile_once() -> None:
    atlas = _atlas(12)

    chips = image_viewer._chips_for_item(atlas)  # noqa: SLF001 - display helper under test
    assert "12 tiles" in chips
    assert "24 tiles" not in chips
    assert image_viewer._summary_for_item(atlas) == "12 tiles"  # noqa: SLF001


def test_atlas_context_panel_counts_tiles_and_names_file_counts_as_files() -> None:
    text = describe_object(_atlas(12))

    assert "Tiles: 12" in text
    assert "Tile image files: 24" in text
    assert "Tile metadata files: 12" in text


def test_atlas_group_context_panel_sums_tiles_not_files() -> None:
    group = EntityGroup(label="Atlases", values=[_atlas(12), _atlas(5, atlas_id="24560289")])

    assert "Tiles: 17" in describe_object(group)


def test_dashboard_atlas_row_counts_each_tile_once() -> None:
    session = Session(
        id="screening",
        name="Atlas_Reacquisition_Demo",
        path=Path("Atlas_Reacquisition_Demo"),
        kind=SessionKind.ATLAS_SCREENING,
        samples=[
            Sample(
                id="sample3",
                name="3. SA_5J",
                path=Path("Atlas_Reacquisition_Demo/Sample3"),
                atlas=_atlas(12),
            )
        ],
    )

    summary = session_dashboard_model(session).atlas_summary
    assert summary is not None
    assert [row.tile_count for row in summary.rows] == [12]

    QApplication.instance() or QApplication([])
    card = SessionDashboard()._build_atlas_summary_card(summary)  # noqa: SLF001 - widget-level check
    details = [label.text_full() for label in card.findChildren(ElidedLabel, "metaPlain")]
    assert any("12 tiles" in text for text in details), details
    assert not any("24 tiles" in text for text in details), details
    card.close()
