from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QElapsedTimer, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.display_names import format_overview_display_name
from tomography_session_browser.domain.markers import ImageMarker, MarkerType, SelectionState
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.reports import ProjectReportGroup, build_session_report
from tomography_session_browser.services.marker_service import MarkerContext, markers_for_object
from tomography_session_browser.services.item_status import ItemListStatus, build_item_status_context, item_list_status
from tomography_session_browser.services.loading_profiler import LoadingProfiler
from tomography_session_browser.services.navigation_service import (
    resolve_tilt_series_navigation_targets,
    tab_label_for_object,
)
from tomography_session_browser.services.tilt_angle_service import stack_order_tilt_angles
from tomography_session_browser.services.session_loader import SessionLoader
from tomography_session_browser.services.settings_service import (
    Settings,
    add_recent_session,
    save_settings,
)
from tomography_session_browser.ui.image_viewer import (
    PreviewSources,
    ViewerNavigationAction,
    ViewerTab,
    atlas_preview_path,
    batch_position_preview_path,
    clear_preview_cache,
    overview_preview_path,
    search_map_preview_path,
    search_tile_preview_path,
    tilt_series_preview_path,
)
from tomography_session_browser.services.timeline_service import (
    SessionTimeline,
    build_acquisition_timeline,
    build_session_timeline,
    detect_inferred_pauses,
)
from tomography_session_browser.ui.animations import fade_in
from tomography_session_browser.ui.branding import TitleBarLockup, brand_window_icon
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.project_model import (
    ProjectTreeGroup,
    build_project_tree_groups,
    group_contains_value,
    session_key,
)
from tomography_session_browser.ui.report_scope import ReportScopeDialog, normalise_report_scope
from tomography_session_browser.ui.session_linking import linked_sample_groups, sample_sort_key_for_group
from tomography_session_browser.ui.theme import apply_theme, current_palette, palette_for
from tomography_session_browser.ui.widgets.metadata_panel import MetadataPanel
from tomography_session_browser.ui.widgets.loading_overlay import LoadingOverlay
from tomography_session_browser.ui.widgets.session_dashboard import SessionDashboard
from tomography_session_browser.ui.session_presenter import (
    DashboardEntityScope,
    EntityGroup,
    LinkedAtlasDashboardScope,
    LinkedSampleGroup,
    SessionSummarySection,
    build_linked_sample_group,
    dedupe_entities,
    describe_object,
    grouped_warnings,
    session_counts,
    session_dashboard_model,
    session_summary_lines,
    session_summary_sections,
    session_warnings,
)


TAB_LABELS = ["Session", "Atlas", "Overview", "Search map", "Search", "Batch position", "Tilt series"]
OBJECT_ROLE = int(Qt.ItemDataRole.UserRole)
EXPANSION_ROLE = int(Qt.ItemDataRole.UserRole) + 1
HIGHLIGHT_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_ACTIVE_CONTEXT = object()
SESSION_FOLDER_HELP_TEXT = (
    "Select the folder for your atlas and/or data collection session"
)
LINKED_IMPORT_HELP_TEXT = "On import, connected atlas and data collection sessions will be linked."
DIRECT_DM_FILE_MESSAGE = "Please select the containing folder, not an individual session file."


def _format_tilt_angle(angle: float) -> str:
    if abs(angle) < 0.05:
        return "0.0"
    return f"{angle:+.1f}"


def _compact_pair(left: Any, right: Any) -> str | None:
    if left is None and right is None:
        return None
    return f"{left if left is not None else 'unknown'} / {right if right is not None else 'unknown'}"


def _dedupe_by_id(values: list[Any]) -> list[Any]:
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = str(getattr(value, "id", id(value)))
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _path_identity_key(path: str | Path) -> str:
    value = Path(path)
    if not value.is_absolute():
        value = Path.cwd() / value
    return str(value).replace("\\", "/").casefold()


def _timeline_from_tilt_series(tilt_series: list[TiltSeries]) -> SessionTimeline:
    """Build a SessionTimeline from a flat tilt-series list.

    ``build_session_timeline`` only accepts a Session, but Sample- and
    LinkedSampleGroup-scoped dashboards need the same timeline data without
    materialising a fake Session. We replicate the per-series collation here.
    """

    segments = []
    pauses = []
    for tilt in dedupe_entities(tilt_series):
        segment = build_acquisition_timeline(tilt)
        if segment is None:
            continue
        segments.append(segment)
        pauses.extend(detect_inferred_pauses(segment))
    segments.sort(key=lambda s: s.start)
    pauses.sort(key=lambda p: p.start)
    return SessionTimeline(segments=tuple(segments), pauses=tuple(pauses))


def _raw_marker_name(marker: ImageMarker) -> str | None:
    raw = marker.metadata.get("raw") if marker.metadata else None
    if isinstance(raw, dict):
        value = raw.get("Name")
        return str(value) if value is not None else None
    return None


