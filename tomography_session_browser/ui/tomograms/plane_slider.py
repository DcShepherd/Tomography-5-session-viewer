"""Plane-slider marks with an independent, bounded capture-history consumer."""
from __future__ import annotations
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QRectF, QSignalBlocker, QSize, Qt, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QMenu, QSlider, QStyle, QStyleOptionSlider, QVBoxLayout

from tomography_session_browser.domain.tomograms import Tomogram
from tomography_session_browser.parsers.volume_header import fingerprint
from tomography_session_browser.presentation.tomogram_capture_history import CapturePage, capture_page
from tomography_session_browser.presentation.tomogram_capture_marks import CaptureMarks, MarkCluster, capture_marks, cluster_marks
from tomography_session_browser.presentation.tomogram_geometry import normal_axis
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.tomograms.history_loading import HistoryJob, HistoryLoader
from tomography_session_browser.ui.widgets.atlas_thumbnail import AtlasThumbnail

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab

QueryKey = tuple[tuple[int, int], Tomogram, int]
BASE_HINT = "Go to a different plane (PageUp, PageDown). Shift + wheel over a pane also steps planes. F8 opens capture marks."


@dataclass(frozen=True, slots=True)
class MarkJump:
    identity: str
    plane: int
    state: TomogramViewState
    displayed: RenderRecipe
    view_revision: int


@dataclass(frozen=True, slots=True)
class MarkQuery:
    key: QueryKey
    page: CapturePage
    jump: MarkJump | None
    changed_sources: frozenset[str]


