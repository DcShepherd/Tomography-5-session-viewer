"""Export for the image viewer."""
from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
from PySide6.QtCore import QPoint, QSize, QStandardPaths, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, MessageAction, MessageContent
from tomography_session_browser.ui.widgets import message_sheet
from tomography_session_browser.services.output_boundary import check_output_path
from tomography_session_browser.ui.motion import live_section_reveal
from tomography_session_browser.ui.viewer.constants import FLOATING_CONTROL_BOTTOM_INSET_PX, FLOATING_CONTROL_INSET_PX
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.mrc_loading import PreviewLoader
    from tomography_session_browser.ui.viewer.overlay_panel import OverlayPanel
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


class ImageExportDialog(QDialog):
    """Preview an offscreen viewer render and collect its PNG destination."""

    def __init__(
        self,
        image: QImage,
        default_path: Path,
        parent: QWidget | None = None,
        *, protected_roots: tuple[Path, ...] = (),
    ) -> None:
        super().__init__(parent)
        self._selected_path: Path | None = None
        self._protected_roots = protected_roots
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

        self.path_error = QLabel(self)
        self.path_error.setObjectName("outputPathError")
        self.path_error.setWordWrap(True)
        self.path_error.setTextFormat(Qt.TextFormat.PlainText)
        self.path_error.hide()
        self.path_edit.textChanged.connect(self._clear_path_error)
        layout.addWidget(self.path_error)

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

    def _clear_path_error(self) -> None:
        self.path_error.hide()
        self.path_edit.setAccessibleDescription("")

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
            message_sheet.choose_action(self, ActionPrompt(MessageContent("Choose where to save the PNG", "The image has not been exported."),
                (MessageAction("close", "Choose destination"),), "close", "close"))
            return
        path = Path(raw_path)
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
            self.path_edit.setText(str(path))
        try:
            check_output_path(path, self._protected_roots)
        except (OSError, ValueError, RuntimeError) as exc:
            self.path_error.setText("Choose a folder outside the loaded sources. The image has not been exported.")
            self.path_error.setToolTip(str(exc))
            self.path_error.show()
            self.path_edit.setAccessibleDescription(self.path_error.text())
            self.path_edit.setFocus()
            return
        if path.exists():
            answer = message_sheet.choose_action(self, ActionPrompt(
                MessageContent("Replace this image?", "The existing PNG will be replaced by this exported view.", (str(path),)),
                (MessageAction("cancel", "Cancel"), MessageAction("replace", "Replace image", ActionRole.DESTRUCTIVE)), "cancel", "cancel"))
            if answer != "replace":
                return
        self._selected_path = path
        self.accept()


class ViewerExport:
    """Saves what one ViewerTab's canvas shows as a PNG, with the scale bar and
    legend drawn in; named after the file ``loader`` displays.

    ``overlays`` says whether this tab has an Atlas legend to draw.
    """

    def __init__(self, view: ViewerTab, *, loader: PreviewLoader, overlays: OverlayPanel,
                 protected_roots: Callable[[], tuple[Path, ...]] = lambda: ()) -> None:
        self.view = view
        self.loader = loader
        self.overlays = overlays
        self.protected_roots = protected_roots

    def export_current_view(self) -> None:
        rendered = self.view.viewer.render_current_view()
        if rendered is None:
            message_sheet.choose_action(self.view, ActionPrompt(MessageContent("No image to export", "Load an image before exporting."),
                (MessageAction("close", "Close"),), "close", "close"))
            return
        image, render_scale = rendered
        roots = self.protected_roots()
        self.composite_export_overlays(image, render_scale)
        dialog = ImageExportDialog(
            image,
            self.default_export_path(),
            self.view,
            protected_roots=roots,
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            destination = dialog.selected_path
            if destination is None:
                return
        finally:
            dialog.deleteLater()
        try:
            check_output_path(destination, roots)
            if not image.save(str(destination), "PNG", 100):
                raise OSError("The PNG writer could not save this image.")
        except (OSError, ValueError, RuntimeError) as exc:
            message_sheet.choose_action(self.view, ActionPrompt(
                MessageContent("The image was not saved", "Your view is still available. Try another destination.", (str(destination),), str(exc)),
                (MessageAction("close", "Close"),), "close", "close"))

    def default_export_path(self) -> Path:
        base_name = (
            self.loader.displayed_path.stem
            if self.loader.displayed_path is not None
            else "tomography_image"
        )
        if (
            self.loader.displayed_path is not None
            and self.loader.displayed_path.suffix.lower() == ".mrc"
            and self.loader.displayed_slice_index is not None
        ):
            base_name += f"_frame_{self.loader.displayed_slice_index + 1:04d}"
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
        if self.overlays.atlas_lod and not self.view.atlas_marker_legend.isHidden():
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
                widget_image = self.render_widget_for_export(
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

