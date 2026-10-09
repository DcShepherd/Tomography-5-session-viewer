"""Header components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.ui.theme import TIER_LANDMARK, TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.dashboard.constants import META_ROW_HEIGHT
from tomography_session_browser.ui.dashboard.layout import FlowLayout
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def context_eyebrow_text(dashboard: SessionDashboard, title: str = "") -> str:
    """The review scope, unless the title below already says it.

    The context header sits above every page including this one, and the
    dashboard title is usually the scope's own name — so on a session-scope
    dashboard an eyebrow repeating it made the same words appear three
    times within two lines. It earns its place when the title is something
    narrower than the scope (a sample inside a linked group), and stays out
    of the way when it is not.
    """

    stack = getattr(dashboard, "_context_stack", None)
    if stack is None or not stack.scope_label:
        return ""
    # Compare the bare scope name, not the badged label: the badge is
    # already on the context header and in the project tree, so it is not
    # on its own a reason to repeat the name.
    if title and stack.scope.casefold() == title.casefold():
        return ""
    return stack.scope_label.upper()


def build_header(dashboard: SessionDashboard, model: DashboardModel) -> QWidget:
    wrap = QWidget()
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_S)

    title_row = QHBoxLayout()
    title_row.setSpacing(SPACE_M)
    left = QVBoxLayout()
    left.setContentsMargins(0, 0, 0, 0)
    left.setSpacing(SPACE_XS)
    # The eyebrow used to read a hard-coded "PROJECT · SESSION" on every
    # screen, which told the reviewer nothing about where they were. It
    # now shows the real review scope supplied by the context stack.
    eyebrow_text = dashboard._context_eyebrow_text(model.title)
    eyebrow = QLabel(eyebrow_text)
    eyebrow.setObjectName("screenEyebrow")
    apply_tier(eyebrow, TIER_SUPPORTING)
    eyebrow.setToolTip(eyebrow_text)
    eyebrow.setVisible(bool(eyebrow_text))
    left.addWidget(eyebrow)
    title = ElidedLabel(model.title)
    title.setObjectName("screenTitle")
    apply_tier(title, TIER_LANDMARK)
    title.setToolTip(model.title)
    left.addWidget(title)
    title_row.addLayout(left, stretch=1)
    layout.addLayout(title_row)

    # One meta row (plan C2): the stacked caps labels cost ~220 px of
    # height and put the copy button a screen-width away from the path.
    # Groups wrap as whole units, and the path elides in the middle once
    # it has a line to itself.
    meta_wrap = QWidget()
    meta_wrap.setObjectName("dashboardMetaRow")
    meta_row = FlowLayout(spacing=SPACE_L, line_spacing=SPACE_XS)
    meta_wrap.setLayout(meta_row)
    if model.kind:
        kind_icon = (
            "link"
            if "linked" in model.kind.casefold()
            else "session-dashboard"
        )
        meta_row.addWidget(dashboard._meta_item("Kind", model.kind, icon_name=kind_icon))
    if model.microscope:
        meta_row.addWidget(dashboard._meta_item("Microscope", model.microscope, icon_name="microscope"))
    if model.acquisition_start and model.acquisition_end:
        window_text = f"{model.acquisition_start}  →  {model.acquisition_end}"
        if model.acquisition_duration:
            window_text += f"  ({model.acquisition_duration})"
        meta_row.addWidget(dashboard._meta_item("Acquisition", window_text, icon_name="clock"))

    copy = QToolButton()
    copy.setObjectName("metaCopyButton")
    copy.setIcon(themed_icon("copy", size=14))
    copy.setIconSize(QSize(14, 14))
    copy.setToolTip("Copy session path")
    copy.setAccessibleName("Copy session path")
    copy.setFixedSize(META_ROW_HEIGHT, META_ROW_HEIGHT)
    copy.setCursor(Qt.CursorShape.PointingHandCursor)
    copy.clicked.connect(lambda: dashboard._copy(model.path, source=copy))
    meta_row.addWidget(
        dashboard._meta_item(
            "Path",
            model.path,
            icon_name="folder-open",
            icon_accessible_name="Session folder",
            value_object_name="metaValuePath",
            elide_mode=Qt.TextElideMode.ElideMiddle,
            trailing=copy,
        )
    )
    layout.addWidget(meta_wrap)
    return wrap


def meta_item(
    dashboard: SessionDashboard,
    label: str,
    value: str,
    *,
    icon_name: str,
    icon_accessible_name: str | None = None,
    value_object_name: str = "metaValue",
    elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    trailing: QWidget | None = None,
) -> QWidget:
    """One inline ``[icon] Key: value`` group of the header's meta row."""

    item = QWidget()
    item.setObjectName("dashboardMetaItem")
    item.setMinimumHeight(META_ROW_HEIGHT)
    row = QHBoxLayout(item)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_XS)
    row.addWidget(
        dashboard._metadata_icon_label(icon_name, icon_accessible_name or label),
        alignment=Qt.AlignmentFlag.AlignVCenter,
    )
    key = QLabel(f"{label}:")
    key.setObjectName("metaKey")
    row.addWidget(key)
    val = ElidedLabel(value, mode=elide_mode, prefer_full_width=True)
    val.setObjectName(value_object_name)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    val.setAccessibleName(label)
    row.addWidget(val, stretch=1)
    if trailing is not None:
        row.addWidget(trailing)
    return item


def metadata_icon_label(icon_name: str, accessible_name: str) -> QLabel:
    icon = QLabel()
    icon.setObjectName("metaIcon")
    icon.setProperty("iconName", icon_name)
    icon.setPixmap(themed_icon(icon_name, size=14).pixmap(QSize(14, 14)))
    icon.setFixedSize(14, 16)
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon.setAccessibleName(f"{accessible_name} icon")
    return icon
