"""Where the UI keeps unsaved songs and which song was open last.

- Unsaved songs: $XDG_DATA_HOME/muzdaw/untitled*.muz.json (folder 700).
- Last open song: $XDG_CONFIG_HOME/muzdaw/recent.json (folder 700, file 600).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..model import Channel, Pattern, Project, save
from ..presets import PRESETS

# A new song starts like FL: a few ready channels and an empty Pattern 1 (all fast, no Surge).
STARTER = [("kick", "kick"), ("clap", "gm-drums"), ("hat", "hat"), ("bass", "bass"), ("piano", "piano")]


def starter_project() -> Project:
    p = Project(name="untitled")
    p.channels = [Channel(name=n, **PRESETS[preset]) for n, preset in STARTER if preset in PRESETS]
    p.patterns = [Pattern("Pattern 1")]
    return p


def _dir(env: str, fallback: str) -> Path:
    base = Path(os.environ.get(env) or Path.home() / fallback)
    d = base / "muzdaw"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def data_dir() -> Path:
    return _dir("XDG_DATA_HOME", ".local/share")


def config_dir() -> Path:
    return _dir("XDG_CONFIG_HOME", ".config")


def is_untitled(path: Path) -> bool:
    return path.resolve().parent == data_dir().resolve() and path.name.startswith("untitled")


def new_untitled() -> Path:
    """A fresh empty song in the data folder (never overwrites an older one)."""
    d = data_dir()
    path = d / "untitled.muz.json"
    if path.exists():
        path = d / f"untitled-{time.strftime('%Y%m%d-%H%M%S')}.muz.json"
        i = 2
        while path.exists():
            path = d / f"untitled-{time.strftime('%Y%m%d-%H%M%S')}-{i}.muz.json"
            i += 1
    save(starter_project(), path)
    return path


def last_song() -> Path | None:
    f = config_dir() / "recent.json"
    try:
        p = Path(json.loads(f.read_text(encoding="utf-8"))["last"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return p if p.is_file() else None


def remember(path: Path) -> None:
    f = config_dir() / "recent.json"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"last": str(Path(path).resolve())}, fh)
    os.chmod(f, 0o600)
