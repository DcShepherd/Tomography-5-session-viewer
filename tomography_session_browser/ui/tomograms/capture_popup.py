"""Bounded row history and explicit menu traversal; transactions stay with Captures."""
from __future__ import annotations
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from dataclasses import dataclass
from typing import TYPE_CHECKING
from PySide6.QtCore import QEvent, Qt, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QAbstractButton, QMenu, QWidgetAction

from tomography_session_browser.presentation.tomogram_capture_history import CapturePage, capture_page
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.ui.tomograms.capture_rows import CaptureRow, RecoveryRow
from tomography_session_browser.ui.tomograms.history_loading import HistoryJob, HistoryLoader
from tomography_session_browser.ui.tomograms.thumbnail_admission import CaptureThumbnailAdmission

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.captures import CaptureJob

MENU_KEYS = frozenset((Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Up, Qt.Key.Key_Down,
    Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter,
    Qt.Key.Key_Space, Qt.Key.Key_Delete, Qt.Key.Key_Escape))

@dataclass(frozen=True, slots=True)
class FocusAnchor:
    key: str | CaptureJob
    control: int | None


class CapturePopup(QMenu):
    def __init__(self, captures, row_key: str) -> None:
        super().__init__(captures.tab)
        self.captures, self.tab, self.row_key = captures, captures.tab, row_key
        self.entries = {}
        self.rows = []
        self.row_actions: dict[QAction, CaptureRow | RecoveryRow] = {}
        self.action_keys: dict[QAction, str | CaptureJob] = {}
        self.thumbnails = CaptureThumbnailAdmission(self, self.visible_thumbnails)
        self.loader = HistoryLoader(self.tab.history.pool)
        self.loader.signals.finished.connect(self._loaded)
        self.loader.signals.failed.connect(self._failed)
        self.setToolTipsVisible(True)
        self.setMaximumWidth(540)
        self.aboutToHide.connect(self.cancel)
        self.refresh_page()

    def cancel(self) -> None:
        self.loader.cancel()
        self.thumbnails.stop()

    def visible_thumbnails(self):
        return tuple(row.thumbnail for action, row in self.row_actions.items() if isinstance(row, CaptureRow)
                     and self.rect().intersects(self.actionGeometry(action)) and not row.visibleRegion().isEmpty())

    def widget(self, widget: CaptureRow | RecoveryRow) -> None:
        action = QWidgetAction(self)
        action.setDefaultWidget(widget)
        self.addAction(action)
        self.row_actions[action] = widget
        self.action_keys[action] = widget.entry.id if isinstance(widget, CaptureRow) else widget.failure.job
        for child in (widget, *widget.controls):
            child.installEventFilter(self)

    def footer(self, key: str, text: str, callback) -> QAction:
        action = self.addAction(text, callback)
        self.action_keys[action] = key
        return action

    def current_entry(self) -> QAction | None:
        focused = QApplication.focusWidget()
        for action, row in self.row_actions.items():
            if focused is row or (focused is not None and row.isAncestorOf(focused)):
                return action
        return self.activeAction()

    def anchor(self) -> FocusAnchor | None:
        action = self.current_entry()
        if action not in self.action_keys:
            return None
        row = self.row_actions.get(action)
        controls = row.controls if row is not None else ()
        focused = QApplication.focusWidget()
        return FocusAnchor(self.action_keys[action], controls.index(focused) if focused in controls else None)

    def focus_entry(self, action: QAction, control: QAbstractButton | None = None) -> None:
        self.setActiveAction(action)
        target = control or self.row_actions.get(action) or self
        target.setFocus(Qt.FocusReason.PopupFocusReason)

    def restore_anchor(self, anchor: FocusAnchor | None) -> None:
        if anchor is None or not self.isVisible():
            return
        for action, key in self.action_keys.items():
            if key != anchor.key or not action.isEnabled():
                continue
            row = self.row_actions.get(action)
            controls = row.controls if row is not None else ()
            control = controls[anchor.control] if anchor.control is not None and anchor.control < len(controls) else None
            self.focus_entry(action, control if control and control.isVisible() and control.isEnabled() else None)
            return
        # The row was removed. Keep the menu usable without firing an action.
        self.focus_entry(next(action for action in self.action_keys
                              if action not in self.row_actions and action.isEnabled()))

    def focusNextPrevChild(self, forward: bool) -> bool:  # noqa: N802
        targets: list[QAction | QAbstractButton] = []
        for action in self.actions():
            if not action.isEnabled() or not action.isVisible() or action.isSeparator():
                continue
            targets.append(action)
            row = self.row_actions.get(action)
            if row is not None:
                targets.extend(button for button in row.controls if button.isVisible() and button.isEnabled())
        if not targets:
            return False
        focused = QApplication.focusWidget()
        current = focused if focused in targets else self.current_entry()
        index = targets.index(current) if current in targets else (-1 if forward else 0)
        target = targets[(index + (1 if forward else -1)) % len(targets)]
        if isinstance(target, QAction):
            self.focus_entry(target)
        else:
            action = next(action for action, row in self.row_actions.items() if target in row.controls)
            self.focus_entry(action, target)
        return True

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in MENU_KEYS:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in MENU_KEYS:
            self.keyPressEvent(event)
            return True
        return super().eventFilter(watched, event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in MENU_KEYS:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        action = self.current_entry()
        row = self.row_actions.get(action)
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.focusNextPrevChild(key == Qt.Key.Key_Tab and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            entries = [value for value in self.actions() if value.isEnabled() and value.isVisible() and not value.isSeparator()]
            index = entries.index(action) if action in entries else (-1 if key == Qt.Key.Key_Down else 0)
            if entries:
                self.focus_entry(entries[(index + (1 if key == Qt.Key.Key_Down else -1)) % len(entries)])
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and row is not None:
            controls = [button for button in row.controls if button.isVisible() and button.isEnabled()]
            self.focus_entry(action, controls[0] if key == Qt.Key.Key_Right and controls else None)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            focused = QApplication.focusWidget()
            if row is not None and focused in row.controls:
                focused.click()
            elif isinstance(row, CaptureRow):
                if row.restore_button.isEnabled():
                    row.restore_button.click()
            elif isinstance(row, RecoveryRow):
                controls = [button for button in row.controls if button.isVisible() and button.isEnabled()]
                if controls:
                    self.focus_entry(action, controls[0])
            elif action is not None and action.isEnabled():
                self.close()
                action.trigger()
        elif key == Qt.Key.Key_Delete and isinstance(row, CaptureRow):
            if row.remove_button.isEnabled() and row.remove_button.isVisible():
                row.remove_button.click()
        elif key == Qt.Key.Key_Escape:
            self.close()
        else:
            return super().keyPressEvent(event)
        event.accept()

    def build(self, page=None, error="") -> None:
        anchor = self.anchor()
        self.thumbnails.set_clients(())
        self.rows.clear()
        self.row_actions.clear()
        self.action_keys.clear()
        self.clear()
        row = self.tab.review_list.rows.get(self.row_key)
        name = row.tomogram.name if row else "Tomogram outside this scope"
        counts = self.tab.history.count_snapshot
        total = page.total if page is not None else counts.rows.get(self.row_key) if counts else None
        self.addAction(f"{name} · {count_phrase(total, 'capture') if total is not None else '… captures'}").setEnabled(False)
        for failure in self.captures.failures:
            if failure.job.request.row_key != self.row_key:
                continue
            recovery = RecoveryRow(failure, self)
            recovery.set_retry_reason(self.captures.queue_reason(failure.job))
            recovery.retry_requested.connect(self.retry)
            recovery.folder_requested.connect(self.select_folder)
            recovery.dismissed.connect(self.dismiss)
            self.widget(recovery)
        self.entries = {entry.id: entry for entry in page.entries} if page is not None else {}
        for entry in self.entries.values():
            panel = CaptureRow(entry, self, show_version=len(review_versions(row.tomogram)) > 1)
            panel.set_restore_reason(self.captures.restore_reason(entry))
            panel.activated.connect(self.restore)
            panel.shown.connect(self.show_folder)
            panel.removed.connect(self.remove)
            target = self.tab.tags.target()
            panel.tag_button.setVisible(self.tab.tags.editable() and target is not None and target.row.id == entry.record.request.row_key)
            panel.tag_requested.connect(lambda _, e=entry: self.tab.tags.capture_links.link_existing(e))
            self.rows.append(panel)
            self.widget(panel)
        if error:
            self.footer("refresh", "History unavailable · Refresh", self.refresh_page).setToolTip(error)
        elif page is None:
            self.addAction("Loading capture history…").setEnabled(False)
        elif not page.entries:
            self.addAction("No captures of this tomogram").setEnabled(False)
        elif page.total > len(page.entries):
            self.addAction(f"Newest {len(page.entries)} of {page.total} shown").setEnabled(False)
        self.addSeparator()
        self.footer("all", "All captures…", self.show_all)
        contact = self.footer("contact", "Contact sheet (PDF)…", self.contact_sheet)
        contact.setEnabled(bool(self.tab.review_list.ordered_keys) and self.tab.contact_sheet.pending is None)
        if self.tab.contact_sheet.pending is not None:
            action = self.footer("cancel", "Cancel contact sheet export", self.tab.contact_sheet.cancel)
            action.setEnabled(not self.tab.contact_sheet.cancelled.is_set())
        self.restore_anchor(anchor)
        self.thumbnails.set_clients(tuple(row.thumbnail for row in self.rows))

    def refresh_page(self) -> None:
        repository = self.tab.persistence.repository
        writer = self.tab.persistence.writer
        context = self.tab.history.context(frozenset({self.row_key}))
        extra = self.tab.history.extras()
        if not self.actions():
            self.build()
        self.loader.request(lambda cancelled: writer.read(lambda: capture_page(
            repository, context, limit=12, extra=extra, cancelled=cancelled)), stamp=self.tab.history.stamp())

    @Slot(object, object)
    def _loaded(self, job: HistoryJob, page: CapturePage | None) -> None:
        if not self.loader.retire(job) or page is None or self.tab.retired:
            return
        if job.stamp != self.tab.history.stamp():
            self.refresh_page()
            return
        self.build(page)

    @Slot(object, str)
    def _failed(self, job: HistoryJob, message: str) -> None:
        if self.loader.retire(job) and not self.tab.retired:
            self.build(error=message)

    def restore(self, identity: str) -> None:
        entry = self.entries.get(identity)
        self.close()
        if entry is not None:
            self.captures.restore(entry)

    def show_folder(self, identity: str) -> None:
        entry = self.entries.get(identity)
        self.close()
        if entry is not None:
            self.captures.show_in_folder(entry)

    def remove(self, identity: str) -> None:
        entry = self.entries.get(identity)
        if entry is not None:
            self.captures.forget(entry)

    def show_all(self) -> None:
        self.close()
        self.tab.history.show_browser()

    def contact_sheet(self) -> None:
        self.close()
        self.tab.contact_sheet.choose()

    def retry(self, failure) -> None:
        self.close()
        self.captures.retry(failure)

    def select_folder(self, failure) -> None:
        self.close()
        self.captures.retry(failure, choose_folder=True)

    def dismiss(self, failure) -> None:
        self.close()
        self.captures.dismiss(failure)


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('tomograms.capture_popup.captures-menu-up', 'Captures menu', InteractionKind.KEYBOARD, ('Up', 'Down'), 'Select a row'),
    LocalBinding('tomograms.capture_popup.captures-menu-tab', 'Captures menu', InteractionKind.KEYBOARD, ('Tab', 'Shift+Tab'), 'Move through row actions'),
    LocalBinding('tomograms.capture_popup.captures-menu-right', 'Captures menu', InteractionKind.KEYBOARD, ('Right',), 'Enter row actions'),
    LocalBinding('tomograms.capture_popup.captures-menu-left', 'Captures menu', InteractionKind.KEYBOARD, ('Left',), 'Return to the row'),
    LocalBinding('tomograms.capture_popup.captures-menu-enter', 'Captures menu', InteractionKind.KEYBOARD, ('Enter', 'Return', 'Space'), 'Activate the focused action'),
    LocalBinding('tomograms.capture_popup.captures-menu-delete', 'Captures menu', InteractionKind.KEYBOARD, ('Delete',), 'Remove capture history'),
    LocalBinding('tomograms.capture_popup.captures-menu-esc', 'Captures menu', InteractionKind.KEYBOARD, ('Esc',), 'Close the menu'),
)
