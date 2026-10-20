"""Marker layer for the image viewer."""
from __future__ import annotations
from dataclasses import replace
import shiboken6
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractGraphicsShapeItem, QGraphicsItem, QGraphicsLineItem
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.services.atlas_lod import atlas_batch_view, atlas_cluster_inputs
from tomography_session_browser.services.batch_label_service import LABEL_STROKE_WIDTH_PX, AtlasLabelPlacement, batch_label_screen_font_size_px
from tomography_session_browser.services.atlas_marker_style import LEAF_DIAMETER_DP
from tomography_session_browser.services.marker_clustering import NO_CLUSTER_ABOVE_ZOOM, PER_EXPOSURE_MIN_SCREEN_PX, SEARCH_MAP_FOOTPRINT_MIN_SCREEN_PX, SEARCH_MAP_TILE_MIN_SCREEN_PX, ScreenTransform, cluster_markers, marker_set_fingerprint, screen_extent_visible, zoom_bucket
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.presentation.design_tokens import SANS_FONT_NAME
from tomography_session_browser.presentation.marker_palette import FRAME_HALO
from tomography_session_browser.ui.viewer.atlas_lod_items import AtlasLodLabelItem, AtlasLodMarkerItem
from tomography_session_browser.ui.viewer.constants import ATLAS_LOD_CLUSTER, ATLAS_LOD_EXPOSURES, ATLAS_LOD_LABELS, ATLAS_LOD_TILE_GRIDS, EXPOSURE_MARKER_HIGHLIGHT_WIDTH, HIGH_DETAIL_SCALE, MEDIUM_DETAIL_SCALE, ROWLESS_MARKER_TYPE_ROWS
from tomography_session_browser.ui.viewer.marker_paint import batch_label_color, batch_label_scene_origin, batch_label_text_path, exposure_highlight_color, marker_colors, marker_label_anchor, marker_outline, marker_scene_extent, metadata_box, metadata_point
from tomography_session_browser.ui.viewer.marker_goo import GooBlob, MarkerGoo, blobs_from_markers, start_goo
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.preview_view import ImagePreviewView


