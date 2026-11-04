"""The app's fold: a body that opens and closes under its header, with the project tree's springs."""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PySide6.QtWidgets import QAbstractButton, QApplication, QHBoxLayout, QLayout, QVBoxLayout, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.ui.motion.spring import Spring
from tomography_session_browser.ui.motion.tokens import (MOTION_SECTION_CLOSE, MOTION_SECTION_OPEN,
                                                         PIXEL_SETTLE_DISTANCE, PIXEL_SETTLE_VELOCITY)
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.section_header import SectionHeader


class Fold(QWidget):
    """A header and, under it, a body that opens and closes with the project tree's springs.

    While the body moves, its layout rests: the rows keep the places they have in
    the open body and the body's edge uncovers them, as the tree uncovers its
    child rows. A layout squeezed at every height put the rows on top of each
    other. A subclass adds its header to ``column``, then calls ``add_body``.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        # A gap under the header is the body's top margin, so it opens with the body and not in one jump.
        self.column.setSpacing(0)

    def add_body(self, toggle: QAbstractButton, open_: bool, margins: tuple[int, int, int, int]) -> None:
        self.toggle = toggle
        self.body = QWidget(self)
        self.layout_ = QVBoxLayout(self.body)
        self.layout_.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.layout_.setContentsMargins(*margins)
        self.layout_.setSpacing(SPACE_S)
        self.column.addWidget(self.body)
        self.full_height = 0
        self.spring = Spring(spec=MOTION_SECTION_OPEN, widget=self, on_update=self.apply_height, on_settled=self.finish,
                             settle_distance=PIXEL_SETTLE_DISTANCE, settle_velocity=PIXEL_SETTLE_VELOCITY)
        spring = self.spring
        self.destroyed.connect(lambda: spring.reset(0))
        toggle.toggled.connect(self.show_open)
        self.show_open(open_, animate=False)

    def show_open(self, open_: bool, *, animate: bool = True) -> None:
        focused = QApplication.focusWidget()
        if not open_ and focused is not None and self.body.isAncestorOf(focused):
            self.toggle.setFocus(Qt.FocusReason.OtherFocusReason)
        if not self.spring.is_animating:
            self.spring.reset(self.body.height() if self.body.isVisible() else 0)
        self.layout_.setEnabled(True)
        self.body.setVisible(True)
        self.layout_.activate()
        self.hold_rows()
        self.spring.spec = MOTION_SECTION_OPEN if open_ else MOTION_SECTION_CLOSE
        self.spring.set_target(self.full_height if open_ else 0, animate=animate and self.isVisible())

    def hold_rows(self) -> None:
        """Measure the open body at this width, put its rows there, and leave them there during the motion."""
        margins = self.column.contentsMargins()
        width = self.width() - margins.left() - margins.right()
        layout = self.layout_
        # A wrapped line is taller than its size hint says at this width; the target must not stop short of it.
        self.full_height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height()
        layout.setGeometry(QRect(0, 0, width, self.full_height))
        layout.setEnabled(False)

    def apply_height(self, height: float) -> None:
        self.body.setMaximumHeight(max(0, round(height)))
        self.update_layouts_above()

    def finish(self) -> None:
        opened = self.toggle.isChecked()
        self.layout_.setEnabled(True)
        self.body.setVisible(opened)
        self.body.setMaximumHeight(16777215 if opened else 0)
        if opened:
            # A row that changed during the motion, or a new width, reaches the layout now.
            self.layout_.invalidate()
            self.layout_.activate()
        self.update_layouts_above()

    def update_layouts_above(self) -> None:
        """Bring the layouts above the body up to date in this frame.

        A layout's request to its parent is queued after a paint that is already
        waiting, so each level was a frame late: a paint then saw the old height
        of the fold around the new height of its body, and the header stretched.
        """
        widget: QWidget | None = self
        while widget is not None:
            QCoreApplication.sendPostedEvents(widget, QEvent.Type.LayoutRequest)
            widget = None if widget.isWindow() else widget.parentWidget()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.spring.jump_to(self.spring.target)
        super().hideEvent(event)


class FoldingSection(Fold):
    """A compact summary while folded; the body clips during motion only."""

    def __init__(self, title: str, parent: QWidget, *, open_: bool = False) -> None:
        super().__init__(parent)
        header = QHBoxLayout()
        header.setSpacing(SPACE_S)
        toggle = SectionHeader(title, self, open_=open_)
        # Elided, so no summary can widen the panel (a longer text once moved the image).
        self.summary = ElidedLabel(parent=self)
        self.summary.setObjectName("tomogramSectionSummary")
        self.summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(toggle)
        header.addWidget(self.summary, 1)
        self.column.addLayout(header)
        self.add_body(toggle, open_, (0, SPACE_S, 0, 0))

    def show_open(self, open_: bool, *, animate: bool = True) -> None:
        self.summary.setVisible(not open_)
        super().show_open(open_, animate=animate)
