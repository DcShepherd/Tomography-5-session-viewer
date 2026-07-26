"""Camera-dose scatter plot for the Session dashboard."""

from __future__ import annotations

from typing import Any

from tomography_session_browser.ui.session_presenter import (
    DoseInformationPlotModel,
    dose_information_point_tooltip,
)
from tomography_session_browser.ui.widgets.defocus_plot import DefocusScatterPlot


class DoseInformationScatterPlot(DefocusScatterPlot):
    """Theme-aware scatter plot for camera dose per tilt image."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Camera dose plot")
        self.setAccessibleDescription(
            "Scatter plot of per-image camera dose values from MRC metadata. "
            "Double-click a point to open the corresponding tilt series frame."
        )

    def set_model(self, model: DoseInformationPlotModel | None) -> None:
        self._model = model
        self.update()

    def _point_value(self, point: Any) -> float:
        return point.dose_e_per_angstrom2

    def _y_axis_label_text(self) -> str:
        return "Camera dose per image (e⁻/Å²)"

    def _format_y_tick(self, value: float) -> str:
        return f"{value:.1f}"

    def _y_range(self, points: list[Any]) -> tuple[float, float]:
        """Keep dose/fluence axes physically meaningful after chart padding."""

        lower, upper = super()._y_range(points)
        return max(0.0, lower), max(0.1, upper)

    def _empty_title(self) -> str:
        return "No camera-dose metadata available"

    def _empty_detail(self) -> str:
        return "MRC dose-per-image values were not found for this selection."

    def _point_tooltip(self, point: Any) -> str:
        return dose_information_point_tooltip(point)
