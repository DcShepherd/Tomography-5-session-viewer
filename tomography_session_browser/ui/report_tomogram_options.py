"""The report dialog's tomogram options: linked tomograms and central slices (D4 §5.1).

The two checkboxes sit in the dialog's INCLUDE line (plan B6). They hold
remembered preferences. Only a click on an enabled checkbox changes a
preference: a disabled state, Select all, Clear and group ticks never do. What
the report prints follows from the normalised scope
(``ReportScope.prints_tomograms`` and ``prints_slices``), not from these boxes.
A disabled box gives its reason in its tooltip; the preview pane says what
prints.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_L
from tomography_session_browser.presentation.report_scope import ReportScope

TOMOGRAMS_HELP = "Adds a Tomograms section to each dataset."
SLICES_HELP = "Adds one image for each linked tomogram."
FOLDER_SLICES_HELP = "Adds one image for each tomogram."
NO_SELECTION = "Not available: tick a dataset first."
NO_TOMOGRAM_OPTION = "Not available: include linked tomograms first."
NO_DATASET_TOMOGRAMS = "Not available: the ticked folders have no tomograms for the selected datasets."
NO_LINKED_TOMOGRAMS = "Not available: the report has no linked tomograms."
NO_FOLDER_TOMOGRAMS = "Not available: the ticked folders have no tomograms."
NO_READABLE_HEADER = "Not available: the app cannot read a volume header in this report."
FREEZE_FAILED = ("Not available: the app cannot read the tomogram and tag state. "
                 "You can still make acquisition reports.")


class TomogramOptions(QWidget):
    """Two preference checkboxes on one line."""

    changed = Signal()

    def __init__(self, *, include_tomograms: bool, include_slices: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.prefer_tomograms, self.prefer_slices = include_tomograms, include_slices
        self._syncing = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_L)
        self.tomograms = QCheckBox("Linked tomograms", self)
        self.slices = QCheckBox("Central slices", self)
        layout.addWidget(self.tomograms)
        layout.addWidget(self.slices)

        self.tomograms.toggled.connect(lambda checked: self._chosen("prefer_tomograms", checked))
        self.slices.toggled.connect(lambda checked: self._chosen("prefer_slices", checked))

    def preferences(self) -> tuple[bool, bool]:
        return self.prefer_tomograms, self.prefer_slices

    def _chosen(self, name: str, checked: bool) -> None:
        if self._syncing:
            return
        setattr(self, name, checked)
        self.changed.emit()

    @staticmethod
    def _show(box: QCheckBox, preference: bool, reason: str | None, text: str) -> None:
        """A box is ticked only while it is enabled; the reason replaces the help text."""
        box.setEnabled(reason is None)
        box.setChecked(preference and reason is None)
        box.setToolTip(reason or text)
        box.setAccessibleDescription(reason or text)

    def show_scope(self, scope: ReportScope | None, issue: str | None) -> None:
        """Enable each option from the scope it previews; ``issue`` is a freeze failure."""
        folder = scope is not None and scope.folder_report
        self._syncing = True
        try:
            # A folder report always prints its tomograms, so it has no box for them.
            self.tomograms.setVisible(not folder)
            tomograms_reason = issue
            if tomograms_reason is None and (scope is None or scope.tomograms is None):
                tomograms_reason = NO_SELECTION
            elif tomograms_reason is None and not folder and scope is not None and not scope.tomograms_available:
                tomograms_reason = NO_DATASET_TOMOGRAMS
            self._show(self.tomograms, self.prefer_tomograms, tomograms_reason, TOMOGRAMS_HELP)
            included = scope is not None and (folder or (self.tomograms.isEnabled() and self.prefer_tomograms))
            slices_reason = issue or (self._slices_reason(scope) if included else None)
            if slices_reason is None and not included:
                slices_reason = tomograms_reason or NO_TOMOGRAM_OPTION
            self._show(self.slices, self.prefer_slices, slices_reason, FOLDER_SLICES_HELP if folder else SLICES_HELP)
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
