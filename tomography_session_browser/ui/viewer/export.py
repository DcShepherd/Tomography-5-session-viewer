"""Export for the image viewer."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QPoint, QSize, QStandardPaths, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from tomography_session_browser.ui.motion import live_section_reveal
from tomography_session_browser.ui.viewer.constants import FLOATING_CONTROL_BOTTOM_INSET_PX, FLOATING_CONTROL_INSET_PX
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


class ImageExportDialog(QDialog):
    """Preview an offscreen viewer render and collect its PNG destination."""

    def __init__(
        self,
        image: QImage,
        default_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._selected_path: Path | None = None
        self.setWindowTitle("Export image")
        self.setModal(True)
        self.resize(820, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        preview = QLabel(self)
        preview.setObjectName("imageExportPreview")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setMinimumSize(480, 320)
        preview.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        preview_image = image.scaled(
            QSize(760, 480),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        preview.setPixmap(QPixmap.fromImage(preview_image))
        preview.setToolTip(
            "Preview of the exported image; the PNG is saved at full resolution."
        )
        layout.addWidget(preview, stretch=1)

        details = QLabel(
            f"PNG · {image.width():,} × {image.height():,} px",
            self,
        )
        details.setObjectName("viewerStatus")
        layout.addWidget(details)

        destination_row = QHBoxLayout()
        destination_row.setSpacing(8)
        destination_label = QLabel("Save to", self)
        self.path_edit = QLineEdit(str(default_path), self)
        self.path_edit.setAccessibleName("Export destination")
        self.path_edit.setToolTip("Destination for the full-resolution PNG")
        browse_button = QPushButton("Browse…", self)
        browse_button.clicked.connect(self._browse)
        destination_row.addWidget(destination_label)
        destination_row.addWidget(self.path_edit, stretch=1)
        destination_row.addWidget(browse_button)
        layout.addLayout(destination_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._accept_path)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def selected_path(self) -> Path | None:
        return self._selected_path

    def _browse(self) -> None:
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Export image",
            self.path_edit.text().strip(),
            "PNG images (*.png)",
        )
        if selected:
            self.path_edit.setText(selected)

    def _accept_path(self) -> None:
        raw_path = self.path_edit.text().strip()
        if not raw_path:
            QMessageBox.warning(
                self,
                "Export image",
                "Choose where the PNG should be saved.",
            )
            return
        path = Path(raw_path)
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
            self.path_edit.setText(str(path))
        if path.exists():
            answer = QMessageBox.question(
                self,
                "Replace image?",
                f"{path.name} already exists. Replace it?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._selected_path = path
        self.accept()


class ViewerExport:
    """Export operations for one ViewerTab."""

    def __init__(self, view: ViewerTab) -> None:
        self.view = view

    def export_current_view(self) -> None:
        rendered = self.view.viewer.render_current_view()
        if rendered is None:
            QMessageBox.information(
                self.view,
                "Export image",
                "Load an image before exporting.",
            )
            return
        image, render_scale = rendered
        self.view._composite_export_overlays(image, render_scale)
        dialog = ImageExportDialog(
            image,
            self.view._default_export_path(),
            self.view,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        destination = dialog.selected_path
        if destination is None:
            return
        if not image.save(str(destination), "PNG", 100):
            QMessageBox.critical(
                self.view,
                "Export failed",
                f"Could not save the PNG to:\n{destination}",
            )

    def default_export_path(self) -> Path:
        base_name = (
            self.view.parts.mrc_loading.displayed_path.stem
            if self.view.parts.mrc_loading.displayed_path is not None
            else "tomography_image"
        )
        if (
            self.view.parts.mrc_loading.displayed_path is not None
            and self.view.parts.mrc_loading.displayed_path.suffix.lower() == ".mrc"
            and self.view.parts.mrc_loading.displayed_slice_index is not None
        ):
            base_name += f"_frame_{self.view.parts.mrc_loading.displayed_slice_index + 1:04d}"
        zoom_percent = max(1, round(self.view.viewer.zoom_scale() * 100))
        filename = f"{base_name}_view_{zoom_percent}pct.png"
        export_directory = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation
        )
        if not export_directory:
            export_directory = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            )
        return Path(export_directory or Path.cwd()) / filename

    def composite_export_overlays(
        self,
        image: QImage,
        render_scale: float,
    ) -> None:
        """Add visible image-bound widgets to the offscreen scene render."""

        overlays: list[tuple[QWidget, int]] = []
        if not self.view.scale_bar.isHidden():
            overlays.append((self.view.scale_bar, 0))
        if self.view.parts.overlay_panel.atlas_lod and not self.view.atlas_marker_legend.isHidden():
            # A legend part-way open is painted by its reveal; the export
            # takes the legend as it now is.
            reveal = live_section_reveal(self.view.atlas_marker_legend)
            if reveal is not None:
                reveal.dismiss()
            overlays.append((self.view.atlas_marker_legend, 12))
        if not overlays:
            return

        left_inset = round(FLOATING_CONTROL_INSET_PX * render_scale)
        bottom = image.height() - round(
            FLOATING_CONTROL_BOTTOM_INSET_PX * render_scale
        )
        painter = QPainter(image)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform,
            True,
        )
        try:
            for widget, spacing_above in overlays:
                bottom -= round(spacing_above * render_scale)
                widget_image = self.view._render_widget_for_export(
                    widget,
                    render_scale,
                )
                bottom -= widget_image.height()
                painter.drawImage(
                    QPoint(left_inset, max(0, bottom)),
                    widget_image,
                )
        finally:
            painter.end()

    @staticmethod
    def render_widget_for_export(
        widget: QWidget,
        render_scale: float,
    ) -> QImage:
        target = QImage(
            QSize(
                max(1, round(widget.width() * render_scale)),
                max(1, round(widget.height() * render_scale)),
            ),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        target.fill(Qt.GlobalColor.transparent)
        painter = QPainter(target)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform,
            True,
        )
        try:
            painter.scale(render_scale, render_scale)
            widget.render(
                painter,
                QPoint(),
                QRegion(),
                QWidget.RenderFlag.DrawChildren,
            )
        finally:
            painter.end()
        return target

