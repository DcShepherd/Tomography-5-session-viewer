"""Header components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.project_tree import PROJECT_BADGE_MEANINGS, SESSION_KIND_BADGES
from tomography_session_browser.ui.theme import TIER_LANDMARK, TIER_PRIMARY, TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
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


def build_summary_header(
    dashboard: SessionDashboard,
    model: DashboardModel,
    source_folders: int = 0,
    *,
    extra: QWidget | None = None,
) -> QWidget:
    """The header of the collection and sample summaries: the name and its
    badge, then the time span, and the link to the source folders when
    ``source_folders`` is given (design review B3.2, B3.3).

    Kind, path and microscope are not here: the badge says what kind of project
    this is, and Context lists every folder with a path that copies and the
    acquisition settings.
    """

    wrap = QWidget()
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_S)

    # The scope, when the title is something narrower than it (a renamed group
    # over its sessions' names): the same rule as every other page's header.
    eyebrow_text = dashboard._context_eyebrow_text(model.title)
    eyebrow = QLabel(eyebrow_text)
    eyebrow.setObjectName("screenEyebrow")
    apply_tier(eyebrow, TIER_SUPPORTING)
    eyebrow.setToolTip(eyebrow_text)
    eyebrow.setVisible(bool(eyebrow_text))
    layout.addWidget(eyebrow)

    title_row = QHBoxLayout()
    title_row.setSpacing(SPACE_M)
    # Asks for its whole width, so the badge sits against the name; a name too
    # long for the page still elides.
    title = ElidedLabel(model.title, prefer_full_width=True)
    title.setObjectName("screenTitle")
    apply_tier(title, TIER_LANDMARK)
    title.setToolTip(model.title)
    title_row.addWidget(title, stretch=0)
    stack = getattr(dashboard, "_context_stack", None)
    # A project or group has its badge from the stack; a session inside a group
    # has the one its row has in the tree.
    badge_text = (stack.scope_badge if stack is not None else "") or SESSION_KIND_BADGES.get(model.kind, "")
    if badge_text:
        badge = QLabel(badge_text)
        badge.setObjectName("scopeBadge")
        badge.setToolTip(PROJECT_BADGE_MEANINGS.get(badge_text, f"Project type: {badge_text}"))
        title_row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignVCenter)
    title_row.addStretch(1)
    layout.addLayout(title_row)

    meta_wrap = QWidget()
    meta_wrap.setObjectName("dashboardMetaRow")
    meta_row = FlowLayout(spacing=SPACE_L, line_spacing=SPACE_XS)
    meta_wrap.setLayout(meta_row)
    if model.acquisition_start and model.acquisition_end:
        span = QLabel(f"{model.acquisition_start}  →  {model.acquisition_end}")
        apply_tier(span, TIER_PRIMARY)
        span.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        span.setAccessibleName("Acquisition time span")
        meta_row.addWidget(span)
        if model.acquisition_duration:
            duration = QLabel(model.acquisition_duration)
            apply_tier(duration, TIER_SUPPORTING)
            meta_row.addWidget(duration)
    if source_folders:
        noun = "source folder" if source_folders == 1 else "source folders"
        link = QToolButton()
        link.setObjectName("tableLink")
        link.setText(f"{source_folders:,} {noun}")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setToolTip("Show the source folders in Context")
        link.setAccessibleName("Show source folders in Context")
        link.clicked.connect(dashboard.source_folders_requested.emit)
        meta_row.addWidget(link)
    if extra is not None:
        meta_row.addWidget(extra)
    layout.addWidget(meta_wrap)
    return wrap
