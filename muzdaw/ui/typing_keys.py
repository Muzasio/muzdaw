"""Typing keyboard: computer keys play notes on the selected channel (while the window is active).

Each key row is a chromatic run of notes from its own start note, so rows can
sit in any octave (or any start note) you like. Settings live in
$XDG_CONFIG_HOME/muzdaw/keyboard.json (file 600).

PageUp / PageDown shift every row by one octave.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QLabel, QLineEdit, QSpinBox, QTextEdit, QVBoxLayout)

from ..cli import parse_pitch
from ..model import ProjectError
from .piano_roll import note_name

DEFAULT_ROWS = [["qwertyuiop[]", 72], ["asdfghjkl;'", 60], ["zxcvbnm,./", 48]]  # C5, C4, C3


@dataclass
class KeyLayout:
    enabled: bool = True
    velocity: int = 100
    octave: int = 0                       # shift for every row, in octaves
    rows: list = field(default_factory=lambda: [list(r) for r in DEFAULT_ROWS])  # [keys, start pitch]

    def validate(self) -> None:
        if not 1 <= self.velocity <= 127:
            raise ProjectError("velocity must be 1-127")
        if not -5 <= self.octave <= 5:
            raise ProjectError("octave shift must be -5..5")
        seen = set()
        for keys, start in self.rows:
            if not isinstance(keys, str) or not 0 <= int(start) <= 127:
                raise ProjectError(f"bad row {keys!r} {start!r}")
            for k in keys:
                if k in seen:
                    raise ProjectError(f"key {k!r} is used twice")
                seen.add(k)

    def pitch_of(self, char: str) -> int | None:
        for keys, start in self.rows:
            i = keys.find(char)
            if i >= 0:
                p = int(start) + i + 12 * self.octave
                return p if 0 <= p <= 127 else None
        return None

    def describe(self) -> list[str]:
        out = []
        for keys, start in self.rows:
            lo, hi = int(start) + 12 * self.octave, int(start) + len(keys) - 1 + 12 * self.octave
            out.append(f"{' '.join(keys)}   →   {note_name(max(0, lo))} … {note_name(min(127, hi))}")
        return out


def config_path():
    from .session import config_dir
    return config_dir() / "keyboard.json"


def load_layout() -> KeyLayout:
    try:
        d = json.loads(config_path().read_text(encoding="utf-8"))
        lay = KeyLayout(enabled=bool(d["enabled"]), velocity=int(d["velocity"]), octave=int(d["octave"]),
                        rows=[[str(k), int(s)] for k, s in d["rows"]])
        lay.validate()
        return lay
    except (OSError, ValueError, KeyError, TypeError, ProjectError):
        return KeyLayout()


def save_layout(lay: KeyLayout) -> None:
    lay.validate()
    f = config_path()
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(asdict(lay), fh, indent=1)
    os.chmod(f, 0o600)


def key_char(key: int) -> str | None:
    """Qt key code -> lower-case char ('Q' -> 'q', BracketLeft -> '[')."""
    return chr(key).lower() if 0x20 < key < 0x7f else None


class TypingKeyboard(QObject):
    """App-wide key filter. Emits noteOn/noteOff; only for the given window, never inside text boxes."""
    noteOn = Signal(int, int)    # pitch, velocity
    noteOff = Signal(int)        # pitch
    changed = Signal()           # layout changed (octave, on/off)

    def __init__(self, window, layout: KeyLayout | None = None):
        super().__init__(window)
        self.window = window
        self.layout = layout or load_layout()
        self.held: dict[str, int] = {}  # key char -> pitch
        QApplication.instance().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self._app_state)

    def _typing_in_text(self) -> bool:
        w = QApplication.focusWidget()
        if isinstance(w, (QLineEdit, QTextEdit, QAbstractSpinBox)):
            return True
        return isinstance(w, QComboBox) and w.isEditable()

    def _ours(self, e) -> bool:
        if not self.layout.enabled or QApplication.activeWindow() is not self.window:
            return False
        if e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                            | Qt.KeyboardModifier.MetaModifier):
            return False
        return not self._typing_in_text()

    _KEY_EVENTS = frozenset((QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride))

    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t not in self._KEY_EVENTS:  # most events (paint, mouse...) leave here
            return False
        if not self._ours(e):
            return False
        key = e.key()
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            if t == QEvent.Type.KeyPress and not e.isAutoRepeat():
                self.shift_octave(1 if key == Qt.Key.Key_PageUp else -1)
            e.accept()
            return True
        ch = key_char(key)
        if ch is None or (self.layout.pitch_of(ch) is None and ch not in self.held):
            return False
        if t == QEvent.Type.ShortcutOverride:
            e.accept()  # our key: don't let a shortcut or a focused widget take it
            return True
        if e.isAutoRepeat():
            return True
        # returning True eats the event at its first receiver, so each key is handled once
        if t == QEvent.Type.KeyPress and ch not in self.held:
            pitch = self.layout.pitch_of(ch)
            self.held[ch] = pitch
            self.noteOn.emit(pitch, self.layout.velocity)
        elif t == QEvent.Type.KeyRelease and ch in self.held:
            self.noteOff.emit(self.held.pop(ch))
        return True

    def release_all(self) -> None:
        for ch in list(self.held):
            self.noteOff.emit(self.held.pop(ch))

    def _app_state(self, state) -> None:
        if state != Qt.ApplicationState.ApplicationActive:
            self.release_all()  # window lost focus: no stuck notes

    def shift_octave(self, d: int) -> None:
        self.release_all()
        self.layout.octave = max(-5, min(5, self.layout.octave + d))
        self._store()

    def set_enabled(self, on: bool) -> None:
        self.release_all()
        self.layout.enabled = on
        self._store()

    def set_layout(self, lay: KeyLayout) -> None:
        self.release_all()
        lay.validate()
        self.layout = lay
        self._store()

    def _store(self) -> None:
        try:
            save_layout(self.layout)
        except OSError:
            pass
        self.changed.emit()


class KeyboardDialog(QDialog):
    """Set each row's start note, the octave shift and the velocity."""

    def __init__(self, parent, layout: KeyLayout):
        super().__init__(parent)
        self.setWindowTitle("Typing keyboard")
        self.layout_ = KeyLayout(**asdict(layout))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Each key row plays notes in a row (12 keys = one octave).\n"
                             "Type the row's first note: C4, F#3, Bb2 or a number 0-127.\n"
                             "PageUp / PageDown in the main window shift all rows by one octave."))
        form = QFormLayout()
        self.enabled = QCheckBox("On")
        self.enabled.setChecked(layout.enabled)
        form.addRow("Typing keyboard", self.enabled)
        self.starts = []
        for keys, start in layout.rows:
            box = QLineEdit(note_name(int(start)))
            box.setMaximumWidth(80)
            self.starts.append(box)
            form.addRow(f"{keys}  starts at", box)
        self.octave = QSpinBox()
        self.octave.setRange(-5, 5)
        self.octave.setValue(layout.octave)
        form.addRow("Octave shift (all rows)", self.octave)
        self.velocity = QSpinBox()
        self.velocity.setRange(1, 127)
        self.velocity.setValue(layout.velocity)
        form.addRow("Velocity (1 soft - 127 hard)", self.velocity)
        lay.addLayout(form)
        self.error = QLabel()
        self.error.setStyleSheet("color: #e05050")
        lay.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                              | QDialogButtonBox.StandardButton.RestoreDefaults)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._defaults)
        lay.addWidget(bb)

    def _defaults(self) -> None:
        for box, (_, start) in zip(self.starts, DEFAULT_ROWS):
            box.setText(note_name(start))
        self.octave.setValue(0)
        self.velocity.setValue(100)

    def result_layout(self) -> KeyLayout:
        rows = []
        for box, (keys, _) in zip(self.starts, self.layout_.rows):
            rows.append([keys, parse_pitch(box.text().strip())])
        lay = KeyLayout(enabled=self.enabled.isChecked(), velocity=self.velocity.value(),
                        octave=self.octave.value(), rows=rows)
        lay.validate()
        return lay

    def _ok(self) -> None:
        try:
            self.layout_ = self.result_layout()
        except ProjectError as e:
            self.error.setText(str(e))
            return
        self.accept()
