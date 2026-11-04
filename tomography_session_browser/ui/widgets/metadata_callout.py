"""One shared warning callout for plain metadata and typed Context."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.ui.widgets.metadata_text import display_value

class MetadataCallout(QFrame):
    """One warning, in the dashboard's tone for its severity (C1).

    ``Title: detail`` warnings show the title in bold over the detail; the
    whole warning stays selectable and is the accessible name.
    """

    def __init__(self, text: str, tone: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metaCallout")
        self.setProperty("tone", tone)
        self.setAccessibleName(f"Warning: {text}")
        self.warning = text
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
        layout.setSpacing(SPACE_S)
        dot = QLabel("\u25cf", self)
        dot.setObjectName("statusDot")
        dot.setProperty("compact", True)
        dot.setProperty("tone", tone)
        layout.addWidget(dot, alignment=Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        title, separator, detail = text.partition(": ")
        if not separator or not detail.strip():
            title, detail = text, ""
        self._title = QLabel(display_value(title.strip()), self)
        self._title.setObjectName("metaCalloutTitle")
        self._detail = QLabel(display_value(detail.strip()), self)
        self._detail.setObjectName("metaCalloutDetail")
        for label in (self._title, self._detail):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            column.addWidget(label)
        self._detail.setVisible(bool(detail.strip()))
        layout.addLayout(column, stretch=1)


