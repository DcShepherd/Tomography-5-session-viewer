"""The report dialog's tomogram options: linked tomograms and central slices (D4 §5.1).

The two checkboxes hold remembered preferences. Only a click on an enabled
checkbox changes a preference: a disabled state, Select all, Clear and group
ticks never do. What the report prints follows from the normalised scope
(``ReportScope.prints_tomograms`` and ``prints_slices``), not from these boxes.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_XS
from tomography_session_browser.presentation.report_scope import ReportScope

#: Said once in each mode that prints tomograms: what of the user's own review enters the PDF (GR-02).
ANNOTATIONS_HELP = "It includes review tags, counts and notes as they were when this dialog opened, also unsaved edits."
TOMOGRAMS_HELP = f"Adds a Tomograms section to each dataset. {ANNOTATIONS_HELP}"
SLICES_HELP = "Adds one image for each linked tomogram."
FOLDER_SLICES_HELP = "Adds one image for each tomogram."
FOLDER_TITLE = "Folder report"
FOLDER_HELP = ("The report shows all tomograms in the ticked folders. For a tomogram without a link, it gives the cause. "
               f"{ANNOTATIONS_HELP}")
NO_DATASET_TOMOGRAMS = "Not available: the ticked folders have no tomograms for the selected datasets."
NO_LINKED_TOMOGRAMS = "Not available: the report has no linked tomograms."
NO_FOLDER_TOMOGRAMS = "Not available: the ticked folders have no tomograms."
NO_READABLE_HEADER = "Not available: the app cannot read a volume header in this report."
FREEZE_FAILED = ("Not available: the app cannot read the tomogram and tag state. "
                 "You can still make acquisition reports.")


def _help(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("mutedLabel")
    label.setWordWrap(True)
    return label


class TomogramOptions(QWidget):
    """Two preference checkboxes and the folder-report explanation."""

    changed = Signal()

    def __init__(self, *, include_tomograms: bool, include_slices: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.prefer_tomograms, self.prefer_slices = include_tomograms, include_slices
        self._syncing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)

        self.folder_title = QLabel(FOLDER_TITLE, self)
        self.folder_title.setObjectName("cardTitle")
        self.folder_help = _help(FOLDER_HELP, self)
        self.tomograms = QCheckBox("Include linked tomograms", self)
        self.tomograms_help = _help(TOMOGRAMS_HELP, self)
        self.slices = QCheckBox("Include central slices", self)
        self.slices_help = _help(SLICES_HELP, self)
        indented = QWidget(self)
        indent = QVBoxLayout(indented)
        indent.setContentsMargins(SPACE_M * 2, 0, 0, 0)
        indent.setSpacing(SPACE_XS)
        indent.addWidget(self.slices)
        indent.addWidget(self.slices_help)
        for widget in (self.folder_title, self.folder_help, self.tomograms, self.tomograms_help, indented):
            layout.addWidget(widget)

        self.tomograms.toggled.connect(lambda checked: self._chosen("prefer_tomograms", checked))
        self.slices.toggled.connect(lambda checked: self._chosen("prefer_slices", checked))

    def preferences(self) -> tuple[bool, bool]:
        return self.prefer_tomograms, self.prefer_slices

    def _chosen(self, name: str, checked: bool) -> None:
        if self._syncing:
            return
        setattr(self, name, checked)
        self.changed.emit()

    def _show(self, box: QCheckBox, help_label: QLabel, preference: bool, reason: str | None, text: str) -> None:
        """A box is ticked only while it is enabled; the reason replaces the help text."""
        box.setEnabled(reason is None)
        box.setChecked(preference and reason is None)
        help_label.setText(reason or text)
        box.setAccessibleDescription(reason or text)

    def show_scope(self, scope: ReportScope | None, issue: str | None) -> None:
        """Enable each option from the scope it previews; ``issue`` is a freeze failure."""
        folder = scope is not None and scope.folder_report
        self._syncing = True
        try:
            for widget in (self.folder_title, self.folder_help):
                widget.setVisible(folder)
            for widget in (self.tomograms, self.tomograms_help):
                widget.setVisible(not folder)
            tomograms_reason = issue
            if tomograms_reason is None and not folder and scope is not None and not scope.tomograms_available:
                tomograms_reason = NO_DATASET_TOMOGRAMS
            unselected = scope is None or scope.tomograms is None
            self._show(self.tomograms, self.tomograms_help, self.prefer_tomograms,
                       tomograms_reason, TOMOGRAMS_HELP)
            if unselected and tomograms_reason is None:
                self.tomograms.setEnabled(False)
                self.tomograms.setChecked(False)
            slices_reason = issue or self._slices_reason(scope)
            self._show(self.slices, self.slices_help, self.prefer_slices, slices_reason,
                       FOLDER_SLICES_HELP if folder else SLICES_HELP)
            included = scope is not None and (folder or (self.tomograms.isEnabled() and self.prefer_tomograms))
            if not included:
                self.slices.setEnabled(False)
                self.slices.setChecked(False)
        finally:
            self._syncing = False

    @staticmethod
    def _slices_reason(scope: ReportScope | None) -> str | None:
        if scope is None or scope.tomograms is None or scope.slices_available:
            return None
        if not scope.folder_report and not (scope.tomograms_available and scope.include_tomograms):
            return None                       # the tomogram option is off or gives the reason
        if not scope.tomograms.members:
            return NO_FOLDER_TOMOGRAMS if scope.folder_report else NO_LINKED_TOMOGRAMS
        return NO_READABLE_HEADER
