"""Undo/redo for edits (CLI and UI share it).

Each edit stores the project as it was BEFORE the change, as one small gzip
file in a folder next to the project:

    <project>.history.d/undo/000001.json.gz ...   (newest = highest number)
    <project>.history.d/redo/...

Only one file is written per edit, so editing stays fast even for big songs
with a long history. (Older versions kept everything in one
`<project>.history.json`; it is converted on first use.)
"""
from __future__ import annotations

import gzip
import json
import os
import tempfile
from pathlib import Path

from .model import Project, ProjectError, dumps, save

LIMIT = 100


def _dir(project_path: Path) -> Path:
    return project_path.with_name(project_path.name + ".history.d")


def _old_file(project_path: Path) -> Path:
    return project_path.with_name(project_path.name + ".history.json")


def _stack(project_path: Path, name: str) -> Path:
    d = _dir(project_path) / name
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d.parent, 0o700)
        os.chmod(d, 0o700)
    return d


def _entries(stack: Path) -> list[Path]:
    """Files of a stack, oldest first."""
    return sorted(p for p in stack.glob("*.json.gz") if p.name.split(".")[0].isdigit())


def _push(stack: Path, label: str, state_text: str) -> None:
    files = _entries(stack)
    n = int(files[-1].name.split(".")[0]) + 1 if files else 1
    body = json.dumps({"label": label}) + "\n" + state_text  # line 1: label, rest: project JSON
    fd, tmp = tempfile.mkstemp(dir=stack, prefix=".tmp-", suffix=".gz")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(gzip.compress(body.encode("utf-8"), compresslevel=1))
        os.replace(tmp, stack / f"{n:06d}.json.gz")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _pop(stack: Path) -> tuple[str, str] | None:
    """Newest entry (label, project JSON), removed from the stack. Broken files are skipped."""
    for path in reversed(_entries(stack)):
        try:
            text = gzip.decompress(path.read_bytes()).decode("utf-8")
            head, state = text.split("\n", 1)
            label = json.loads(head)["label"]
        except (OSError, ValueError, KeyError, EOFError):
            path.unlink(missing_ok=True)  # broken entry: drop it, never block editing
            continue
        path.unlink()
        return label, state
    return None


def _clear(stack: Path) -> None:
    for p in _entries(stack):
        p.unlink(missing_ok=True)


def _trim(stack: Path) -> None:
    files = _entries(stack)
    for p in files[:-LIMIT]:
        p.unlink(missing_ok=True)


def _migrate(project_path: Path) -> None:
    """Convert the old single-file history once."""
    old = _old_file(project_path)
    if not old.exists():
        return
    try:
        data = json.loads(old.read_text(encoding="utf-8"))
        for name in ("undo", "redo"):
            stack = _stack(project_path, name)
            if _entries(stack):
                continue
            for e in data.get(name, [])[-LIMIT:]:
                _push(stack, str(e["label"]), json.dumps(e["state"], sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass  # broken old history: start fresh
    old.unlink(missing_ok=True)


def commit(project_path: str | Path, before: Project, after: Project, label: str,
           before_text: str | None = None) -> str:
    """Save `after` and remember `before` so it can be undone. Returns the saved text of `after`.

    before_text: `before` already as JSON (the UI keeps it), so it isn't encoded twice.
    """
    project_path = Path(project_path)
    _migrate(project_path)
    text = save(after, project_path)
    undo = _stack(project_path, "undo")
    _push(undo, label, before_text if before_text is not None else dumps(before))
    _trim(undo)
    _clear(_stack(project_path, "redo"))
    return text


def _step(project_path: Path, src: str, dst: str) -> str:
    _migrate(project_path)
    entry = _pop(_stack(project_path, src))
    if entry is None:
        raise ProjectError(f"nothing to {src}")
    label, state = entry
    try:
        restored = Project.from_dict(json.loads(state))  # check it before changing anything
    except ValueError as e:
        raise ProjectError(f"broken history entry ({label}): {e}") from e
    current_text = project_path.read_text(encoding="utf-8")
    _push(_stack(project_path, dst), label, current_text)
    _trim(_stack(project_path, dst))
    save(restored, project_path)
    return label


def undo(project_path: str | Path) -> str:
    return _step(Path(project_path), "undo", "redo")


def redo(project_path: str | Path) -> str:
    return _step(Path(project_path), "redo", "undo")
