"""Application-wide native style policies, installed before the theme."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QProxyStyle, QStyle


class AppStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802
        if hint == QStyle.StyleHint.SH_Button_FocusPolicy:
            return Qt.FocusPolicy.TabFocus.value
        if hint in (QStyle.StyleHint.SH_SpinBox_SelectOnStep,
                    QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons):
            return 0
        return super().styleHint(hint, option, widget, returnData)
