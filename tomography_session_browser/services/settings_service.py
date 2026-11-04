"""Application settings persistence.

Stores a small JSON document under the user's per-OS config directory so the
window restores theme, panel visibility, and last-tab choice between runs.
Deliberately tolerant: a corrupted or partially-written settings file is
treated as "no saved state" rather than an error.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass
class Settings:
    theme: str = "dark"
    compact: bool = False
    project_panel_visible: bool = True
    context_panel_visible: bool = True
    last_tab: str = "Session"
    recent_sessions: list[str] = field(default_factory=list)
    last_report_directory: str | None = None
    tomogram_snapshot_folder: str | None = None
    tomogram_memory_percent: int = 40
    tomogram_navigator_visible: bool = True
    tomogram_tags_splitter: list[int] = field(default_factory=lambda: [400, 240])
    # Movie export choices, remembered after a successful export (folder, Save,
    # Compression, Size, Shape, Framing, Loop-ready, label chips). Never the
    # source, planes, rate or end behaviour; the movie presenter validates them.
    tomogram_movie: dict[str, str | bool] = field(default_factory=dict)
    # Base64 of ``QWidget.saveGeometry()``: window size, position and
    # maximised state. Display preference only; empty means "first run".
    window_geometry: str = ""
    # The report dialog's "exposure thumbnails" option, as last chosen.
    report_exposure_thumbnails: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        # Be permissive: ignore unknown keys, fall back on defaults for bad
        # values. This prevents an old settings file from blocking startup
        # after a schema change.
        defaults = cls()
        report_dir = data.get("last_report_directory")
        geometry = data.get("window_geometry")
        return cls(
            theme=str(data.get("theme", defaults.theme)) or defaults.theme,
            compact=bool(data.get("compact", defaults.compact)),
            project_panel_visible=bool(data.get("project_panel_visible", defaults.project_panel_visible)),
            context_panel_visible=bool(data.get("context_panel_visible", defaults.context_panel_visible)),
            last_tab=str(data.get("last_tab", defaults.last_tab)) or defaults.last_tab,
            recent_sessions=[str(p) for p in data.get("recent_sessions", []) if p],
            last_report_directory=str(report_dir) if report_dir else None,
            window_geometry=geometry if isinstance(geometry, str) else defaults.window_geometry,
            report_exposure_thumbnails=data.get("report_exposure_thumbnails") is True,
            tomogram_snapshot_folder=data.get("tomogram_snapshot_folder") if isinstance(data.get("tomogram_snapshot_folder"), str) else None,
            tomogram_memory_percent=data["tomogram_memory_percent"] if type(data.get("tomogram_memory_percent")) is int and 10 <= data["tomogram_memory_percent"] <= 60 else 40,
            tomogram_navigator_visible=data["tomogram_navigator_visible"] if type(data.get("tomogram_navigator_visible")) is bool else True,
            tomogram_tags_splitter=list(data["tomogram_tags_splitter"]) if isinstance(data.get("tomogram_tags_splitter"), list)
                and len(data["tomogram_tags_splitter"]) == 2 and all(type(v) is int and 0 < v <= 10000 for v in data["tomogram_tags_splitter"])
                else [400, 240],
            tomogram_movie={key: value for key, value in data["tomogram_movie"].items()
                            if isinstance(key, str) and type(value) in (str, bool)}
                if isinstance(data.get("tomogram_movie"), dict) else {},
        )


def _config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "TomographySessionBrowser"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "TomographySessionBrowser"
    return Path.home() / ".config" / "TomographySessionBrowser"


def settings_path() -> Path:
    return _config_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    target = path or settings_path()
    try:
        if not target.exists():
            return Settings()
        text = target.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return Settings()
        return Settings.from_dict(data)
    except (OSError, ValueError) as exc:
        LOGGER.warning("Could not read settings from %s: %s", target, exc)
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    target = path or settings_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("Could not save settings to %s: %s", target, exc)


def add_recent_session(settings: Settings, session_path: str, *, limit: int = 8) -> Settings:
    """Insert ``session_path`` at the head of recent sessions, deduped."""

    cleaned = [str(p) for p in settings.recent_sessions if p and p != session_path]
    cleaned.insert(0, session_path)
    settings.recent_sessions = cleaned[:limit]
    return settings