class PlaneSlider(QSlider):
    preview: QFrame | None = None
    hovered: MarkCluster | None = None

    def __init__(self, tab: TomogramTab) -> None:
        super().__init__(Qt.Orientation.Horizontal, tab)
        self.tab = tab
        self.loader: HistoryLoader | None = None
        self.query_key: QueryKey | None = None
        self.needs_query = False
        self.page = CapturePage((), None, 0)
        self.changed_sources: frozenset[str] = frozenset()
        self.marks = CaptureMarks()
        self.error = ""
        self.observed_state = tab.state
        self.view_revision = 0
        self.hovered: MarkCluster | None = None
        self.preview: QFrame | None = None
        self.chooser: QMenu | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(32)

    def sync_marks(self) -> None:
        if not self.tab.constructed or self.tab.retired:
            return
        row = self.tab.current_object()
        key = (self.tab.history.stamp(), row, id(self.tab.persistence.repository)) if row else None
        if key != self.query_key:
            self.cancel_marks()
            self.query_key = key
            self.needs_query = key is not None
        if self.needs_query and self.tab.history.pending_counts is None:
            self.needs_query = False
            self.query()
        self.project()

    def suspend_marks(self) -> None:
        """Let a foreground count/page query use the single metadata worker first."""
        if self.loader is not None and self.loader.loading:
            self.loader.cancel()
            self.needs_query = self.query_key is not None

    def refresh_marks(self) -> None:
        self.cancel_marks()
        self.sync_marks()

    def cancel_marks(self) -> None:
        if self.loader is not None:
            self.loader.cancel()
        self.query_key = None
        self.needs_query = False
        self.page = CapturePage((), None, 0)
        self.changed_sources = frozenset()
        self.marks = CaptureMarks()
        self.error = ""
        self.close_preview()
        self.close_chooser()
        self.update()

    def query(self, jump: MarkJump | None = None) -> None:
        key = self.query_key
        if key is None:
            return
        if self.loader is None:
            self.loader = HistoryLoader(self.tab.history.pool)
            self.loader.signals.finished.connect(self.loaded)
            self.loader.signals.failed.connect(self.failed)
        history = self.tab.history
        repository, context, extras = self.tab.persistence.repository, history.context(frozenset((key[1].id,))), history.extras()
        writer = self.tab.persistence.writer
        self.error = ""
        def read(cancelled):
            def snapshot():
                page = capture_page(repository, context, limit=200, extra=extras, cancelled=cancelled)
                changed = set()
                for volume in review_versions(key[1]):
                    if cancelled():
                        raise InterruptedError("Obsolete capture marks")
                    try:
                        unchanged = fingerprint(volume.path) == volume.fingerprint
                    except (OSError, ValueError):
                        unchanged = False
                    if not unchanged:
                        changed.add(volume.id)
                return MarkQuery(key, page, jump, frozenset(changed))
            return writer.read(snapshot)
        self.loader.request(read, stamp=key[0])
        self.project()

    @Slot(object, object)
    def loaded(self, job: HistoryJob, result: MarkQuery | None) -> None:
        if self.loader is None or not self.loader.retire(job) or self.tab.retired or result is None:
            return
        if result.key != self.query_key or job.stamp != self.tab.history.stamp():
            return
        self.page = result.page
        self.changed_sources = result.changed_sources
        self.project()
        jump = result.jump
        if jump is None:
            return
        if (jump.view_revision != self.view_revision or jump.state != self.tab.state
                or jump.displayed != self.tab.rendering.displayed_recipe()):
            return  # The user's newer view intent wins over this asynchronous click.
        if not any(value.entry.id == jump.identity and value.plane == jump.plane for value in self.marks.captures):
            self.error = "That capture is no longer eligible for a plane jump."
            self.project()
            return
        with QSignalBlocker(self):
            self.setValue(jump.plane)
        self.tab.plane_changed(jump.plane, immediate=True)

    @Slot(object, str)
    def failed(self, job: HistoryJob, message: str) -> None:
        if self.loader is None or not self.loader.retire(job) or self.tab.retired or job.stamp != self.tab.history.stamp():
            return
        self.page = CapturePage((), None, 0)
        self.error = f"Capture marks unavailable. Use F8 or the context menu to retry. {message}"
        self.project()

    def project(self) -> None:
        if self.observed_state != self.tab.state:
            self.observed_state = self.tab.state
            self.view_revision += 1
        row, volume = self.tab.current_object(), self.tab.volume()
        marks = capture_marks(self.page, row, volume, self.tab.state, self.tab.rendering.displayed_recipe(),
                              changed_sources=self.changed_sources) if row and volume else CaptureMarks()
        if marks != self.marks:
            self.close_preview()
            self.close_chooser()
        self.marks = marks
        notices = ((self.error,) if self.error else ()) + marks.notices
        detail = f"{len(marks.captures)} eligible captures. " + " ".join(notices)
        self.setToolTip(BASE_HINT + "\n" + detail)
        self.setAccessibleDescription(detail + " Press F8 for the capture-plane chooser.")
        self.update()

    def clusters(self) -> tuple[MarkCluster, ...]:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self)
        start, end = groove.left() + handle.width() / 2, groove.right() - handle.width() / 2 + 1
        return cluster_marks(self.marks, self.maximum(), start, max(start, end), reversed_=option.upsideDown)

    def mark_y(self) -> int:
        return 5

    def at(self, point: QPoint) -> MarkCluster | None:
        if point.y() > 12 or not self.isEnabled():
            return None
        candidates = [cluster for cluster in self.clusters() if abs(cluster.position - point.x()) <= 6]
        return min(candidates, key=lambda cluster: abs(cluster.position - point.x())) if candidates else None

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = current_palette()
        for cluster in self.clusters():
            radius = 4 if cluster == self.hovered else 3
            colour = QColor(palette.accent if self.isEnabled() else palette.text_muted)
            painter.setPen(QPen(colour, 1.5))
            painter.setBrush(colour if any(value.current_version for value in cluster.captures) else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(cluster.position - radius, self.mark_y() - radius, 2 * radius, 2 * radius))
        painter.end()

    def close_preview(self) -> None:
        if self.preview is not None:
            self.preview.close()
            self.preview.deleteLater()
            self.preview = None
        self.hovered = None

    def show_preview(self, cluster: MarkCluster) -> None:
        self.close_preview()
        self.hovered = cluster
        frame = self.preview = QFrame(self.tab, Qt.WindowType.ToolTip)
        frame.setObjectName("captureRow")
        layout = QVBoxLayout(frame)
        # One deterministic advisory thumbnail; every plane remains in the chooser.
        first = cluster.captures[0].entry
        layout.addWidget(AtlasThumbnail(first.record.path, QSize(96, 72), fit="contain",
                                       description="Saved capture preview", parent=frame))
        planes = ", ".join(str(value + 1) for value in cluster.planes[:6])
        if len(cluster.planes) > 6:
            planes += f" and {len(cluster.planes) - 6} more"
        versions = ", ".join(sorted({value.entry.record.request.volume.version for value in cluster.captures}))
        count = len(cluster.captures)
        noun = "capture" if count == 1 else "captures"
        text = f"{normal_axis(self.tab.state.axis)} {planes} · {versions}\n{count} {noun}"
        if len(cluster.planes) > 1:
            text += " · click to choose a plane"
        text += "\n" + " ".join(self.marks.notices)
        label = QLabel(text, frame)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMaximumWidth(320)
        layout.addWidget(label)
        frame.adjustSize()
        self.position_preview()
        frame.show()
        self.update()

    def position_preview(self) -> None:
        frame, cluster = self.preview, self.hovered
        if frame is None or cluster is None:
            return
        point = self.mapToGlobal(QPoint(round(cluster.position), -frame.height() - 4))
        screen = self.screen().availableGeometry()
        frame.move(max(screen.left(), min(point.x(), screen.right() - frame.width())),
                   max(screen.top(), min(point.y(), screen.bottom() - frame.height())))

    def activate_capture(self, identity: str, plane: int) -> None:
        displayed = self.tab.rendering.displayed_recipe()
        if displayed is not None and not self.tab.retired:
            self.close_preview()
            self.project()
            self.query(MarkJump(identity, plane, self.tab.state, displayed, self.view_revision))

    def choose(self, clusters: tuple[MarkCluster, ...], position: QPoint) -> None:
        self.close_preview()
        self.close_chooser()
        menu = QMenu(self)
        self.chooser = menu
        for cluster in clusters:
            for plane in cluster.planes:
                captures = tuple(value for value in cluster.captures if value.plane == plane)
                versions = ", ".join(sorted({value.entry.record.request.volume.version for value in captures}))
                noun = "capture" if len(captures) == 1 else "captures"
                action = menu.addAction(f"{normal_axis(self.tab.state.axis)} {plane + 1} · {versions} · {len(captures)} {noun}")
                action.triggered.connect(lambda checked=False, value=captures[0]: self.activate_capture(value.entry.id, value.plane))
        if self.marks.notices:
            menu.addSeparator()
            for notice in self.marks.notices:
                menu.addAction(notice).setEnabled(False)
        if not self.marks.captures and not self.error:
            menu.addAction("No eligible capture planes").setEnabled(False)
        if self.error:
            menu.addAction(self.error).setEnabled(False)
            menu.addAction("Retry capture marks", self.refresh_marks)
        def retired():
            if self.chooser is menu:
                self.chooser = None
            menu.deleteLater()
        menu.aboutToHide.connect(retired)
        menu.popup(position)

    def close_chooser(self) -> None:
        if self.chooser is not None:
            self.chooser.close()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.isSliderDown():
            self.close_preview()
            super().mouseMoveEvent(event)
            return
        cluster = self.at(event.position().toPoint())
        if cluster != self.hovered:
            if cluster is None:
                self.close_preview()
                self.update()
            else:
                self.show_preview(cluster)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.close_preview()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        cluster = self.at(event.position().toPoint())
        if cluster is not None and event.button() == Qt.MouseButton.LeftButton:
            if len(cluster.planes) == 1:
                self.activate_capture(cluster.captures[0].entry.id, cluster.planes[0])
            else:
                self.choose((cluster,), event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.choose(self.clusters(), event.globalPos())
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F8:
            self.choose(self.clusters(), self.mapToGlobal(QPoint(0, self.height())))
            event.accept()
            return
        super().keyPressEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip and self.hovered is not None:
            return True
        result = super().event(event)
        if event.type() in (QEvent.Type.Resize, QEvent.Type.StyleChange, QEvent.Type.DevicePixelRatioChange):
            if self.hovered is not None:
                identity = self.hovered.captures[0].entry.id
                cluster = next((value for value in self.clusters() if any(c.entry.id == identity for c in value.captures)), None)
                if cluster is not None and cluster.captures == self.hovered.captures:
                    self.hovered = cluster
                    self.position_preview()
                else:
                    self.close_preview()  # Resizing changed the cluster membership.
            self.update()
        return result


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('tomograms.plane_slider.plane-slider-f8', 'Plane slider', InteractionKind.KEYBOARD, ('F8',), 'Choose a capture at this plane'),
)
