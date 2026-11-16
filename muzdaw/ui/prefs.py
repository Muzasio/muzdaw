"""Small UI preferences (window layout, toggles) in $XDG_CONFIG_HOME/muzdaw/ui.json (file 600)."""
from __future__ import annotations

import json
import os


def _path():
    from .session import config_dir
    return config_dir() / "ui.json"


def load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def get(key: str, default=None):
    return load().get(key, default)


def set_(key: str, value) -> None:
    """Store one value (JSON types only). Never raises: a preference must not break the app."""
    d = load()
    d[key] = value
    try:
        f = _path()
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.chmod(f, 0o600)
    except OSError:
        pass
