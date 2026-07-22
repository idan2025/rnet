"""Persistent user settings for the GUI (~/.rnet/settings.json).

Keeps theme, default identity, node start config, download dir, and
window geometry. Survives restarts. Lives outside the SQLite DB so it can
be read before the controller opens the DB and edited by hand if needed.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


DEFAULTS: Dict[str, Any] = {
    "theme": "dark",
    "default_identity": None,
    "capabilities": ["messaging", "relay", "naming", "storage"],
    "low_power": False,
    "max_bandwidth": "medium",
    "announce_interval": 120.0,
    "download_dir": None,
    "window_geometry": None,
    "active_tab": 0,
    "autostart": True,
}


class SettingsStore:
    def __init__(self, path: str):
        self.path = path
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Merge so new defaults backfill older files.
                merged = dict(DEFAULTS)
                merged.update(data)
                self._data = merged
        except FileNotFoundError:
            pass
        except Exception:  # pragma: no cover - corrupt settings shouldn't crash GUI
            self._data = dict(DEFAULTS)

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception:  # pragma: no cover
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def update(self, **kwargs: Any) -> None:
        self._data.update(kwargs)
        self.save()

    def all(self) -> Dict[str, Any]:
        return dict(self._data)