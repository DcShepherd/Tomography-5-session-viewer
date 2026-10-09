"""Atlas components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from tomography_session_browser.presentation.dashboard.models import AtlasRowModel, AtlasSummaryModel
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_S, SPACE_XS
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.dashboard.common import card_with_title
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


class SampleRowWidget(QWidget):
    clicked = Signal(str)

    def __init__(self, sample_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sample_id = sample_id
        self.setObjectName("dashboardInteractiveRow")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Open dashboard item")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._sample_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.clicked.emit(self._sample_id)
            event.accept()
            return
        super().keyPressEvent(event)


def build_atlas_summary_card(dashboard: SessionDashboard, atlas_summary: AtlasSummaryModel) -> QWidget:
    """Atlas-specific dashboard block.

    Shown for atlas-only sessions (where the collection cards would
    otherwise all read zero) and as a complement to the collection cards
    on mixed sessions.

    The headline row displays four distinct counts (atlas sessions /
    data-collection samples / samples with atlas / sample atlases)
    rather than a single ambiguous "Atlases" number — the previous
    implementation conflated the standalone screening session with
    per-grid atlases and data-collection sample atlases, producing
    an inflated 14 / 14 readout for the 12-grid test dataset.
    """

    counts = atlas_summary.counts
    card, layout = card_with_title("Atlas summary")

    # Top-line summary row. Three distinct counts in user-requested
    # order: atlas-session totals first, then how many samples have
    # an atlas, then how many of those are actually data-collection
    # samples. The previously-shown "Sample atlases" field has been
    # dropped — for the common case (one atlas per sample) it is
    # numerically identical to "Samples with atlas".
    summary_row = QHBoxLayout()
    summary_row.setSpacing(SPACE_L)
    summary_row.addWidget(
        dashboard._meta_pair_compact("Atlas sessions", str(counts.atlas_session_count))
    )
    summary_row.addWidget(
        dashboard._meta_pair_compact(
            "Samples with atlas", str(counts.samples_with_atlas_count)
        )
    )
    summary_row.addWidget(
        dashboard._meta_pair_compact(
            "Data collection samples", str(counts.data_collection_sample_count)
        )
    )
    # With a single atlas its own row below already states pixel size and
    # image size, so the summary pairs only repeated them.
    show_ranges = len(atlas_summary.rows) != 1
    if show_ranges and atlas_summary.pixel_size_range_um is not None:
        lo, hi = atlas_summary.pixel_size_range_um
        value = (
            f"{lo:.2f} µm/px"
            if abs(lo - hi) < 1e-6
            else f"{lo:.2f} – {hi:.2f} µm/px"
        )
        summary_row.addWidget(dashboard._meta_pair_compact("Pixel size", value))
    if show_ranges and atlas_summary.image_size_range is not None:
        small, large = atlas_summary.image_size_range
        text = (
            f"{small[0]} × {small[1]} px"
            if small == large
            else f"{small[0]} × {small[1]} – {large[0]} × {large[1]} px"
        )
        summary_row.addWidget(dashboard._meta_pair_compact("Image size", text))
    summary_row.addStretch(1)
    wrap = QWidget()
    wrap.setLayout(summary_row)
    layout.addWidget(wrap)

    # Per-atlas rows (capped to keep the card compact)
    max_rows = 8
    for row_model in atlas_summary.rows[:max_rows]:
        layout.addWidget(dashboard._atlas_row_widget(row_model))
    if len(atlas_summary.rows) > max_rows:
        more = QLabel(f"+{len(atlas_summary.rows) - max_rows} more atlas{'es' if len(atlas_summary.rows) - max_rows != 1 else ''}")
        more.setObjectName("metaKey")
        layout.addWidget(more)
    return card


def meta_pair_compact(label: str, value: str) -> QWidget:
    """Smaller variant of ``_meta_pair`` for use inside dashboard cards."""

    wrap = QWidget()
    v = QVBoxLayout(wrap)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(SPACE_XS)
    l = QLabel(label.upper())
    l.setObjectName("cardTitle")
    v.addWidget(l)
    # Ask for the whole value's width. A plain ElidedLabel sized itself to
    # its elided text, so "0.47 µm/px" stayed "0.47 µ…" beside a trailing
    # stretch holding most of the card's width.
    val = ElidedLabel(value, prefer_full_width=True)
    val.setObjectName("metaValue")
    v.addWidget(val)
    return wrap


def atlas_row_widget(row_model: AtlasRowModel) -> QWidget:
    wrap = QWidget()
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(0, SPACE_XS, 0, SPACE_XS)
    layout.setSpacing(SPACE_S)

    sample_label = ElidedLabel(row_model.sample_label)
    sample_label.setObjectName("metaValue")
    sample_label.setMinimumWidth(160)
    sample_label.setToolTip(row_model.sample_label)
    layout.addWidget(sample_label)

    details: list[str] = []
    if row_model.image_size is not None:
        details.append(f"{row_model.image_size[0]} × {row_model.image_size[1]} px")
    if row_model.pixel_size_um is not None:
        details.append(f"{row_model.pixel_size_um:.2f} µm/px")
    if row_model.tile_count:
        details.append(f"{row_model.tile_count} tiles")
    if row_model.acquisition_time:
        details.append(row_model.acquisition_time)
    if not details:
        details.append("(no additional metadata)")
    details_text = "    ".join(details)
    details_label = ElidedLabel(details_text)
    details_label.setObjectName("metaPlain")
    details_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    layout.addWidget(details_label, stretch=1)
    return wrap
