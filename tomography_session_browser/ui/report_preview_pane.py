"""The report dialog's "In this report" pane (plan B6, Q7 B, board 2b).

It only shows a ``ReportPreview``: ``presentation/report_preview.py`` decides
every line from the normalised scope. Each update replaces the body; the old
body is hidden at once so it cannot show under the new one before Qt deletes it.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.report_preview import (
    ORDER_NOTE, TOTAL, PreviewBlock, PreviewItem, ReportPreview,
)
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.rounded_viewport import round_viewport

#: Inside the pane's border, as the tree's rows sit inside its border.
PANE_MARGINS = (SPACE_M + 2, SPACE_M - 2, SPACE_M + 2, SPACE_M - 2)


def _label(text: str, name: str, parent: QWidget, *, wrap: bool = False) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _elided(text: str, name: str, parent: QWidget, mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle) -> ElidedLabel:
    label = ElidedLabel(text, mode=mode, parent=parent)
    label.setObjectName(name)
    return label


class ReportPreviewPane(QScrollArea):
    """A bordered, scrolling list of the report's blocks, in report order."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("reportPreview")
        self.setAccessibleName("In this report")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The text is the pane's accessible description; Tab goes from the tree to the options.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        round_viewport(self)
        content = QWidget(self)
        content.setObjectName("reportPreviewContent")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(*PANE_MARGINS)
        self._layout.setSpacing(SPACE_M)
        self._body = QWidget(content)
        self._layout.addWidget(self._body)
        self._layout.addStretch(1)
        self.order_note = _label(ORDER_NOTE, "reportPreviewCaption", content)
        self._layout.addWidget(self.order_note)
        self.setWidget(content)
        self.preview = ReportPreview()
        self.show_preview(self.preview)

    def show_preview(self, preview: ReportPreview) -> None:
        self.preview = preview
        body = QWidget(self.widget())
        body.setObjectName("reportPreviewBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_M)
        if preview.lead:
            lead = _label(preview.lead, "reportPreviewLead", body, wrap=True)
            # The folder-report switch (Q8) stands out; "Nothing is ticked." does not.
            lead.setProperty("notice", bool(preview.blocks))
            layout.addWidget(lead)
        for block in preview.blocks:
            layout.addWidget(self._block(block, body))
        if preview.totals:
            layout.addWidget(self._totals(preview.totals, body))
        for note in preview.notes:
            layout.addWidget(_label(note, "reportPreviewCaption", body, wrap=True))
        old, self._body = self._body, body
        old.hide()
        self._layout.replaceWidget(old, body)
        old.deleteLater()
        self.order_note.setVisible(bool(preview.blocks))
        self.setAccessibleDescription(preview.text)

    def content_height(self) -> int:
        """The height that shows every line without a scroll bar, at the current width."""
        content, viewport = self.widget(), self.viewport()
        if content is None or viewport is None:     # set in __init__; Qt types them as Optional
            return 2 * self.frameWidth()
        width = max(viewport.width(), 1)
        height = content.heightForWidth(width) if content.hasHeightForWidth() else -1
        return max(height, content.sizeHint().height()) + 2 * self.frameWidth()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().sizeHint().width(), self.content_height())

    @staticmethod
    def _block(block: PreviewBlock, parent: QWidget) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS // 2)
        layout.addWidget(_elided(block.title, "reportPreviewTitle", widget))
        if block.subtitle:
            layout.addWidget(_elided(block.subtitle, "reportPreviewCaption", widget))
        if block.items:
            grid = QGridLayout()
            grid.setContentsMargins(0, SPACE_XS, 0, 0)
            grid.setHorizontalSpacing(SPACE_S)
            grid.setVerticalSpacing(SPACE_XS)
            grid.setColumnStretch(1, 1)
            for row, item in enumerate(block.items):
                bullet = _label("•", "reportPreviewBullet", widget)
                grid.addWidget(bullet, row, 0)
                grid.addWidget(_elided(item.label, "reportPreviewItem", widget, Qt.TextElideMode.ElideRight), row, 1)
                if item.count is not None:
                    grid.addWidget(_count(item, widget), row, 2)
            layout.addLayout(grid)
        if block.note:
            layout.addWidget(_label(block.note, "reportPreviewCaption", widget, wrap=True))
        return widget

    @staticmethod
    def _totals(items: tuple[PreviewItem, ...], parent: QWidget) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        rule = QFrame(widget)
        rule.setObjectName("reportPreviewRule")
        rule.setFrameShape(QFrame.Shape.NoFrame)
        rule.setFixedHeight(1)
        layout.addWidget(rule)
        layout.addSpacing(SPACE_XS)
        layout.addWidget(_label(TOTAL, "cardTitle", widget))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(SPACE_XS)
        grid.setColumnStretch(0, 1)
        for row, item in enumerate(items):
            grid.addWidget(_elided(item.label, "reportPreviewItem", widget, Qt.TextElideMode.ElideRight), row, 0)
            grid.addWidget(_count(item, widget), row, 1)
        layout.addLayout(grid)
        return widget


def _count(item: PreviewItem, parent: QWidget) -> QLabel:
    count = _label(f"{item.count:,}", "reportPreviewCount", parent)
    count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return count
