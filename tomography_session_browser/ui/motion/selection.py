"""A display-only selection background for single-selection tree delegates."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QMarginsF, QModelIndex, QObject, QPersistentModelIndex, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractItemView, QTreeView
import shiboken6

from tomography_session_browser.presentation.design_tokens import RADIUS_CONTROL
from tomography_session_browser.ui.list_decorations import paint_selection_marker
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.tokens import DURATION_MICRO_MS, INTERRUPTING_INPUT
from tomography_session_browser.ui.motion.tween import Tween
from tomography_session_browser.ui.theme import current_palette


class SelectionGlide(QObject):
    """Move only the fill/marker; Qt publishes selection and activation at once.

    Delegates paint the same moving rectangle, clipped to each row, before its
    text. No row pictures, model roles or hit regions move. Geometry changes cut
    to the live selection; the current model index is never inferred from pixels.
    """

    def __init__(self, view: QTreeView, *,
                 fill_margins: QMarginsF = QMarginsF(2, 2, 2, 2),
                 marker_margins: QMarginsF = QMarginsF(2, 1, 0, 1)) -> None:
        if view.selectionMode() != QAbstractItemView.SelectionMode.SingleSelection:
            raise ValueError("Selection glide requires a single-selection tree")
        super().__init__(view)
        self._view, self._viewport = view, view.viewport()
        self._fill_margins, self._marker_margins = fill_margins, marker_margins
        self._index = self._selected()
        self._start = self._target = self._rect = QRectF()
        self._auto_repeat = False
        self.tween = Tween(DURATION_MICRO_MS, on_update=self._moved, widget=view)
        view.destroyed.connect(self.tween.stop)
        setattr(view, "_tomo_selection_glide", self)  # retain the Python event filter
        view.installEventFilter(self)
        self._viewport.installEventFilter(self)
        view.selectionModel().selectionChanged.connect(self._selection_changed)
        model = view.model()
        for signal in (model.modelAboutToBeReset, model.layoutAboutToBeChanged,
                       model.rowsAboutToBeInserted, model.rowsAboutToBeRemoved, model.dataChanged,
                       view.expanded, view.collapsed, view.header().sectionResized,
                       view.verticalScrollBar().valueChanged, view.horizontalScrollBar().valueChanged):
            signal.connect(self.stop)

    def _selected(self) -> QPersistentModelIndex:
        rows = self._view.selectionModel().selectedRows(0)
        return QPersistentModelIndex(rows[0]) if len(rows) == 1 else QPersistentModelIndex()

    def _bounds(self, index: QPersistentModelIndex) -> QRectF:
        if not index.isValid():
            return QRectF()
        rect = QRectF(self._view.visualRect(QModelIndex(index)))
        rect.setRight(self._viewport.width())
        return rect if rect.isValid() and rect.intersects(QRectF(self._viewport.rect())) else QRectF()

    def _selection_changed(self, *_args) -> None:
        index = self._selected()
        start = self.rect()
        target = self._bounds(index)
        self.tween.stop()
        self._index = index
        self._start, self._target, self._rect = start, target, start
        if (not self._auto_repeat and self._view.isVisible() and motion_enabled(self._view)
                and start.isValid() and target.isValid() and start != target):
            self.tween.start()
        else:
            self.stop()

    def _moved(self, amount: float) -> None:
        previous = self._rect
        self._rect = QRectF(*(a + (b - a) * amount for a, b in zip(
            self._start.getRect(), self._target.getRect())))
        self._viewport.update(previous.united(self._rect).adjusted(-1, -1, 1, 1).toAlignedRect())

    def stop(self, *_args) -> None:
        """Drop motion; the next paint reads the live selection geometry."""
        self.tween.stop()
        if shiboken6.isValid(self._viewport):
            self._viewport.update()

    def rect(self) -> QRectF:
        if self.tween.is_animating:
            if motion_enabled(self._view):
                return QRectF(self._rect)
            self.stop()  # redraw the destination too when reduced motion cuts
        return self._bounds(self._index)

    def paint(self, painter: QPainter, row_rect) -> None:
        # Blocked selection signals during refills must not leave an old row's
        # pixels behind. This is a display sync, never a selection publication.
        index = self._selected()
        if index != self._index:
            self.tween.stop()
            self._index = index
        rect = self.rect()
        clip = QRectF(0, row_rect.top(), self._viewport.width(), row_rect.height())
        if not rect.isValid() or not rect.intersects(clip):
            return
        palette = current_palette()
        painter.save()
        painter.setClipRect(clip, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette.selection))
        painter.drawRoundedRect(rect.marginsRemoved(self._fill_margins), RADIUS_CONTROL, RADIUS_CONTROL)
        paint_selection_marker(painter, rect.marginsRemoved(self._marker_margins).toRect(), palette)
        painter.restore()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        kind = event.type()
        if watched not in (self._view, self._viewport):
            return False
        if kind in (QEvent.Type.Hide, QEvent.Type.Close):
            self.tween.stop()  # do not touch a viewport being destroyed
            self._auto_repeat = False
        elif kind in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate):
            self._auto_repeat = False
            self.stop()
        elif kind in INTERRUPTING_INPUT:
            self._auto_repeat = kind == QEvent.Type.KeyPress and event.isAutoRepeat()
            self.stop()
        elif kind == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            self._auto_repeat = False
        elif kind in (QEvent.Type.Resize, QEvent.Type.DevicePixelRatioChange, QEvent.Type.Show):
            self.stop()
        return False
