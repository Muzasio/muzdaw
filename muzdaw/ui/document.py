"""The open project: every UI edit goes through `edit()`, which saves + records undo."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import history
from ..model import Pattern, Project, ProjectError, dumps, load


class Document(QObject):
    changed = Signal()          # project content changed (edit, undo, redo, reload)
    selection = Signal()        # current pattern / channel changed
    message = Signal(str)       # status text

    def __init__(self, path: str | Path):
        super().__init__()
        self.path = Path(path)
        self.project: Project = load(self.path)
        self._text = dumps(self.project)  # current project as saved JSON (reused for undo)
        self.pattern: str | None = None
        self.channel: str | None = None
        self._fix_selection()

    # ---- editing -----------------------------------------------------------
    def edit(self, label: str, fn) -> bool:
        """Apply fn(project_copy). On any error nothing changes and the reason is shown."""
        before = self.project
        after = before.clone()
        try:
            fn(after)
            after.validate()
            self._text = history.commit(self.path, before, after, label, before_text=self._text)
        except (ProjectError, ValueError, KeyError, IndexError) as e:
            self.message.emit(f"not changed: {e}")
            return False
        self.project = after
        self._fix_selection()
        self.changed.emit()
        self.message.emit(label)
        return True

    def reload(self, word: str = "reloaded") -> None:
        """Read the file again after another process (CLI) changed it."""
        try:
            self.project = load(self.path)
        except ProjectError as e:
            self.message.emit(str(e))
            return
        self._text = dumps(self.project)
        self._fix_selection()
        self.changed.emit()
        self.message.emit(word)

    def undo(self) -> None:
        self._step(history.undo, "undo")

    def redo(self) -> None:
        self._step(history.redo, "redo")

    def _step(self, fn, word: str) -> None:
        try:
            label = fn(self.path)
        except ProjectError as e:
            self.message.emit(str(e))
            return
        self.project = load(self.path)
        self._text = dumps(self.project)
        self._fix_selection()
        self.changed.emit()
        self.message.emit(f"{word}: {label}")

    # ---- selection ---------------------------------------------------------
    def select(self, pattern: str | None = None, channel: str | None = None) -> None:
        if pattern is not None:
            self.pattern = pattern
        if channel is not None:
            self.channel = channel
        self._fix_selection()
        self.selection.emit()

    def _fix_selection(self) -> None:
        pats = [p.name for p in self.project.patterns]
        chans = [c.name for c in self.project.channels]
        if self.pattern not in pats:
            self.pattern = pats[0] if pats else None
        if self.channel not in chans:
            self.channel = chans[0] if chans else None

    def current_pattern(self) -> Pattern | None:
        return self.project.pattern(self.pattern) if self.pattern else None
