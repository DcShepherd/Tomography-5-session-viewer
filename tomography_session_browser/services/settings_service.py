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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        # Be permissive: ignore unknown keys, fall back on defaults for bad
        # values. This prevents an old settings file from blocking startup
        # after a schema change.
        defaults = cls()
        report_dir = data.get("last_report_directory")
        return cls(
            theme=str(data.get("theme", defaults.theme)) or defaults.theme,
            compact=bool(data.get("compact", defaults.compact)),
            project_panel_visible=bool(data.get("project_panel_visible", defaults.project_panel_visible)),
            context_panel_visible=bool(data.get("context_panel_visible", defaults.context_panel_visible)),
            last_tab=str(data.get("last_tab", defaults.last_tab)) or defaults.last_tab,
            recent_sessions=[str(p) for p in data.get("recent_sessions", []) if p],
            last_report_directory=str(report_dir) if report_dir else None,
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