class MarkerLayer:
    """Marker layer operations for one ImagePreviewView."""

    def __init__(self, view: ImagePreviewView) -> None:
        self.view = view
        self.input_markers: list[ImageMarker] | None = None
        self.intrinsic_selection: frozenset[str] = frozenset()
        #: Row-less marker types Remove all overlays hid (``ROWLESS_MARKER_TYPE_ROWS``).
        self.rowless_hidden: set[str] = set()
        #: The Atlas batch glyphs last drawn, with what they were drawn from and at
        #: which zoom, for the next regroup (``marker_goo``); and that regroup.
        self.goo_layout: tuple[tuple[object, ...], int, list[GooBlob]] | None = None
        self.goo: MarkerGoo | None = None

    def set_atlas_lod_enabled(self, enabled: bool) -> None:
        self.atlas_lod_enabled = bool(enabled)
        self.cluster_cache.clear()
        self.view._redraw_markers()

    def set_atlas_lod_option(self, key: str, enabled: bool) -> None:
        if key not in self.atlas_lod_options:
            raise KeyError(f"Unknown Atlas LOD option: {key}")
        self.atlas_lod_options[key] = bool(enabled)
        self.cluster_cache.clear()
        self.view._redraw_markers()

    def set_marker_type_visible(self, marker_type: str, visible: bool) -> None:
        if (marker_type in self.visible_marker_types) == visible:
            # Unchanged. The availability pass sets every type on each item
            # it shows, and a redraw per type cost a tab its first switch.
            return
        if visible:
            self.visible_marker_types.add(marker_type)
            # Layers without a row that Remove all overlays hid come back with
            # the row they are drawn with; nothing else could show them again.
            returning = {key for key in self.rowless_hidden if ROWLESS_MARKER_TYPE_ROWS[key] == marker_type}
            self.visible_marker_types.update(returning)
            self.rowless_hidden.difference_update(returning)
        else:
            self.visible_marker_types.discard(marker_type)
        self.view._redraw_markers()

    def hide_all_markers(self) -> None:
        """Hide every marker layer without discarding its source markers."""
        self.view._close_cluster_popup()
        self.rowless_hidden.update(self.visible_marker_types.intersection(ROWLESS_MARKER_TYPE_ROWS))
        self.visible_marker_types.clear()
        self.view._redraw_markers()

    def set_markers(self, markers: list[ImageMarker], selected_marker_id: str | None = None) -> None:
        self.input_markers = markers
        self.intrinsic_selection = frozenset(marker.id for marker in markers if marker.selected)
        self.view._close_cluster_popup()
        self.cluster_cache.clear()
        marker_ids = {marker.id for marker in markers}
        # Keep the keyboard cursor if its marker survived this refresh.
        # Clearing unconditionally meant a keyboard selection wiped its own
        # cursor: arrowing to a marker notifies the window, which refreshes the
        # overlay, which landed back here — so the next arrow press started
        # over from the edge.
        if self.view.parts.preview_interaction.keyboard_marker_id not in marker_ids:
            self.view.parts.preview_interaction.keyboard_marker_id = None
        self.view.parts.preview_interaction.exposure_highlights.intersection_update(marker_ids)
        self.source_markers = [
            ImageMarker(
                id=marker.id,
                marker_type=marker.marker_type,
                linked_object_id=marker.linked_object_id,
                source_object_id=marker.source_object_id,
                x=marker.x,
                y=marker.y,
                radius=marker.radius,
                bbox=marker.bbox,
                polygon=marker.polygon,
                label=marker.label,
                tooltip=marker.tooltip,
                status=marker.status,
                visible=marker.visible,
                selected=marker.selected or marker.id == selected_marker_id,
                unresolved=marker.unresolved,
                metadata=dict(marker.metadata),
            )
            for marker in markers
        ]
        self.view._redraw_markers()

    def select_marker(self, selected_marker_id: str | None) -> None:
        """Change selection without replacing geometry or invalidating clusters."""
        changed: set[str] = set()
        for marker in self.source_markers:
            selected = marker.id in self.intrinsic_selection or marker.id == selected_marker_id
            if marker.selected != selected:
                marker.selected = selected
                changed.add(marker.id)
        if changed:
            self.redraw_markers(None if self.atlas_lod_enabled else changed)

    @property
    def markers(self) -> tuple[ImageMarker, ...]:
        """Every marker given for the image, shown at this zoom or not."""

        return tuple(self.source_markers)

    def schedule_marker_redraw(self) -> None:
        if self.atlas_lod_enabled:
            self.marker_redraw_debounce.start()
        else:
            self.view._redraw_markers()

    def redraw_markers(self, changed_ids: set[str] | None = None) -> None:
        if changed_ids is None:
            # A full redraw replaces the glyphs a running regroup fades in.
            self.dismiss_goo()
        retained = []
        for item in self.marker_items:
            marker = item.data(0)
            if changed_ids is not None and isinstance(marker, ImageMarker) and marker.id not in changed_ids:
                retained.append(item)
            elif item.scene() is self.view.parts.preview_image.scene:
                self.view.parts.preview_image.scene.removeItem(item)
        self.marker_items = retained
        first_new_item = len(retained)
        if self.view.parts.preview_image.pixmap_item is None:
            self.goo_layout = None
            return
        if changed_ids is None:
            display_markers = self.view._display_markers()
            self.last_display_markers = display_markers
        else:
            # Geometry and visibility were established by the last full draw.
            # Selection only changes paint; retain unrelated graphics items.
            selected = {marker.id: marker.selected for marker in self.source_markers if marker.id in changed_ids}
            self.last_display_markers = [replace(marker, selected=selected[marker.id])
                if marker.id in selected else marker for marker in self.last_display_markers]
            display_markers = [marker for marker in self.last_display_markers if marker.id in changed_ids]
        for marker in display_markers:
            if (
                marker.marker_type == MarkerType.BATCH_CLUSTER
                or marker.metadata.get("atlas_lod_role") in {"batch_position", "unattributed"}
            ):
                shape = AtlasLodMarkerItem(marker)
                shape.setPos(float(marker.x or 0.0), float(marker.y or 0.0))
                shape.setData(0, marker)
                shape.setToolTip(marker.tooltip or marker.label or marker.marker_type)
                self.view.parts.preview_image.scene.addItem(shape)
                self.marker_items.append(shape)
                if marker.label and marker.marker_type != MarkerType.BATCH_CLUSTER:
                    placement = marker.metadata.get("atlas_label_placement")
                    if not isinstance(placement, AtlasLabelPlacement):
                        continue
                    label = AtlasLodLabelItem(marker, placement)
                    label.setPos(float(marker.x or 0.0), float(marker.y or 0.0))
                    label.setZValue(35)
                    label.setData(0, marker)
                    label.setData(1, "atlas_batch_label")
                    label.setToolTip(marker.tooltip or marker.label)
                    self.view.parts.preview_image.scene.addItem(label)
                    self.marker_items.append(label)
                continue
            if marker.marker_type == MarkerType.BATCH_LABEL:
                self.view._add_batch_label(marker)
                continue
            radius = marker.radius or 8.0 * self.style_scene_units()
            pen_color, fill_color = marker_colors(marker)
            pen = QPen(pen_color, 2.0 if marker.selected else 1.4)
            if marker.selected and marker.marker_type == MarkerType.EXPOSURE_AREA:
                # Over the highlight ring (4 px on screen) in its status colour: at a scene
                # width of 2 the line was a fraction of a pixel with a whole tile in view,
                # and the ring hid it.
                pen.setCosmetic(True)
            outline = marker_outline(marker)
            if outline.line == "dash":
                pen.setStyle(Qt.PenStyle.DashLine)
            elif outline.line == "dot":
                pen.setStyle(Qt.PenStyle.DotLine)
            # Markers approximated from MRC stage metadata get a
            # dashed stroke so the user can tell them apart from
            # recorded acquisitions. The PDF render does the same.
            if marker.metadata and marker.metadata.get("inferred_from_stage"):
                pen.setStyle(Qt.PenStyle.DashLine)
            brush = QBrush(fill_color)
            if marker.bbox is not None:
                x, y, width, height = marker.bbox
                atlas_outline = (
                    self.atlas_lod_enabled
                    and (
                        marker.marker_type == MarkerType.OVERVIEW
                        or marker.metadata.get("atlas_lod_role")
                        in {"search_map_footprint", "search_map_tile"}
                    )
                )
                if atlas_outline:
                    pen.setCosmetic(True)
                    pen.setWidthF(1.2)
                    fill = QBrush(Qt.BrushStyle.NoBrush)
                elif marker.marker_type == MarkerType.CAMERA_FOV:
                    pen.setCosmetic(True)
                    fallback_fill = bool(marker.metadata.get("filled_fallback")) if marker.metadata else False
                    fill = QBrush(fill_color if fallback_fill else Qt.BrushStyle.NoBrush)
                elif outline.halo:
                    fill = QBrush(Qt.BrushStyle.NoBrush)
                else:
                    fill = QBrush(QColor(fill_color.red(), fill_color.green(), fill_color.blue(), 35))
                if outline.halo and not marker.selected:
                    # A dark halo under a neutral frame keeps it legible on a
                    # bright image as well as a dark one.
                    halo_pen = QPen(QColor(FRAME_HALO), pen.widthF() + 2.0)
                    halo_pen.setCosmetic(pen.isCosmetic())
                    halo_colour = halo_pen.color()
                    halo_colour.setAlpha(150)
                    halo_pen.setColor(halo_colour)
                    halo = self.view.parts.preview_image.scene.addRect(x, y, width, height, halo_pen, QBrush(Qt.BrushStyle.NoBrush))
                    halo.setZValue(13.5 if marker.marker_type == MarkerType.CAMERA_FOV else 14.5)
                    halo.setData(0, marker)
                    halo.setData(1, "halo")
                    self.marker_items.append(halo)
                shape = self.view.parts.preview_image.scene.addRect(x, y, width, height, pen, fill)
            elif marker.polygon and len(marker.polygon) == 2:
                start, end = marker.polygon
                shape = self.view.parts.preview_image.scene.addLine(start[0], start[1], end[0], end[1], pen)
            elif marker.polygon:
                shape = self.view.parts.preview_image.scene.addPolygon(QPolygonF([QPointF(x, y) for x, y in marker.polygon]), pen, QBrush(QColor(fill_color.red(), fill_color.green(), fill_color.blue(), 35)))
            else:
                assert marker.x is not None and marker.y is not None
                shape = self.view.parts.preview_image.scene.addEllipse(marker.x - radius, marker.y - radius, radius * 2, radius * 2, pen, brush)
            if (
                marker.marker_type
                in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
                and (
                    marker.selected
                    or marker.id in self.view.parts.preview_interaction.exposure_highlights
                )
            ):
                self.view._add_exposure_highlight_edge(marker, radius)
            if marker.marker_type == MarkerType.CAMERA_FOV:
                shape.setZValue(14)
            else:
                shape.setZValue(20 if marker.selected else 15)
            shape.setData(0, marker)
            shape.setToolTip(marker.tooltip or marker.label or marker.marker_type)
            self.marker_items.append(shape)
            if marker.marker_type in {MarkerType.BATCH_POSITION, MarkerType.TILT_SERIES} and marker.x is not None and marker.y is not None:
                self.view._add_cross_marker(marker, radius, pen_color)
            if marker.marker_type == MarkerType.STAGE_CROSSHAIR and marker.x is not None and marker.y is not None:
                # A larger axis-aligned crosshair so the stage origin reads
                # differently from a batch / tilt marker.
                self.view._add_axis_crosshair(marker, radius * 2.4, pen_color)
            if marker.label:
                theme = current_palette()
                label = self.view.parts.preview_image.scene.addText(marker.label, QFont(SANS_FONT_NAME, 9))
                units = self.style_scene_units()
                label.setScale(units)
                label.setDefaultTextColor(QColor(theme.text_strong))
                anchor = marker_label_anchor(marker)
                label.setPos(anchor.x() + radius + 4 * units, anchor.y() - radius - 2 * units)
                label.setZValue(21 if marker.selected else 16)
                label.setData(0, marker)
                label.setData(1, "label")
                label.setToolTip(marker.tooltip or marker.label)
                self.marker_items.append(label)
        # A change in preview resolution moves scene units under the same
        # viewport. Keep visual weights in the reference image's units;
        # scientific marker positions and screen-sized selection rings stay.
        units = self.style_scene_units()
        for item in self.marker_items[first_new_item:]:
            if not isinstance(item, QAbstractGraphicsShapeItem | QGraphicsLineItem):
                continue
            if item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations:
                continue
            pen = item.pen()
            if not pen.isCosmetic():
                pen.setWidthF(pen.widthF() * units)
                item.setPen(pen)
        if changed_ids is None:
            self.regroup(self.marker_items[first_new_item:])
        self.view._update_marker_detail()

    def regroup(self, new_items: list[QGraphicsItem]) -> None:
        """Let the Atlas batch glyphs bud apart or flow together if only the zoom regrouped them."""

        if not self.atlas_lod_enabled:
            self.goo_layout = None
            return
        image = self.view.parts.preview_image
        basis = (
            id(self.source_markers),
            frozenset(self.visible_marker_types),
            tuple(sorted(self.atlas_lod_options.items())),
            image.current_image_key,
            image.current_image_shape,
        )
        zoom = zoom_bucket(self.view._zoom_scale())
        blobs = blobs_from_markers(self.last_display_markers)
        previous = self.goo_layout
        self.goo_layout = (basis, zoom, blobs)
        if previous is None or previous[0] != basis or previous[1] == zoom:
            return
        items_by_key: dict[str, list[QGraphicsItem]] = {}
        for item in new_items:
            marker = item.data(0)
            if isinstance(marker, ImageMarker):
                items_by_key.setdefault(marker.id, []).append(item)
        self.goo = start_goo(self.view, previous[2], blobs, items_by_key)

    def dismiss_goo(self) -> None:
        """End a running regroup now, with every glyph showing in full."""

        goo, self.goo = self.goo, None
        if goo is not None and shiboken6.isValid(goo) and goo.is_animating:
            goo.dismiss()

    def style_scene_units(self) -> float:
        """Displayed scene units per reference pixel, independent of zoom."""
        image = self.view.parts.preview_image
        if image.current_image_shape is None or image.overlay_reference_size is None:
            return 1.0
        return image.current_image_shape[1] / max(1, image.overlay_reference_size[0])

    def add_batch_label(self, marker: ImageMarker) -> None:
        if not marker.label or marker.x is None or marker.y is None or self.view.parts.preview_image.current_image_shape is None:
            return

        scale = self.view._zoom_scale()
        displayed_height, displayed_width = self.view.parts.preview_image.current_image_shape
        font = QFont(SANS_FONT_NAME)
        font.setPixelSize(round(batch_label_screen_font_size_px(scale * self.style_scene_units())))
        font.setWeight(QFont.Weight.Black)

        path = batch_label_text_path(marker.label, font)
        text_rect = path.boundingRect()
        if text_rect.isEmpty():
            return
        scene_w = text_rect.width() / scale
        scene_h = text_rect.height() / scale
        x, y = batch_label_scene_origin(
            marker,
            label_size=(scene_w, scene_h),
            image_size=(displayed_width, displayed_height),
        )

        theme = current_palette()
        shadow_color = QColor(theme.overlay_label_shadow)
        shadow_color.setAlpha(105)
        halo_color = QColor(theme.overlay_label_halo)
        halo_color.setAlpha(178)
        text_color = batch_label_color(self.visible_marker_types)

        shadow = self.view.parts.preview_image.scene.addPath(path, QPen(Qt.PenStyle.NoPen), QBrush(shadow_color))
        shadow.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        shadow.setPos(x + 1.4 / scale, y + 1.4 / scale)
        shadow.setZValue(30)
        shadow.setData(0, marker)
        shadow.setData(1, "batch_label")
        shadow.setToolTip(marker.tooltip or marker.label)
        self.marker_items.append(shadow)

        halo_pen = QPen(halo_color, LABEL_STROKE_WIDTH_PX)
        halo_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        halo_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        halo = self.view.parts.preview_image.scene.addPath(path, halo_pen, QBrush(Qt.BrushStyle.NoBrush))
        halo.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        halo.setPos(x, y)
        halo.setZValue(31)
        halo.setData(0, marker)
        halo.setData(1, "batch_label")
        halo.setToolTip(marker.tooltip or marker.label)
        self.marker_items.append(halo)

        text = self.view.parts.preview_image.scene.addPath(path, QPen(Qt.PenStyle.NoPen), QBrush(text_color))
        text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        text.setPos(x, y)
        text.setZValue(32)
        text.setData(0, marker)
        text.setData(1, "batch_label")
        text.setToolTip(marker.tooltip or marker.label)
        self.marker_items.append(text)

    def add_exposure_highlight_edge(self, marker: ImageMarker, radius: float) -> None:
        pen = QPen(exposure_highlight_color(), EXPOSURE_MARKER_HIGHLIGHT_WIDTH)
        pen.setCosmetic(True)
        brush = QBrush(Qt.BrushStyle.NoBrush)
        if marker.bbox is not None:
            x, y, width, height = marker.bbox
            edge = self.view.parts.preview_image.scene.addRect(x, y, width, height, pen, brush)
        elif marker.polygon and len(marker.polygon) == 2:
            start, end = marker.polygon
            edge = self.view.parts.preview_image.scene.addLine(start[0], start[1], end[0], end[1], pen)
        elif marker.polygon:
            edge = self.view.parts.preview_image.scene.addPolygon(QPolygonF([QPointF(x, y) for x, y in marker.polygon]), pen, brush)
        else:
            assert marker.x is not None and marker.y is not None
            edge = self.view.parts.preview_image.scene.addEllipse(marker.x - radius, marker.y - radius, radius * 2, radius * 2, pen, brush)
        # Draw just below the exposure marker so the blue reads as a highlight edge
        # while the exposure's own status-coloured line and tint remain.
        edge.setZValue(19)
        edge.setData(0, marker)
        edge.setToolTip(marker.tooltip or marker.label or marker.marker_type)
        self.marker_items.append(edge)

    def update_marker_detail(self) -> None:
        scale = self.view._zoom_scale() * self.style_scene_units()
        for item in self.marker_items:
            marker = item.data(0)
            if isinstance(marker, ImageMarker) and item.data(1) == "batch_label":
                item.setVisible(True)
            elif isinstance(marker, ImageMarker) and item.data(1) == "label":
                show_medium_label = marker.marker_type in {MarkerType.BATCH_POSITION, MarkerType.SEARCH_MAP, MarkerType.OVERVIEW}
                item.setVisible(marker.selected or scale >= HIGH_DETAIL_SCALE or (scale >= MEDIUM_DETAIL_SCALE and show_medium_label))

    def display_markers(self) -> list[ImageMarker]:
        markers = [
            self.view._project_marker(marker)
            for marker in self.source_markers
            if marker.visible
            and not marker.unresolved
            and marker.marker_type in self.visible_marker_types
            and (marker.marker_type != MarkerType.LINK_LINE or MarkerType.EXPOSURE_AREA in self.visible_marker_types)
            and (marker.x is not None or marker.bbox is not None or marker.polygon)
        ]
        if not self.atlas_lod_enabled:
            return markers
        return self.view._atlas_lod_display_markers(markers)

    def atlas_lod_display_markers(self, markers: list[ImageMarker]) -> list[ImageMarker]:
        screen_scale = self.view._zoom_scale()
        static_markers: list[ImageMarker] = []
        batch_markers: list[ImageMarker] = []
        unattributed: list[ImageMarker] = []
        for marker in markers:
            role = marker.metadata.get("atlas_lod_role")
            if role == "batch_position":
                batch_markers.append(marker)
                continue
            if role == "unattributed":
                unattributed.append(marker)
                continue
            if role == "search_map_footprint":
                if marker.bbox is not None and screen_extent_visible(
                    marker.bbox[2],
                    marker.bbox[3],
                    screen_scale=screen_scale,
                    threshold_px=SEARCH_MAP_FOOTPRINT_MIN_SCREEN_PX,
                ):
                    static_markers.append(marker)
                continue
            if role == "search_map_tile":
                if (
                    self.atlas_lod_options[ATLAS_LOD_TILE_GRIDS]
                    and marker.bbox is not None
                    and screen_extent_visible(
                        marker.bbox[2],
                        marker.bbox[3],
                        screen_scale=screen_scale,
                        threshold_px=SEARCH_MAP_TILE_MIN_SCREEN_PX,
                        use_short_edge=True,
                    )
                ):
                    static_markers.append(marker)
                continue
            if role == "per_exposure":
                width, height = marker_scene_extent(marker)
                if (
                    self.atlas_lod_options[ATLAS_LOD_EXPOSURES]
                    and screen_extent_visible(
                        width,
                        height,
                        screen_scale=screen_scale,
                        threshold_px=PER_EXPOSURE_MIN_SCREEN_PX,
                        use_short_edge=True,
                    )
                ):
                    static_markers.append(marker)
                continue
            static_markers.append(marker)

        cluster_inputs = atlas_cluster_inputs(batch_markers)
        scope_id = next(
            (marker.source_object_id for marker in batch_markers if marker.source_object_id),
            self.view.parts.preview_image.current_image_key,
        )
        fingerprint = marker_set_fingerprint(cluster_inputs)
        clustering_enabled = self.atlas_lod_options[ATLAS_LOD_CLUSTER]
        cache_key = (
            scope_id,
            zoom_bucket(screen_scale),
            fingerprint,
            clustering_enabled,
            self.view.parts.preview_camera.zoom >= NO_CLUSTER_ABOVE_ZOOM,
        )
        clusters = self.cluster_cache.get(cache_key)
        if clusters is None:
            clusters = cluster_markers(
                cluster_inputs,
                transform=ScreenTransform(screen_scale, screen_scale),
                logical_zoom=(
                    self.view.parts.preview_camera.zoom
                    if clustering_enabled
                    else NO_CLUSTER_ABOVE_ZOOM + 1.0
                ),
            )
            self.cluster_cache[cache_key] = clusters
            self.cluster_compute_count += 1

        # Which positions are leaves, which merge, and which get numbers is
        # the shared Atlas rule (services/atlas_lod.py), which the PDF follows.
        view = atlas_batch_view(
            batch_markers,
            unattributed,
            screen_scale=screen_scale,
            logical_zoom=self.view.parts.preview_camera.zoom,
            labels=self.atlas_lod_options[ATLAS_LOD_LABELS],
            clusters=clusters,
        )
        cluster_index = {id(cluster): index for index, cluster in enumerate(clusters)}
        cluster_markers_out: list[ImageMarker] = []
        for cluster, members in view.clusters:
            index = cluster_index[id(cluster)]
            summaries = [
                f"{member.label or member.id}: {(member.status or 'unknown').title()}"
                for member in members
            ]
            status_counts = dict(cluster.status_counts)
            cluster_markers_out.append(
                ImageMarker(
                    id=f"{scope_id}:batch-cluster:{index}:{fingerprint[:10]}",
                    marker_type=MarkerType.BATCH_CLUSTER,
                    linked_object_id=None,
                    source_object_id=str(scope_id) if scope_id is not None else None,
                    x=cluster.centroid_scene[0],
                    y=cluster.centroid_scene[1],
                    radius=cluster.diameter_px / (2 * max(screen_scale, 0.01)),
                    tooltip="\n".join(
                        [f"{len(members)} batch positions", *summaries]
                    ),
                    status=cluster.status,
                    selected=self.view.parts.preview_interaction.keyboard_marker_id
                    == f"{scope_id}:batch-cluster:{index}:{fingerprint[:10]}",
                    metadata={
                        "diameter_px": cluster.diameter_px,
                        "member_ids": cluster.member_ids,
                        "member_bounds_scene": cluster.bounds_scene,
                        "status_counts": status_counts,
                        "navigation_enabled": True,
                    },
                )
            )

        leaves = list(view.leaves)
        for leaf in leaves:
            placement = view.labels.get(leaf.id)
            if placement is None:
                leaf.label = None
            else:
                leaf.metadata["atlas_label_placement"] = placement
            leaf.selected = leaf.selected or leaf.id == self.view.parts.preview_interaction.keyboard_marker_id
            leaf.metadata["diameter_px"] = LEAF_DIAMETER_DP
        return static_markers + cluster_markers_out + leaves

    def add_cross_marker(self, marker: ImageMarker, radius: float, color: QColor) -> None:
        assert marker.x is not None and marker.y is not None
        pen = QPen(color, 2.0 if marker.selected else 1.5)
        lines = [
            self.view.parts.preview_image.scene.addLine(marker.x - radius, marker.y - radius, marker.x + radius, marker.y + radius, pen),
            self.view.parts.preview_image.scene.addLine(marker.x - radius, marker.y + radius, marker.x + radius, marker.y - radius, pen),
        ]
        for line in lines:
            line.setZValue(22 if marker.selected else 17)
            line.setData(0, marker)
            line.setToolTip(marker.tooltip or marker.label or marker.marker_type)
            self.marker_items.append(line)

    def add_axis_crosshair(self, marker: ImageMarker, length: float, color: QColor) -> None:
        """Render an axis-aligned crosshair (vertical + horizontal) for a STAGE_CROSSHAIR marker."""

        assert marker.x is not None and marker.y is not None
        pen = QPen(color, 1.6)
        pen.setStyle(Qt.PenStyle.DashLine)
        lines = [
            self.view.parts.preview_image.scene.addLine(marker.x - length, marker.y, marker.x + length, marker.y, pen),
            self.view.parts.preview_image.scene.addLine(marker.x, marker.y - length, marker.x, marker.y + length, pen),
        ]
        for line in lines:
            line.setZValue(18)
            line.setData(0, marker)
            line.setToolTip(marker.tooltip or marker.label or "Stage origin")
            self.marker_items.append(line)

    def project_marker(self, marker: ImageMarker) -> ImageMarker:
        image_size = marker.metadata.get("image_size")
        if not (
            isinstance(image_size, tuple)
            and len(image_size) == 2
            and isinstance(image_size[0], int | float)
            and isinstance(image_size[1], int | float)
            and self.view.parts.preview_image.current_image_shape is not None
        ):
            return replace(marker, metadata=dict(marker.metadata))
        source_width, source_height = float(image_size[0]), float(image_size[1])
        if source_width <= 0 or source_height <= 0:
            return replace(marker, metadata=dict(marker.metadata))
        displayed_height, displayed_width = self.view.parts.preview_image.current_image_shape
        scale_x = displayed_width / source_width
        scale_y = displayed_height / source_height
        bbox = None
        if marker.bbox is not None:
            x, y, width, height = marker.bbox
            bbox = (x * scale_x, y * scale_y, width * scale_x, height * scale_y)
        polygon = [(x * scale_x, y * scale_y) for x, y in marker.polygon] if marker.polygon else None
        metadata = dict(marker.metadata)
        if marker.marker_type == MarkerType.BATCH_LABEL:
            geometry = metadata_box(metadata.get("label_source_geometry"))
            if geometry is not None:
                gx, gy, gw, gh = geometry
                metadata["label_source_geometry"] = (gx * scale_x, gy * scale_y, gw * scale_x, gh * scale_y)
            anchor = metadata_point(metadata.get("label_anchor"))
            if anchor is not None:
                metadata["label_anchor"] = (anchor[0] * scale_x, anchor[1] * scale_y)

        return ImageMarker(
            id=marker.id,
            marker_type=marker.marker_type,
            linked_object_id=marker.linked_object_id,
            source_object_id=marker.source_object_id,
            x=marker.x * scale_x if marker.x is not None else None,
            y=marker.y * scale_y if marker.y is not None else None,
            radius=marker.radius * ((scale_x + scale_y) / 2) if marker.radius is not None else None,
            bbox=bbox,
            polygon=polygon,
            label=marker.label,
            tooltip=marker.tooltip,
            status=marker.status,
            visible=marker.visible,
            selected=marker.selected,
            unresolved=marker.unresolved,
            metadata=metadata,
        )

