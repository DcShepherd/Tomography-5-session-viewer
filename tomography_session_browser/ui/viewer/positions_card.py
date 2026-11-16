"""The Atlas tab's Positions card: what each batch-position glyph means, with counts.

It sits above the scale bar and opens upwards. The rows come from the one glyph
rule (``services.atlas_lod.atlas_glyph_key``), so a count here is the number of
glyphs of that kind on the canvas. A row with no glyphs is hidden. The From
button (``set_collections``) opens the collection filter; it is hidden while
the atlas has one collection or none.

The Colour by button in the header (batch 4, ``ColourBy``) names the colour
mode and opens its menu. The counts here never change with the mode: they are
glyph counts. A notice line under the header says, for a moment, why a mode
returned to Status.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.presentation.status_meanings import ATLAS_GLYPH_LABELS
from tomography_session_browser.services.atlas_lod import ATLAS_GLYPH_KEYS, atlas_glyph_key
from tomography_session_browser.ui.motion.section_reveal import conceal_with_effect
from tomography_session_browser.ui.viewer.atlas_lod_items import paint_chrome_leaf_glyph, paint_legend_cluster_glyph

#: The marker roles the card counts: batch positions and failed tilt series with no batch link.
COUNTED_ROLES = frozenset(("batch_position", "unattributed"))
#: The widest mode name the Colour by button shows before it elides.
COLOUR_TEXT_MAX_PX = 100


class _Glyph(QWidget):
    """One small glyph painted on the card: a glyph key, or the cluster sample."""

    def __init__(self, key: str | None, parent: QWidget) -> None:
        super().__init__(parent)
        self.key = key
        self.setFixedSize(20, 20)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            centre = QPointF(self.width() / 2.0, self.height() / 2.0)
            if self.key is None:
                paint_legend_cluster_glyph(painter, centre)
            else:
                paint_chrome_leaf_glyph(painter, centre, self.key, diameter=12.0, halo=True)
        finally:
            painter.end()


class _Row(QWidget):
    def __init__(self, key: str | None, text: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("positionsCardRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 12, 0)
        layout.setSpacing(6)
        self.glyph = _Glyph(key, self)
        self.label = QLabel(text, self)
        self.label.setObjectName("positionsCardLabel")
        self.count = QLabel("", self)
        self.count.setObjectName("positionsCardCount")
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.glyph)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.count)
        self.setFixedHeight(24)


class PositionsCard(QFrame):
    """Collapsed-by-default status card, anchored above the scale bar.

    Closed, it is as wide as its header; open (or with a notice), it is at
    least ``WIDTH``, so its rows keep their room. The width moves with the
    open and close reveal (``overlay_panel.set_atlas_legend_expanded``).
    """

    #: The open card's least width.
    WIDTH = 228
    #: The card opened (True) or closed (False).
    expanded_changed = Signal(bool)
    HEADER_HEIGHT = 34

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("positionsCard")
        self._expanded = False
        self._toggle_handler: Callable[[bool], None] | None = None
        self.counts: dict[str, int] = {key: 0 for key in ATLAS_GLYPH_KEYS}
        self._concealed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 0, 1, 1)
        layout.setSpacing(0)
        self.header = QWidget(self)
        self.header.setObjectName("positionsCardHeader")
        self.header.setFixedHeight(self.HEADER_HEIGHT)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setToolTip("Show or hide what the batch-position markers mean")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 0, 6, 0)
        header_layout.setSpacing(6)
        header_layout.addWidget(_Glyph(None, self.header))
        self.title = QLabel("Positions", self.header)
        self.title.setObjectName("positionsCardTitle")
        header_layout.addWidget(self.title, 1)
        self.colour_button = QPushButton("Status", self.header)
        self.colour_button.setObjectName("positionsCardColour")
        self.colour_button.setAccessibleName("Colour by")
        self.colour_button.setVisible(False)
        header_layout.addWidget(self.colour_button)
        self.collapse_button = QToolButton(self.header)
        self.collapse_button.setObjectName("positionsCardCollapse")
        self.collapse_button.setAutoRaise(True)
        self.collapse_button.setAccessibleName("Positions card")
        self.collapse_button.clicked.connect(self._toggle)
        header_layout.addWidget(self.collapse_button)
        layout.addWidget(self.header)
        self.notice = QLabel("", self)
        self.notice.setObjectName("positionsCardNotice")
        self.notice.setWordWrap(True)
        self.notice.setContentsMargins(10, 2, 10, 4)
        self.notice.setVisible(False)
        layout.addWidget(self.notice)

        self.body = QWidget(self)
        self.body.setObjectName("positionsCardBody")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 6)
        body_layout.setSpacing(0)
        self.rows = {key: _Row(key, ATLAS_GLYPH_LABELS[key], self.body) for key in ATLAS_GLYPH_KEYS}
        for row in self.rows.values():
            row.setVisible(False)
            body_layout.addWidget(row)
        self.rule = QFrame(self.body)
        self.rule.setObjectName("positionsCardRule")
        self.rule.setFrameShape(QFrame.Shape.HLine)
        self.rule.setFixedHeight(1)
        body_layout.addSpacing(2)
        body_layout.addWidget(self.rule)
        self.cluster_row = _Row(None, "n = members, ring = mix", self.body)
        body_layout.addWidget(self.cluster_row)
        self.from_button = QPushButton("From: all", self.body)
        self.from_button.setObjectName("positionsCardFrom")
        self.from_button.setToolTip("Choose the data collections whose batch positions show")
        self.from_button.setVisible(False)
        from_row = QHBoxLayout()
        from_row.setContentsMargins(8, 4, 8, 0)
        from_row.addWidget(self.from_button)
        body_layout.addLayout(from_row)
        self.body.setVisible(False)
        layout.addWidget(self.body)
        self._sync()

    # State --------------------------------------------------------------
    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """Open or close the card at once."""

        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._sync()
        self.expanded_changed.emit(expanded)

    def set_toggle_handler(self, handler: Callable[[bool], None] | None) -> None:
        """Route a click through ``handler(expanded)``, which may animate it."""

        self._toggle_handler = handler

    def set_concealed(self, concealed: bool) -> None:
        """Paint nothing while a reveal shows the card's picture."""

        if concealed != self._concealed:
            self._concealed = concealed
            conceal_with_effect(self, concealed)

    def set_markers(self, markers: Sequence[ImageMarker]) -> None:
        counts = {key: 0 for key in ATLAS_GLYPH_KEYS}
        for marker in markers:
            if marker.metadata.get("atlas_lod_role") not in COUNTED_ROLES:
                continue
            key = atlas_glyph_key(marker)
            if key in counts:
                counts[key] += 1
        if counts != self.counts:
            self.counts = counts
            self._sync()

    def set_from_text(self, text: str | None) -> None:
        """Show the From button with ``text``, or hide it with ``None``."""

        if text is not None:
            self.from_button.setText(text)
        if self.from_button.isHidden() != (text is None):
            self.from_button.setVisible(text is not None)
            self._sync()

    def set_colour_text(self, mode: str | None, short: str | None = None) -> None:
        """Name the colour mode on the Colour by button (``short`` on the button), or hide it with ``None``."""

        changed = False
        if mode is not None:
            metrics = self.colour_button.fontMetrics()
            text = metrics.elidedText(short or mode, Qt.TextElideMode.ElideRight, COLOUR_TEXT_MAX_PX)
            changed = text != self.colour_button.text()
            self.colour_button.setText(text)
            self.colour_button.setToolTip(f"Colour by: {mode}. Choose what the marker colours show")
        if self.colour_button.isHidden() != (mode is None):
            self.colour_button.setVisible(mode is not None)
            changed = True
        if changed:
            self._sync()

    def set_notice(self, text: str | None) -> None:
        """Show ``text`` under the header, or remove it with ``None``."""

        self.notice.setText(text or "")
        if self.notice.isHidden() != (not text):
            self.notice.setVisible(bool(text))
            self._sync()

    # Geometry -----------------------------------------------------------
    def closed_width(self) -> int:
        """The header's own width: the glyph, the title, the Colour by button and the arrow."""

        margins = self.layout().contentsMargins()
        return self.header.layout().sizeHint().width() + margins.left() + margins.right()

    def open_width(self) -> int:
        return max(self.WIDTH, self.closed_width())

    def riding_width(self) -> int:
        """How far the header's right-hand controls sit from the card's right edge.

        A width reveal moves these columns with the edge and clips the rest.
        """

        first = self.colour_button if not self.colour_button.isHidden() else self.collapse_button
        layout = self.header.layout()
        right = layout.contentsMargins().right() + self.layout().contentsMargins().right()
        controls = [first] + ([self.collapse_button] if first is not self.collapse_button else [])
        return right + sum(widget.sizeHint().width() for widget in controls) + layout.spacing() * len(controls)

    def notice_height(self) -> int:
        if self.notice.isHidden():
            return 0
        return self.notice.heightForWidth(self.open_width() - 2)

    def collapsed_height(self) -> int:
        return self.HEADER_HEIGHT + self.notice_height()

    def expanded_height(self) -> int:
        """The card's height when open, from the rows it shows now."""

        return self.collapsed_height() + self.body.layout().sizeHint().height() + 1

    def picture(self) -> QPixmap:
        """The open card, its arrow showing the current state."""

        concealed = self._concealed
        self.set_concealed(False)
        opened = self._expanded
        if not opened:
            self.body.setVisible(True)
            self.setFixedSize(self.open_width(), self.expanded_height())
            self.layout().activate()
        try:
            return self.grab()
        finally:
            if not opened:
                self.body.setVisible(False)
                self.setFixedSize(self.shown_width(), self.collapsed_height())
                self.layout().activate()
            self.set_concealed(concealed)

    def shown_width(self) -> int:
        """Open or with a notice: the open width; closed: the header's."""

        return self.open_width() if self._expanded or not self.notice.isHidden() else self.closed_width()

    def _sync(self) -> None:
        any_rows = False
        for key, row in self.rows.items():
            count = self.counts[key]
            row.count.setText(str(count))
            row.setVisible(count > 0)
            any_rows = any_rows or count > 0
        self.rule.setVisible(any_rows)
        self.cluster_row.setVisible(any_rows)
        self.body.setVisible(self._expanded)
        self.collapse_button.setArrowType(Qt.ArrowType.DownArrow if self._expanded else Qt.ArrowType.UpArrow)
        self.collapse_button.setToolTip("Close the Positions card" if self._expanded else "Open the Positions card")
        self.setFixedSize(self.shown_width(), self.expanded_height() if self._expanded else self.collapsed_height())

    # Input --------------------------------------------------------------
    def _toggle(self) -> None:
        if self._toggle_handler is not None:
            self._toggle_handler(not self._expanded)
        else:
            self.set_expanded(not self._expanded)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle()
            event.accept()
            return
        super().mousePressEvent(event)
