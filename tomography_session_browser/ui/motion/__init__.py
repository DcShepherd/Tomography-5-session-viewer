"""Compatibility exports; implementation lives in ui.motion."""
from __future__ import annotations

from collections.abc import Callable

from dataclasses import dataclass

import logging

import math

import os

import subprocess

import sys

import threading

import time

import weakref

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer

from PySide6.QtGui import QBrush, QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap, QRegion, QTransform

from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QGraphicsOpacityEffect,
    QWidget,
)

import shiboken6

from tomography_session_browser.ui.motion.tokens import ANNULUS_BAND_PX
from tomography_session_browser.ui.motion.contracts import Animation
from tomography_session_browser.ui.motion.branch_reveal import BranchReveal
from tomography_session_browser.ui.motion.tokens import CAMERA_PATH_RHO
from tomography_session_browser.ui.motion.tokens import DURATION_CAMERA_MAX_MS
from tomography_session_browser.ui.motion.tokens import DURATION_CAMERA_MS
from tomography_session_browser.ui.motion.tokens import DURATION_GOO_MS
from tomography_session_browser.ui.motion.tokens import DURATION_MICRO_MS
from tomography_session_browser.ui.motion.tokens import DURATION_PANEL_MS
from tomography_session_browser.ui.motion.tokens import DURATION_PULSE_MS
from tomography_session_browser.ui.motion.tokens import DURATION_SMALL_MS
from tomography_session_browser.ui.motion.tokens import FRAME_INTERVAL_MS
from tomography_session_browser.ui.motion.tokens import INTERRUPTING_INPUT
from tomography_session_browser.ui.motion.hover import ItemHoverAnimator
from tomography_session_browser.ui.motion.preferences import LOGGER
from tomography_session_browser.ui.motion.tokens import MAX_STEP_S
from tomography_session_browser.ui.motion.tokens import MOTION_MICRO
from tomography_session_browser.ui.motion.tokens import MOTION_PANEL
from tomography_session_browser.ui.motion.tokens import MOTION_SCROLL
from tomography_session_browser.ui.motion.tokens import MOTION_SMALL
from tomography_session_browser.ui.motion.tokens import MOTION_ZOOM
from tomography_session_browser.ui.motion.pulse import MarkerPulse
from tomography_session_browser.ui.motion.tokens import PAGE_FADE_START_OPACITY
from tomography_session_browser.ui.motion.fade import PageFade
from tomography_session_browser.ui.motion.panel_slide import PanelSlide
from tomography_session_browser.ui.motion.fade import PictureFade
from tomography_session_browser.ui.motion.tokens import STAGGER_MAX_ITEMS
from tomography_session_browser.ui.motion.tokens import STAGGER_MS
from tomography_session_browser.ui.motion.section_reveal import SectionReveal
from tomography_session_browser.ui.motion.scroll import SmoothScroller
from tomography_session_browser.ui.motion.spring import Spring
from tomography_session_browser.ui.motion.tokens import SpringSpec
from tomography_session_browser.ui.motion.tokens import THEME_REVEAL_FEATHER_PX
from tomography_session_browser.ui.motion.tokens import THEME_REVEAL_FEATHER_RINGS
from tomography_session_browser.ui.motion.tokens import THEME_REVEAL_GLOW_ALPHA
from tomography_session_browser.ui.motion.tokens import THEME_REVEAL_GLOW_WIDTH_PX
from tomography_session_browser.ui.motion.tokens import THEME_REVEAL_MS
from tomography_session_browser.ui.motion.tokens import THEME_REVEAL_SETTLE_TURNS
from tomography_session_browser.ui.motion.ticker import TICKER
from tomography_session_browser.ui.motion.theme_reveal import ThemeRevealOverlay
from tomography_session_browser.ui.motion.theme_reveal import ThemeSeed
from tomography_session_browser.ui.motion.tween import Tween
from tomography_session_browser.ui.motion.camera import ZoomPanPath
from tomography_session_browser.ui.motion.section_reveal import CONCEAL_EFFECT_NAME as _CONCEAL_EFFECT_NAME
from tomography_session_browser.ui.motion.preferences import ENV_VAR as _ENV_VAR
from tomography_session_browser.ui.motion.preferences import FALSY as _FALSY
from tomography_session_browser.ui.motion.tokens import HOVER_EVENTS as _HOVER_EVENTS
from tomography_session_browser.ui.motion.spring import SETTLE_DISTANCE as _SETTLE_DISTANCE
from tomography_session_browser.ui.motion.spring import SETTLE_VELOCITY as _SETTLE_VELOCITY
from tomography_session_browser.ui.motion.panel_slide import SlideBackdrop as _SlideBackdrop
from tomography_session_browser.ui.motion.preferences import TRUTHY as _TRUTHY
from tomography_session_browser.ui.motion.ticker import Ticker as _Ticker
from tomography_session_browser.ui.motion.preferences import WATCH_PROPERTY as _WATCH_PROPERTY
from tomography_session_browser.ui.motion.tokens import WHEEL_MODIFIERS as _WHEEL_MODIFIERS
from tomography_session_browser.ui.motion.preferences import application_state_changed as _application_state_changed
from tomography_session_browser.ui.motion.section_reveal import conceal_with_effect as _conceal_with_effect
from tomography_session_browser.ui.motion.preferences import query_system_reduced_motion as _query_system_reduced_motion
from tomography_session_browser.ui.motion.hover import row_key as _row_key
from tomography_session_browser.ui.motion.preferences import system_reduced_motion_cache as _system_reduced_motion
from tomography_session_browser.ui.motion.theme_reveal import annulus_region
from tomography_session_browser.ui.motion.colors import blend
from tomography_session_browser.ui.motion.camera import camera_duration_ms
from tomography_session_browser.ui.motion.easing import ease_in_out_cubic
from tomography_session_browser.ui.motion.easing import ease_out_cubic
from tomography_session_browser.ui.motion.section_reveal import live_section_reveal
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.theme_reveal import paint_theme_reveal
from tomography_session_browser.ui.motion.theme_reveal import paint_theme_seed
from tomography_session_browser.ui.motion.section_reveal import reveal_strips
from tomography_session_browser.ui.motion.section_reveal import settle_layouts
from tomography_session_browser.ui.motion.preferences import system_prefers_reduced_motion
from tomography_session_browser.ui.motion.fade import veil
from tomography_session_browser.ui.motion.preferences import watch_system_reduced_motion
from tomography_session_browser.ui.motion.camera import zoom_pan_path