def _normalise_exposure_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\\", "/").split("/")[-1].split(":")[-1]
    for suffix in (".mrc", ".mdoc", ".xml", ".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        if text.lower().endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip().casefold()


def _search_tile_sort_key(value: SearchTile) -> tuple[Any, ...]:
    fallback_path = value.image_path or value.xml_path
    return (
        natural_key(value.name or ""),
        value.tile_index if value.tile_index is not None else 10**9,
        natural_key(fallback_path.name if fallback_path is not None else ""),
        value.id,
    )


def _looks_like_direct_file_path(path: Path) -> bool:
    return path.is_file() or (path.suffix.casefold() == ".dm" and not path.is_dir())


def _normalise_session_folder_for_import(parent: QWidget | None, raw_path: str | Path) -> str | None:
    path = Path(raw_path)
    if not _looks_like_direct_file_path(path):
        return str(path)
    parent_folder = path.parent
    if parent_folder.exists() and parent_folder.is_dir():
        answer = QMessageBox.question(
            parent,
            "Select session folder",
            f"You selected {path.name}. Would you like to import its parent folder instead?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            return str(parent_folder)
    QMessageBox.information(parent, "Select session folder", DIRECT_DM_FILE_MESSAGE)
    return None


def _select_session_folder(
    parent: QWidget | None,
    title: str,
    start_path: str,
    accept_label: str,
) -> str:
    dialog = QFileDialog(parent, title, start_path)
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dialog.setLabelText(QFileDialog.DialogLabel.Accept, accept_label)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return ""
    selected = dialog.selectedFiles()
    return selected[0] if selected else ""


class _SessionLoadSignals(QObject):
    status = Signal(str)
    loaded = Signal(str, bool, bool, bool, object, object)
    failed = Signal(str, bool, bool, bool, str)


class _SessionLoadTask(QRunnable):
    def __init__(
        self,
        path: str,
        replace: bool,
        quiet: bool,
        animate: bool,
        signals: _SessionLoadSignals,
        profile: LoadingProfiler,
    ) -> None:
        super().__init__()
        self._path = path
        self._replace = replace
        self._quiet = quiet
        self._animate = animate
        self._signals = signals
        self._profile = profile

    @Slot()
    def run(self) -> None:
        profile = self._profile
        # Attach the inspect_mrc cache hooks for the duration of this load so
        # the perf report reflects hits/misses for this specific session.
        from tomography_session_browser.parsers import mrc_parser as _mrc_parser
        from tomography_session_browser.services.loading_profiler import active_profile

        previous_hit = _mrc_parser.inspect_cache_hit_hook
        previous_miss = _mrc_parser.inspect_cache_miss_hook
        _mrc_parser.inspect_cache_hit_hook = lambda: profile.increment("parser_cache_hits")
        _mrc_parser.inspect_cache_miss_hook = lambda: profile.increment("parser_cache_misses")
        try:
            self._signals.status.emit("Parsing metadata and MRC headers...")
            with profile.phase("load_session_worker"), active_profile(profile):
                session = SessionLoader().load(self._path, profile=profile)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread
            LOGGER.exception("Could not load tomography session path=%s", self._path)
            profile.emit_perf_report(LOGGER, path=self._path)
            self._signals.failed.emit(self._path, self._replace, self._quiet, self._animate, str(exc))
            return
        finally:
            _mrc_parser.inspect_cache_hit_hook = previous_hit
            _mrc_parser.inspect_cache_miss_hook = previous_miss
        self._signals.loaded.emit(self._path, self._replace, self._quiet, self._animate, session, profile)


@dataclass(slots=True)
class _PreparedViewerTab:
    label: str
    items: list[Any]
    statuses: dict[str, ItemListStatus]
    sources: dict[str, PreviewSources]


@dataclass(slots=True)
class _PreparedSessionUi:
    dashboard_model: Any | None
    timeline: SessionTimeline | None
    warnings: list[str]
    viewer_tabs: dict[str, _PreparedViewerTab]


@dataclass(slots=True)
class _SelectedExposureArea:
    batch_position_id: str
    exposure_index: int | None
    exposure_area_id: str | None
    exposure_type: str
    linked_tilt_series_id: str | None = None


@dataclass(slots=True)
class _ExposureTiltResolution:
    exposure: _SelectedExposureArea | None
    tilt_series: TiltSeries | None
    tooltip: str
    ambiguous: bool = False


@dataclass(slots=True)
class _ExposureSearchTileResolution:
    marker: ImageMarker | None
    search_tile: SearchTile | None
    tooltip: str
    ambiguous: bool = False


class _SessionUiPrepareSignals(QObject):
    prepared = Signal(object)
    failed = Signal(str)


class _SessionUiPrepareTask(QRunnable):
    def __init__(
        self,
        sessions: list[Session],
        active_context: Any,
        signals: _SessionUiPrepareSignals,
        profile: LoadingProfiler | None = None,
    ) -> None:
        super().__init__()
        self._sessions = list(sessions)
        self._active_context = active_context
        self._signals = signals
        self._profile = profile

    @Slot()
    def run(self) -> None:
        from tomography_session_browser.services.loading_profiler import active_profile

        try:
            with self._profile.phase("prepare_ui_payload_worker") if self._profile is not None else _null_phase(), active_profile(self._profile):
                prepared = _prepare_session_ui_payload(self._sessions, self._active_context, profile=self._profile)
            self._signals.prepared.emit(prepared)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread
            LOGGER.exception("Could not prepare tomography session UI payload")
            self._signals.failed.emit(str(exc))


def _prepare_session_ui_payload(
    sessions: list[Session],
    active_context: Any,
    *,
    profile: LoadingProfiler | None = None,
) -> _PreparedSessionUi:
    """Build expensive non-widget models for the initial UI in a worker."""

    with profile.phase("prepare_dashboard_model") if profile is not None else _null_phase():
        scope_value = _dashboard_scope_value_for_sessions(sessions, active_context)
        dashboard_model = session_dashboard_model(scope_value) if sessions else None
        timeline = _timeline_for_scope_value(scope_value)
    warnings: list[str] = []
    with profile.phase("prepare_warning_groups") if profile is not None else _null_phase():
        for session in sessions:
            warnings.extend(session_warnings(session))

    with profile.phase("prepare_viewer_tab_items") if profile is not None else _null_phase():
        tab_items = _viewer_tab_items_for_sessions(sessions, active_context)
    with profile.phase("prepare_item_status_context") if profile is not None else _null_phase():
        status_context = build_item_status_context(
            search_maps=tab_items["Search map"],
            search_tiles=tab_items["Search"],
            batch_positions=tab_items["Batch position"],
            tilt_series=tab_items["Tilt series"],
        )
    prepared_tabs: dict[str, _PreparedViewerTab] = {}
    for label, items in tab_items.items():
        statuses: dict[str, ItemListStatus] = {}
        if label in {"Search map", "Search", "Batch position", "Tilt series"}:
            with profile.phase(f"prepare_{label.lower().replace(' ', '_')}_statuses", count=len(items)) if profile is not None else _null_phase():
                statuses = {
                    getattr(value, "id", str(index)): item_list_status(value, status_context)
                    for index, value in enumerate(items)
                }
        with profile.phase(f"prepare_{label.lower().replace(' ', '_')}_preview_sources", count=len(items)) if profile is not None else _null_phase():
            sources = _prepared_preview_sources(label, items)
        prepared_tabs[label] = _PreparedViewerTab(label=label, items=items, statuses=statuses, sources=sources)
    return _PreparedSessionUi(
        dashboard_model=dashboard_model,
        timeline=timeline,
        warnings=warnings,
        viewer_tabs=prepared_tabs,
    )


class _ProjectTreeDelegate(QStyledItemDelegate):
    """Use the project tree's stylesheet selection without native focus trails."""

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        paint_option = QStyleOptionViewItem(option)
        paint_option.state &= ~QStyle.StateFlag.State_HasFocus
        highlighted = bool(index.sibling(index.row(), 0).data(HIGHLIGHT_ROLE))
        if highlighted and not (paint_option.state & QStyle.StateFlag.State_Selected):
            palette = current_palette()
            fill = QColor(palette.accent)
            fill.setAlpha(34)
            painter.fillRect(paint_option.rect, fill)
        super().paint(painter, paint_option, index)
        if highlighted and index.column() == 0:
            palette = current_palette()
            bar = paint_option.rect.adjusted(0, 3, 0, -3)
            bar.setWidth(3)
            painter.fillRect(bar, QColor(palette.accent_strong))


def _dashboard_scope_value_for_sessions(sessions: list[Session], active_context: Any) -> Any:
    if isinstance(active_context, ProjectTreeGroup):
        if len(active_context.sessions) == 1:
            return active_context.sessions[0]
        return list(active_context.sessions)
    if isinstance(active_context, Session):
        linked_atlases = _linked_atlases_for_session_static(sessions, active_context)
        if linked_atlases:
            return LinkedAtlasDashboardScope(
                source=active_context,
                atlases=[(_atlas_display_label_static(sessions, atlas), atlas) for atlas in linked_atlases],
            )
    if isinstance(active_context, Sample):
        linked_atlases = _linked_atlases_for_sample_static(sessions, active_context)
        if linked_atlases:
            return LinkedAtlasDashboardScope(
                source=active_context,
                atlases=[(_atlas_display_label_static(sessions, atlas), atlas) for atlas in linked_atlases],
            )
    if isinstance(active_context, Session | Sample | LinkedSampleGroup):
        return active_context
    if len(sessions) == 1:
        return sessions[0]
    return list(sessions)


def _timeline_for_scope_value(scope_value: Any) -> SessionTimeline | None:
    try:
        if isinstance(scope_value, LinkedAtlasDashboardScope):
            return _timeline_for_scope_value(scope_value.source)
        if isinstance(scope_value, Session):
            return build_session_timeline(scope_value)
        if isinstance(scope_value, list):
            segments: list = []
            pauses: list = []
            for session in scope_value:
                tl = build_session_timeline(session)
                segments.extend(tl.segments)
                pauses.extend(tl.pauses)
            return SessionTimeline(segments=tuple(segments), pauses=tuple(pauses))
        if isinstance(scope_value, Sample):
            return _timeline_from_tilt_series(list(scope_value.tilt_series))
        if isinstance(scope_value, LinkedSampleGroup):
            return _timeline_from_tilt_series(list(scope_value.tilt_series))
        if isinstance(scope_value, DashboardEntityScope):
            return _timeline_from_tilt_series(list(scope_value.tilt_series))
    except Exception:  # pragma: no cover - defensive; UI falls back gracefully
        LOGGER.warning("Timeline model build failed", exc_info=True)
    return None


def _viewer_tab_items_for_sessions(sessions: list[Session], active_context: Any) -> dict[str, list[Any]]:
    return {
        "Atlas": _context_atlases_for_sessions(sessions, active_context),
        "Overview": _context_overviews_for_sessions(sessions, active_context),
        "Search map": _context_search_maps_for_sessions(sessions, active_context),
        "Search": sorted(_context_search_tiles_for_sessions(sessions, active_context), key=_search_tile_sort_key),
        "Batch position": _context_batch_positions_for_sessions(sessions, active_context),
        "Tilt series": _context_tilt_series_for_sessions(sessions, active_context),
    }


def _prepared_preview_sources(label: str, items: list[Any]) -> dict[str, PreviewSources]:
    source_for = {
        "Atlas": atlas_preview_path,
        "Overview": overview_preview_path,
        "Search map": search_map_preview_path,
        "Search": search_tile_preview_path,
        "Batch position": batch_position_preview_path,
        "Tilt series": tilt_series_preview_path,
    }.get(label)
    if source_for is None:
        return {}
    return {
        str(getattr(value, "id", str(index))): source_for(value)
        for index, value in enumerate(items)
    }


class _null_phase:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


def _context_atlases_for_sessions(sessions: list[Session], context: Any) -> list[Atlas]:
    if isinstance(context, ProjectTreeGroup):
        return _context_atlases_for_sessions(context.sessions, None)
    if isinstance(context, EntityGroup):
        return [value for value in context.values if isinstance(value, Atlas)]
    if isinstance(context, LinkedSampleGroup):
        return [context.atlas] if context.atlas is not None else []
    if isinstance(context, Sample):
        return _linked_atlases_for_sample_static(sessions, context) or ([context.atlas] if context.atlas is not None else [])
    if isinstance(context, Session):
        return _linked_atlases_for_session_static(sessions, context) or _display_atlases_for_session(context)
    atlases: list[Atlas] = []
    for session in sessions:
        atlases.extend(_display_atlases_for_session(session))
    return _dedupe_atlases_static(atlases)


def _context_overviews_for_sessions(sessions: list[Session], context: Any) -> list[Overview]:
    if isinstance(context, ProjectTreeGroup):
        return _context_overviews_for_sessions(context.sessions, None)
    if isinstance(context, EntityGroup):
        return [value for value in context.values if isinstance(value, Overview)]
    if isinstance(context, LinkedSampleGroup):
        return context.overviews
    if isinstance(context, Sample):
        return context.overviews
    if isinstance(context, Session):
        return dedupe_entities(context.overviews + [value for sample in context.samples for value in sample.overviews])
    return dedupe_entities([value for session in sessions for value in session.overviews] + [
        value for sample in _all_samples_for_sessions(sessions) for value in sample.overviews
    ])


def _context_search_maps_for_sessions(sessions: list[Session], context: Any) -> list[SearchMap]:
    if isinstance(context, ProjectTreeGroup):
        return _context_search_maps_for_sessions(context.sessions, None)
    if isinstance(context, EntityGroup):
        return [value for value in context.values if isinstance(value, SearchMap)]
    if isinstance(context, LinkedSampleGroup):
        return context.search_maps
    if isinstance(context, Sample):
        return context.search_maps
    if isinstance(context, Session):
        return dedupe_entities(context.search_maps + [value for sample in context.samples for value in sample.search_maps])
    return dedupe_entities([value for session in sessions for value in session.search_maps] + [
        value for sample in _all_samples_for_sessions(sessions) for value in sample.search_maps
    ])


def _context_search_tiles_for_sessions(sessions: list[Session], context: Any) -> list[SearchTile]:
    if isinstance(context, ProjectTreeGroup):
        return _context_search_tiles_for_sessions(context.sessions, None)
    if isinstance(context, EntityGroup):
        return [value for value in context.values if isinstance(value, SearchTile)]
    if isinstance(context, LinkedSampleGroup):
        return context.search_tiles
    if isinstance(context, Sample):
        return context.search_tiles
    if isinstance(context, Session):
        return dedupe_entities(context.search_tiles + [value for sample in context.samples for value in sample.search_tiles])
    return dedupe_entities([value for session in sessions for value in session.search_tiles] + [
        value for sample in _all_samples_for_sessions(sessions) for value in sample.search_tiles
    ])


def _context_batch_positions_for_sessions(sessions: list[Session], context: Any) -> list[BatchPosition]:
    if isinstance(context, ProjectTreeGroup):
        return _context_batch_positions_for_sessions(context.sessions, None)
    if isinstance(context, EntityGroup):
        return [value for value in context.values if isinstance(value, BatchPosition)]
    if isinstance(context, LinkedSampleGroup):
        return context.batch_positions
    if isinstance(context, Sample):
        return context.batch_positions
    if isinstance(context, Session):
        return dedupe_entities(context.batch_positions + [value for sample in context.samples for value in sample.batch_positions])
    return dedupe_entities([value for session in sessions for value in session.batch_positions] + [
        value for sample in _all_samples_for_sessions(sessions) for value in sample.batch_positions
    ])


def _context_tilt_series_for_sessions(sessions: list[Session], context: Any) -> list[TiltSeries]:
    if isinstance(context, ProjectTreeGroup):
        return _context_tilt_series_for_sessions(context.sessions, None)
    if isinstance(context, EntityGroup):
        return [value for value in context.values if isinstance(value, TiltSeries)]
    if isinstance(context, LinkedSampleGroup):
        return context.tilt_series
    if isinstance(context, Sample):
        return context.tilt_series
    if isinstance(context, Session):
        return dedupe_entities(context.tilt_series + [value for sample in context.samples for value in sample.tilt_series])
    return dedupe_entities([value for session in sessions for value in session.tilt_series] + [
        value for sample in _all_samples_for_sessions(sessions) for value in sample.tilt_series
    ])


def _all_samples_for_sessions(sessions: list[Session]) -> list[Sample]:
    return [sample for session in sessions for sample in session.samples]


def _display_atlases_for_session(session: Session) -> list[Atlas]:
    sample_atlases = [
        sample.atlas
        for sample in session.samples
        if sample.atlas is not None and not _sample_is_single_atlas_placeholder_static(sample)
    ]
    if sample_atlases:
        return _dedupe_atlases_static(sample_atlases)
    if session.atlas is not None:
        return [session.atlas]
    return []


def _dedupe_atlases_static(atlases: list[Atlas]) -> list[Atlas]:
    seen: set[str] = set()
    unique: list[Atlas] = []
    for atlas in atlases:
        key = _atlas_key_static(atlas)
        if key in seen:
            continue
        seen.add(key)
        unique.append(atlas)
    return unique


def _linked_atlases_for_session_static(sessions: list[Session], session: Session) -> list[Atlas]:
    atlases: list[Atlas] = []
    for sample in session.samples:
        atlases.extend(_linked_atlases_for_sample_static(sessions, sample))
    return _dedupe_atlases_static(atlases)


def _linked_atlases_for_sample_static(sessions: list[Session], sample: Sample) -> list[Atlas]:
    try:
        groups = linked_sample_groups(sessions)
    except Exception:  # pragma: no cover - linker is intentionally defensive elsewhere too
        LOGGER.warning("linked_sample_groups failed while resolving linked sample atlas", exc_info=True)
        return []
    for samples in groups:
        if not any(candidate is sample for candidate in samples):
            continue
        atlases = [
            candidate.atlas
            for candidate in samples
            if candidate.atlas is not None and not _sample_is_single_atlas_placeholder_static(candidate)
        ]
        return _dedupe_atlases_static(atlases)
    return []


def _atlas_display_label_static(sessions: list[Session], atlas: Atlas) -> str:
    for session in sessions:
        for sample in session.samples:
            if sample.atlas is atlas:
                return _display_sample_name_static(sample.name)
        if session.atlas is atlas:
            return f"{session.name} Atlas"
    if atlas.image_path is not None and atlas.image_path.parent.name:
        return f"Atlas - {atlas.image_path.parent.name}"
    return "Atlas"


def _display_sample_name_static(name: str) -> str:
    text = re.sub(r"^\s*\d+\.\s*", "", name or "").strip()
    return text or name or "Sample"


def _atlas_key_static(atlas: Atlas) -> str:
    path = atlas.image_path
    if path is not None:
        return _path_identity_key(path)
    return atlas.id


def _sample_is_single_atlas_placeholder_static(sample: Sample) -> bool:
    return "single atlas" in sample.name.lower() and not _sample_has_collection_data_static(sample)


def _sample_has_collection_data_static(sample: Sample) -> bool:
    return bool(sample.overviews or sample.search_maps or sample.search_tiles or sample.batch_positions or sample.tilt_series)


class MainWindow(QMainWindow):
    def __init__(self, *, settings: Settings | None = None) -> None:
        super().__init__()
        self._loader = SessionLoader()
        self._session: Session | None = None
        self._sessions: list[Session] = []
        self._current_path: str | None = None
        self._viewer_tabs: dict[str, ViewerTab] = {}
        self._active_context: Session | Sample | LinkedSampleGroup | EntityGroup | ProjectTreeGroup | None = None
        self._dashboard_focus_value: Any | None = None
        self._dashboard_highlighted_tilt_series_id: str | None = None
        self._tilt_viewer_selected_tilt_series_id: str | None = None
        self._pending_dashboard_point_click: tuple[str, object] | None = None
        self._pending_project_tree_clear_tilt_id: str | None = None
        self._dashboard_scope_prefers_tree = False
        self._project_tree_tilt_items_by_id: dict[str, list[QTreeWidgetItem]] = {}
        self._project_display_names: dict[str, str] = {}
        self._dashboard_model_cache: dict[str, tuple[Any, SessionTimeline | None]] = {}
        self._deferred_loading_dashboard: tuple[Any, SessionTimeline | None, bool] | None = None
        self._deferred_loading_dashboard_refresh = False
        self._suppress_viewer_context = False
        self._preserve_tree_root_context = False
        self._selection_state = SelectionState()
        self._settings = settings if settings is not None else Settings()
        self._theme_actions: list[QAction] = []  # actions whose icon needs refreshing on theme change
        self._failed_tilt_ids_cache: frozenset[str] | None = None
        # Perf-report bookkeeping: surface dashboard rebuild count and the
        # latest widget-build time through the LoadingProfiler at session
        # load completion. Reset on each load via :meth:`_begin_loading`.
        self._dashboard_rebuild_count: int = 0
        self._last_dashboard_widget_build_ms: int = 0
        # Main-thread stall sampler. Live for the duration of a load and
        # stopped at completion (success or queue advance). None when no
        # load is in flight.
        self._stall_monitor: Any | None = None
        # Token for the ContextVar binding of the active profile on the
        # main thread (so context-panel updates etc. can record aggregate
        # phases via record_aggregate_phase). Reset on load completion.
        self._main_thread_profile_token: Any | None = None
        self._load_queue: list[tuple[str, bool, bool, bool]] = []
        self._loading_task_active = False
        self._load_thread_pool = QThreadPool.globalInstance()
        self._session_load_signals = _SessionLoadSignals(self)
        self._session_load_signals.status.connect(self._set_loading_status)
        self._session_load_signals.loaded.connect(self._on_session_loaded)
        self._session_load_signals.failed.connect(self._on_session_load_failed)
        self._session_ui_prepare_signals = _SessionUiPrepareSignals(self)
        self._session_ui_prepare_signals.prepared.connect(self._on_session_ui_prepared)
        self._session_ui_prepare_signals.failed.connect(self._on_session_ui_prepare_failed)
        self._pending_ui_integration: tuple[Session, str, bool, bool, bool, LoadingProfiler | None] | None = None
        self._loading_status_timer = QTimer(self)
        self._loading_status_timer.setSingleShot(True)
        self._loading_status_timer.timeout.connect(self._flush_pending_loading_status)
        self._dashboard_point_click_timer = QTimer(self)
        self._dashboard_point_click_timer.setSingleShot(True)
        self._dashboard_point_click_timer.timeout.connect(self._apply_pending_dashboard_point_click)
        self._project_tree_tilt_click_timer = QTimer(self)
        self._project_tree_tilt_click_timer.setSingleShot(True)
        self._project_tree_tilt_click_timer.timeout.connect(self._apply_pending_project_tree_highlight_clear)
        self._pending_loading_status: str | None = None
        self._last_loading_status_at = 0.0
        self._loading_status_interval_s = 0.06
        # entity.id → list of Samples in the same linked group (for overlay
        # scoping). A "linked group" can span multiple sessions — e.g. a
        # screening session and a collection session can each contribute one
        # Sample to the same biological grid. Scoping by linked group is what
        # the user expects: opening a sample's Atlas should show that sample's
        # Search Maps even when
        # they live in a different session.
        self._sample_index: dict[str, list[Sample]] = {}
        self.setWindowTitle("Tomography Session Browser")
        self.setWindowIcon(brand_window_icon())
        # Default size targets the comfortable laptop / desktop layout the
        # user pinned in screenshots: a wide central image area with a
        # compact project tree and metadata panel on either side.
        # The minimum size keeps the app usable down to ~720p.
        self.resize(2000, 1100)
        self.setMinimumSize(QSize(960, 640))
        self._build_toolbar()
        self._build_left_tree()
        self._build_right_panel()
        self._build_tabs()
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.setGeometry(self.rect())
        self.loading_overlay.hidden.connect(self._flush_deferred_loading_dashboard)
        # Establish initial dock proportions matching the user's reference
        # screenshot (left project tree ~245 px, right metadata panel
        # ~300 px). The centre tab area gets the remainder (~1440 px on a
        # 2000-wide window). Splitter / drag-resize behaviour is unaffected.
        self.resizeDocks(
            [self.project_dock, self.context_dock],
            [255, 300],
            Qt.Orientation.Horizontal,
        )
        self._update_session_pill()
        self._update_status_summary()
        self._restore_from_settings()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.setGeometry(self.rect())
        self._refresh_branding()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setFixedHeight(50)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        self.brand_label = TitleBarLockup(self)
        self.brand_label.setToolTip("Tomography Session Browser")
        # Target the asset-sheet 50 px row, but let the toolbar grow if a
        # platform's fallback font produces a taller lockup (e.g. macOS SF
        # Pro or some Linux desktops). The +8 accounts for the toolbar's
        # 3 px QSS padding above and below the widget plus the 1 px bottom
        # border. Without this backstop the tagline can clip when widget
        # height > toolbar content area.
        toolbar.setFixedHeight(max(50, self.brand_label.height() + 8))
        toolbar.addWidget(self.brand_label)

        self.session_pill = QLabel("No session loaded")
        self.session_pill.setObjectName("sessionPill")
        toolbar.addWidget(self.session_pill)
        toolbar.addSeparator()

        # Primary file actions ------------------------------------------------
        self.open_action = self._action("folder-open", "Open folder", "Open one or more Tomography 5 session folders.")
        self.open_action.triggered.connect(self.open_session)
        toolbar.addAction(self.open_action)

        self.import_action = self._action(
            "folder-plus",
            "Import folder",
            "Add a related read-only atlas or data collection session folder to the current project view.",
        )
        self.import_action.triggered.connect(self.import_session)
        toolbar.addAction(self.import_action)

        self.refresh_action = self._action(
            "refresh", "Refresh", "Reload the currently opened session folders."
        )
        self.refresh_action.triggered.connect(self.refresh_session)
        self.refresh_action.setEnabled(False)
        toolbar.addAction(self.refresh_action)

        toolbar.addSeparator()

        # Report action -------------------------------------------------------
        self.report_action = self._action(
            "file-text", "Report", "Export a PDF report for the loaded session(s)."
        )
        self.report_action.setEnabled(False)
        self.report_action.triggered.connect(self._on_generate_report)
        toolbar.addAction(self.report_action)

        # Spacer pushes the remaining group to the right end of the toolbar.
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # View / theme actions ------------------------------------------------
        self.compact_action = self._action(
            "layout-compact", "Compact", "Dense layout mode is planned, but not available in this build."
        )
        self.compact_action.setCheckable(True)
        self.compact_action.setEnabled(False)
        self.compact_action.setVisible(False)
        self.compact_action.setStatusTip("Dense layout mode is not available yet.")
        toolbar.addAction(self.compact_action)

        self.project_panel_action = self._action(
            "panel-left", "Project", "Show or hide the project / session tree."
        )
        self.project_panel_action.setCheckable(True)
        self.project_panel_action.setChecked(True)
        self.project_panel_action.toggled.connect(self._toggle_project_panel)
        toolbar.addAction(self.project_panel_action)

        self.context_panel_action = self._action(
            "panel-right", "Context", "Show or hide the context / metadata panel."
        )
        self.context_panel_action.setCheckable(True)
        self.context_panel_action.setChecked(True)
        self.context_panel_action.toggled.connect(self._toggle_context_panel)
        toolbar.addAction(self.context_panel_action)

        self.theme_action = self._action(
            "moon", "Theme", "Switch between dark and light themes."
        )
        self.theme_action.setCheckable(True)
        self.theme_action.toggled.connect(self._on_theme_toggled)
        toolbar.addAction(self.theme_action)

        # Loading indicator -------------------------------------------------
        # Lives in the status bar so it never fights the toolbar for space.
        self.loading_progress = QProgressBar()
        self.loading_progress.setRange(0, 0)
        self.loading_progress.setMaximumWidth(180)
        self.loading_progress.setTextVisible(False)
        self.loading_progress.setVisible(False)
        self.status_scope = QLabel("scope: none")
        self.status_scope.setObjectName("statusSegment")
        self.status_scope.setToolTip("Current project, session, sample, or group scope.")
        self.status_counts = QLabel("Overview: 0 · Search map: 0 · Search: 0 · Batch position: 0 · Tilt series: 0")
        self.status_counts.setObjectName("statusSegment")
        self.status_counts.setToolTip("Loaded entities: overviews, search maps, search tiles, batch positions, tilt series.")
        self.status_runtime = QLabel(f"v0.1.0 · py {sys.version_info.major}.{sys.version_info.minor}")
        self.status_runtime.setObjectName("statusSegment")
        self.status_runtime.setToolTip("Application version and Python runtime.")
        self.statusBar().addWidget(self.status_scope)
        self.statusBar().addPermanentWidget(self.status_counts)
        self.statusBar().addPermanentWidget(self.loading_progress)
        self.statusBar().addPermanentWidget(self.status_runtime)

        # ---- Backwards-compat aliases used by older tests -------------------
        # The old toolbar exposed ``QPushButton`` attributes; tests inspect them
        # through ``isEnabled()`` / ``isChecked()`` / ``click()``. ``QAction``
        # supports the same surface, so we alias the new actions back to the
        # historical attribute names.
        self.open_button = self.open_action
        self.import_button = self.import_action
        self.refresh_button = self.refresh_action
        self.report_button = self.report_action
        self.compact_button = self.compact_action
        self.project_panel_button = self.project_panel_action
        self.context_panel_button = self.context_panel_action
        self._refresh_branding()

    # ------------------------------------------------------------------ helpers

    def _action(self, icon_name: str, label: str, tooltip: str) -> QAction:
        """Create a QAction with a themed icon and remember it for theme changes."""

        action = QAction(themed_icon(icon_name), label, self)
        action.setToolTip(tooltip)
        action.setData(icon_name)  # remember the icon source for refresh
        self._theme_actions.append(action)
        return action

    def _refresh_action_icons(self) -> None:
        """Re-render all toolbar action icons with the active theme's stroke colour."""

        for action in self._theme_actions:
            name = action.data()
            if action is getattr(self, "theme_action", None):
                name = "sun" if self.theme_action.isChecked() else "moon"
            if isinstance(name, str):
                action.setIcon(themed_icon(name))

    def _refresh_branding(self) -> None:
        if not hasattr(self, "brand_label"):
            return
        palette = current_palette()
        compact = self.width() < 1080
        self.brand_label.set_theme(palette.name)
        self.brand_label.set_compact(compact)

    def _update_session_pill(self) -> None:
        if not hasattr(self, "session_pill"):
            return
        if not self._sessions:
            self.session_pill.setText("● No session loaded")
            self.session_pill.setToolTip("Open a Tomography 5 session folder to begin review.")
            return
        if len(self._sessions) == 1:
            session = self._sessions[0]
            sample_count = len(self._display_samples(session))
            self.session_pill.setText(f"● {session.name} · {sample_count} samples")
            self.session_pill.setToolTip(f"{session.name}\n{session.path}")
            return
        groups = self._project_groups()
        self.session_pill.setText(f"● Project · {len(groups)} groups · {len(self._sessions)} sessions")
        linked = "\n".join(
            f"{group.display_name}: {', '.join(session.name for session in group.sessions)}"
            for group in groups
        )
        self.session_pill.setToolTip(f"Loaded project groups\n{linked}")

    def _update_tab_counts(self) -> None:
        if not hasattr(self, "tabs"):
            return
        context = self._tab_count_context()
        counts = {
            "Atlas": len(self._context_atlases(context)) if self._sessions else 0,
            "Overview": len(self._context_overviews(context)) if self._sessions else 0,
            "Search map": len(self._context_search_maps(context)) if self._sessions else 0,
            "Search": len(self._context_search_tiles(context)) if self._sessions else 0,
            "Batch position": len(self._context_batch_positions(context)) if self._sessions else 0,
            "Tilt series": len(self._context_tilt_series(context)) if self._sessions else 0,
        }
        for index, label in enumerate(TAB_LABELS):
            if label == "Session":
                self.tabs.setTabText(index, "Session")
            else:
                self.tabs.setTabText(index, f"{label}  {counts.get(label, 0)}")

    def _tab_count_context(self) -> Any:
        """Return the broad scope that should drive top-tab counts.

        Category nodes such as ``Overviews`` or ``Tilt series`` carry a narrow
        ``EntityGroup`` value so they can navigate directly to the matching
        tab. The tab badges, however, should describe the parent
        data-collection scope. Resolve that scope from the selected tree item
        first, falling back to the active context for programmatic navigation.
        """

        if hasattr(self, "tree"):
            context = self._tree_item_scope_context(self.tree.currentItem())
            if context is not _ACTIVE_CONTEXT:
                return context
        if isinstance(self._active_context, EntityGroup):
            context = self._context_for_entity_group(self._active_context)
            if context is not None:
                return context
        return self._active_context

    def _viewer_tab_context(self) -> Any:
        """Return the scope used to populate entity lists in viewer tabs.

        The tab badges already use the current project-tree scope. Keep the
        list payloads on that same scope whenever a tree-level selection is
        being preserved, so passive preview loading or stale list selections
        cannot narrow a linked root to its first sample.
        """

        if hasattr(self, "tree"):
            context = self._tree_item_scope_context(self.tree.currentItem())
            if isinstance(context, ProjectTreeGroup):
                return context
            if context is not _ACTIVE_CONTEXT and self._preserve_tree_root_context:
                return context
        if isinstance(self._active_context, EntityGroup):
            context = self._context_for_entity_group(self._active_context)
            if context is not None:
                return context
        return self._active_context

    def _update_status_summary(self, scope: str | None = None) -> None:
        if not hasattr(self, "status_counts"):
            return
        label = scope or self._current_scope_label()
        self.status_scope.setText(f"scope: {label}")
        self.status_scope.setToolTip(f"Current review scope: {label}")
        self.status_counts.setText(
            f"Overview: {len(self._all_overviews()) if self._sessions else 0} · "
            f"Search map: {len(self._all_search_maps()) if self._sessions else 0} · "
            f"Search: {len(self._all_search_tiles()) if self._sessions else 0} · "
            f"Batch position: {len(self._all_batch_positions()) if self._sessions else 0} · "
            f"Tilt series: {len(self._all_tilt_series()) if self._sessions else 0}"
        )
        self.status_counts.setToolTip(
            "Loaded entities in the current project: "
            f"{len(self._all_overviews()) if self._sessions else 0} overviews, "
            f"{len(self._all_search_maps()) if self._sessions else 0} search maps, "
            f"{len(self._all_search_tiles()) if self._sessions else 0} search tiles, "
            f"{len(self._all_batch_positions()) if self._sessions else 0} batch positions, "
            f"{len(self._all_tilt_series()) if self._sessions else 0} tilt series."
        )

    def _current_scope_label(self) -> str:
        context = self._active_context
        if isinstance(context, ProjectTreeGroup):
            return context.display_name
        if isinstance(context, LinkedSampleGroup):
            return context.label
        if isinstance(context, Session | Sample):
            return context.name
        if isinstance(context, EntityGroup):
            return context.label
        return "project"

    def _on_theme_toggled(self, checked: bool) -> None:
        new_theme = "light" if checked else "dark"
        self._settings.theme = new_theme
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, palette_for(new_theme))
        self._refresh_action_icons()
        self._refresh_theme_dependent_widgets()
        if hasattr(self, "tree"):
            self._populate_tree()
        save_settings(self._settings)

    def _refresh_theme_dependent_widgets(self) -> None:
        """Repaint custom/icon-based widgets that Qt stylesheets do not recolour."""

        for viewer_tab in getattr(self, "_viewer_tabs", {}).values():
            viewer_tab.refresh_theme()
        self._refresh_branding()
        if hasattr(self, "context_panel"):
            self.context_panel.refresh_theme()
        if hasattr(self, "session_dashboard"):
            if self._sessions:
                self._render_dashboard_for_scope(animate=False)
            else:
                self.session_dashboard.update()
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.update()

    def _restore_from_settings(self) -> None:
        settings = self._settings
        # Theme: app.py applies the theme before the window is built, so here
        # we only mirror the toggle state of the action.
        self.theme_action.blockSignals(True)
        self.theme_action.setChecked(settings.theme == "light")
        self.theme_action.blockSignals(False)
        self._refresh_action_icons()
        # Panel visibility — the toggled-callback also writes settings, so
        # block signals while we restore to avoid an immediate re-save.
        self.project_panel_action.blockSignals(True)
        self.project_panel_action.setChecked(settings.project_panel_visible)
        self.project_panel_action.blockSignals(False)
        # Avoid calling setVisible(True) on dock widgets before the main
        # window is shown. On Windows, native dock widgets can briefly appear
        # as tiny top-level windows during startup if explicitly shown early.
        if not settings.project_panel_visible:
            self.project_dock.hide()
        self.context_panel_action.blockSignals(True)
        self.context_panel_action.setChecked(settings.context_panel_visible)
        self.context_panel_action.blockSignals(False)
        if not settings.context_panel_visible:
            self.context_dock.hide()
        # Compact mode is wired in a later milestone; sync the toggle anyway.
        self.compact_action.blockSignals(True)
        self.compact_action.setChecked(settings.compact)
        self.compact_action.blockSignals(False)
        # Last-active tab.
        if settings.last_tab in TAB_LABELS:
            self.tabs.setCurrentIndex(TAB_LABELS.index(settings.last_tab))
        # Tab changes need to be persisted.
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int) -> None:
        if 0 <= index < len(TAB_LABELS):
            label = TAB_LABELS[index]
            self._settings.last_tab = label
            save_settings(self._settings)
            viewer_tab = self._viewer_tabs.get(label)
            if viewer_tab is not None:
                viewer_tab.ensure_initial_preview_loaded(
                    notify_selection=not self._tab_activation_preserves_broad_scope()
                )

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt signature
        self._settings.project_panel_visible = self.project_panel_action.isChecked()
        self._settings.context_panel_visible = self.context_panel_action.isChecked()
        self._settings.compact = self.compact_action.isChecked()
        if 0 <= self.tabs.currentIndex() < len(TAB_LABELS):
            self._settings.last_tab = TAB_LABELS[self.tabs.currentIndex()]
        save_settings(self._settings)
        super().closeEvent(event)

    def _build_left_tree(self) -> None:
        self.project_dock = QDockWidget("", self)
        self.project_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.project_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.project_dock.setTitleBarWidget(QWidget(self.project_dock))
        self.project_dock.setMinimumWidth(220)

        panel = QWidget(self.project_dock)
        panel.setObjectName("projectPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(panel)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 8)
        header_layout.setSpacing(8)
        title = QLabel("PROJECT")
        title.setObjectName("panelHeader")
        self.project_count = QLabel("0 sessions")
        self.project_count.setObjectName("panelCount")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(self.project_count)
        layout.addWidget(header)

        self.project_filter = QLineEdit()
        self.project_filter.setObjectName("treeSearch")
        self.project_filter.setPlaceholderText("Filter project...")
        self.project_filter.textChanged.connect(self._filter_project_tree)
        filter_wrap = QWidget(panel)
        filter_layout = QVBoxLayout(filter_wrap)
        filter_layout.setContentsMargins(10, 6, 10, 8)
        filter_layout.addWidget(self.project_filter)
        layout.addWidget(filter_wrap)

        self.tree = QTreeWidget()
        self.tree.setObjectName("projectTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setItemDelegate(_ProjectTreeDelegate(self.tree))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_project_tree_context_menu)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setMinimumSectionSize(44)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.currentItemChanged.connect(self._tree_selection_changed)
        self.tree.itemPressed.connect(self._project_tree_item_pressed)
        self.tree.itemDoubleClicked.connect(self._project_tree_item_double_clicked)
        root = QTreeWidgetItem(["No session loaded", ""])
        root.setToolTip(0, "No session loaded")
        self._style_tree_item(root)
        self.tree.addTopLevelItem(root)
        layout.addWidget(self.tree, stretch=1)
        self.project_dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)

    def _build_right_panel(self) -> None:
        self.context_dock = QDockWidget("", self)
        self.context_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.context_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.context_dock.setTitleBarWidget(QWidget(self.context_dock))
        self.context_dock.setMinimumWidth(260)
        panel = QWidget(self.context_dock)
        panel.setObjectName("contextShell")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.context_panel = MetadataPanel(panel)
        # Backwards-compatible aliases for tests / older call sites.
        self.context_title = self.context_panel
        self.context_text = self.context_panel
        layout.addWidget(self.context_panel, stretch=1)
        self.context_dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.context_dock)

    def _build_tabs(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        for label in TAB_LABELS:
            tab = QWidget(self.tabs)
            tab.setObjectName("tabPage")
            layout = QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            if label == "Session":
                # New graphical Session tab. The trees that previously lived
                # here are kept as hidden members so any code that rebuilds
                # them (now also driven from ``_render_session_summary``)
                # can populate them without touching the visible layout.
                self.session_dashboard = SessionDashboard(tab)
                self.session_dashboard.navigate_requested.connect(self._navigate_to_tab)
                self.session_dashboard.search_map_clicked.connect(self._on_dashboard_search_map_clicked)
                self.session_dashboard.tile_item_clicked.connect(self._on_dashboard_tile_clicked)
                self.session_dashboard.sample_clicked.connect(self._on_dashboard_sample_clicked)
                self.session_dashboard.filter_requested.connect(self._on_dashboard_filter_requested)
                self.session_dashboard.point_clicked.connect(self._on_dashboard_plot_point_clicked)
                self.session_dashboard.point_double_clicked.connect(self._on_dashboard_plot_point_double_clicked)
                self.session_dashboard.timeline_tilt_series_requested.connect(self._on_dashboard_timeline_tilt_series_requested)
                self.session_dashboard.highlight_clear_requested.connect(self._on_dashboard_highlight_clear_requested)
                layout.addWidget(self.session_dashboard, stretch=1)
                # Keep the tree widgets for backwards-compat tests / debug —
                # never parented into the visible UI.
                self.session_summary = QTreeWidget(tab)
                self.session_summary.setHeaderHidden(True)
                self.session_summary.setVisible(False)
                self.warning_tree = QTreeWidget(tab)
                self.warning_tree.setHeaderHidden(True)
                self.warning_tree.setVisible(False)
            else:
                viewer_tab = ViewerTab(
                    f"Open a session to browse {label.lower()} previews.",
                    show_list=True,
                    show_tilt_controls=label == "Tilt series",
                    on_item_selected=self._viewer_item_selected,
                    on_frame_changed=self._viewer_frame_changed,
                    tilt_angle_for=self._tilt_angle_for,
                    on_marker_selected=self._viewer_marker_selected,
                    on_marker_opened=self._viewer_marker_opened,
                    show_marker_controls=label != "Tilt series",
                    navigation_actions_for=self._viewer_navigation_actions
                    if label in {"Overview", "Search", "Search map", "Batch position", "Tilt series"}
                    else None,
                    on_navigation_requested=self._viewer_navigation_requested
                    if label in {"Overview", "Search", "Search map", "Batch position", "Tilt series"}
                    else None,
                )
                self._viewer_tabs[label] = viewer_tab
                layout.addWidget(viewer_tab, stretch=1)
            self.tabs.addTab(tab, label)
        container = QWidget(self)
        container.setObjectName("centralShell")
        outer = QHBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.tabs)
        self.setCentralWidget(container)

    def open_session(self) -> None:
        dialog = OpenSessionsDialog(self._loader, self._current_path or "", self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        paths = dialog.selected_paths()
        if not paths:
            return
        replacing_empty_project = not self._sessions
        self._queue_session_loads(
            [
                (path, replacing_empty_project and index == 0, index > 0, index == len(paths) - 1)
                for index, path in enumerate(paths)
            ]
        )

    def import_session(self) -> None:
        path = _select_session_folder(
            self,
            "Import related session folder",
            self._current_path or "",
            "Import folder",
        )
        if not path:
            return
        self.load_session(path, replace=False)

    def refresh_session(self) -> None:
        if self._current_path:
            paths = [str(session.path) for session in self._sessions] or [self._current_path]
            self._sessions = []
            self._queue_session_loads(
                [
                    (path, index == 0, True, index == len(paths) - 1)
                    for index, path in enumerate(paths)
                ]
            )

    def load_session(
        self,
        path: str,
        replace: bool = True,
        quiet: bool = False,
        *,
        animate: bool = True,
    ) -> None:
        self._queue_session_loads([(path, replace, quiet, animate)])

    def _queue_session_loads(self, loads: list[tuple[str, bool, bool, bool]]) -> None:
        if not loads:
            return
        normalised_loads: list[tuple[str, bool, bool, bool]] = []
        for path, replace, quiet, animate in loads:
            folder_path = _normalise_session_folder_for_import(self, path)
            if folder_path is None:
                continue
            normalised_loads.append((folder_path, replace, quiet, animate))
        if not normalised_loads:
            return
        self._load_queue.extend(normalised_loads)
        if not self._loading_task_active:
            self._begin_loading("Loading session...")
            self._start_next_session_load()

    def _start_next_session_load(self) -> None:
        if not self._load_queue:
            self._loading_task_active = False
            self._finish_loading_after_repaint("Loading complete")
            return
        path, replace, quiet, animate = self._load_queue.pop(0)
        self._loading_task_active = True
        self._set_loading_status("Discovering files...", force=True)
        # Build the profile on the main thread so the stall monitor can
        # share it with the worker's parsing run. The worker calls
        # ``record_stall`` indirectly via the monitor; the worker itself
        # owns the phase/counter additions. Binding the profile on the
        # main thread too lets context-panel updates and other main-
        # thread aggregate phases land in the same report.
        profile = LoadingProfiler()
        self._bind_main_thread_profile(profile)
        self._attach_stall_monitor(profile)
        task = _SessionLoadTask(path, replace, quiet, animate, self._session_load_signals, profile)
        self._load_thread_pool.start(task)

    def _bind_main_thread_profile(self, profile: LoadingProfiler) -> None:
        """Bind ``profile`` as the main thread's active loader profile.

        Lets main-thread code (context panel updates, marker generation,
        etc.) call :func:`record_aggregate_phase` without threading the
        profile through every method. The corresponding :meth:`_unbind`
        must run on the same thread.
        """

        self._unbind_main_thread_profile()
        try:
            from tomography_session_browser.services import loading_profiler as _lp

            self._main_thread_profile_token = _lp._ACTIVE_PROFILE.set(profile)  # noqa: SLF001
        except Exception:  # pragma: no cover - perf must not break loads
            LOGGER.debug("Could not bind main-thread profile", exc_info=True)
            self._main_thread_profile_token = None

    def _unbind_main_thread_profile(self) -> None:
        token = self._main_thread_profile_token
        if token is None:
            return
        try:
            from tomography_session_browser.services import loading_profiler as _lp

            _lp._ACTIVE_PROFILE.reset(token)  # noqa: SLF001
        except Exception:  # pragma: no cover - defensive
            LOGGER.debug("Could not unbind main-thread profile", exc_info=True)
        finally:
            self._main_thread_profile_token = None

    def _attach_stall_monitor(self, profile: LoadingProfiler) -> None:
        """Start a :class:`MainThreadStallMonitor` for ``profile``.

        Safe to call repeatedly; an existing monitor is stopped first.
        """

        if self._stall_monitor is not None:
            try:
                self._stall_monitor.stop()
            except Exception:  # pragma: no cover - defensive
                LOGGER.debug("Could not stop previous stall monitor", exc_info=True)
            self._stall_monitor = None
        try:
            from tomography_session_browser.services.main_thread_stall_monitor import (
                MainThreadStallMonitor,
            )

            monitor = MainThreadStallMonitor(profile)
            monitor.start()
            self._stall_monitor = monitor
        except Exception:  # pragma: no cover - perf must not break loads
            LOGGER.debug("Could not start stall monitor", exc_info=True)
            self._stall_monitor = None

    def _detach_stall_monitor(self) -> None:
        if self._stall_monitor is None:
            return
        try:
            self._stall_monitor.stop()
        except Exception:  # pragma: no cover - defensive
            LOGGER.debug("Could not stop stall monitor", exc_info=True)
        self._stall_monitor = None

    def _on_session_loaded(
        self,
        path: str,
        replace: bool,
        quiet: bool,
        animate: bool,
        session: object,
        profile: object,
    ) -> None:
        loading_profile = profile if isinstance(profile, LoadingProfiler) else None
        if not isinstance(session, Session):
            self._on_session_load_failed(path, replace, quiet, animate, "Loader returned an unexpected session object.")
            return
        if not replace and self._session_for_path(session.path) is not None:
            if not quiet:
                self.statusBar().showMessage(f"{session.name} is already loaded.", 4000)
            self._after_loaded_session_integrated(animate=False)
            return
        self._integrate_loaded_session_cooperatively(
            session,
            path,
            replace=replace,
            quiet=quiet,
            animate=animate,
            profile=loading_profile,
        )

    def _on_session_load_failed(self, path: str, replace: bool, quiet: bool, animate: bool, error: str) -> None:
        del replace, quiet, animate
        self._load_queue.clear()
        self._pending_ui_integration = None
        self._loading_task_active = False
        self._detach_stall_monitor()
        self._unbind_main_thread_profile()
        self._finish_loading("Loading failed", fade=False)
        QMessageBox.critical(self, "Could not load session", f"{error}\n\nPath: {path}")

    def _on_session_ui_prepare_failed(self, error: str) -> None:
        pending = self._pending_ui_integration
        self._pending_ui_integration = None
        if pending is None:
            return
        session, path, replace, quiet, animate, _profile = pending
        del session
        self._on_session_load_failed(path, replace, quiet, animate, error)

    def _on_session_ui_prepared(self, prepared: object) -> None:
        pending = self._pending_ui_integration
        self._pending_ui_integration = None
        if pending is None:
            return
        if not isinstance(prepared, _PreparedSessionUi):
            session, path, replace, quiet, animate, _profile = pending
            del session
            self._on_session_load_failed(path, replace, quiet, animate, "UI preparation returned an unexpected result.")
            return
        session, path, replace, quiet, animate, profile = pending
        self._apply_prepared_session_ui_cooperatively(
            prepared,
            session,
            path,
            replace=replace,
            quiet=quiet,
            animate=animate,
            profile=profile,
        )

    def _apply_prepared_session_ui_cooperatively(
        self,
        prepared: _PreparedSessionUi,
        session: Session,
        path: str,
        *,
        replace: bool,
        quiet: bool,
        animate: bool,
        profile: LoadingProfiler | None = None,
    ) -> None:
        tab_labels = ["Atlas", "Overview", "Search map", "Search", "Batch position", "Tilt series"]
        steps: list[tuple[str, Callable[[], None]]] = [
            # Two-step session-summary render. Splitting the summary-tree
            # rebuild from the dashboard widget rebuild lets the
            # LoadingOverlay animation timer tick between them; together
            # they ran ~36ms back-to-back on a large reference session and stole >70ms from
            # the animation budget. The staged loader inserts a
            # ``QTimer.singleShot(0, ...)`` between every entry below.
            (
                "Rendering session summaries...",
                lambda: self._render_loaded_session_summary(animate=animate, prepared=prepared, dashboard_only=False),
            ),
            (
                "Building session dashboard...",
                lambda: self._render_loaded_session_summary(animate=animate, prepared=prepared, dashboard_only=True),
            ),
            *[
                (
                    f"Building {label.lower()} browser...",
                    lambda label=label: self._render_loaded_session_tabs(prepared=prepared, labels=[label]),
                )
                for label in tab_labels
            ],
            (
                "Finalising interface...",
                lambda: self._finalise_loaded_session(session, path, replace=replace, quiet=quiet, animate=animate),
            ),
        ]

        def run_step(index: int = 0) -> None:
            if index >= len(steps):
                self._after_loaded_session_integrated(animate=animate, profile=profile, profile_path=path)
                return
            message, step = steps[index]
            self._set_loading_status(message, force=True)
            try:
                started = time.perf_counter()
                step()
                if profile is not None:
                    profile.add_phase(
                        message.rstrip(".").lower().replace(" ", "_"),
                        time.perf_counter() - started,
                        ui_updates=1,
                    )
                    profile.record_ui_update()
            except Exception as exc:  # noqa: BLE001 - show a clear UI error and keep diagnostics
                LOGGER.exception("Could not render prepared tomography session UI path=%s", path)
                self._on_session_load_failed(path, replace, quiet, animate, str(exc))
                return
            QTimer.singleShot(0, lambda: run_step(index + 1))

        QTimer.singleShot(0, run_step)

    def _integrate_loaded_session_cooperatively(
        self,
        session: Session,
        path: str,
        *,
        replace: bool,
        quiet: bool,
        animate: bool,
        profile: LoadingProfiler | None = None,
    ) -> None:
        """Integrate a parsed session without treating parsing as UI readiness."""

        def prepare_tree() -> None:
            self._set_loading_status("Building project tree...", force=True)
            try:
                started = time.perf_counter()
                self._prepare_loaded_session(session, path, replace=replace)
                if profile is not None:
                    profile.add_phase("populate_project_tree", time.perf_counter() - started, ui_updates=1)
                    profile.record_ui_update()
            except Exception as exc:  # noqa: BLE001 - show a clear UI error and keep diagnostics
                LOGGER.exception("Could not integrate loaded tomography session path=%s", path)
                self._on_session_load_failed(path, replace, quiet, animate, str(exc))
                return
            self._pending_ui_integration = (session, path, replace, quiet, animate, profile)
            self._set_loading_status("Building data model...", force=True)
            task = _SessionUiPrepareTask(list(self._sessions), self._active_context, self._session_ui_prepare_signals, profile)
            self._load_thread_pool.start(task)

        QTimer.singleShot(0, prepare_tree)

    def _integrate_loaded_session(
        self,
        session: Session,
        path: str,
        *,
        replace: bool,
        quiet: bool,
        animate: bool,
    ) -> None:
        self._prepare_loaded_session(session, path, replace=replace)
        self._render_loaded_session_summary(animate=animate)
        self._render_loaded_session_tabs()
        self._finalise_loaded_session(session, path, replace=replace, quiet=quiet, animate=animate)

    def _prepare_loaded_session(
        self,
        session: Session,
        path: str,
        *,
        replace: bool,
    ) -> None:
        if replace:
            clear_preview_cache()
            self._sessions = [session]
        else:
            self._sessions.append(session)
        self._failed_tilt_ids_cache = None
        self._dashboard_model_cache.clear()
        self._session = self._sessions[0]
        self._current_path = str(session.path)
        self._active_context = self._project_group_for_session(session) or session
        self._rebuild_sample_index()
        add_recent_session(self._settings, str(session.path))
        save_settings(self._settings)
        self.refresh_button.setEnabled(True)
        self._populate_tree()

    def _render_loaded_session_summary(
        self,
        *,
        animate: bool,
        prepared: _PreparedSessionUi | None = None,
        dashboard_only: bool | None = None,
    ) -> None:
        """Render the Session-tab summary during a staged load.

        ``dashboard_only`` controls which half runs:

        * ``False`` — rebuild the summary tree + warnings only.
        * ``True`` — rebuild the dashboard widget only.
        * ``None`` — both halves synchronously (legacy callers).

        The staged loader splits the work across two event-loop ticks so
        the LoadingOverlay animation timer never has to wait for the
        full sequence to complete.
        """

        if dashboard_only is None:
            self._render_session_summary(animate_dashboard=animate, prepared=prepared)
            return
        if dashboard_only:
            self._render_session_summary_dashboard(animate_dashboard=animate, prepared=prepared)
            return
        warnings = self._render_session_summary_tree(prepared=prepared)
        if self._sessions:
            self._render_warning_groups(warnings)
            self.tabs.setTabText(0, "Session")
            self._update_tab_counts()

    def _render_loaded_session_tabs(
        self,
        *,
        prepared: _PreparedSessionUi | None = None,
        labels: list[str] | None = None,
    ) -> None:
        self._render_viewer_tabs(prepared=prepared, labels=labels)

    def _finalise_loaded_session(
        self,
        session: Session,
        path: str,
        *,
        replace: bool,
        quiet: bool,
        animate: bool,
    ) -> None:
        active_group = self._project_group_for_session(session)
        if active_group is not None:
            self._active_context = active_group
            self._preserve_tree_root_context = True
            self.context_panel.set_title(active_group.display_name)
            self.context_panel.set_text(self._describe_project_group(active_group))
        else:
            self.context_panel.set_title("Session")
            self.context_panel.set_text(describe_object(session))
        self._update_session_pill()
        self._update_tab_counts()
        self._update_status_summary(active_group.display_name if active_group is not None else "Session")
        if not quiet:
            action = "Loaded" if replace else "Imported"
            self.statusBar().showMessage(f"{action} {session.name}")
        LOGGER.debug("Integrated tomography session path=%s replace=%s animate=%s", path, replace, animate)

    def _fold_main_thread_perf_into_profile(self, profile: LoadingProfiler) -> None:
        """Push UI-thread metrics gathered during this load into ``profile``
        so they appear in the perf report.

        The worker threads attach phases directly; the UI thread keeps its
        timings on ``self`` until the final emit so they can be merged in one
        place. Intentionally additive — never overwrites worker phases.
        """

        try:
            if self._last_dashboard_widget_build_ms > 0:
                profile.add_phase(
                    "build_dashboard_widget",
                    self._last_dashboard_widget_build_ms / 1000.0,
                    note="latest set_model() call",
                )
            if self._dashboard_rebuild_count:
                profile.counters["dashboard_rebuilds"] = self._dashboard_rebuild_count
        except Exception:  # pragma: no cover - perf reporting must never break a load
            LOGGER.debug("Could not fold main-thread perf into profile", exc_info=True)

    def _after_loaded_session_integrated(
        self,
        *,
        animate: bool,
        profile: LoadingProfiler | None = None,
        profile_path: str = "",
    ) -> None:
        if self._load_queue:
            if profile is not None:
                self._fold_main_thread_perf_into_profile(profile)
                profile.emit_perf_report(LOGGER, path=profile_path)
            self._start_next_session_load()
            return
        self._loading_task_active = False
        self._record_loading_animation_profile(profile)
        self._finish_loading_after_repaint("Loading complete", fade=True)
        self._detach_stall_monitor()
        self._unbind_main_thread_profile()
        if profile is not None:
            self._fold_main_thread_perf_into_profile(profile)
            profile.emit_perf_report(LOGGER, path=profile_path)
        if animate:
            QTimer.singleShot(0, self._animate_import_refresh)

    def _begin_loading(self, message: str) -> None:
        self._pending_loading_status = None
        self._loading_status_timer.stop()
        self._last_loading_status_at = 0.0
        self._deferred_loading_dashboard = None
        self._deferred_loading_dashboard_refresh = False
        # Reset perf counters for this load so the report attributes
        # rebuild count and widget build cost to *this* load only.
        self._dashboard_rebuild_count = 0
        self._last_dashboard_widget_build_ms = 0
        # Stall monitor is created with a placeholder profile and then
        # rebound when the worker constructs the real LoadingProfiler.
        # See :meth:`_attach_stall_monitor`.
        if self._stall_monitor is not None:
            try:
                self._stall_monitor.stop()
            except Exception:  # pragma: no cover - defensive
                LOGGER.debug("Could not stop previous stall monitor", exc_info=True)
            self._stall_monitor = None
        # Drop any prior main-thread profile binding so a new load's
        # aggregate-phase recordings (context panel, etc.) don't leak
        # into a stale profile.
        self._unbind_main_thread_profile()
        self.loading_progress.setVisible(True)
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.show_loading(message)
        self._set_loading_status(message, force=True)
        self.open_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.refresh_button.setEnabled(False)

    def _set_loading_status(self, message: str, *, force: bool = False) -> None:
        if not message:
            return
        now = time.monotonic()
        elapsed = now - self._last_loading_status_at
        if not force and self._last_loading_status_at and elapsed < self._loading_status_interval_s:
            self._pending_loading_status = message
            remaining_ms = max(1, int((self._loading_status_interval_s - elapsed) * 1000))
            if not self._loading_status_timer.isActive():
                self._loading_status_timer.start(remaining_ms)
            return
        self._apply_loading_status(message)

    def _flush_pending_loading_status(self) -> None:
        if self._pending_loading_status is None:
            return
        message = self._pending_loading_status
        self._pending_loading_status = None
        self._apply_loading_status(message)

    def _apply_loading_status(self, message: str) -> None:
        self._last_loading_status_at = time.monotonic()
        if hasattr(self, "loading_overlay") and self.loading_overlay.isVisible():
            self.loading_overlay.update_loading_message(message)
        self.statusBar().showMessage(message)

    def _finish_loading_after_repaint(self, message: str, *, fade: bool = True) -> None:
        self._set_loading_status("Finalising interface...", force=True)
        self.update()
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.update()

        def finish_after_repaint() -> None:
            QTimer.singleShot(0, lambda: self._finish_loading(message, fade=fade))

        QTimer.singleShot(0, finish_after_repaint)

    def _finish_loading(self, message: str, *, fade: bool = True) -> None:
        self._pending_loading_status = None
        self._loading_status_timer.stop()
        self.loading_progress.setVisible(False)
        self._set_loading_status(message, force=True)
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.hide_loading(fade=fade)
        self.open_button.setEnabled(True)
        self.import_button.setEnabled(True)
        self.refresh_button.setEnabled(bool(self._sessions))
        self.report_action.setEnabled(bool(self._sessions))

    def _loading_overlay_active(self) -> bool:
        return bool(
            self._loading_task_active
            or (hasattr(self, "loading_overlay") and self.loading_overlay.isVisible())
        )

    def _record_loading_animation_profile(self, profile: LoadingProfiler | None) -> None:
        if profile is None or not hasattr(self, "loading_overlay"):
            return
        snapshot = self.loading_overlay.performance_snapshot()
        max_interval_ms = float(snapshot.get("max_interval_ms", 0.0))
        profile.add_phase(
            "loading_animation_timer",
            max_interval_ms / 1000.0,
            count=int(snapshot.get("tick_count", 0)),
            note=(
                f"target_ms={snapshot.get('target_interval_ms', 0)} "
                f"median_ms={snapshot.get('median_interval_ms', 0)} "
                f"max_ms={snapshot.get('max_interval_ms', 0)} "
                f"dropped_frames={snapshot.get('dropped_frames', 0)}"
            ),
        )

    def _flush_deferred_loading_dashboard(self) -> None:
        pending = self._deferred_loading_dashboard
        refresh = self._deferred_loading_dashboard_refresh
        self._deferred_loading_dashboard = None
        self._deferred_loading_dashboard_refresh = False
        if pending is None and not refresh:
            return

        def render_after_overlay_paint() -> None:
            if pending is not None:
                model, timeline, animate = pending
                self._render_dashboard_for_scope(
                    model=model,
                    timeline=timeline,
                    animate=animate,
                    defer_heavy_cards=False,
                )
            else:
                self._render_dashboard_for_scope(defer_heavy_cards=False)

        QTimer.singleShot(0, render_after_overlay_paint)

    def _animate_import_refresh(self) -> None:
        fade_in(self.tree.viewport(), duration_ms=150, start_opacity=0.72)
        fade_in(self.context_panel, duration_ms=150, delay_ms=35, start_opacity=0.76)

    def _on_generate_report(self) -> None:
        if not self._sessions:
            QMessageBox.information(
                self,
                "No session loaded",
                "No sessions loaded. Load a session before generating a report.",
            )
            return

        groups = self._project_groups()
        dialog = ReportScopeDialog(
            groups,
            current_scope=self._report_default_scope_value(),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            scope = normalise_report_scope(self._sessions, groups, dialog.selected_request())
        except ValueError as exc:
            QMessageBox.warning(self, "No report scope selected", str(exc))
            return

        primary = scope.sessions[0]
        default_dir = self._settings.last_report_directory or str(primary.path.parent)
        default_name = scope.default_filename
        default_path = str(Path(default_dir) / default_name)

        target, _ = QFileDialog.getSaveFileName(
            self,
            "Save session report",
            default_path,
            "PDF (*.pdf)",
        )
        if not target:
            return
        if not target.lower().endswith(".pdf"):
            target += ".pdf"

        output_path = Path(target)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._set_loading_status("Generating PDF report...")
            result = build_session_report(
                scope.sessions,
                output_path,
                project_title=scope.project_title,
                project_groups=scope.project_groups,
            )
        except Exception as exc:  # noqa: BLE001 — surface any reportlab error to the user
            LOGGER.exception("Failed to generate PDF report")
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self,
                "Report failed",
                f"Could not generate the PDF report:\n\n{exc}",
            )
            self.statusBar().showMessage("Report generation failed", 5000)
            return
        else:
            QApplication.restoreOverrideCursor()

        self._settings.last_report_directory = str(output_path.parent)
        save_settings(self._settings)
        self.statusBar().showMessage(
            f"Saved {output_path.name} ({result.page_count} pages)", 5000
        )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Report saved")
        box.setText(f"Saved to {output_path}")
        if result.warnings:
            box.setDetailedText("\n".join(result.warnings))
        open_button = box.addButton("Open folder", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_path.parent)))

    def _report_default_scope_value(self) -> Any | None:
        if not hasattr(self, "tree"):
            return None
        item = self.tree.currentItem()
        selected_session: Session | None = None
        while item is not None:
            value = item.data(0, OBJECT_ROLE)
            if isinstance(value, ProjectTreeGroup):
                return value
            if isinstance(value, Sample):
                return value
            if isinstance(value, Session):
                selected_session = value
                break
            item = item.parent()
        if selected_session is not None:
            return selected_session
        return None

    def _project_report_title(self) -> str | None:
        groups = self._project_groups()
        if not groups:
            return None
        if len(groups) == 1:
            return groups[0].display_name
        return "Tomography Project Report (" + ", ".join(group.display_name for group in groups) + ")"

    def _project_report_groups(self) -> list[ProjectReportGroup]:
        groups: list[ProjectReportGroup] = []
        for group in self._project_groups():
            groups.append(
                ProjectReportGroup(
                    display_name=group.display_name,
                    automatic_name=group.automatic_name,
                    kind=self._project_group_tag(group),
                    session_names=tuple(session.name for session in group.sessions),
                    session_paths=tuple(str(session.path) for session in group.sessions),
                )
            )
        return groups

    def _project_groups(self) -> list[ProjectTreeGroup]:
        return build_project_tree_groups(self._sessions, self._project_display_names)

    def _session_for_path(self, path: str | Path) -> Session | None:
        key = _path_identity_key(path)
        for session in self._sessions:
            if session_key(session) == key or _path_identity_key(session.path) == key:
                return session
        return None

    def _project_group_for_session(self, session: Session) -> ProjectTreeGroup | None:
        for group in self._project_groups():
            if any(candidate is session for candidate in group.sessions):
                return group
        return None

    def _populate_tree(self) -> None:
        expanded_ids = self._collect_expanded_node_ids()
        current_key = self._selected_project_group_key()
        previous_signal_state = self.tree.blockSignals(True)
        self.tree.setUpdatesEnabled(False)
        try:
            self._project_tree_tilt_items_by_id = {}
            self.tree.clear()
            groups = self._project_groups()
            if self._sessions:
                group_label = f"{len(groups)} group{'s' if len(groups) != 1 else ''}"
                session_label = f"{len(self._sessions)} session{'s' if len(self._sessions) != 1 else ''}"
                self.project_count.setText(f"{group_label} / {session_label}")
            else:
                self.project_count.setText("0 sessions")
                root = QTreeWidgetItem(["No session loaded", ""])
                root.setToolTip(0, "No session loaded")
                self._style_tree_item(root)
                self.tree.addTopLevelItem(root)

            current_item: QTreeWidgetItem | None = None
            first_group_item: QTreeWidgetItem | None = None
            for group in groups:
                group_item = self._project_group_item(group)
                self._populate_project_group_item(group_item, group)
                self.tree.addTopLevelItem(group_item)
                if first_group_item is None:
                    first_group_item = group_item
                if group.key == current_key:
                    current_item = group_item
            if current_item is None:
                current_item = first_group_item
            if current_item is not None:
                self.tree.setCurrentItem(current_item)
            self._add_recent_session_items()
            self._restore_expanded_node_ids(expanded_ids)
            self._filter_project_tree(self.project_filter.text())
            self._sync_project_tree_highlight_state()
        finally:
            self.tree.setUpdatesEnabled(True)
            self.tree.blockSignals(previous_signal_state)

    def _populate_session_item(self, session_item: QTreeWidgetItem, session: Session) -> None:
        display_samples = self._display_samples(session)
        if display_samples:
            for sample in display_samples:
                session_item.addChild(self._sample_item(sample))
        else:
            self._add_entity_groups(session_item, session)

    def _selected_project_group_key(self) -> str | None:
        if isinstance(self._active_context, ProjectTreeGroup):
            return self._active_context.key
        item = self.tree.currentItem() if hasattr(self, "tree") else None
        while item is not None:
            value = item.data(0, OBJECT_ROLE)
            if isinstance(value, ProjectTreeGroup):
                return value.key
            item = item.parent()
        return None

    def _collect_expanded_node_ids(self) -> set[str]:
        expanded: set[str] = set()
        if not hasattr(self, "tree"):
            return expanded

        def visit(item: QTreeWidgetItem) -> None:
            node_id = item.data(0, EXPANSION_ROLE)
            if isinstance(node_id, str) and item.isExpanded():
                expanded.add(node_id)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        return expanded

    def _restore_expanded_node_ids(self, expanded_ids: set[str]) -> None:
        def visit(item: QTreeWidgetItem) -> None:
            node_id = item.data(0, EXPANSION_ROLE)
            item.setExpanded(isinstance(node_id, str) and node_id in expanded_ids)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def _project_group_item(self, group: ProjectTreeGroup) -> QTreeWidgetItem:
        item = QTreeWidgetItem([group.display_name, self._project_group_tag(group)])
        item.setToolTip(0, self._project_group_tooltip(group))
        item.setToolTip(1, self._project_group_badge_tooltip(group))
        item.setData(0, OBJECT_ROLE, group)
        item.setData(0, EXPANSION_ROLE, f"project:{group.key}")
        self._style_tree_item(item)
        self._style_project_group_item(item, group)
        return item

    def _populate_project_group_item(self, group_item: QTreeWidgetItem, group: ProjectTreeGroup) -> None:
        if group.kind == "linked" and group.atlas_session is not None:
            atlas_item = self._item("Atlas", group.atlas_session)
            self._populate_session_item(atlas_item, group.atlas_session)
            group_item.addChild(atlas_item)
            for session in group.collection_sessions:
                session_item = self._item(session.name, session)
                self._populate_session_item(session_item, session)
                group_item.addChild(session_item)
            return

        session = group.sessions[0] if group.sessions else None
        if session is not None:
            self._populate_session_item(group_item, session)

    def _project_group_tag(self, group: ProjectTreeGroup) -> str:
        if group.kind == "linked":
            return "LS"
        if group.kind == "atlas":
            return "AT"
        if group.kind == "unresolved":
            return "!"
        return "DC"

    def _project_group_tooltip(self, group: ProjectTreeGroup) -> str:
        lines = [group.display_name]
        if group.is_renamed:
            lines.append(f"Automatic name: {group.automatic_name}")
        if group.kind == "linked":
            lines.append(f"Linked data collections: {len(group.collection_sessions)}")
        lines.extend(f"{session.name}: {session.path}" for session in group.sessions)
        lines.extend(group.warnings)
        return "\n".join(lines)

    def _style_project_group_item(self, item: QTreeWidgetItem, group: ProjectTreeGroup) -> None:
        font = item.font(0)
        font.setWeight(QFont.Weight.DemiBold)
        item.setFont(0, font)
        item.setFont(1, font)
        palette = current_palette()
        item.setForeground(0, QBrush(QColor(palette.text_strong)))
        item.setBackground(0, QBrush(QColor(palette.surface_alt)))
        item.setBackground(1, QBrush(QColor(palette.surface_alt)))
        icon_name = "layers" if group.kind == "linked" else "folder-open"
        item.setIcon(0, themed_icon(icon_name))

    def _project_group_badge_tooltip(self, group: ProjectTreeGroup) -> str:
        if group.kind == "linked":
            count = len(group.collection_sessions)
            return f"Linked Session - {count} data collection session{'s' if count != 1 else ''}"
        if group.kind == "atlas":
            return "Atlas-only project group"
        if group.kind == "unresolved":
            return "Unresolved or ambiguous link; kept separate"
        return "Unlinked data collection project group"

    def _linked_sample_items(self, sessions: list[Session] | None = None) -> list[QTreeWidgetItem]:
        sessions = sessions if sessions is not None else self._sessions
        items: list[QTreeWidgetItem] = []
        for samples in sorted(linked_sample_groups(sessions), key=sample_sort_key_for_group):
            if not any(sample.atlas or self._sample_has_collection_data(sample) for sample in samples):
                continue
            label = self._linked_sample_label(samples)
            item = self._item(label, build_linked_sample_group(label, samples))
            atlases = [sample.atlas for sample in samples if sample.atlas is not None]
            if atlases:
                item.addChild(self._item("Atlas", atlases[0]))
            self._add_group(item, "Overviews", [value for sample in samples for value in sample.overviews])
            self._add_group(item, "Search maps", [value for sample in samples for value in sample.search_maps])
            self._add_group(item, "Search", [value for sample in samples for value in sample.search_tiles])
            self._add_group(item, "Batch positions", [value for sample in samples for value in sample.batch_positions])
            self._add_group(item, "Tilt series", [value for sample in samples for value in sample.tilt_series])
            items.append(item)
        return items

    def _sample_item(self, sample: Sample) -> QTreeWidgetItem:
        sample_item = self._item(sample.name, sample)
        if sample.atlas:
            sample_item.addChild(self._item("Atlas", sample.atlas))
        self._add_collection_groups(sample_item, sample)
        return sample_item

    def _add_entity_groups(self, parent: QTreeWidgetItem, session: Session) -> None:
        if session.atlas:
            parent.addChild(self._item("Atlas", session.atlas))
        self._add_group(parent, "Overviews", session.overviews)
        self._add_group(parent, "Search maps", session.search_maps)
        self._add_group(parent, "Search", session.search_tiles)
        self._add_group(parent, "Batch positions", session.batch_positions)
        self._add_group(parent, "Tilt series", session.tilt_series)

    def _add_collection_groups(self, parent: QTreeWidgetItem, sample: Sample) -> None:
        self._add_group(parent, "Overviews", sample.overviews)
        self._add_group(parent, "Search maps", sample.search_maps)
        self._add_group(parent, "Search", sample.search_tiles)
        self._add_group(parent, "Batch positions", sample.batch_positions)
        self._add_group(parent, "Tilt series", sample.tilt_series)

    def _add_group(self, parent: QTreeWidgetItem, label: str, values: list[Any]) -> None:
        if not values:
            return
        group = QTreeWidgetItem([label, str(len(values))])
        group.setToolTip(0, group.text(0))
        entity_group = EntityGroup(label=label, values=values)
        group.setData(0, OBJECT_ROLE, entity_group)
        group.setData(0, EXPANSION_ROLE, self._tree_expansion_id_for(label, entity_group))
        self._style_tree_item(group)
        parent.addChild(group)
        for value in values:
            group.addChild(self._item(self._label_for(value), value))

    def _item(self, label: str, value: Any) -> QTreeWidgetItem:
        tag = self._tree_tag_for(value)
        item = QTreeWidgetItem([label, tag])
        item.setToolTip(0, label)
        if tag:
            item.setToolTip(1, self._tree_tag_tooltip_for(value))
        item.setData(0, OBJECT_ROLE, value)
        item.setData(0, EXPANSION_ROLE, self._tree_expansion_id_for(label, value))
        self._style_tree_item(item)
        if isinstance(value, TiltSeries):
            self._project_tree_tilt_items_by_id.setdefault(value.id, []).append(item)
        return item

    def _tree_expansion_id_for(self, label: str, value: Any) -> str:
        if isinstance(value, ProjectTreeGroup):
            return f"project:{value.key}"
        if isinstance(value, Session):
            return f"session:{session_key(value)}"
        if isinstance(value, Sample):
            return f"sample:{self._path_key(value.path)}"
        if isinstance(value, Atlas):
            return f"atlas:{self._atlas_key(value)}"
        if isinstance(value, Overview | SearchMap | SearchTile | BatchPosition | TiltSeries):
            return f"{type(value).__name__}:{getattr(value, 'id', label)}"
        if isinstance(value, LinkedSampleGroup):
            sample_ids = ",".join(self._path_key(sample.path) for sample in value.samples)
            return f"linked-sample:{sample_ids}"
        if isinstance(value, EntityGroup):
            child_ids = ",".join(getattr(child, "id", str(index)) for index, child in enumerate(value.values[:12]))
            return f"entity-group:{value.label}:{child_ids}"
        return f"label:{label}"

    def _path_key(self, path: Path) -> str:
        return _path_identity_key(path)

    def _style_tree_item(self, item: QTreeWidgetItem) -> None:
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _tree_tag_for(self, value: Any) -> str:
        if isinstance(value, Sample):
            count = len(value.batch_positions)
            return f"{count} bp" if count else ""
        if isinstance(value, LinkedSampleGroup):
            count = len(value.batch_positions)
            return f"{count} bp" if count else ""
        if isinstance(value, Session):
            if value.kind == SessionKind.ATLAS_SCREENING:
                return "AT"
            if value.kind in {SessionKind.MULTIGRID, SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION}:
                return "DC"
            return "?"
        return ""

    def _tree_tag_tooltip_for(self, value: Any) -> str:
        if isinstance(value, Sample):
            count = len(value.batch_positions)
            return f"{count} batch position{'s' if count != 1 else ''}"
        if isinstance(value, LinkedSampleGroup):
            count = len(value.batch_positions)
            return f"{count} linked batch position{'s' if count != 1 else ''}"
        if isinstance(value, Session):
            if value.kind == SessionKind.ATLAS_SCREENING:
                return "Atlas screening session"
            if value.kind in {SessionKind.MULTIGRID, SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION}:
                return "Data collection session"
            return "Unknown session type"
        return ""

    def _add_recent_session_items(self) -> None:
        recent = [
            path
            for path in self._settings.recent_sessions
            if path and path not in {str(session.path) for session in self._sessions}
        ][:4]
        if not recent:
            return
        section = QTreeWidgetItem(["Recent sessions", ""])
        section.setToolTip(0, "Recent sessions")
        section.setData(0, EXPANSION_ROLE, "recent-sessions")
        self._style_tree_item(section)
        self.tree.addTopLevelItem(section)
        for path in recent:
            label = Path(path).name or path
            child = QTreeWidgetItem([label, "closed"])
            child.setToolTip(0, path)
            child.setToolTip(1, "Recently opened session")
            child.setData(0, EXPANSION_ROLE, f"recent:{self._path_key(Path(path))}")
            self._style_tree_item(child)
            section.addChild(child)

    def _label_for(self, value: Any) -> str:
        if isinstance(value, Overview):
            return format_overview_display_name(value.name)
        if isinstance(value, SearchMap | TiltSeries):
            return value.name
        if isinstance(value, SearchTile):
            return value.name
        if isinstance(value, BatchPosition):
            return value.name or value.id
        if isinstance(value, Atlas):
            return self._atlas_display_label(value)
        return str(value)

    def _atlas_display_label(self, atlas: Atlas) -> str:
        """Human label for atlas list rows without changing atlas identity."""

        sample = self._sample_for_atlas(atlas)
        if sample is not None:
            return self._display_sample_name(sample.name)

        session = self._session_for_atlas(atlas)
        if session is not None:
            return f"{session.name} Atlas"

        if atlas.image_path is not None:
            parent = atlas.image_path.parent.name
            if parent:
                return f"Atlas - {parent}"
        metadata = atlas.metadata if isinstance(atlas.metadata, dict) else {}
        timestamp = metadata.get("acquisitionDateTime") or metadata.get("created")
        if timestamp:
            return f"Atlas - {timestamp}"
        return "Atlas"

    def _display_sample_name(self, name: str) -> str:
        text = re.sub(r"^\s*\d+\.\s*", "", name or "").strip()
        return text or name or "Sample"

    def _sample_for_atlas(self, atlas: Atlas) -> Sample | None:
        for sample in self._all_samples():
            if sample.atlas is atlas:
                return sample
        return None

    def _session_for_atlas(self, atlas: Atlas) -> Session | None:
        for session in self._sessions:
            if session.atlas is atlas:
                return session
        return None

    def _render_session_summary(
        self,
        *,
        animate_dashboard: bool = False,
        prepared: _PreparedSessionUi | None = None,
    ) -> None:
        """Synchronous summary + dashboard render. Used by selection-driven
        rebuilds where the user expects an immediate refresh. The staged
        loader splits the two parts across event-loop iterations via
        :meth:`_render_session_summary_tree` and
        :meth:`_render_session_summary_dashboard` so the LoadingOverlay
        animation gets a guaranteed tick between them.
        """

        warnings = self._render_session_summary_tree(prepared=prepared)
        if not self._sessions:
            return
        self._render_warning_groups(warnings)
        self.tabs.setTabText(0, "Session")
        self._update_tab_counts()
        self._render_session_summary_dashboard(animate_dashboard=animate_dashboard, prepared=prepared)

    def _render_session_summary_tree(
        self,
        *,
        prepared: _PreparedSessionUi | None = None,
    ) -> list[str]:
        """First half of :meth:`_render_session_summary`: rebuild the
        Session-tab summary tree and gather warnings. Returns the warning
        list so the caller can pass it to :meth:`_render_warning_groups`.
        """

        if not self._sessions:
            self.session_summary.clear()
            self.session_summary.addTopLevelItem(QTreeWidgetItem(["Open a Tomography session folder to view parsed session details."]))
            self.warning_tree.clear()
            self.session_dashboard.set_model(None)
            return []
        self.session_summary.setUpdatesEnabled(False)
        try:
            self.session_summary.clear()
            warnings: list[str] = [] if prepared is None else list(prepared.warnings)
            summary_items: list[QTreeWidgetItem] = []
            for session in self._sessions:
                summary_items.append(self._session_summary_item(session))
                if prepared is None:
                    warnings.extend(session_warnings(session))
            self.session_summary.addTopLevelItems(summary_items)
            self.session_summary.expandAll()
        finally:
            self.session_summary.setUpdatesEnabled(True)
        return warnings

    def _render_session_summary_dashboard(
        self,
        *,
        animate_dashboard: bool = False,
        prepared: _PreparedSessionUi | None = None,
    ) -> None:
        """Second half of :meth:`_render_session_summary`: rebuild the
        dashboard widget for the active scope. Split out so the staged
        loader can run it on a separate event-loop tick from the summary
        tree rebuild, keeping the LoadingOverlay animation smooth.
        """

        self._render_dashboard_for_scope(
            animate=animate_dashboard,
            model=prepared.dashboard_model if prepared is not None else None,
            timeline=prepared.timeline if prepared is not None else None,
            defer_heavy_cards=prepared is not None and self._loading_overlay_active(),
        )

    def _render_dashboard_for_scope(
        self,
        *,
        animate: bool = False,
        model: Any | None = None,
        timeline: SessionTimeline | None = None,
        defer_heavy_cards: bool = False,
    ) -> None:
        """Rebuild the Session-tab dashboard for the current ``_active_context``.

        Mapping:
        * ``Sample`` selected → dashboard scoped to that sample only.
        * ``LinkedSampleGroup`` selected → aggregate scope across the group.
        * ``Session`` selected → that session.
        * ``EntityGroup`` / ``None`` / project root → all loaded sessions
          (one session if only one is loaded; otherwise an aggregate).
        """

        timer = QElapsedTimer()
        timer.start()
        scope_value = self._dashboard_scope_value()
        loading_active = self._loading_overlay_active()
        defer_heavy_cards = defer_heavy_cards or loading_active
        scope_ms = timer.elapsed()
        # Count every dashboard refresh request, including cache hits, so a
        # high rebuild count during load tells us the dashboard is being
        # asked to re-render too often. Cached rebuilds are still ~0 ms of
        # presenter work, but the widget set_model path still runs.
        self._dashboard_rebuild_count += 1
        try:
            if model is None:
                cache_key = self._dashboard_scope_cache_key(scope_value)
                cached = self._dashboard_model_cache.get(cache_key)
                if cached is not None:
                    model, timeline = cached
                    model_ms = 0
                    timeline_ms = 0
                elif loading_active:
                    self._deferred_loading_dashboard_refresh = True
                    LOGGER.debug(
                        "dashboard refresh deferred while loading scope_type=%s",
                        type(scope_value).__name__,
                    )
                    return
                else:
                    model_timer = QElapsedTimer()
                    model_timer.start()
                    model = session_dashboard_model(scope_value)
                    model_ms = model_timer.elapsed()
                    timeline_timer = QElapsedTimer()
                    timeline_timer.start()
                    timeline = self._build_timeline_for_scope(scope_value)
                    timeline_ms = timeline_timer.elapsed()
                    self._dashboard_model_cache[cache_key] = (model, timeline)
            else:
                model_ms = 0
                timeline_ms = 0
                cache_key = self._dashboard_scope_cache_key(scope_value)
                self._dashboard_model_cache[cache_key] = (model, timeline)
            if loading_active:
                self._deferred_loading_dashboard = (model, timeline, animate)
                self._deferred_loading_dashboard_refresh = False
            render_timer = QElapsedTimer()
            render_timer.start()
            self.session_dashboard.set_model(
                model,
                timeline,
                animate=animate and not defer_heavy_cards,
                defer_heavy_cards=defer_heavy_cards,
                highlighted_tilt_series_id=self._dashboard_highlighted_tilt_series_id,
            )
            render_ms = render_timer.elapsed()
            self._last_dashboard_widget_build_ms = int(render_ms)
            LOGGER.debug(
                "dashboard refresh timings scope_type=%s scope_ms=%d model_ms=%d timeline_ms=%d widget_ms=%d total_ms=%d defer_heavy=%s loading_active=%s",
                type(scope_value).__name__,
                scope_ms,
                model_ms,
                timeline_ms,
                render_ms,
                timer.elapsed(),
                defer_heavy_cards,
                loading_active,
            )
        except Exception as exc:  # pragma: no cover — defensive fallback
            LOGGER.warning("Dashboard model build failed: %s", exc, exc_info=True)
            self.session_dashboard.set_model(None)

    def _dashboard_scope_cache_key(self, scope_value: Any) -> str:
        def key_for(value: Any) -> str:
            if isinstance(value, Session):
                return f"session:{session_key(value)}"
            if isinstance(value, Sample | Atlas | Overview | SearchMap | SearchTile | BatchPosition | TiltSeries):
                return f"{type(value).__name__}:{getattr(value, 'id', id(value))}"
            if isinstance(value, LinkedSampleGroup):
                return "linked-sample:" + ",".join(key_for(sample) for sample in value.samples)
            if isinstance(value, ProjectTreeGroup):
                return f"project-group:{value.key}"
            return f"{type(value).__name__}:{id(value)}"

        if isinstance(scope_value, list):
            return "sessions:" + ",".join(key_for(session) for session in scope_value)
        if isinstance(scope_value, LinkedAtlasDashboardScope):
            atlas_keys = ",".join(key_for(atlas) for _label, atlas in scope_value.atlases)
            return f"linked-atlas:{key_for(scope_value.source)}:{atlas_keys}"
        if isinstance(scope_value, DashboardEntityScope):
            tilt_keys = ",".join(key_for(tilt) for tilt in scope_value.tilt_series)
            batch_keys = ",".join(key_for(batch) for batch in scope_value.batch_positions)
            search_keys = ",".join(key_for(search_map) for search_map in scope_value.search_maps)
            return f"entity:{key_for(scope_value.source)}:{search_keys}:{batch_keys}:{tilt_keys}"
        return key_for(scope_value)

    def _dashboard_scope_value(self) -> Any:
        if isinstance(self._dashboard_focus_value, SearchMap | BatchPosition) and self._dashboard_focus_matches_tree():
            return self._dashboard_scope_for_focused_entity(self._dashboard_focus_value)
        tree_context = self._tree_item_scope_context(self.tree.currentItem()) if hasattr(self, "tree") else _ACTIVE_CONTEXT
        if (
            tree_context is not _ACTIVE_CONTEXT
            and (
                self._dashboard_scope_prefers_tree
                or self._dashboard_focus_value is not None
            )
        ):
            return self._dashboard_scope_value_for_context(tree_context)
        return self._dashboard_scope_value_for_context(self._active_context)

    def _dashboard_scope_for_focused_entity(self, value: SearchMap | BatchPosition) -> Any:
        context = self._context_for_object(value)
        samples = self._samples_for_context(context)
        sample_groups = [(sample.name, [sample]) for sample in samples]
        if isinstance(value, SearchMap):
            batches = self._batch_positions_for_search_map(value, context)
            tilts = self._tilt_series_for_search_map(value, context, batches)
            return DashboardEntityScope(
                source=value,
                search_maps=[value],
                batch_positions=batches,
                tilt_series=tilts,
                samples=samples,
                sample_groups=sample_groups,
                title=value.name or value.id,
                path=str(value.mrc_path or value.image_path or value.xml_path or ""),
                kind="search map",
            )
        batches = [value]
        tilts = self._tilt_series_for_batch_position(value, context)
        return DashboardEntityScope(
            source=value,
            search_maps=self._search_maps_for_batch_position_scope(value, context),
            batch_positions=batches,
            tilt_series=tilts,
            samples=samples,
            sample_groups=sample_groups,
            title=value.name or value.id,
            path=str(value.search_image_path or value.tracking_image_path or value.exposure_image_path or ""),
            kind="batch position",
        )

    def _dashboard_scope_value_for_context(self, ctx: Any) -> Any:
        if isinstance(ctx, ProjectTreeGroup):
            if len(ctx.sessions) == 1:
                return ctx.sessions[0]
            return list(ctx.sessions)
        if isinstance(ctx, Session):
            linked_atlases = self._linked_atlases_for_session(ctx)
            if linked_atlases:
                return LinkedAtlasDashboardScope(
                    source=ctx,
                    atlases=[(self._label_for(atlas), atlas) for atlas in linked_atlases],
                )
        if isinstance(ctx, Sample):
            linked_atlases = self._linked_atlases_for_sample(ctx)
            if linked_atlases:
                return LinkedAtlasDashboardScope(
                    source=ctx,
                    atlases=[(self._label_for(atlas), atlas) for atlas in linked_atlases],
                )
        if isinstance(ctx, (Session, Sample, LinkedSampleGroup)):
            return ctx
        # EntityGroup, None, or anything else → fall back to the whole
        # loaded set so the dashboard still renders something useful.
        if len(self._sessions) == 1:
            return self._sessions[0]
        return list(self._sessions)

    def _dashboard_focus_matches_tree(self) -> bool:
        """Return true only when the project tree itself selected the focus.

        Viewer-tab/list selections are intentionally separate from Session
        Summary scope. A tilt series should narrow the dashboard only when the
        corresponding project-tree item is the active tree selection; otherwise
        a stale list-level tilt selection can leak into broader parent scopes.
        """

        if not hasattr(self, "tree"):
            return False
        item = self.tree.currentItem()
        if item is None:
            return False
        return item.data(0, OBJECT_ROLE) is self._dashboard_focus_value

    def _build_timeline_for_scope(self, scope_value: Any) -> SessionTimeline | None:
        """Build a timeline appropriate for ``scope_value`` so the strip in the
        dashboard reflects the same scope as the rest of the cards."""

        try:
            if isinstance(scope_value, Session):
                return build_session_timeline(scope_value)
            if isinstance(scope_value, list):
                # Concatenate per-session timelines into a single SessionTimeline.
                segments: list = []
                pauses: list = []
                for session in scope_value:
                    tl = build_session_timeline(session)
                    segments.extend(tl.segments)
                    pauses.extend(tl.pauses)
                return SessionTimeline(segments=tuple(segments), pauses=tuple(pauses))
            if isinstance(scope_value, Sample):
                tilt_series = list(scope_value.tilt_series)
                return _timeline_from_tilt_series(tilt_series)
            if isinstance(scope_value, LinkedSampleGroup):
                return _timeline_from_tilt_series(list(scope_value.tilt_series))
            if isinstance(scope_value, DashboardEntityScope):
                return _timeline_from_tilt_series(list(scope_value.tilt_series))
            if isinstance(scope_value, LinkedAtlasDashboardScope):
                return self._build_timeline_for_scope(scope_value.source)
            if isinstance(scope_value, ProjectTreeGroup):
                return self._build_timeline_for_scope(list(scope_value.sessions))
        except Exception:  # pragma: no cover — defensive
            return None
        return None

    def _navigate_to_tab(self, target: str) -> None:
        if target in TAB_LABELS:
            self.tabs.setCurrentIndex(TAB_LABELS.index(target))

    def _on_dashboard_search_map_clicked(self, search_map_id: str) -> None:
        self._on_dashboard_tile_clicked("Search map", search_map_id)

    def _on_dashboard_tile_clicked(self, destination: str, item_id: str) -> None:
        """Handle a click on a status tile in any of the four count cards.

        Strategy:
        * resolve the id back to its parsed entity via ``_find_object_by_id``,
        * if the active context doesn't already include that entity, switch
          context to the entity's parent so its tab list contains the row,
        * jump to the destination tab and select the row.

        Falls back to a plain tab switch when the id can't be resolved.
        """

        if not item_id:
            self._navigate_to_tab(destination)
            return

        target = self._find_object_by_id(item_id)
        if target is None:
            self._navigate_to_tab(destination)
            return
        if isinstance(target, TiltSeries):
            self.navigate_to_tilt_series(target.id, source="dashboard tile")
            return

        # Make sure the destination tab's list actually contains this entity
        # — switch active context to the right scope first if needed. The
        # existing helper handles all four entity types.
        context = self._context_for_object(target)
        if not self._same_context(context, self._active_context):
            self._active_context = context
            self._render_viewer_tabs()
        # ``_select_viewer_object`` already switches to the right tab AND
        # selects the matching list row when present.
        self._select_viewer_object(target)

    def _on_dashboard_plot_point_clicked(self, tilt_series_id: str, frame_index: object = None) -> None:
        if not tilt_series_id:
            return
        self._pending_dashboard_point_click = (tilt_series_id, frame_index)
        self._dashboard_point_click_timer.start(QApplication.doubleClickInterval())

    def _apply_pending_dashboard_point_click(self) -> None:
        pending = self._pending_dashboard_point_click
        self._pending_dashboard_point_click = None
        if pending is None:
            return
        tilt_series_id, _frame_index = pending
        if self._dashboard_highlighted_tilt_series_id == tilt_series_id:
            self._set_dashboard_highlighted_tilt_series_id(None)
        else:
            self._set_dashboard_highlighted_tilt_series_id(tilt_series_id)

    def _on_dashboard_plot_point_double_clicked(self, tilt_series_id: str, frame_index: object = None) -> None:
        self._dashboard_point_click_timer.stop()
        self._pending_dashboard_point_click = None
        self.navigate_to_tilt_series(
            tilt_series_id,
            frame_index=frame_index,
            source="summary plot",
            set_summary_highlight=True,
        )

    def _on_dashboard_timeline_tilt_series_requested(self, tilt_series_id: str, frame_index: object = None) -> None:
        self.navigate_to_tilt_series(
            tilt_series_id,
            frame_index=frame_index,
            source="timeline",
            set_summary_highlight=False,
        )

    def _on_dashboard_highlight_clear_requested(self) -> None:
        self._dashboard_point_click_timer.stop()
        self._pending_dashboard_point_click = None
        self._set_dashboard_highlighted_tilt_series_id(None)

    def _set_dashboard_highlighted_tilt_series_id(self, tilt_series_id: str | None) -> None:
        if self._dashboard_highlighted_tilt_series_id == tilt_series_id:
            return
        total_timer = QElapsedTimer()
        total_timer.start()
        previous = self._dashboard_highlighted_tilt_series_id
        self._dashboard_highlighted_tilt_series_id = tilt_series_id
        tree_timings = self._set_project_tree_highlighted_tilt_series_id(previous, tilt_series_id)
        widget_timings: dict[str, int] = {}
        if hasattr(self, "session_dashboard"):
            widget_timings = self.session_dashboard.set_highlighted_tilt_series_id(tilt_series_id)
        LOGGER.info(
            "Highlight switch timing\n"
            "Lookup previous row:       %d ms\n"
            "Lookup new row:            %d ms\n"
            "Tree row repaint request:  %d ms\n"
            "Defocus repaint request:    %d ms\n"
            "Dose repaint request:       %d ms\n"
            "Timeline repaint request:   %d ms\n"
            "Total highlight switch:     %d ms\n"
            "Dashboard rebuilt:          no\n"
            "Plot data recomputed:       no\n"
            "Tree rebuilt:               no",
            tree_timings.get("lookup_previous_ms", 0),
            tree_timings.get("lookup_new_ms", 0),
            tree_timings.get("repaint_ms", 0),
            widget_timings.get("defocus_ms", 0),
            widget_timings.get("dose_ms", 0),
            widget_timings.get("timeline_ms", 0),
            total_timer.elapsed(),
        )

    def navigate_to_tilt_series(
        self,
        tilt_series_id: str,
        frame_index: object = None,
        *,
        source: str = "timeline",
        set_summary_highlight: bool = False,
    ) -> None:
        target = self._find_object_by_id(tilt_series_id)
        if not isinstance(target, TiltSeries):
            self.statusBar().showMessage("No linked tilt series", 2000)
            return
        context = self._context_for_object(target)
        self._dashboard_focus_value = None
        self._dashboard_scope_prefers_tree = False
        self._tilt_viewer_selected_tilt_series_id = target.id
        if set_summary_highlight:
            self._set_dashboard_highlighted_tilt_series_id(target.id)
        if context is not None and not self._same_context(context, self._active_context):
            self._active_context = context
            self._preserve_tree_root_context = False
            self._render_viewer_tabs()
            self._update_tab_counts()
            self._render_dashboard_for_scope()
        else:
            if context is not None:
                self._active_context = context
            self._render_dashboard_for_scope()
        self._select_viewer_object(target)
        frame = self._zero_based_frame_index(frame_index)
        if frame is not None:
            QTimer.singleShot(0, lambda frame=frame: self._select_tilt_series_frame(frame))
        self.statusBar().showMessage(f"Opened {self._label_for(target)} from {source}.", 2500)

    def _select_tilt_series_frame(self, frame_index: int) -> None:
        viewer = self._viewer_tabs.get("Tilt series")
        if viewer is not None:
            viewer.select_frame(frame_index)

    @staticmethod
    def _zero_based_frame_index(frame_index: object) -> int | None:
        if frame_index is None:
            return None
        try:
            value = int(frame_index)
        except (TypeError, ValueError):
            return None
        return max(0, value - 1)

    def _on_dashboard_sample_clicked(self, sample_id: str) -> None:
        target = self._find_object_by_id(sample_id)
        if target is None:
            return
        context = self._context_for_object(target)
        if isinstance(context, LinkedSampleGroup):
            self._dashboard_focus_value = None
            self._set_dashboard_highlighted_tilt_series_id(None)
            self._dashboard_scope_prefers_tree = False
            self._active_context = context
            self._preserve_tree_root_context = True
            self._render_viewer_tabs()
            self._render_dashboard_for_scope()
            self._update_tab_counts()
            self.tabs.setCurrentIndex(TAB_LABELS.index("Session"))
            self._set_context(context.label, context)
            return
        if isinstance(target, Sample):
            self._dashboard_focus_value = None
            self._set_dashboard_highlighted_tilt_series_id(None)
            self._dashboard_scope_prefers_tree = False
            self._active_context = target
            self._preserve_tree_root_context = True
            self._render_viewer_tabs()
            self._render_dashboard_for_scope()
            self._update_tab_counts()
            self.tabs.setCurrentIndex(TAB_LABELS.index("Session"))
            self._set_context(target.name, target)
            return
        self.tabs.setCurrentIndex(TAB_LABELS.index("Session"))

    def _on_dashboard_filter_requested(self, destination: str, query: str) -> None:
        if destination not in TAB_LABELS:
            return
        self.tabs.setCurrentIndex(TAB_LABELS.index(destination))
        viewer_tab = self._viewer_tabs.get(destination)
        if viewer_tab is not None:
            viewer_tab.list_filter.setText(query)
            if query:
                self.statusBar().showMessage(f"Filtered {destination.lower()} list: {query}", 3000)
            else:
                self.statusBar().showMessage(f"Showing all {destination.lower()} entries", 3000)

    def _render_warning_groups(self, warnings: list[str]) -> None:
        self.warning_tree.setUpdatesEnabled(False)
        try:
            self.warning_tree.clear()
            if not warnings:
                self.warning_tree.addTopLevelItem(QTreeWidgetItem(["No warnings"]))
                return
            groups: list[QTreeWidgetItem] = []
            for label, items in grouped_warnings(warnings).items():
                group = QTreeWidgetItem([f"{label} ({len(items)})"])
                group.addChildren([QTreeWidgetItem([warning]) for warning in items])
                groups.append(group)
            self.warning_tree.addTopLevelItems(groups)
        finally:
            self.warning_tree.setUpdatesEnabled(True)

    def _tree_selection_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        timer = QElapsedTimer()
        timer.start()
        self._repaint_project_tree_selection_rows(current, previous)
        if current is None:
            return
        value = current.data(0, OBJECT_ROLE)
        if value is None:
            self.context_panel.set_title(current.text(0))
            self.context_panel.set_text("")
            return
        self._select_from_left_tree(value, current.text(0))
        LOGGER.debug(
            "project tree selection changed label=%s value_type=%s total_ms=%d",
            current.text(0),
            type(value).__name__,
            timer.elapsed(),
        )

    def _project_tree_item_pressed(self, item: QTreeWidgetItem, column: int) -> None:
        value = item.data(0, OBJECT_ROLE)
        self._project_tree_tilt_click_timer.stop()
        self._pending_project_tree_clear_tilt_id = None
        if not isinstance(value, TiltSeries):
            return
        if self._dashboard_highlighted_tilt_series_id != value.id:
            self._set_dashboard_highlighted_tilt_series_id(value.id)
            return
        self._pending_project_tree_clear_tilt_id = value.id
        self._project_tree_tilt_click_timer.start(QApplication.doubleClickInterval())

    def _apply_pending_project_tree_highlight_clear(self) -> None:
        tilt_series_id = self._pending_project_tree_clear_tilt_id
        self._pending_project_tree_clear_tilt_id = None
        if not tilt_series_id:
            return
        if self._dashboard_highlighted_tilt_series_id == tilt_series_id:
            self._set_dashboard_highlighted_tilt_series_id(None)

    def _project_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        value = item.data(0, OBJECT_ROLE)
        if not isinstance(value, TiltSeries):
            return
        self._project_tree_tilt_click_timer.stop()
        self._pending_project_tree_clear_tilt_id = None
        self.navigate_to_tilt_series(value.id, source="project panel")

    def _repaint_project_tree_selection_rows(
        self,
        current: QTreeWidgetItem | None,
        previous: QTreeWidgetItem | None,
    ) -> None:
        viewport = self.tree.viewport()
        dirty_rect = None
        for item in (current, previous):
            if item is None:
                continue
            rect = self.tree.visualItemRect(item)
            if not rect.isValid():
                continue
            rect.setLeft(0)
            rect.setRight(viewport.width())
            rect = rect.adjusted(0, -3, 0, 3)
            dirty_rect = rect if dirty_rect is None else dirty_rect.united(rect)
        if dirty_rect is None:
            return
        viewport.update(dirty_rect)
        QTimer.singleShot(0, lambda rect=dirty_rect: viewport.update(rect))

    def _set_project_tree_highlighted_tilt_series_id(
        self,
        previous_tilt_series_id: str | None,
        tilt_series_id: str | None,
    ) -> dict[str, int]:
        timings = {"lookup_previous_ms": 0, "lookup_new_ms": 0, "repaint_ms": 0}
        if not hasattr(self, "tree"):
            return timings
        previous_items: tuple[QTreeWidgetItem, ...] = ()
        new_items: tuple[QTreeWidgetItem, ...] = ()
        if previous_tilt_series_id:
            timer = QElapsedTimer()
            timer.start()
            previous_items = tuple(self._project_tree_tilt_items_by_id.get(previous_tilt_series_id, ()))
            timings["lookup_previous_ms"] = timer.elapsed()
        if tilt_series_id:
            timer = QElapsedTimer()
            timer.start()
            new_items = tuple(self._project_tree_tilt_items_by_id.get(tilt_series_id, ()))
            timings["lookup_new_ms"] = timer.elapsed()
        timer = QElapsedTimer()
        timer.start()
        if previous_items:
            self._set_project_tree_tilt_rows_highlighted(previous_items, False)
        if new_items:
            self._set_project_tree_tilt_rows_highlighted(new_items, True)
        timings["repaint_ms"] = timer.elapsed()
        return timings

    def _set_project_tree_tilt_rows_highlighted(
        self,
        items: tuple[QTreeWidgetItem, ...],
        highlighted: bool,
    ) -> None:
        changed: list[QTreeWidgetItem] = []
        for item in items:
            self._set_project_tree_item_highlight_state(item, highlighted, changed)
        for item in changed:
            self._repaint_project_tree_selection_rows(item, item)

    def _set_project_tree_item_highlight_state(
        self,
        item: QTreeWidgetItem,
        highlighted: bool,
        changed: list[QTreeWidgetItem],
    ) -> None:
        if bool(item.data(0, HIGHLIGHT_ROLE)) == highlighted:
            return
        item.setData(0, HIGHLIGHT_ROLE, highlighted)
        item.setData(1, HIGHLIGHT_ROLE, highlighted)
        changed.append(item)

    def _sync_project_tree_highlight_state(self) -> None:
        self._set_project_tree_highlighted_tilt_series_id(None, self._dashboard_highlighted_tilt_series_id)

    def _on_project_tree_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        value = item.data(0, OBJECT_ROLE)
        group = self._project_group_for_item(item)
        menu = QMenu(self)

        if isinstance(value, ProjectTreeGroup):
            rename_action = menu.addAction("Rename group...")
            revert_action = menu.addAction("Revert group name")
            revert_action.setEnabled(value.key in self._project_display_names)
            menu.addSeparator()
            remove_action = menu.addAction("Remove group from project")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(position))
            if chosen is rename_action:
                self._rename_project_group(value)
            elif chosen is revert_action:
                self._revert_project_group_name(value)
            elif chosen is remove_action:
                self._remove_project_group(value)
            return

        if isinstance(value, Session) and group is not None and group.kind == "linked":
            label = "Remove atlas session from project" if value is group.atlas_session else "Remove data collection from project"
            remove_action = menu.addAction(label)
            chosen = menu.exec(self.tree.viewport().mapToGlobal(position))
            if chosen is remove_action:
                self._remove_session_from_project(value)

    def _project_group_for_item(self, item: QTreeWidgetItem | None) -> ProjectTreeGroup | None:
        current = item
        while current is not None:
            value = current.data(0, OBJECT_ROLE)
            if isinstance(value, ProjectTreeGroup):
                return value
            current = current.parent()
        return None

    def _rename_project_group(self, group: ProjectTreeGroup) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "Rename project group",
            "Display name",
            text=group.display_name,
        )
        if not accepted:
            return
        display_name = name.strip()
        if not display_name:
            return
        if display_name == group.automatic_name:
            self._project_display_names.pop(group.key, None)
        else:
            self._project_display_names[group.key] = display_name
        self._refresh_project_state_after_edit(select_group_key=group.key)

    def _revert_project_group_name(self, group: ProjectTreeGroup) -> None:
        self._project_display_names.pop(group.key, None)
        self._refresh_project_state_after_edit(select_group_key=group.key)

    def _remove_project_group(self, group: ProjectTreeGroup) -> None:
        session_word = "session" if len(group.sessions) == 1 else "sessions"
        response = QMessageBox.question(
            self,
            "Remove project group",
            f"Remove '{group.display_name}' and its {len(group.sessions)} loaded {session_word} from the project?\n\n"
            "Source files on disk will not be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        remove_keys = {session_key(session) for session in group.sessions}
        self._sessions = [session for session in self._sessions if session_key(session) not in remove_keys]
        self._project_display_names.pop(group.key, None)
        self._refresh_project_state_after_edit()

    def _remove_session_from_project(self, session: Session) -> None:
        self._sessions = [candidate for candidate in self._sessions if candidate is not session]
        self._refresh_project_state_after_edit()

    def _refresh_project_state_after_edit(self, *, select_group_key: str | None = None) -> None:
        self._failed_tilt_ids_cache = None
        self._dashboard_model_cache.clear()
        self._session = self._sessions[0] if self._sessions else None
        self._active_context = None
        self._rebuild_sample_index()
        self._populate_tree()
        if select_group_key:
            self._select_project_group_by_key(select_group_key)
        elif self.tree.currentItem() is not None:
            self._tree_selection_changed(self.tree.currentItem(), None)
        self._render_session_summary()
        self._render_viewer_tabs()
        self._update_session_pill()
        self._update_tab_counts()
        self._update_status_summary()
        self.refresh_button.setEnabled(bool(self._sessions))
        self.report_action.setEnabled(bool(self._sessions))

    def _select_project_group_by_key(self, key: str) -> None:
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            value = item.data(0, OBJECT_ROLE)
            if isinstance(value, ProjectTreeGroup) and value.key == key:
                self.tree.setCurrentItem(item)
                return

    def _filter_project_tree(self, text: str) -> None:
        query = text.strip().lower()

        def apply(item: QTreeWidgetItem) -> bool:
            own_match = not query or query in item.text(0).lower() or query in item.toolTip(0).lower()
            child_match = False
            for index in range(item.childCount()):
                child_match = apply(item.child(index)) or child_match
            visible = own_match or child_match
            item.setHidden(not visible)
            if query and child_match:
                item.setExpanded(True)
            return visible

        for index in range(self.tree.topLevelItemCount()):
            apply(self.tree.topLevelItem(index))

    def _display_samples(self, session: Session) -> list[Sample]:
        if len(self._sessions) > 1:
            return session.samples
        if session.kind.value == "multigrid":
            return [sample for sample in session.samples if self._sample_has_collection_data(sample)]
        return session.samples

    def _sample_has_collection_data(self, sample: Sample) -> bool:
        return bool(sample.overviews or sample.search_maps or sample.search_tiles or sample.batch_positions or sample.tilt_series)

    def _linked_sample_label(self, samples: list[Sample]) -> str:
        atlas_sample = next((sample for sample in samples if sample.atlas is not None), None)
        if atlas_sample is not None:
            return atlas_sample.name
        collection_sample = next((sample for sample in samples if self._sample_has_collection_data(sample)), samples[0])
        return collection_sample.name

    def _toggle_project_panel(self) -> None:
        visible = self.project_panel_button.isChecked()
        self.project_dock.setVisible(visible)
        self._settings.project_panel_visible = visible
        save_settings(self._settings)

    def _toggle_context_panel(self) -> None:
        visible = self.context_panel_button.isChecked()
        self.context_dock.setVisible(visible)
        self._settings.context_panel_visible = visible
        save_settings(self._settings)

    def _session_summary_item(self, session: Session) -> QTreeWidgetItem:
        item = QTreeWidgetItem([session.name])
        for line in session_summary_lines(session)[:4]:
            if line:
                item.addChild(QTreeWidgetItem([line]))
        counts_item = QTreeWidgetItem(["Counts"])
        for label, value in session_counts(session).items():
            counts_item.addChild(QTreeWidgetItem([f"{label}: {value}"]))
        item.addChild(counts_item)
        for section in session_summary_sections(session):
            item.addChild(self._summary_section_item(section))
        return item

    def _summary_section_item(self, section: SessionSummarySection) -> QTreeWidgetItem:
        item = QTreeWidgetItem([section.label])
        for label, value in section.fields:
            item.addChild(QTreeWidgetItem([f"{label}: {value}"]))
        return item

    def _render_viewer_tabs(
        self,
        *,
        prepared: _PreparedSessionUi | None = None,
        labels: list[str] | None = None,
    ) -> None:
        previous = self._suppress_viewer_context
        self._suppress_viewer_context = True
        # Pre-compute the validator-flagged failed tilt-series ids so
        # the marker pipeline can paint failed batches' overlays in
        # red (template / exposure / tracking / focus / batch dot).
        all_tilt_series = self._all_tilt_series()
        failed_tilt_ids = self._failed_tilt_ids()
        if prepared is None:
            context = self._viewer_tab_context()
            context_atlases = self._context_atlases(context)
            context_overviews = self._context_overviews(context)
            context_search_maps = self._context_search_maps(context)
            context_search_tiles = self._context_search_tiles(context)
            context_batch_positions = self._context_batch_positions(context)
            context_tilt_series = self._context_tilt_series(context)
            item_status_context = build_item_status_context(
                search_maps=context_search_maps,
                search_tiles=context_search_tiles,
                batch_positions=context_batch_positions,
                tilt_series=context_tilt_series,
            )

            tab_payloads = {
                "Atlas": _PreparedViewerTab("Atlas", context_atlases, {}, _prepared_preview_sources("Atlas", context_atlases)),
                "Overview": _PreparedViewerTab("Overview", context_overviews, {}, _prepared_preview_sources("Overview", context_overviews)),
                "Search map": _PreparedViewerTab(
                    "Search map",
                    context_search_maps,
                    {getattr(value, "id", str(index)): item_list_status(value, item_status_context) for index, value in enumerate(context_search_maps)},
                    _prepared_preview_sources("Search map", context_search_maps),
                ),
                "Search": _PreparedViewerTab("Search", [], {}, {}),
                "Batch position": _PreparedViewerTab(
                    "Batch position",
                    context_batch_positions,
                    {getattr(value, "id", str(index)): item_list_status(value, item_status_context) for index, value in enumerate(context_batch_positions)},
                    _prepared_preview_sources("Batch position", context_batch_positions),
                ),
                "Tilt series": _PreparedViewerTab(
                    "Tilt series",
                    context_tilt_series,
                    {getattr(value, "id", str(index)): item_list_status(value, item_status_context) for index, value in enumerate(context_tilt_series)},
                    _prepared_preview_sources("Tilt series", context_tilt_series),
                ),
            }
            sorted_search_tiles = sorted(context_search_tiles, key=_search_tile_sort_key)
            tab_payloads["Search"] = _PreparedViewerTab(
                "Search",
                sorted_search_tiles,
                {getattr(value, "id", str(index)): item_list_status(value, item_status_context) for index, value in enumerate(sorted_search_tiles)},
                _prepared_preview_sources("Search", sorted_search_tiles),
            )
        else:
            tab_payloads = prepared.viewer_tabs

        # The "fallback" context contains every overlayable item across every
        # loaded session. ``_marker_context_for`` narrows this to the active
        # value's parent sample so e.g. Vellio Search Maps don't bleed onto
        # the Rothia Atlas. Items with no known parent sample (top-level
        # session atlases) keep the fallback so we don't silently hide them.
        fallback_context = MarkerContext(
            overviews=tuple(self._all_overviews()),
            search_maps=tuple(self._all_search_maps()),
            batch_positions=tuple(self._all_batch_positions()),
            tilt_series=tuple(all_tilt_series),
            failed_tilt_ids=failed_tilt_ids,
        )

        def scoped(value: Any) -> list[ImageMarker]:
            return markers_for_object(
                value,
                context=self._marker_context_for(value, fallback_context),
            )

        def status_from_payload(payload: _PreparedViewerTab) -> Callable[[Any], ItemListStatus]:
            def list_status(value: Any) -> ItemListStatus:
                return payload.statuses.get(getattr(value, "id", ""), ItemListStatus(status="unknown", summary=""))

            return list_status

        try:
            labels_to_render = labels or ["Atlas", "Overview", "Search map", "Search", "Batch position", "Tilt series"]
            current_tab_label = TAB_LABELS[self.tabs.currentIndex()] if hasattr(self, "tabs") else "Session"
            for label in labels_to_render:
                payload = tab_payloads[label]
                auto_load_preview = label == current_tab_label
                if label == "Atlas":
                    self._viewer_tabs[label].set_items(
                        payload.items,
                        atlas_preview_path,
                        self._label_for,
                        markers_for=scoped,
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                    )
                elif label == "Overview":
                    self._viewer_tabs[label].set_items(
                        payload.items,
                        overview_preview_path,
                        self._label_for,
                        markers_for=scoped,
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                    )
                elif label == "Search map":
                    self._viewer_tabs[label].set_items(
                        payload.items,
                        search_map_preview_path,
                        self._label_for,
                        markers_for=scoped,
                        item_status_for=status_from_payload(payload),
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                    )
                elif label == "Search":
                    self._viewer_tabs[label].set_items(
                        payload.items,
                        search_tile_preview_path,
                        self._label_for,
                        markers_for=scoped,
                        item_status_for=status_from_payload(payload),
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                    )
                elif label == "Batch position":
                    self._viewer_tabs[label].set_items(
                        payload.items,
                        batch_position_preview_path,
                        self._label_for,
                        markers_for=scoped,
                        item_status_for=status_from_payload(payload),
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                    )
                elif label == "Tilt series":
                    self._viewer_tabs[label].set_items(
                        payload.items,
                        tilt_series_preview_path,
                        self._label_for,
                        item_status_for=status_from_payload(payload),
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                    )
        finally:
            self._suppress_viewer_context = previous

    def _select_viewer_object(self, value: Any, *, update_context: bool = True) -> None:
        previous = self._suppress_viewer_context
        self._suppress_viewer_context = previous or not update_context
        try:
            if isinstance(value, Atlas):
                self.tabs.setCurrentIndex(TAB_LABELS.index("Atlas"))
                self._viewer_tabs["Atlas"].select_object(value)
            elif isinstance(value, Overview):
                self.tabs.setCurrentIndex(TAB_LABELS.index("Overview"))
                self._viewer_tabs["Overview"].select_object(value)
            elif isinstance(value, SearchMap):
                self.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
                self._viewer_tabs["Search map"].select_object(value)
            elif isinstance(value, SearchTile):
                self.tabs.setCurrentIndex(TAB_LABELS.index("Search"))
                self._viewer_tabs["Search"].select_object(value)
            elif isinstance(value, BatchPosition):
                self.tabs.setCurrentIndex(TAB_LABELS.index("Batch position"))
                self._viewer_tabs["Batch position"].select_object(value)
            elif isinstance(value, TiltSeries):
                self.tabs.setCurrentIndex(TAB_LABELS.index("Tilt series"))
                self._viewer_tabs["Tilt series"].select_object(value)
        finally:
            self._suppress_viewer_context = previous
        if update_context:
            self._set_context(self._label_for(value), value)

    def _select_from_left_tree(self, value: Any, label: str) -> None:
        self._dashboard_scope_prefers_tree = False
        if isinstance(value, ProjectTreeGroup):
            self._dashboard_focus_value = None
            self._set_dashboard_highlighted_tilt_series_id(None)
            self._active_context = value
            self._preserve_tree_root_context = True
            self._render_viewer_tabs()
            self._render_dashboard_for_scope()
            self._update_tab_counts()
            self.tabs.setCurrentIndex(TAB_LABELS.index("Session"))
            self.context_panel.set_title(value.display_name)
            self.context_panel.set_text(self._describe_project_group(value))
            self._update_status_summary(value.display_name)
            return
        if isinstance(value, list):
            # The "Linked sessions" project-tree root carries the full session
            # list. Treat it as the aggregate scope: dashboard rebuilds, the
            # Session tab is brought forward, and the right-hand context shows
            # the multi-session summary.
            self._dashboard_focus_value = None
            self._set_dashboard_highlighted_tilt_series_id(None)
            self._active_context = None  # signals "use the project root scope"
            self._preserve_tree_root_context = True
            self._render_viewer_tabs()
            self._render_dashboard_for_scope()
            self._update_tab_counts()
            self.tabs.setCurrentIndex(TAB_LABELS.index("Session"))
            # Show a short context-panel summary listing the loaded sessions.
            summary = "\n".join(
                [f"Linked sessions ({len(value)}):"] + [f"  - {s.name}" for s in value]
            )
            self.context_panel.set_title(label)
            self.context_panel.set_text(summary)
            self._update_status_summary(label)
            return
        if isinstance(value, Session | Sample | LinkedSampleGroup):
            self._dashboard_focus_value = None
            self._set_dashboard_highlighted_tilt_series_id(None)
            self._active_context = value
            self._preserve_tree_root_context = True
            self._render_viewer_tabs()
            # Rebuild the Session-tab dashboard for the new scope and bring
            # the Session tab forward so the user lands on the summary view.
            # This is what the spec calls "the user is jumped to the summary
            # tab which displays summary information for that specific data
            # collection".
            self._render_dashboard_for_scope()
            self._update_tab_counts()
            self.tabs.setCurrentIndex(TAB_LABELS.index("Session"))
            self._set_context(label, value)
            return
        if isinstance(value, EntityGroup):
            self._dashboard_focus_value = None
            self._set_dashboard_highlighted_tilt_series_id(None)
            self._preserve_tree_root_context = False
            scope_context = self._tree_item_scope_context(self.tree.currentItem())
            if scope_context is _ACTIVE_CONTEXT:
                scope_context = self._context_for_entity_group(value)
            self._active_context = scope_context
            self._render_viewer_tabs()
            self._update_tab_counts()
            self._switch_to_group_tab(value.label)
            first_value = next(iter(value.values), None)
            if first_value is not None:
                self._select_viewer_object(first_value, update_context=False)
                self._set_context(self._label_for(first_value), first_value)
            else:
                self._set_context(label, value)
            return
        if isinstance(value, TiltSeries):
            self._dashboard_focus_value = None
            self._set_context(label, value)
            self._preserve_tree_root_context = False
            self._active_context = self._context_for_object(value)
            return
        self._dashboard_focus_value = value if isinstance(value, SearchMap | BatchPosition) else None
        if isinstance(value, SearchMap | BatchPosition):
            self._set_dashboard_highlighted_tilt_series_id(None)
        self._set_context(label, value)
        self._preserve_tree_root_context = False
        self._active_context = self._context_for_object(value)
        self._render_viewer_tabs()
        self._update_tab_counts()
        self._select_viewer_object(value)
        if isinstance(value, SearchMap | BatchPosition | TiltSeries):
            self._render_dashboard_for_scope()

    def _describe_project_group(self, group: ProjectTreeGroup) -> str:
        lines = [
            f"Project group: {group.display_name}",
            f"Automatic name: {group.automatic_name}",
            f"Type: {self._project_group_tag(group)}",
            f"Loaded sessions: {len(group.sessions)}",
        ]
        if group.kind == "linked":
            lines.append(f"Linked data collections: {len(group.collection_sessions)}")
        for session in group.sessions:
            lines.append("")
            lines.append(f"{session.name}")
            lines.append(f"  Kind: {session.kind.value}")
            lines.append(f"  Path: {session.path}")
        if group.warnings:
            lines.append("")
            lines.extend(group.warnings)
        return "\n".join(lines)

    def _viewer_item_selected(self, value: Any) -> None:
        if self._suppress_viewer_context:
            return
        if isinstance(value, TiltSeries):
            self._tilt_viewer_selected_tilt_series_id = value.id
        self._selection_state = SelectionState(
            selected_object_type=type(value).__name__,
            selected_object_id=getattr(value, "id", None),
            selected_marker_id=None,
            source="list",
        )
        self._dashboard_scope_prefers_tree = True
        if self._tree_scope_should_remain_active_for_viewer_selection(value):
            self._update_tab_counts()
            self._set_context(self._label_for(value), value)
            return
        if isinstance(value, Atlas) and self._current_viewer_scope_contains(value):
            self._update_tab_counts()
            self._set_context(self._label_for(value), value)
            return
        self._preserve_tree_root_context = False
        context = self._context_for_object(value)
        if not self._same_context(context, self._active_context):
            self._active_context = context
            self._render_viewer_tabs()
            self._update_tab_counts()
            self._select_viewer_object(value)
            if isinstance(value, TiltSeries):
                self._dashboard_focus_value = None
                self._set_context(self._label_for(value), value)
            elif isinstance(value, SearchMap | BatchPosition):
                self._dashboard_focus_value = value
                self._set_dashboard_highlighted_tilt_series_id(None)
                self._render_dashboard_for_scope()
            return
        self._active_context = context
        self._update_tab_counts()
        self._set_context(self._label_for(value), value)
        if isinstance(value, TiltSeries):
            self._dashboard_focus_value = None
        elif isinstance(value, SearchMap | BatchPosition):
            self._dashboard_focus_value = value
            self._set_dashboard_highlighted_tilt_series_id(None)
            self._render_dashboard_for_scope()

    def _current_viewer_scope_contains(self, value: Any) -> bool:
        context = self._active_context
        if context is None:
            return any(self._session_scope_contains(session, value) for session in self._sessions)
        if isinstance(context, Session):
            return self._session_scope_contains(context, value) or value in self._linked_atlases_for_session(context)
        if isinstance(context, Sample):
            return self._sample_contains(context, value) or value in self._linked_atlases_for_sample(context)
        if isinstance(context, LinkedSampleGroup):
            return any(self._sample_contains(sample, value) for sample in context.samples)
        if isinstance(context, ProjectTreeGroup):
            return group_contains_value(context, value)
        if isinstance(context, EntityGroup):
            return value in context.values
        return False

    def _tree_scope_should_remain_active_for_viewer_selection(self, value: Any) -> bool:
        if hasattr(self, "tree"):
            context = self._tree_item_scope_context(self.tree.currentItem())
            if isinstance(context, ProjectTreeGroup):
                return group_contains_value(context, value)
        if not self._preserve_tree_root_context:
            return False
        return self._current_viewer_scope_contains(value)

    def _tab_activation_preserves_broad_scope(self) -> bool:
        if hasattr(self, "tree"):
            context = self._tree_item_scope_context(self.tree.currentItem())
            if isinstance(context, ProjectTreeGroup):
                return True
        return self._preserve_tree_root_context and (
            self._active_context is None or isinstance(self._active_context, ProjectTreeGroup)
        )

    def _session_scope_contains(self, session: Session, value: Any) -> bool:
        return self._session_contains(session, value) or any(
            self._sample_contains(sample, value) for sample in session.samples
        )

    def _viewer_marker_selected(self, marker: ImageMarker) -> None:
        self._select_marker(marker, navigate=False)

    def _viewer_marker_opened(self, marker: ImageMarker) -> None:
        self._select_marker(marker, navigate=True)

    def _viewer_navigation_actions(self, value: Any) -> list[ViewerNavigationAction]:
        if isinstance(value, TiltSeries):
            return self._tilt_navigation_actions(value)
        if isinstance(value, SearchTile):
            return self._search_tile_navigation_actions(value)
        if isinstance(value, BatchPosition):
            return self._batch_position_navigation_actions(value)
        if isinstance(value, Overview):
            return self._overview_navigation_actions(value)
        if isinstance(value, SearchMap):
            return self._search_map_navigation_actions(value)
        return []

    def _tilt_navigation_actions(self, value: TiltSeries) -> list[ViewerNavigationAction]:
        if not isinstance(value, TiltSeries):
            return []
        targets, _context = self._resolve_tilt_navigation(value)
        inferred = f" Inferred group: {targets.inferred_batch_label}." if targets.inferred_batch_label else ""
        return [
            ViewerNavigationAction(
                "batch",
                "\u2197 Batch",
                enabled=targets.batch_position is not None,
                tooltip=(
                    "Open linked batch position"
                    if targets.batch_position is not None
                    else f"No linked batch position found.{inferred}"
                ),
            ),
            ViewerNavigationAction(
                "search",
                "\u2197 Search",
                enabled=targets.search_tile is not None,
                tooltip=(
                    "Open linked search tile"
                    if targets.search_tile is not None
                    else f"No linked search tile found.{inferred}"
                ),
            ),
            ViewerNavigationAction(
                "search_map",
                "\u2197 Search map",
                enabled=targets.search_map is not None,
                tooltip=(
                    "Open linked search map"
                    if targets.search_map is not None
                    else f"No linked search map found.{inferred}"
                ),
            ),
            ViewerNavigationAction(
                "overview",
                "\u2197 Overview",
                enabled=targets.overview is not None,
                tooltip=(
                    "Open linked overview"
                    if targets.overview is not None
                    else f"No linked overview found.{inferred}"
                ),
            ),
        ]

    def _search_tile_navigation_actions(self, value: SearchTile) -> list[ViewerNavigationAction]:
        marker_context = self._marker_context_for_search_tile(value)
        batch = self._batch_for_search_tile(value, marker_context)
        search_map = self._search_map_for_search_tile(value, marker_context)
        overview = self._overview_for_search_map(search_map, marker_context.overviews) if search_map is not None else None
        tilt = self._tilt_for_search_tile(value, marker_context)
        return [
            ViewerNavigationAction(
                "batch",
                "\u2197 Batch",
                enabled=batch is not None,
                tooltip="Open linked batch position" if batch is not None else "No linked batch position found.",
            ),
            ViewerNavigationAction(
                "search_map",
                "\u2197 Search map",
                enabled=search_map is not None,
                tooltip="Open linked search map" if search_map is not None else "No linked search map found.",
            ),
            ViewerNavigationAction(
                "overview",
                "\u2197 Overview",
                enabled=overview is not None,
                tooltip="Open linked overview" if overview is not None else "No linked overview found.",
            ),
            ViewerNavigationAction(
                "tilt_series",
                "\u2197 Tilt series",
                enabled=tilt is not None,
                tooltip="Open linked tilt series" if tilt is not None else "No linked tilt series found.",
            ),
        ]

    def _batch_position_navigation_actions(self, value: BatchPosition) -> list[ViewerNavigationAction]:
        marker_context = self._marker_context_for_batch_position(value)
        search_map = self._search_map_for_batch_position(value, marker_context)
        overview = self._overview_for_batch_position(value, search_map, marker_context)
        tilt_resolution = self._tilt_resolution_for_selected_batch_exposure(value, marker_context=marker_context)
        return [
            ViewerNavigationAction(
                "search_map",
                "\u2197 Search map",
                enabled=search_map is not None,
                tooltip="Open linked search map" if search_map is not None else "No linked search map found.",
            ),
            ViewerNavigationAction(
                "overview",
                "\u2197 Overview",
                enabled=overview is not None,
                tooltip="Open linked overview" if overview is not None else "No linked overview found.",
            ),
            ViewerNavigationAction(
                "tilt_series",
                "\u2197 Tilt series",
                enabled=tilt_resolution.tilt_series is not None,
                tooltip=tilt_resolution.tooltip,
            ),
        ]

    def _overview_navigation_actions(self, value: Overview) -> list[ViewerNavigationAction]:
        marker_context = self._marker_context_for_overview(value)
        search_resolution = self._search_tile_resolution_for_selected_exposure(
            value,
            marker_context=marker_context,
        )
        return [
            ViewerNavigationAction(
                "search",
                "\u2197 Search",
                enabled=search_resolution.search_tile is not None,
                tooltip=search_resolution.tooltip,
            ),
        ]

    def _search_map_navigation_actions(self, value: SearchMap) -> list[ViewerNavigationAction]:
        """Search-map jump buttons, mirroring the Search and Batch tabs.

        ``SearchMap.overview`` (parsed at session load) is the primary
        overview association; the exposure-sensitive Search jump resolves
        through the same selected-marker metadata used for double-click
        navigation.
        """

        marker_context = self._marker_context_for_search_map(value)
        overview = self._overview_for_search_map(value, marker_context.overviews)
        search_resolution = self._search_tile_resolution_for_selected_exposure(
            value,
            marker_context=marker_context,
        )
        return [
            ViewerNavigationAction(
                "search",
                "\u2197 Search",
                enabled=search_resolution.search_tile is not None,
                tooltip=search_resolution.tooltip,
            ),
            ViewerNavigationAction(
                "overview",
                "\u2197 Overview",
                enabled=overview is not None,
                tooltip="Open linked overview" if overview is not None else "No linked overview found.",
            ),
        ]

    def _viewer_navigation_requested(self, value: Any, key: str) -> None:
        if isinstance(value, TiltSeries):
            targets, marker_context = self._resolve_tilt_navigation(value)
            target_by_key = {
                "batch": targets.batch_position,
                "search": targets.search_tile,
                "search_map": targets.search_map,
                "overview": targets.overview,
            }
            target = target_by_key.get(key)
        elif isinstance(value, SearchTile):
            marker_context = self._marker_context_for_search_tile(value)
            batch = self._batch_for_search_tile(value, marker_context)
            search_map = self._search_map_for_search_tile(value, marker_context)
            overview = self._overview_for_search_map(search_map, marker_context.overviews) if search_map is not None else None
            tilt = self._tilt_for_search_tile(value, marker_context)
            targets = None
            target_by_key = {
                "batch": batch,
                "search_map": search_map,
                "overview": overview,
                "tilt_series": tilt,
            }
            target = target_by_key.get(key)
        elif isinstance(value, BatchPosition):
            marker_context = self._marker_context_for_batch_position(value)
            search_map = self._search_map_for_batch_position(value, marker_context)
            overview = self._overview_for_batch_position(value, search_map, marker_context)
            tilt_resolution = self._tilt_resolution_for_selected_batch_exposure(value, marker_context=marker_context)
            targets = None
            target_by_key = {
                "search_map": search_map,
                "overview": overview,
                "tilt_series": tilt_resolution.tilt_series,
            }
            target = target_by_key.get(key)
        elif isinstance(value, Overview):
            marker_context = self._marker_context_for_overview(value)
            search_resolution = self._search_tile_resolution_for_selected_exposure(
                value,
                marker_context=marker_context,
            )
            targets = None
            target_by_key = {
                "search": search_resolution.search_tile,
            }
            target = target_by_key.get(key)
        elif isinstance(value, SearchMap):
            marker_context = self._marker_context_for_search_map(value)
            overview = self._overview_for_search_map(value, marker_context.overviews)
            search_resolution = self._search_tile_resolution_for_selected_exposure(
                value,
                marker_context=marker_context,
            )
            targets = None
            target_by_key = {
                "search": search_resolution.search_tile,
                "overview": overview,
            }
            target = target_by_key.get(key)
        else:
            return
        if target is None:
            self.statusBar().showMessage(f"No linked {key.replace('_', ' ')} found for {value.name}.")
            return

        if isinstance(value, BatchPosition) and key == "tilt_series" and isinstance(target, TiltSeries):
            self.navigate_to_tilt_series(target.id, source="batch position exposure", set_summary_highlight=False)
            return

        self._preserve_tree_root_context = False
        context = self._context_for_object(target)
        if context is not None:
            self._active_context = context
            self._render_viewer_tabs()
        self._select_viewer_object(target)

        tab_label = tab_label_for_object(target)
        marker_id: str | None = None
        if isinstance(value, TiltSeries):
            marker_id = self._marker_id_for_tilt_navigation(
                target,
                value,
                batch=targets.batch_position,
                context=marker_context,
            )
        elif isinstance(value, SearchTile):
            marker_id = self._marker_id_for_search_tile_navigation(target, value, context=marker_context)
        elif isinstance(value, BatchPosition):
            marker_id = self._marker_id_for_batch_position_navigation(target, value, context=marker_context)
        elif isinstance(value, Overview) and key == "search" and isinstance(target, SearchTile):
            marker_id = self._marker_id_for_exposure_search_navigation(
                target,
                search_resolution.marker,
                context=marker_context,
            )
        elif isinstance(value, SearchMap):
            if key == "search" and isinstance(target, SearchTile):
                marker_id = self._marker_id_for_exposure_search_navigation(
                    target,
                    search_resolution.marker,
                    context=marker_context,
                )
            else:
                marker_id = self._marker_id_for_search_map_navigation(target, value, context=marker_context)
        if tab_label is not None and marker_id:
            self._viewer_tabs[tab_label].select_marker(marker_id)
        self.statusBar().showMessage(f"Opened {self._label_for(target)} linked to {value.name}.")

    def _resolve_tilt_navigation(
        self,
        tilt: TiltSeries,
    ):
        marker_context = self._marker_context_for_tilt(tilt)
        targets = resolve_tilt_series_navigation_targets(
            tilt,
            batch_positions=marker_context.batch_positions,
            search_maps=marker_context.search_maps,
            overviews=marker_context.overviews,
            search_tiles=self._context_search_tiles(self._context_for_object(tilt)),
        )
        search_map = targets.search_map or self._search_map_from_tilt_marker(tilt, marker_context)
        overview = targets.overview
        if overview is None and search_map is not None:
            overview = self._overview_for_search_map(search_map, marker_context.overviews)
        if overview is None:
            overview = self._overview_from_tilt_marker(tilt, marker_context)
        if search_map is targets.search_map and overview is targets.overview:
            return targets, marker_context
        return type(targets)(
            batch_position=targets.batch_position,
            search_tile=targets.search_tile,
            search_map=search_map,
            overview=overview,
            inferred_batch_label=targets.inferred_batch_label,
        ), marker_context

    def _marker_context_for_tilt(self, tilt: TiltSeries) -> MarkerContext:
        failed_tilt_ids = self._failed_tilt_ids()
        fallback_context = MarkerContext(
            overviews=tuple(self._all_overviews()),
            search_maps=tuple(self._all_search_maps()),
            batch_positions=tuple(self._all_batch_positions()),
            tilt_series=tuple(self._all_tilt_series()),
            failed_tilt_ids=failed_tilt_ids,
        )
        return self._marker_context_for(tilt, fallback_context)

    def _marker_context_for_search_tile(self, search_tile: SearchTile) -> MarkerContext:
        failed_tilt_ids = self._failed_tilt_ids()
        fallback_context = MarkerContext(
            overviews=tuple(self._all_overviews()),
            search_maps=tuple(self._all_search_maps()),
            batch_positions=tuple(self._all_batch_positions()),
            tilt_series=tuple(self._all_tilt_series()),
            failed_tilt_ids=failed_tilt_ids,
        )
        return self._marker_context_for(search_tile, fallback_context)

    def _marker_context_for_batch_position(self, batch_position: BatchPosition) -> MarkerContext:
        failed_tilt_ids = self._failed_tilt_ids()
        fallback_context = MarkerContext(
            overviews=tuple(self._all_overviews()),
            search_maps=tuple(self._all_search_maps()),
            batch_positions=tuple(self._all_batch_positions()),
            tilt_series=tuple(self._all_tilt_series()),
            failed_tilt_ids=failed_tilt_ids,
        )
        return self._marker_context_for(batch_position, fallback_context)

    def _marker_context_for_overview(self, overview: Overview) -> MarkerContext:
        failed_tilt_ids = self._failed_tilt_ids()
        fallback_context = MarkerContext(
            overviews=tuple(self._all_overviews()),
            search_maps=tuple(self._all_search_maps()),
            batch_positions=tuple(self._all_batch_positions()),
            tilt_series=tuple(self._all_tilt_series()),
            failed_tilt_ids=failed_tilt_ids,
        )
        return self._marker_context_for(overview, fallback_context)

    def _marker_context_for_search_map(self, search_map: SearchMap) -> MarkerContext:
        failed_tilt_ids = self._failed_tilt_ids()
        fallback_context = MarkerContext(
            overviews=tuple(self._all_overviews()),
            search_maps=tuple(self._all_search_maps()),
            batch_positions=tuple(self._all_batch_positions()),
            tilt_series=tuple(self._all_tilt_series()),
            failed_tilt_ids=failed_tilt_ids,
        )
        return self._marker_context_for(search_map, fallback_context)

    def _batch_for_search_tile(
        self,
        search_tile: SearchTile,
        context: MarkerContext,
    ) -> BatchPosition | None:
        if search_tile.batch_position_id:
            return next(
                (batch for batch in context.batch_positions if batch.id == search_tile.batch_position_id),
                None,
            )
        return None

    def _search_map_for_search_tile(
        self,
        search_tile: SearchTile,
        context: MarkerContext,
    ) -> SearchMap | None:
        if search_tile.search_map_id:
            return next(
                (search_map for search_map in context.search_maps if search_map.id == search_tile.search_map_id),
                None,
            )
        return None

    def _tilt_for_search_tile(
        self,
        search_tile: SearchTile,
        context: MarkerContext,
    ) -> TiltSeries | None:
        linked_ids = set(search_tile.linked_tilt_series_ids or [])
        if not linked_ids:
            return None
        return next((tilt for tilt in context.tilt_series if tilt.id in linked_ids), None)

    def _search_map_for_batch_position(
        self,
        batch_position: BatchPosition,
        context: MarkerContext,
    ) -> SearchMap | None:
        if batch_position.linked_search_map_id:
            match = next(
                (search_map for search_map in context.search_maps if search_map.id == batch_position.linked_search_map_id),
                None,
            )
            if match is not None:
                return match
        batch_id = batch_position.id
        tilt_ids = set(batch_position.linked_tilt_series_ids or [])
        for search_map in context.search_maps:
            if batch_id in (search_map.linked_batch_position_ids or []):
                return search_map
            if tilt_ids and tilt_ids.intersection(search_map.linked_tilt_series_ids or []):
                return search_map
        for search_map in context.search_maps:
            markers = markers_for_object(search_map, context=context)
            if self._preferred_batch_marker(markers, batch_position) is not None:
                return search_map
        return None

    def _overview_for_batch_position(
        self,
        batch_position: BatchPosition,
        search_map: SearchMap | None,
        context: MarkerContext,
    ) -> Overview | None:
        if batch_position.linked_overview_id:
            match = next(
                (overview for overview in context.overviews if overview.id == batch_position.linked_overview_id),
                None,
            )
            if match is not None:
                return match
        if search_map is not None:
            overview = self._overview_for_search_map(search_map, context.overviews)
            if overview is not None:
                return overview
        for overview in context.overviews:
            markers = markers_for_object(overview, context=context)
            if self._preferred_batch_marker(markers, batch_position) is not None:
                return overview
        return None

    def _failed_tilt_ids(self) -> frozenset[str]:
        if self._failed_tilt_ids_cache is not None:
            return self._failed_tilt_ids_cache
        from tomography_session_browser.services.tilt_series_validation import (
            STATUS_FAILED,
            validate_session_tilt_series,
        )

        self._failed_tilt_ids_cache = frozenset(
            validation.tilt_series_id
            for validation in validate_session_tilt_series(self._all_tilt_series())
            if validation.status == STATUS_FAILED
        )
        return self._failed_tilt_ids_cache

    def _search_map_from_tilt_marker(
        self,
        tilt: TiltSeries,
        context: MarkerContext,
    ) -> SearchMap | None:
        for search_map in context.search_maps:
            markers = markers_for_object(search_map, context=context)
            if self._preferred_navigation_marker(markers, tilt, batch=None) is not None:
                return search_map
        return None

    def _overview_from_tilt_marker(
        self,
        tilt: TiltSeries,
        context: MarkerContext,
    ) -> Overview | None:
        for overview in context.overviews:
            markers = markers_for_object(overview, context=context)
            if self._preferred_navigation_marker(markers, tilt, batch=None) is not None:
                return overview
        return None

    def _overview_for_search_map(
        self,
        search_map: SearchMap,
        overviews: tuple[Overview, ...],
    ) -> Overview | None:
        if search_map.overview is not None:
            return search_map.overview
        return next(
            (overview for overview in overviews if search_map.id in (overview.linked_search_map_ids or [])),
            None,
        )

    def _marker_id_for_tilt_navigation(
        self,
        target: Any,
        tilt: TiltSeries,
        *,
        batch: BatchPosition | None,
        context: MarkerContext,
    ) -> str | None:
        markers = markers_for_object(target, context=context)
        marker = self._preferred_navigation_marker(markers, tilt, batch=batch)
        return marker.id if marker is not None else None

    def _marker_id_for_search_tile_navigation(
        self,
        target: Any,
        search_tile: SearchTile,
        *,
        context: MarkerContext,
    ) -> str | None:
        if isinstance(target, TiltSeries):
            return None
        markers = markers_for_object(target, context=context)
        linked_tilt = self._tilt_for_search_tile(search_tile, context)
        batch = self._batch_for_search_tile(search_tile, context)
        if linked_tilt is not None:
            marker = self._preferred_navigation_marker(markers, linked_tilt, batch=batch)
            if marker is not None:
                return marker.id
        if batch is None:
            return None
        marker = next((item for item in markers if item.linked_object_id == batch.id), None)
        return marker.id if marker is not None else None

    def _marker_id_for_batch_position_navigation(
        self,
        target: Any,
        batch_position: BatchPosition,
        *,
        context: MarkerContext,
    ) -> str | None:
        markers = markers_for_object(target, context=context)
        marker = self._preferred_batch_marker(markers, batch_position)
        return marker.id if marker is not None else None

    def _marker_id_for_search_map_navigation(
        self,
        target: Any,
        search_map: SearchMap,
        *,
        context: MarkerContext,
    ) -> str | None:
        """Find the marker on ``target`` that represents ``search_map``.

        The overview tab draws a region marker per linked search map; we
        pick the one whose ``linked_object_id`` matches so the user lands
        on the right region instead of a generic centred overview view.
        """

        if not isinstance(target, Overview):
            return None
        markers = markers_for_object(target, context=context)
        marker = next(
            (item for item in markers if item.linked_object_id == search_map.id),
            None,
        )
        return marker.id if marker is not None else None

    def _marker_id_for_exposure_search_navigation(
        self,
        target: SearchTile,
        source_marker: ImageMarker | None,
        *,
        context: MarkerContext,
    ) -> str | None:
        if source_marker is None:
            return None
        markers = markers_for_object(target, context=context)
        marker_key = self._marker_exposure_match_key(source_marker)
        if marker_key:
            exact = [
                marker
                for marker in markers
                if marker.marker_type in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
                and self._marker_exposure_match_key(marker) == marker_key
            ]
            if len(exact) == 1:
                return exact[0].id
        area_name = str(target.metadata.get("ExposureAreaName") or "").strip().casefold()
        if area_name:
            match = next(
                (
                    marker
                    for marker in markers
                    if marker.marker_type in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
                    and str(marker.metadata.get("area_name") or "").strip().casefold() == area_name
                ),
                None,
            )
            if match is not None:
                return match.id
        return None

    def _preferred_batch_marker(
        self,
        markers: list[ImageMarker],
        batch_position: BatchPosition,
    ) -> ImageMarker | None:
        linked = [marker for marker in markers if marker.linked_object_id == batch_position.id]
        for marker_type in (
            MarkerType.EXPOSURE_AREA,
            MarkerType.CAMERA_FOV,
            MarkerType.TEMPLATE_AREA,
            MarkerType.BATCH_POSITION,
            MarkerType.TRACKING_AREA,
            MarkerType.FOCUS_AREA,
        ):
            match = next((marker for marker in linked if marker.marker_type == marker_type), None)
            if match is not None:
                return match
        if linked:
            return linked[0]

        batch_key = _normalise_exposure_key(batch_position.name or batch_position.id)
        if not batch_key:
            return None
        for marker_type in (
            MarkerType.EXPOSURE_AREA,
            MarkerType.CAMERA_FOV,
            MarkerType.BATCH_POSITION,
            MarkerType.TEMPLATE_AREA,
            MarkerType.TRACKING_AREA,
            MarkerType.FOCUS_AREA,
        ):
            for marker in markers:
                if marker.marker_type != marker_type:
                    continue
                marker_key = self._marker_exposure_match_key(marker)
                if marker_key == batch_key:
                    return marker
        return None

    def _preferred_navigation_marker(
        self,
        markers: list[ImageMarker],
        tilt: TiltSeries,
        *,
        batch: BatchPosition | None,
    ) -> ImageMarker | None:
        direct = next((marker for marker in markers if marker.linked_object_id == tilt.id), None)
        if direct is not None:
            return direct
        exposure = self._exact_exposure_marker(markers, tilt, batch=batch)
        if exposure is not None:
            return exposure
        if batch is None:
            return None
        linked = [marker for marker in markers if marker.linked_object_id == batch.id]
        for marker_type in (
            MarkerType.EXPOSURE_AREA,
            MarkerType.TEMPLATE_AREA,
            MarkerType.BATCH_POSITION,
            MarkerType.TRACKING_AREA,
            MarkerType.FOCUS_AREA,
        ):
            match = next((marker for marker in linked if marker.marker_type == marker_type), None)
            if match is not None:
                return match
        return next(iter(linked), None)

    def _exact_exposure_marker(
        self,
        markers: list[ImageMarker],
        tilt: TiltSeries,
        *,
        batch: BatchPosition | None,
    ) -> ImageMarker | None:
        candidates = self._tilt_exposure_match_keys(tilt)
        if not candidates:
            return None
        exposure_markers = [
            marker
            for marker in markers
            if marker.marker_type == MarkerType.EXPOSURE_AREA
            and (batch is None or marker.linked_object_id == batch.id)
        ]
        exact = [
            marker
            for marker in exposure_markers
            if self._marker_exposure_match_key(marker) in candidates
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            LOGGER.debug(
                "multiple exact exposure markers matched tilt=%s candidates=%s markers=%s",
                tilt.name,
                sorted(candidates),
                [marker.id for marker in exact],
            )
            return exact[0]
        if batch is not None and batch.name and _normalise_exposure_key(tilt.name) == _normalise_exposure_key(batch.name):
            return next(
                (
                    marker
                    for marker in exposure_markers
                    if _normalise_exposure_key(marker.metadata.get("area_name") or _raw_marker_name(marker)) == "exposure"
                ),
                None,
            )
        LOGGER.debug(
            "no exact exposure marker matched tilt=%s candidates=%s available=%s",
            tilt.name,
            sorted(candidates),
            [self._marker_exposure_match_key(marker) for marker in exposure_markers],
        )
        return None

    def _tilt_exposure_match_keys(self, tilt: TiltSeries) -> set[str]:
        raw_values = {
            tilt.name,
            tilt.id,
            Path(tilt.id).stem,
            tilt.mrc_path.stem if tilt.mrc_path is not None else None,
        }
        return {key for value in raw_values if (key := _normalise_exposure_key(value))}

    def _marker_exposure_match_key(self, marker: ImageMarker) -> str:
        raw_values = (
            marker.metadata.get("linked_area_name"),
            marker.metadata.get("area_name"),
            _raw_marker_name(marker),
            marker.label,
            marker.id.rsplit(":", 1)[-1] if marker.id else None,
        )
        for value in raw_values:
            key = _normalise_exposure_key(value)
            if key and key != "exposure":
                return key
        return _normalise_exposure_key(marker.metadata.get("area_name") or _raw_marker_name(marker))

    def _should_open_tilt_from_exposure_marker(self, marker: ImageMarker) -> bool:
        if TAB_LABELS[self.tabs.currentIndex()] not in {"Search", "Batch position"}:
            return False
        if marker.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV, MarkerType.LINK_LINE}:
            return False
        return self._batch_for_exposure_marker(marker) is not None

    def _should_open_search_tile_from_exposure_marker(self, marker: ImageMarker) -> bool:
        if TAB_LABELS[self.tabs.currentIndex()] not in {"Overview", "Search map"}:
            return False
        if marker.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}:
            return False
        return self._batch_for_exposure_marker(marker) is not None

    def _batch_for_exposure_marker(self, marker: ImageMarker) -> BatchPosition | None:
        marker_metadata = marker.metadata or {}
        for object_id in (
            marker_metadata.get("batch_id"),
            marker.linked_object_id,
            marker.source_object_id,
        ):
            value = self._find_object_by_id(object_id)
            if isinstance(value, BatchPosition):
                return value
        return None

    def _search_tile_resolution_for_selected_exposure(
        self,
        source: Overview | SearchMap,
        *,
        marker_context: MarkerContext,
    ) -> _ExposureSearchTileResolution:
        viewer = self._viewer_tabs.get(tab_label_for_object(source) or "")
        marker_id = viewer.selected_marker_id() if viewer is not None else None
        if not marker_id:
            return _ExposureSearchTileResolution(
                None,
                None,
                "Select an exposure area to jump to its linked search item.",
            )
        marker = next(
            (item for item in markers_for_object(source, context=marker_context) if item.id == marker_id),
            None,
        )
        if marker is None or marker.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}:
            return _ExposureSearchTileResolution(
                marker,
                None,
                "Select an exposure area to jump to its linked search item.",
            )
        search_tile = self._search_tile_for_exposure_marker(marker)
        if search_tile is None:
            return _ExposureSearchTileResolution(
                marker,
                None,
                "No linked search item found for this exposure area.",
            )
        return _ExposureSearchTileResolution(
            marker,
            search_tile,
            "Jump to the search item associated with this exposure area.",
        )

    def _tilt_resolution_for_selected_batch_exposure(
        self,
        batch: BatchPosition,
        *,
        marker_context: MarkerContext | None = None,
    ) -> _ExposureTiltResolution:
        marker_context = marker_context or self._marker_context_for_batch_position(batch)
        viewer = self._viewer_tabs.get("Batch position")
        marker_id = viewer.selected_marker_id() if viewer is not None else None
        if not marker_id:
            return _ExposureTiltResolution(
                None,
                None,
                "No tilt series is associated with the selected exposure area.",
            )
        marker = next(
            (item for item in markers_for_object(batch, context=marker_context) if item.id == marker_id),
            None,
        )
        if marker is None:
            return _ExposureTiltResolution(
                None,
                None,
                "No tilt series is associated with the selected exposure area.",
            )
        return self._tilt_resolution_for_exposure_marker(marker, batch=batch, marker_context=marker_context)

    def _tilt_resolution_for_exposure_marker(
        self,
        marker: ImageMarker,
        *,
        batch: BatchPosition | None = None,
        marker_context: MarkerContext | None = None,
    ) -> _ExposureTiltResolution:
        if marker.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}:
            return _ExposureTiltResolution(
                None,
                None,
                "No tilt series is associated with the selected exposure area.",
            )
        batch = batch or self._batch_for_exposure_marker(marker)
        if batch is None:
            return _ExposureTiltResolution(
                None,
                None,
                "No tilt series is associated with the selected exposure area.",
            )
        exposure = self._selected_exposure_area(marker, batch)
        marker_context = marker_context or self._marker_context_for_batch_position(batch)

        explicit_tilt = self._explicit_tilt_for_exposure_marker(marker)
        if explicit_tilt is not None:
            exposure.linked_tilt_series_id = explicit_tilt.id
            return _ExposureTiltResolution(
                exposure,
                explicit_tilt,
                "Jump to the tilt series associated with this exposure area.",
            )

        linked_ids = list(batch.linked_tilt_series_ids or [])
        linked_id_set = set(linked_ids)
        context_tilts = list(marker_context.tilt_series)
        linked_candidates = [
            tilt
            for tilt in context_tilts
            if tilt.id in linked_id_set or tilt.linked_batch_position_id == batch.id
        ]
        all_candidates = linked_candidates or context_tilts
        if not all_candidates:
            return _ExposureTiltResolution(
                exposure,
                None,
                "No tilt series is associated with the selected exposure area.",
            )

        expected_keys = self._expected_tilt_keys_for_exposure(batch, exposure)
        if expected_keys:
            linked_matches = [
                tilt
                for tilt in linked_candidates
                if self._tilt_exposure_match_keys(tilt).intersection(expected_keys)
            ]
            if len(linked_matches) == 1:
                exposure.linked_tilt_series_id = linked_matches[0].id
                return _ExposureTiltResolution(exposure, linked_matches[0], "Jump to the tilt series associated with this exposure area.")
            if len(linked_matches) > 1:
                return _ExposureTiltResolution(exposure, None, "Multiple tilt series candidates found for this exposure area.", ambiguous=True)
            name_matches = [
                tilt
                for tilt in context_tilts
                if self._tilt_exposure_match_keys(tilt).intersection(expected_keys)
            ]
            if len(name_matches) == 1:
                exposure.linked_tilt_series_id = name_matches[0].id
                return _ExposureTiltResolution(exposure, name_matches[0], "Jump to the tilt series associated with this exposure area.")
            if len(name_matches) > 1:
                return _ExposureTiltResolution(exposure, None, "Multiple tilt series candidates found for this exposure area.", ambiguous=True)

        marker_key = self._marker_exposure_match_key(marker)
        if marker_key:
            exact = [
                tilt
                for tilt in all_candidates
                if marker_key in self._tilt_exposure_match_keys(tilt)
            ]
            if len(exact) == 1:
                exposure.linked_tilt_series_id = exact[0].id
                return _ExposureTiltResolution(exposure, exact[0], "Jump to the tilt series associated with this exposure area.")
            if len(exact) > 1:
                return _ExposureTiltResolution(exposure, None, "Multiple tilt series candidates found for this exposure area.", ambiguous=True)

        ordered = self._linked_tilt_candidates_in_batch_order(batch, linked_candidates)
        if exposure.exposure_index is not None and 0 <= exposure.exposure_index < len(ordered):
            tilt = ordered[exposure.exposure_index]
            exposure.linked_tilt_series_id = tilt.id
            return _ExposureTiltResolution(exposure, tilt, "Jump to the tilt series associated with this exposure area.")
        if exposure.exposure_index in (None, 0) and len(all_candidates) == 1:
            exposure.linked_tilt_series_id = all_candidates[0].id
            return _ExposureTiltResolution(exposure, all_candidates[0], "Jump to the tilt series associated with this exposure area.")
        if len(all_candidates) > 1:
            return _ExposureTiltResolution(exposure, None, "Multiple tilt series candidates found for this exposure area.", ambiguous=True)
        return _ExposureTiltResolution(
            exposure,
            None,
            "No tilt series is associated with the selected exposure area.",
        )

    def _selected_exposure_area(self, marker: ImageMarker, batch: BatchPosition) -> _SelectedExposureArea:
        index = self._exposure_index_for_marker(marker, batch)
        exposure_type = str(marker.metadata.get("exposure_type") or ("main" if index == 0 else "additional"))
        return _SelectedExposureArea(
            batch_position_id=batch.id,
            exposure_index=index,
            exposure_area_id=str(marker.metadata.get("exposure_marker_id") or marker.id),
            exposure_type=exposure_type,
        )

    def _exposure_index_for_marker(self, marker: ImageMarker, batch: BatchPosition) -> int | None:
        raw_index = marker.metadata.get("exposure_index")
        try:
            if raw_index is not None:
                return int(raw_index)
        except (TypeError, ValueError):
            pass
        area_name = str(marker.metadata.get("area_name") or _raw_marker_name(marker) or "").strip()
        if area_name.casefold() == "exposure":
            return 0
        match = re.fullmatch(r"exposure\s*(\d+)", area_name.strip(), flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
        batch_key = _normalise_exposure_key(batch.name or batch.id)
        marker_key = self._marker_exposure_match_key(marker)
        if marker_key and batch_key:
            if marker_key == batch_key:
                return 0
            suffix = marker_key.removeprefix(f"{batch_key}_")
            if suffix != marker_key and suffix.isdigit():
                return max(0, int(suffix) - 1)
        return None

    def _expected_tilt_keys_for_exposure(self, batch: BatchPosition, exposure: _SelectedExposureArea) -> set[str]:
        batch_key = _normalise_exposure_key(batch.name or batch.id)
        if not batch_key or exposure.exposure_index is None:
            return set()
        if exposure.exposure_index == 0:
            return {batch_key}
        return {f"{batch_key}_{exposure.exposure_index + 1}"}

    def _linked_tilt_candidates_in_batch_order(
        self,
        batch: BatchPosition,
        candidates: list[TiltSeries],
    ) -> list[TiltSeries]:
        order = {tilt_id: index for index, tilt_id in enumerate(batch.linked_tilt_series_ids or [])}
        return sorted(
            candidates,
            key=lambda tilt: (
                order.get(tilt.id, 10**9),
                natural_key(tilt.name or tilt.id),
            ),
        )

    def _explicit_tilt_for_exposure_marker(self, marker: ImageMarker) -> TiltSeries | None:
        keys = (
            "tilt_series_id",
            "TiltSeriesId",
            "TiltSeriesID",
            "linked_tilt_series_id",
            "LinkedTiltSeriesId",
        )
        metadata = marker.metadata or {}
        raw = metadata.get("raw")
        values = [metadata.get(key) for key in keys]
        if isinstance(raw, dict):
            values.extend(raw.get(key) for key in keys)
        for value in values:
            if not value:
                continue
            target = self._find_object_by_id(str(value))
            if isinstance(target, TiltSeries):
                return target
        return None

    def _tilt_for_exposure_marker(self, marker: ImageMarker) -> TiltSeries | None:
        batch = self._batch_for_exposure_marker(marker)
        if batch is None:
            return None
        return self._tilt_resolution_for_exposure_marker(marker, batch=batch).tilt_series

    def _search_tile_for_search_map_exposure_marker(self, marker: ImageMarker) -> SearchTile | None:
        return self._search_tile_for_exposure_marker(marker)

    def _search_tile_for_exposure_marker(self, marker: ImageMarker) -> SearchTile | None:
        batch = self._batch_for_exposure_marker(marker)
        if batch is None:
            return None
        source = self._find_object_by_id(marker.source_object_id)
        if not isinstance(source, Overview | SearchMap):
            current_label = TAB_LABELS[self.tabs.currentIndex()]
            viewer = self._viewer_tabs.get(current_label)
            current = getattr(viewer, "_current_value", None) if viewer is not None else None
            source = current if isinstance(current, Overview | SearchMap) else None
        context = self._context_for_object(source or batch)
        search_map_ids = self._search_map_ids_for_exposure_search(source, batch, context)
        candidates = [
            tile
            for tile in self._context_search_tiles(context)
            if tile.batch_position_id == batch.id
            and (not search_map_ids or tile.search_map_id in search_map_ids)
        ]
        if not candidates:
            return None

        area_keys = {
            key
            for value in (
                marker.metadata.get("area_name"),
                marker.metadata.get("linked_area_name"),
                _raw_marker_name(marker),
                marker.label,
            )
            if (key := _normalise_exposure_key(value))
        }
        if area_keys:
            exact = [
                tile
                for tile in candidates
                if area_keys.intersection(
                    {
                        key
                        for value in (
                            tile.metadata.get("ExposureAreaName"),
                            tile.metadata.get("ExposureDisplayName"),
                            tile.name,
                        )
                        if (key := _normalise_exposure_key(value))
                    }
                )
            ]
            if len(exact) == 1:
                return exact[0]
            if len(exact) > 1:
                return None

        exposure_index = self._exposure_index_for_marker(marker, batch)
        primary = [tile for tile in candidates if tile.metadata.get("ExposureIsPrimary") is True]
        if exposure_index in (None, 0) and len(primary) == 1:
            return primary[0]
        return candidates[0] if len(candidates) == 1 else None

    def _search_map_ids_for_exposure_search(
        self,
        source: Overview | SearchMap | None,
        batch: BatchPosition,
        context: Session | Sample | LinkedSampleGroup | ProjectTreeGroup | None,
    ) -> set[str]:
        if isinstance(source, SearchMap):
            return {source.id}
        search_map_ids: set[str] = set()
        if batch.linked_search_map_id:
            search_map_ids.add(batch.linked_search_map_id)
        if isinstance(source, Overview):
            search_map_ids.update(source.linked_search_map_ids or [])
            for search_map in self._context_search_maps(context):
                if search_map.overview is source:
                    search_map_ids.add(search_map.id)
                    continue
                if search_map.overview is not None and search_map.overview.id == source.id:
                    search_map_ids.add(search_map.id)
        return search_map_ids

    def _select_marker(self, marker: ImageMarker, *, navigate: bool) -> None:
        open_exposure_search_tile = navigate and self._should_open_search_tile_from_exposure_marker(marker)
        open_exposure_tilt = navigate and not open_exposure_search_tile and self._should_open_tilt_from_exposure_marker(marker)
        if open_exposure_search_tile:
            linked = self._search_tile_for_exposure_marker(marker)
        elif open_exposure_tilt:
            linked = self._tilt_for_exposure_marker(marker)
        else:
            linked = None
        if linked is None and not open_exposure_tilt and not open_exposure_search_tile:
            linked = self._find_object_by_id(marker.linked_object_id)
        self._selection_state = SelectionState(
            selected_object_type=type(linked).__name__ if linked is not None else None,
            selected_object_id=getattr(linked, "id", None) or marker.linked_object_id,
            selected_marker_id=marker.id,
            source="marker-open" if navigate else "marker",
        )
        if linked is None:
            self.context_panel.set_title(marker.label or "Marker")
            status_text = (
                "No linked tilt series found for this exposure area."
                if open_exposure_tilt
                else "No linked search item found for this exposure area."
                if open_exposure_search_tile
                else "Marker link could not be resolved."
            )
            lines = [
                marker.tooltip or status_text,
                "",
                f"Type: {marker.marker_type}",
                f"Linked object: {marker.linked_object_id or 'none'}",
                "Status: unresolved",
            ]
            self.context_panel.set_text("\n".join(lines))
            self.statusBar().showMessage(status_text)
            return
        if not navigate:
            self._select_visible_tab_object(linked)
            self._set_context(self._label_for(linked), linked)
            self.statusBar().showMessage(f"Selected {self._label_for(linked)} from marker.")
            return
        self._preserve_tree_root_context = False
        self._dashboard_scope_prefers_tree = True
        context = self._context_for_object(linked)
        if context is not None:
            self._active_context = context
            self._render_viewer_tabs()
        self._select_viewer_object(linked)
        self.statusBar().showMessage(f"Opened {self._label_for(linked)} from marker.")

    def _viewer_frame_changed(self, value: Any, frame_index: int, frame_count: int) -> None:
        if self._suppress_viewer_context or self._preserve_tree_root_context:
            return
        if not isinstance(value, TiltSeries):
            return
        self.context_panel.set_title(self._label_for(value))
        lines = [
            describe_object(value),
            "",
            "Current frame:",
            f"  Frame: {frame_index + 1} of {frame_count}",
            f"  Raw stack index: {frame_index}",
        ]
        angle = self._tilt_angle_for(value, frame_index)
        source = self._frame_angle_source(value, frame_count)
        if angle is not None:
            lines.append(f"  Tilt angle: {angle}")
        lines.append(f"  Angle source: {source}")
        frame_metadata = self._frame_metadata_for(value, frame_index)
        if frame_metadata:
            beam_shift = _compact_pair(frame_metadata.get("beam_shift_x"), frame_metadata.get("beam_shift_y"))
            if beam_shift:
                lines.append(f"  Beam shift X/Y: {beam_shift}")
        self.context_panel.set_text("\n".join(lines))

    def _set_context(self, label: str, value: Any) -> None:
        timer = QElapsedTimer()
        timer.start()
        self.context_panel.set_title(label)
        self.context_panel.set_text(self._context_description(value))
        self._update_status_summary(label)
        elapsed_ms = timer.elapsed()
        LOGGER.debug(
            "context panel update label=%s value_type=%s total_ms=%d",
            label,
            type(value).__name__,
            elapsed_ms,
        )
        # Aggregate into the active profile so it surfaces in the perf
        # report even when many small updates happen in sequence.
        try:
            from tomography_session_browser.services.loading_profiler import (
                record_aggregate_phase,
            )

            record_aggregate_phase("update_context_panel", elapsed_ms / 1000.0)
        except Exception:  # pragma: no cover - perf must never break selection
            LOGGER.debug("Could not record context panel timing", exc_info=True)

    def _context_description(self, value: Any) -> str:
        text = describe_object(value)
        atlas_lines = self._atlas_linkage_context_lines(value)
        if not atlas_lines:
            return self._normalise_missing_atlas_text(text, value)
        lines = [
            line
            for line in text.splitlines()
            if line.strip() not in {"Atlas: no", "Atlas: yes", "Atlas: No atlas associated"}
        ]
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("Atlas linkage:")
        lines.extend(f"  {line}" for line in atlas_lines)
        return "\n".join(lines)

    def _normalise_missing_atlas_text(self, text: str, value: Any) -> str:
        if isinstance(value, Sample | LinkedSampleGroup):
            return text.replace("Atlas: no", "Atlas: No atlas associated")
        if isinstance(value, Session) and not self._context_atlases(value):
            suffix = "\n\nAtlas linkage:\n  Atlas: No atlas associated"
            return text if "Atlas linkage:" in text else f"{text}{suffix}"
        return text

    def _atlas_linkage_context_lines(self, value: Any) -> list[str]:
        direct, linked = self._atlas_associations_for_context(value)
        if direct:
            return self._atlas_linkage_lines("Direct atlas", direct, value)
        if linked:
            return self._atlas_linkage_lines("Linked atlas from screening session", linked, value)
        return []

    def _atlas_associations_for_context(self, value: Any) -> tuple[list[Atlas], list[Atlas]]:
        if isinstance(value, Atlas):
            return [value], []
        if isinstance(value, LinkedSampleGroup):
            if value.atlas is not None:
                return [], [value.atlas]
            return [], []
        if isinstance(value, Sample):
            direct = [value.atlas] if value.atlas is not None else []
            linked = [] if direct else self._linked_atlases_for_sample(value)
            return direct, linked
        if isinstance(value, Session):
            direct = self._display_atlases_for_session(value)
            linked = [] if direct else self._linked_atlases_for_session(value)
            return direct, linked
        if isinstance(value, SearchMap | BatchPosition | TiltSeries | SearchTile | Overview):
            context = self._context_for_object(value)
            if context is value:
                return [], []
            return self._atlas_associations_for_context(context)
        return [], []

    def _atlas_linkage_lines(self, label: str, atlases: list[Atlas], value: Any) -> list[str]:
        if not atlases:
            return []
        atlas = atlases[0]
        lines = [f"Atlas: {label}"]
        sample_labels = self._atlas_linkage_sample_labels(value, atlas)
        if sample_labels:
            lines.append(f"Sample: {', '.join(sample_labels)}")
        path = self._atlas_context_path(atlas)
        if path:
            lines.append(f"Path: {path}")
        if len(atlases) > 1:
            lines.append(f"Additional atlases: {len(atlases) - 1}")
        return lines

    def _atlas_linkage_sample_labels(self, value: Any, atlas: Atlas) -> list[str]:
        samples = self._samples_for_context(value)
        if not samples and isinstance(value, Atlas | Overview | SearchMap | SearchTile | BatchPosition | TiltSeries):
            samples = [sample for sample in self._all_samples() if self._sample_contains(sample, value)]
        if not samples:
            context = self._context_for_object(value)
            samples = self._samples_for_context(context)

        atlas_key = _atlas_key_static(atlas)
        matches: list[Sample] = []
        for sample in samples:
            if sample.atlas is atlas:
                matches.append(sample)
                continue
            if any(
                _atlas_key_static(candidate) == atlas_key
                for candidate in self._linked_atlases_for_sample(sample)
            ):
                matches.append(sample)

        collection_matches = [
            sample for sample in matches if _sample_has_collection_data_static(sample)
        ]
        preferred = collection_matches or matches
        labels: list[str] = []
        seen: set[str] = set()
        for sample in preferred:
            label = _display_sample_name_static(sample.name)
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            labels.append(label)
        return labels

    @staticmethod
    def _atlas_context_path(atlas: Atlas) -> str | None:
        if atlas.image_path is not None:
            return str(atlas.image_path)
        if atlas.mrc_metadata is not None and atlas.mrc_metadata.path is not None:
            return str(atlas.mrc_metadata.path)
        return None

    def _switch_to_group_tab(self, label: str) -> None:
        target_by_label = {
            "Overviews": "Overview",
            "Search maps": "Search map",
            "Search": "Search",
            "Batch positions": "Batch position",
            "Tilt series": "Tilt series",
        }
        target = target_by_label.get(label)
        if target is not None:
            self.tabs.setCurrentIndex(TAB_LABELS.index(target))

    def _select_visible_tab_object(self, value: Any) -> None:
        current_label = TAB_LABELS[self.tabs.currentIndex()]
        if current_label == tab_label_for_object(value):
            previous = self._suppress_viewer_context
            self._suppress_viewer_context = True
            try:
                self._viewer_tabs[current_label].select_object(value)
            finally:
                self._suppress_viewer_context = previous

    def _tree_item_scope_context(self, item: QTreeWidgetItem | None) -> Any:
        current = item
        while current is not None:
            value = current.data(0, OBJECT_ROLE)
            if isinstance(value, list):
                return None
            if isinstance(value, ProjectTreeGroup):
                return value
            if isinstance(value, Session | Sample | LinkedSampleGroup):
                return value
            current = current.parent()
        return _ACTIVE_CONTEXT

    def _context_for_entity_group(self, group: EntityGroup) -> Session | Sample | LinkedSampleGroup | ProjectTreeGroup | None:
        for value in group.values:
            context = self._context_for_object(value)
            if context is not None:
                return context
        return None

    def _context_for_object(self, value: Any) -> Session | Sample | LinkedSampleGroup | ProjectTreeGroup | None:
        if isinstance(value, LinkedSampleGroup):
            return value
        if isinstance(self._active_context, Session) and (
            self._session_scope_contains(self._active_context, value)
            or value in self._linked_atlases_for_session(self._active_context)
        ):
            return self._active_context
        if isinstance(self._active_context, Sample) and (
            self._sample_contains(self._active_context, value)
            or value in self._linked_atlases_for_sample(self._active_context)
        ):
            return self._active_context
        scoped_sessions = self._sessions
        scoped_group = self._active_context if isinstance(self._active_context, ProjectTreeGroup) else None
        if scoped_group is not None and group_contains_value(scoped_group, value):
            scoped_sessions = scoped_group.sessions
        for samples in linked_sample_groups(scoped_sessions):
            if any(self._sample_contains(sample, value) for sample in samples):
                if len(scoped_sessions) > 1 and self._samples_form_cross_session_link(samples, scoped_sessions):
                    label = self._linked_sample_label(samples)
                    return build_linked_sample_group(label, samples)
                return next(sample for sample in samples if self._sample_contains(sample, value))
        for group in self._project_groups():
            if group_contains_value(group, value):
                if len(group.sessions) > 1:
                    return group
                break
        for session in scoped_sessions:
            if self._session_contains(session, value):
                return session
        return self._active_context if isinstance(self._active_context, Session | Sample | LinkedSampleGroup | ProjectTreeGroup) else None

    def _samples_for_context(self, context: Any) -> list[Sample]:
        if isinstance(context, Sample):
            return [context]
        if isinstance(context, LinkedSampleGroup):
            return list(context.samples)
        if isinstance(context, Session):
            return list(context.samples)
        if isinstance(context, ProjectTreeGroup):
            return [sample for session in context.sessions for sample in session.samples]
        return []

    def _batch_positions_for_search_map(self, search_map: SearchMap, context: Any) -> list[BatchPosition]:
        linked_batch_ids = set(search_map.linked_batch_position_ids or [])
        linked_tilt_ids = set(search_map.linked_tilt_series_ids or [])
        matches: list[BatchPosition] = []
        for batch in self._context_batch_positions(context):
            batch_tilt_ids = set(batch.linked_tilt_series_ids or [])
            if (
                batch.id in linked_batch_ids
                or batch.linked_search_map_id == search_map.id
                or (linked_tilt_ids and linked_tilt_ids.intersection(batch_tilt_ids))
            ):
                matches.append(batch)
        return _dedupe_by_id(matches)

    def _tilt_series_for_search_map(
        self,
        search_map: SearchMap,
        context: Any,
        batch_positions: list[BatchPosition],
    ) -> list[TiltSeries]:
        linked_tilt_ids = set(search_map.linked_tilt_series_ids or [])
        linked_batch_ids = {batch.id for batch in batch_positions}
        matches = [
            tilt
            for tilt in self._context_tilt_series(context)
            if tilt.id in linked_tilt_ids or tilt.linked_batch_position_id in linked_batch_ids
        ]
        return _dedupe_by_id(matches)

    def _tilt_series_for_batch_position(self, batch_position: BatchPosition, context: Any) -> list[TiltSeries]:
        linked_tilt_ids = set(batch_position.linked_tilt_series_ids or [])
        matches = [
            tilt
            for tilt in self._context_tilt_series(context)
            if tilt.id in linked_tilt_ids or tilt.linked_batch_position_id == batch_position.id
        ]
        return _dedupe_by_id(matches)

    def _search_maps_for_batch_position_scope(self, batch_position: BatchPosition, context: Any) -> list[SearchMap]:
        linked_tilt_ids = set(batch_position.linked_tilt_series_ids or [])
        matches = [
            search_map
            for search_map in self._context_search_maps(context)
            if (
                batch_position.linked_search_map_id == search_map.id
                or batch_position.id in (search_map.linked_batch_position_ids or [])
                or (linked_tilt_ids and linked_tilt_ids.intersection(search_map.linked_tilt_series_ids or []))
            )
        ]
        return _dedupe_by_id(matches)

    def _samples_form_cross_session_link(self, samples: list[Sample], sessions: list[Session]) -> bool:
        session_ids = {id(session) for session in sessions}
        parent_sessions: set[int] = set()
        has_atlas = False
        has_collection = False
        for session in sessions:
            for sample in session.samples:
                if not any(sample is candidate for candidate in samples):
                    continue
                parent_sessions.add(id(session))
                has_atlas = has_atlas or sample.atlas is not None
                has_collection = has_collection or self._sample_has_collection_data(sample)
        return len(parent_sessions & session_ids) > 1 and has_atlas and has_collection

    def _same_context(self, left: Any, right: Any) -> bool:
        if left is right:
            return True
        if isinstance(left, LinkedSampleGroup) and isinstance(right, LinkedSampleGroup):
            return len(left.samples) == len(right.samples) and all(
                left_sample is right_sample for left_sample, right_sample in zip(left.samples, right.samples)
            )
        if isinstance(left, ProjectTreeGroup) and isinstance(right, ProjectTreeGroup):
            return left.key == right.key
        return False

    def _find_object_by_id(self, object_id: str | None) -> Any | None:
        if object_id is None:
            return None
        candidates: list[Any] = []
        for session in self._sessions:
            candidates.append(session)
            candidates.extend(session.overviews)
            candidates.extend(session.search_maps)
            candidates.extend(session.search_tiles)
            candidates.extend(session.batch_positions)
            candidates.extend(session.tilt_series)
            if session.atlas is not None:
                candidates.append(session.atlas)
            for sample in session.samples:
                candidates.append(sample)
                candidates.extend(sample.overviews)
                candidates.extend(sample.search_maps)
                candidates.extend(sample.search_tiles)
                candidates.extend(sample.batch_positions)
                candidates.extend(sample.tilt_series)
                if sample.atlas is not None:
                    candidates.append(sample.atlas)
        return next((value for value in candidates if getattr(value, "id", None) == object_id), None)

    def _sample_contains(self, sample: Sample, value: Any) -> bool:
        return (
            value is sample
            or value is sample.atlas
            or value in sample.overviews
            or value in sample.search_maps
            or value in sample.search_tiles
            or value in sample.batch_positions
            or value in sample.tilt_series
        )

    def _session_contains(self, session: Session, value: Any) -> bool:
        return (
            value is session
            or value is session.atlas
            or value in session.overviews
            or value in session.search_maps
            or value in session.search_tiles
            or value in session.batch_positions
            or value in session.tilt_series
        )

    def _tilt_angle_for(self, value: Any, index: int) -> str | None:
        if not isinstance(value, TiltSeries):
            return None
        angle_info = self._stack_order_tilt_angles(value)
        if angle_info is None:
            return None
        angles, source = angle_info
        if index >= len(angles):
            return None
        angle = angles[index]
        if isinstance(angle, int | float):
            suffix = " (inferred)" if source == "inferred" else ""
            return f"{_format_tilt_angle(float(angle))}\N{DEGREE SIGN}{suffix}"
        return None

    def _stack_order_tilt_angles(self, tilt_series: TiltSeries) -> tuple[list[float], str] | None:
        frame_count = self._frame_count_for_tilt_series(tilt_series)
        return stack_order_tilt_angles(tilt_series, frame_count)

    def _frame_angle_source(self, tilt_series: TiltSeries, frame_count: int) -> str:
        angle_info = self._stack_order_tilt_angles(tilt_series)
        if angle_info is None:
            return "unavailable"
        _angles, source = angle_info
        return source

    def _frame_count_for_tilt_series(self, tilt_series: TiltSeries) -> int:
        if tilt_series.mrc_metadata is not None and tilt_series.mrc_metadata.nz:
            return tilt_series.mrc_metadata.nz
        return len(tilt_series.sections)

    def _frame_metadata_for(self, tilt_series: TiltSeries, frame_index: int) -> dict[str, Any] | None:
        metadata = tilt_series.mrc_metadata
        if metadata is None or frame_index < 0 or frame_index >= len(metadata.frame_metadata):
            return None
        frame_metadata = metadata.frame_metadata[frame_index]
        return frame_metadata if isinstance(frame_metadata, dict) else None

    def _all_samples(self) -> list[Sample]:
        return [sample for session in self._sessions for sample in session.samples]

    # ------------------------------------------------------------------ overlay scoping

    def _rebuild_sample_index(self) -> None:
        """Build a reverse lookup ``entity.id → [Sample, …]`` keyed by
        *linked-sample group*.

        A linked sample group can span multiple sessions: when an atlas
        screening session and a data-collection session are both loaded,
        ``linked_sample_groups`` pairs the screening ``Sample1`` (which owns
        the Atlas) with the collection ``Sample1`` (which owns the Search Maps
        and Overviews). Scoping the Atlas-tab overlays by
        the parsed ``Sample`` alone hides exactly the linked overlays the
        user wants to see — so we index every entity to the *group* of
        samples it belongs to, and the marker context aggregates items from
        every sample in that group.
        """

        index: dict[str, list[Sample]] = {}
        try:
            groups = linked_sample_groups(self._sessions)
        except Exception:  # pragma: no cover — defensive; linker is robust
            LOGGER.warning("linked_sample_groups failed; falling back to per-Sample scoping", exc_info=True)
            groups = [[sample] for session in self._sessions for sample in session.samples]

        for group in groups:
            members = list(group)
            for sample in members:
                if sample.atlas is not None:
                    index[sample.atlas.id] = members
                for overview in sample.overviews:
                    index[overview.id] = members
                for search_map in sample.search_maps:
                    index[search_map.id] = members
                for search_tile in sample.search_tiles:
                    index[search_tile.id] = members
                for batch_position in sample.batch_positions:
                    index[batch_position.id] = members
                for tilt_series in sample.tilt_series:
                    index[tilt_series.id] = members
        self._sample_index = index

    def _marker_context_for(self, value: Any, fallback: MarkerContext) -> MarkerContext:
        """Return a MarkerContext scoped to ``value``'s linked-sample group.

        Falls back to ``fallback`` when no parent group is known — typical
        for session-level (top-level) entities that don't sit under a
        ``Sample`` in the parsed tree.

        Logs a debug breakdown of how many overlays were filtered out so
        cross-session bleed can be diagnosed quickly.
        """

        members = self._sample_index.get(getattr(value, "id", "")) if hasattr(self, "_sample_index") else None
        if not members:
            LOGGER.debug(
                "overlay context: no parent sample group for %s id=%s — using fallback (%d overviews, %d search_maps, %d batches)",
                type(value).__name__,
                getattr(value, "id", "<unknown>"),
                len(fallback.overviews),
                len(fallback.search_maps),
                len(fallback.batch_positions),
            )
            return fallback

        # Aggregate items across every sample in the linked group. A linked
        # group spans sessions, so the screening sample can contribute the
        # atlas while the collection sample contributes the search maps.
        overviews: list[Overview] = []
        search_maps: list[SearchMap] = []
        batch_positions: list[BatchPosition] = []
        tilt_series: list[TiltSeries] = []
        for sample in members:
            overviews.extend(sample.overviews)
            search_maps.extend(sample.search_maps)
            batch_positions.extend(sample.batch_positions)
            tilt_series.extend(sample.tilt_series)

        scoped = MarkerContext(
            overviews=tuple(overviews),
            search_maps=tuple(search_maps),
            batch_positions=tuple(batch_positions),
            tilt_series=tuple(tilt_series),
            failed_tilt_ids=fallback.failed_tilt_ids,
        )

        if LOGGER.isEnabledFor(logging.DEBUG):
            group_label = " + ".join(sorted({sample.name for sample in members}))
            hidden_ovs = len(fallback.overviews) - len(scoped.overviews)
            hidden_sm = len(fallback.search_maps) - len(scoped.search_maps)
            hidden_bp = len(fallback.batch_positions) - len(scoped.batch_positions)
            LOGGER.debug(
                "overlay context: scoped to linked group [%s] for %s id=%s "
                "(visible: ov=%d sm=%d bp=%d, hidden: ov=%d sm=%d bp=%d)",
                group_label,
                type(value).__name__,
                getattr(value, "id", "<unknown>"),
                len(scoped.overviews),
                len(scoped.search_maps),
                len(scoped.batch_positions),
                hidden_ovs,
                hidden_sm,
                hidden_bp,
            )
            scoped_ov_ids = {o.id for o in scoped.overviews}
            scoped_sm_ids = {s.id for s in scoped.search_maps}
            for overview in fallback.overviews:
                if overview.id not in scoped_ov_ids:
                    parent = self._sample_index.get(overview.id)
                    parent_label = " + ".join(sorted({s.name for s in parent})) if parent else "<unknown>"
                    LOGGER.debug(
                        "overlay filtered: type=Overview name=%s parent_group=%s active_group=%s reason=different_linked_group",
                        getattr(overview, "name", "<unknown>"),
                        parent_label,
                        group_label,
                    )
            for search_map in fallback.search_maps:
                if search_map.id not in scoped_sm_ids:
                    parent = self._sample_index.get(search_map.id)
                    parent_label = " + ".join(sorted({s.name for s in parent})) if parent else "<unknown>"
                    LOGGER.debug(
                        "overlay filtered: type=SearchMap name=%s parent_group=%s active_group=%s reason=different_linked_group",
                        getattr(search_map, "name", "<unknown>"),
                        parent_label,
                        group_label,
                    )
        return scoped

    def _all_atlases(self) -> list[Atlas]:
        atlases: list[Atlas] = []
        for session in self._sessions:
            atlases.extend(self._display_atlases_for_session(session))
        return self._dedupe_atlases(atlases)

    def _display_atlases_for_session(self, session: Session) -> list[Atlas]:
        sample_atlases = [
            sample.atlas
            for sample in session.samples
            if sample.atlas is not None and not self._sample_is_single_atlas_placeholder(sample)
        ]
        if sample_atlases:
            return self._dedupe_atlases(sample_atlases)
        if session.atlas is not None:
            # Root/session atlases are useful for atlas-only folders with
            # no sample-level atlas, but should not inflate the linked
            # project Atlas tab where per-sample atlases are present.
            return [session.atlas]
        return []

    def _dedupe_atlases(self, atlases: list[Atlas]) -> list[Atlas]:
        seen: set[str] = set()
        unique: list[Atlas] = []
        for atlas in atlases:
            key = self._atlas_key(atlas)
            if key in seen:
                continue
            seen.add(key)
            unique.append(atlas)
        return unique

    def _atlas_key(self, atlas: Atlas) -> str:
        path = atlas.image_path
        if path is not None:
            return _path_identity_key(path)
        return atlas.id

    def _sample_is_single_atlas_placeholder(self, sample: Sample) -> bool:
        return "single atlas" in sample.name.lower() and not self._sample_has_collection_data(sample)

    def _all_overviews(self) -> list[Overview]:
        return dedupe_entities([value for session in self._sessions for value in session.overviews] + [
            value for sample in self._all_samples() for value in sample.overviews
        ])

    def _all_search_maps(self) -> list[SearchMap]:
        return dedupe_entities([value for session in self._sessions for value in session.search_maps] + [
            value for sample in self._all_samples() for value in sample.search_maps
        ])

    def _all_search_tiles(self) -> list[SearchTile]:
        return dedupe_entities([value for session in self._sessions for value in session.search_tiles] + [
            value for sample in self._all_samples() for value in sample.search_tiles
        ])

    def _all_batch_positions(self) -> list[BatchPosition]:
        return dedupe_entities([value for session in self._sessions for value in session.batch_positions] + [
            value for sample in self._all_samples() for value in sample.batch_positions
        ])

    def _all_tilt_series(self) -> list[TiltSeries]:
        return dedupe_entities([value for session in self._sessions for value in session.tilt_series] + [
            value for sample in self._all_samples() for value in sample.tilt_series
        ])

    def _resolved_context(self, context: Any = _ACTIVE_CONTEXT) -> Any:
        return self._active_context if context is _ACTIVE_CONTEXT else context

    def _context_atlases(self, context: Any = _ACTIVE_CONTEXT) -> list[Atlas]:
        context = self._resolved_context(context)
        if isinstance(context, EntityGroup):
            return [value for value in context.values if isinstance(value, Atlas)]
        if isinstance(context, LinkedSampleGroup):
            return [context.atlas] if context.atlas is not None else []
        if isinstance(context, Sample):
            return self._linked_atlases_for_sample(context) or ([context.atlas] if context.atlas is not None else [])
        if isinstance(context, Session):
            return self._linked_atlases_for_session(context) or self._display_atlases_for_session(context)
        if isinstance(context, ProjectTreeGroup):
            atlases: list[Atlas] = []
            for session in context.sessions:
                atlases.extend(self._display_atlases_for_session(session))
            return self._dedupe_atlases(atlases)
        return self._all_atlases()

    def _linked_atlases_for_session(self, session: Session) -> list[Atlas]:
        return _linked_atlases_for_session_static(self._sessions, session)

    def _linked_atlases_for_sample(self, sample: Sample) -> list[Atlas]:
        return _linked_atlases_for_sample_static(self._sessions, sample)

    def _context_overviews(self, context: Any = _ACTIVE_CONTEXT) -> list[Overview]:
        context = self._resolved_context(context)
        if isinstance(context, EntityGroup):
            return [value for value in context.values if isinstance(value, Overview)]
        if isinstance(context, LinkedSampleGroup):
            return context.overviews
        if isinstance(context, Sample):
            return context.overviews
        if isinstance(context, Session):
            return dedupe_entities(context.overviews + [value for sample in context.samples for value in sample.overviews])
        if isinstance(context, ProjectTreeGroup):
            return _context_overviews_for_sessions(context.sessions, None)
        return self._all_overviews()

    def _context_search_maps(self, context: Any = _ACTIVE_CONTEXT) -> list[SearchMap]:
        context = self._resolved_context(context)
        if isinstance(context, EntityGroup):
            return [value for value in context.values if isinstance(value, SearchMap)]
        if isinstance(context, LinkedSampleGroup):
            return context.search_maps
        if isinstance(context, Sample):
            return context.search_maps
        if isinstance(context, Session):
            return dedupe_entities(context.search_maps + [value for sample in context.samples for value in sample.search_maps])
        if isinstance(context, ProjectTreeGroup):
            return _context_search_maps_for_sessions(context.sessions, None)
        return self._all_search_maps()

    def _context_search_tiles(self, context: Any = _ACTIVE_CONTEXT) -> list[SearchTile]:
        context = self._resolved_context(context)
        if isinstance(context, EntityGroup):
            return [value for value in context.values if isinstance(value, SearchTile)]
        if isinstance(context, LinkedSampleGroup):
            return context.search_tiles
        if isinstance(context, Sample):
            return context.search_tiles
        if isinstance(context, Session):
            return dedupe_entities(context.search_tiles + [value for sample in context.samples for value in sample.search_tiles])
        if isinstance(context, ProjectTreeGroup):
            return _context_search_tiles_for_sessions(context.sessions, None)
        return self._all_search_tiles()

    def _context_batch_positions(self, context: Any = _ACTIVE_CONTEXT) -> list[BatchPosition]:
        context = self._resolved_context(context)
        if isinstance(context, EntityGroup):
            return [value for value in context.values if isinstance(value, BatchPosition)]
        if isinstance(context, LinkedSampleGroup):
            return context.batch_positions
        if isinstance(context, Sample):
            return context.batch_positions
        if isinstance(context, Session):
            return dedupe_entities(context.batch_positions + [value for sample in context.samples for value in sample.batch_positions])
        if isinstance(context, ProjectTreeGroup):
            return _context_batch_positions_for_sessions(context.sessions, None)
        return self._all_batch_positions()

    def _context_tilt_series(self, context: Any = _ACTIVE_CONTEXT) -> list[TiltSeries]:
        context = self._resolved_context(context)
        if isinstance(context, EntityGroup):
            return [value for value in context.values if isinstance(value, TiltSeries)]
        if isinstance(context, LinkedSampleGroup):
            return context.tilt_series
        if isinstance(context, Sample):
            return context.tilt_series
        if isinstance(context, Session):
            return dedupe_entities(context.tilt_series + [value for sample in context.samples for value in sample.tilt_series])
        if isinstance(context, ProjectTreeGroup):
            return _context_tilt_series_for_sessions(context.sessions, None)
        return self._all_tilt_series()


class OpenSessionsDialog(QDialog):
    def __init__(self, loader: SessionLoader, start_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._loader = loader
        self._start_path = start_path
        self._parent_buttons: dict[QLineEdit, QPushButton] = {}
        self.setWindowTitle("Select session folder")
        self.resize(780, 260)

        layout = QVBoxLayout(self)
        help_label = QLabel(SESSION_FOLDER_HELP_TEXT)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        form = QFormLayout()
        self.atlas_path = QLineEdit()
        self.collection_path = QLineEdit()
        form.addRow("Atlas session", self._path_row(self.atlas_path))
        form.addRow("Data collection session", self._path_row(self.collection_path))
        layout.addLayout(form)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        self.import_button = QPushButton("Open folders")
        self.import_button.clicked.connect(self.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)

        self.atlas_path.textChanged.connect(self._validate)
        self.collection_path.textChanged.connect(self._validate)
        self._validate()

    def selected_paths(self) -> list[str]:
        return [path for path in [self.atlas_path.text().strip(), self.collection_path.text().strip()] if path]

    def _path_row(self, line_edit: QLineEdit) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        line_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        parent_button = QPushButton("Use parent folder")
        parent_button.setVisible(False)
        parent_button.clicked.connect(lambda: self._use_parent_folder(line_edit))
        browse_button = QPushButton("Browse folder...")
        browse_button.clicked.connect(lambda: self._browse(line_edit))
        layout.addWidget(line_edit)
        layout.addWidget(parent_button)
        layout.addWidget(browse_button)
        self._parent_buttons[line_edit] = parent_button
        return row

    def _browse(self, line_edit: QLineEdit) -> None:
        path = _select_session_folder(
            self,
            "Select session folder",
            line_edit.text().strip() or self._start_path,
            "Open folder",
        )
        if path:
            line_edit.setText(path)

    def _use_parent_folder(self, line_edit: QLineEdit) -> None:
        path = Path(line_edit.text().strip())
        if _looks_like_direct_file_path(path):
            line_edit.setText(str(path.parent))

    def _validate(self) -> None:
        messages: list[str] = []
        valid = True
        atlas = self.atlas_path.text().strip()
        collection = self.collection_path.text().strip()

        for line_edit in (self.atlas_path, self.collection_path):
            parent_button = self._parent_buttons.get(line_edit)
            if parent_button is not None:
                path = Path(line_edit.text().strip()) if line_edit.text().strip() else None
                parent_button.setVisible(bool(path and _looks_like_direct_file_path(path) and path.parent.exists()))

        if not atlas and not collection:
            valid = False

        if atlas:
            atlas_path = Path(atlas)
            if _looks_like_direct_file_path(atlas_path):
                messages.append(DIRECT_DM_FILE_MESSAGE)
                messages.append(f"You selected {atlas_path.name}. Use its parent folder, or browse for the atlas session folder.")
                valid = False
            elif not atlas_path.exists() or not atlas_path.is_dir():
                messages.append("The atlas session field must point to a folder.")
                valid = False
            else:
                atlas_kind = self._loader.classify(atlas)
                if atlas_kind == SessionKind.UNKNOWN:
                    messages.append("No Tomography 5 metadata was found in the atlas session folder.")
                    valid = False
                elif atlas_kind != SessionKind.ATLAS_SCREENING:
                    messages.append("The atlas session field must point to an atlas screening session folder.")
                    valid = False

        if collection:
            collection_path = Path(collection)
            if _looks_like_direct_file_path(collection_path):
                messages.append(DIRECT_DM_FILE_MESSAGE)
                messages.append(
                    f"You selected {collection_path.name}. Use its parent folder, or browse for the data collection session folder."
                )
                valid = False
            elif not collection_path.exists() or not collection_path.is_dir():
                messages.append("The data collection field must point to a folder.")
                valid = False
            else:
                collection_kind = self._loader.classify(collection)
                if collection_kind == SessionKind.UNKNOWN:
                    messages.append("No Tomography 5 metadata was found in the data collection folder.")
                    valid = False
                elif collection_kind == SessionKind.ATLAS_SCREENING:
                    messages.append("The data collection field contains an atlas session folder.")
                    valid = False

        self.message.setText("\n".join(messages) if messages else LINKED_IMPORT_HELP_TEXT)
        self.import_button.setEnabled(valid)

    def accept(self) -> None:  # noqa: D102 - Qt override
        for line_edit in (self.atlas_path, self.collection_path):
            text = line_edit.text().strip()
            if not text:
                continue
            folder_path = _normalise_session_folder_for_import(self, text)
            if folder_path is None:
                return
            line_edit.setText(folder_path)
        self._validate()
        if not self.import_button.isEnabled():
            return
        super().accept()
