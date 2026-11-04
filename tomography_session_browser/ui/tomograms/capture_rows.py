"""Capture presentation shared by a bounded popup and paged item views."""
from __future__ import annotations
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from tomography_session_browser.ui.widgets.link_button import link_button

from datetime import datetime
from PySide6.QtCore import QSize, Signal, Qt
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout

from tomography_session_browser.presentation.tomogram_capture_history import CaptureEntry
from tomography_session_browser.presentation.tomogram_geometry import normal_axis
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.widgets.atlas_thumbnail import AtlasThumbnail
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel


class CaptureRow(QFrame):
    activated = Signal(str)
    shown = Signal(str)
    removed = Signal(str)
    tag_requested = Signal(str)

    def __init__(self, entry: CaptureEntry, parent=None, *, show_version: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("captureRow")
        self.entry = entry
        request, recipe = entry.record.request, entry.record.request.recipe
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(8, 4, 8, 4)
        self.thumbnail = AtlasThumbnail(entry.record.path, QSize(40, 40), fit="contain",
                                        description="Saved capture", placeholder="—", auto_request=False, parent=self)
        self.row.addWidget(self.thumbnail)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.restore_button = link_button(f"{normal_axis(recipe.axis)} {recipe.plane + 1}", self)
        self.restore_button.setAccessibleName("Restore view")
        self.restore_button.clicked.connect(lambda: self.activated.emit(entry.id))
        text.addWidget(self.restore_button)
        parts = [request.volume.version] if show_version else []
        if any(recipe.rotation):
            parts.append("rotated")
        if recipe.lowpass:
            parts.append(f"lowpass {recipe.lowpass:g}")
        if recipe.highpass:
            parts.append(f"highpass {recipe.highpass:g}")
        if recipe.slices > 1:
            parts.append(f"{recipe.slices} slices")
        self.detail = ElidedLabel(" · ".join(parts), parent=self)
        self.detail.setVisible(bool(parts))
        text.addWidget(self.detail)
        self.row.addLayout(text, 1)
        self.show_button = QToolButton(self)
        self.show_button.setIcon(themed_icon("folder-open"))
        self.show_button.setAccessibleName("Show in folder")
        self.show_button.setToolTip("Open the folder that holds this capture")
        self.show_button.clicked.connect(lambda: self.shown.emit(entry.id))
        self.row.addWidget(self.show_button)
        self.tag_button = QToolButton(self)
        self.tag_button.setText("#")
        self.tag_button.setAccessibleName("Link this capture to a tag")
        self.tag_button.clicked.connect(lambda: self.tag_requested.emit(entry.id))
        self.tag_button.hide()
        self.row.addWidget(self.tag_button)
        self.remove_button = QToolButton(self)
        self.remove_button.setText("×")
        self.remove_button.setAccessibleName("Remove from this list")
        self.remove_button.setToolTip("Remove this history entry; keep the exported image")
        self.remove_button.clicked.connect(lambda: self.removed.emit(entry.id))
        self.row.addWidget(self.remove_button)
        try:
            saved = datetime.fromtimestamp(entry.created_ns / 1e9).strftime("%d %b %Y %H:%M") if entry.created_ns else "Save time not recorded"
        except (OverflowError, ValueError, OSError):
            saved = "Save time unavailable"
        detail = (f"{entry.record.path}\n{saved}\n{request.volume.path}\n"
                  f"{recipe.axis} plane {recipe.plane + 1}; crop {recipe.region}\n"
                  f"Rotation {recipe.rotation}; {recipe.slices} slices; levels {recipe.black} to {recipe.white}\n"
                  f"Lowpass {recipe.lowpass}; highpass {recipe.highpass}; inverted {recipe.invert}")
        self.setToolTip(detail)
        self.setAccessibleDescription(detail)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_restore_reason(self, reason: str) -> None:
        self.restore_button.setEnabled(not reason)
        self.restore_button.setToolTip(reason or "Restore this source and its saved view")

    @property
    def controls(self) -> tuple[QAbstractButton, ...]:
        return (self.restore_button, self.show_button, *((self.tag_button,) if not self.tag_button.isHidden() else ()), self.remove_button)

    def refresh_theme(self) -> None:
        self.show_button.setIcon(themed_icon("folder-open"))
        self.thumbnail.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = self.focusWidget()
            if isinstance(focused, QAbstractButton):
                focused.click()
            elif self.restore_button.isEnabled():
                self.activated.emit(self.entry.id)
            event.accept()
        elif event.key() == Qt.Key.Key_Delete and self.remove_button.isVisible():
            self.removed.emit(self.entry.id)
            event.accept()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self.thumbnail.cancel()
        super().hideEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.restore_button.isEnabled():
            self.activated.emit(self.entry.id)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class RecoveryRow(QFrame):
    """Frozen failure data; transaction decisions stay with Captures."""
    retry_requested = Signal(object)
    folder_requested = Signal(object)
    dismissed = Signal(object)

    def __init__(self, failure, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("captureRecovery")
        self.failure = failure
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        job = failure.job
        column = QVBoxLayout(self)
        title = QLabel(("History not saved" if job.saved_record else "Image not saved") + f" · {job.request.name}", self)
        column.addWidget(title)
        self.detail = ElidedLabel(failure.message, parent=self)
        self.detail.setToolTip(f"{job.request.volume.path}\n{job.request.directory}\n{failure.message}\nRecovery ends when this window closes.")
        column.addWidget(self.detail)
        actions = QHBoxLayout()
        self.retry_button = link_button("Retry history" if job.saved_record else "Retry snapshot", self)
        self.folder_button = link_button("Select folder…", self)
        self.dismiss_button = link_button("Dismiss", self)
        self.folder_button.setVisible(job.saved_record is None)
        for button, signal in ((self.retry_button, self.retry_requested),
                               (self.folder_button, self.folder_requested), (self.dismiss_button, self.dismissed)):
            button.clicked.connect(lambda _checked=False, signal=signal: signal.emit(failure))
            actions.addWidget(button)
        column.addLayout(actions)

    @property
    def controls(self) -> tuple[QAbstractButton, ...]:
        return self.retry_button, self.folder_button, self.dismiss_button

    def set_retry_reason(self, reason: str) -> None:
        for button in (self.retry_button, self.folder_button):
            button.setEnabled(not reason)
            button.setToolTip(self.detail.toolTip() + ("\n" + reason if reason else ""))


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('tomograms.capture_rows.capture-row-enter', 'Capture row', InteractionKind.KEYBOARD, ('Enter', 'Return'), 'Restore the capture or activate the focused action'),
    LocalBinding('tomograms.capture_rows.capture-row-delete', 'Capture row', InteractionKind.KEYBOARD, ('Delete',), 'Remove capture history'),
)
