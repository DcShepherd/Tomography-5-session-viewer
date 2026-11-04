"""A shrinkable text button; callers keep the full value accessible."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton, QStylePainter


class ElidedButton(QPushButton):
    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(72, super().minimumSizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: N802
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, max(1, self.width() - 24))
        QStylePainter(self).drawControl(QStyle.ControlElement.CE_PushButton, option)
