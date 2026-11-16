"""Piano Roll: notes of the current channel in the current pattern.

Mouse: click empty = add note (last length); click a note = select it (Shift+click = add/remove);
drag = move all selected (up/down = pitch); drag a note's right edge = resize all selected;
Ctrl+drag on empty grid = box select; right click = delete (all selected if the note is selected);
hold right + sweep over notes = erase every note you pass (FL style, one undo step);
Alt = no snap; Alt+wheel on a note = velocity.
Keys: Delete, Ctrl+A, Ctrl+C, Ctrl+V (paste right after the copy, again = continue), Ctrl+D (duplicate),
Up/Down = semitone (Shift = octave), Left/Right = one snap step, Esc = clear selection.
Velocity lane under the grid: drag up/down over a note's bar = how hard it plays.
Piano keys on the left: hold = the note sounds while held (quick click = short hit);
click near the key's left end = soft, near its right end = hard; drag = glide over keys.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..model import Note

KEYS_W = 110
ROW_H = 16
LANE_H = 64
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
BLACK = {1, 3, 6, 8, 10}
EDGE = 6  # px: grab zone for resizing


def note_name(pitch: int) -> str:
    return f"{NAMES[pitch % 12]}{pitch // 12 - 1}"


def match_indices(notes: list[Note], wanted: list[Note]) -> set[int]:
    """Indices of `wanted` notes inside `notes` (equal values; each wanted note used once)."""
    left = list(wanted)
    out = set()
    for i, n in enumerate(notes):
        if n in left:
            left.remove(n)
            out.add(i)
    return out


class PianoGrid(QWidget):
    keyPressed = Signal(int, int)  # pitch, velocity of a pressed piano key
    keyReleased = Signal()
    audition = Signal(int, int)    # play a short preview: pitch, velocity
    selectionChanged = Signal()
    clipboard: list[Note] = []     # shared by all piano rolls (copied notes, start-relative)

    def __init__(self, doc):
        super().__init__()
        self.pressed_key: int | None = None
        self.held: set[int] = set()  # keys held on the computer keyboard
        self.doc = doc
        self.px_per_beat = 80.0
        self.snap = 0.25
        self.last_length = 0.25
        self.selected: set[int] = set()
        self.drag: dict | None = None
        self.preview: dict[int, Note] = {}   # notes being dragged: index -> new note
        self.box: QRectF | None = None
        self._paste_at: float | None = None
        self._clip_step = 1.0
        self.rec_beat: float | None = None  # red line while recording
        self.play_beat: float | None = None  # green playhead while playing
        self.view_w = 0  # visible width (set by the PianoRoll), so the grid fills the panel
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMinimumHeight(128 * ROW_H)

    # ---- geometry ----------------------------------------------------------
    def notes(self) -> list[Note]:
        pat = self.doc.current_pattern()
        return pat.notes.get(self.doc.channel, []) if pat and self.doc.channel else []

    def length(self) -> float:
        pat = self.doc.current_pattern()
        return pat.length if pat else 4.0

    def shown_beats(self) -> float:
        """Grid width in beats: the whole panel (whole bars), at least the pattern + one bar (FL style)."""
        fit = (max(0, self.view_w - KEYS_W)) / self.px_per_beat
        return max(self.length() + 4, math.ceil(fit / 4) * 4)

    def sizeHint(self):
        return QSize(int(KEYS_W + self.shown_beats() * self.px_per_beat) + 1, 128 * ROW_H)

    def resize_to_pattern(self) -> None:
        self.setMinimumWidth(int(KEYS_W + self.shown_beats() * self.px_per_beat) + 1)
        self.update()

    def x_of(self, beat: float) -> float:
        return KEYS_W + beat * self.px_per_beat

    def y_of(self, pitch: int) -> float:
        return (127 - pitch) * ROW_H

    def beat_at(self, x: float, snap: bool = True) -> float:
        b = max(0.0, (x - KEYS_W) / self.px_per_beat)
        return round(b / self.snap) * self.snap if snap else b

    def floor_beat(self, x: float, snap: bool = True) -> float:
        b = max(0.0, (x - KEYS_W) / self.px_per_beat)
        return (b // self.snap) * self.snap if snap else b

    def pitch_at(self, y: float) -> int:
        return max(0, min(127, 127 - int(y // ROW_H)))

    def rect(self, n: Note) -> QRectF:
        return QRectF(self.x_of(n.start), self.y_of(n.pitch) + 1, max(3.0, n.length * self.px_per_beat), ROW_H - 2)

    def hit(self, x: float, y: float) -> tuple[int, str] | None:
        notes = self.notes()
        for i in reversed(range(len(notes))):
            r = self.rect(notes[i])
            if r.contains(x, y):
                return i, ("resize" if x > r.right() - EDGE else "move")
        return None

    def shown_notes(self) -> list[Note | None]:
        """Notes as drawn right now: dragged ones at their new place, swept-away ones hidden (None)."""
        notes: list[Note | None] = list(self.notes())
        for i, n in self.preview.items():
            if i < len(notes):
                notes[i] = n
        if self.drag and self.drag.get("mode") == "erase":
            for i in self.drag["hits"]:
                if i < len(notes):
                    notes[i] = None
        return notes

    # ---- selection -----------------------------------------------------------
    def set_selection(self, idx: set[int]) -> None:
        n = len(self.notes())
        idx = {i for i in idx if 0 <= i < n}
        if idx != self.selected:
            self.selected = idx
            self.selectionChanged.emit()
        self.update()

    def selected_notes(self) -> list[Note]:
        notes = self.notes()
        return [notes[i] for i in sorted(self.selected) if i < len(notes)]

    # ---- paint -------------------------------------------------------------
    def paintEvent(self, ev):
        p = QPainter(self)
        clip = ev.rect()
        length = self.length()
        for pitch in range(128):
            y = self.y_of(pitch)
            if y + ROW_H < clip.top() or y > clip.bottom():
                continue
            p.fillRect(QRectF(KEYS_W, y, self.width() - KEYS_W, ROW_H),
                       QColor("#2a2e34" if pitch % 12 in BLACK else "#32373e"))
            key_col = ("#e0902c" if pitch == self.pressed_key or pitch in self.held
                       else "#1c1c1c" if pitch % 12 in BLACK else "#e6e6e6")
            p.fillRect(QRectF(0, y, KEYS_W, ROW_H), QColor(key_col))
            if pitch % 12 == 0:
                p.setPen(QColor("#444"))
                p.setFont(QFont("sans", 7))
                p.drawText(QRectF(2, y, KEYS_W - 4, ROW_H), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           note_name(pitch))
            p.setPen(QColor("#24272c"))
            p.drawLine(KEYS_W, int(y + ROW_H), self.width(), int(y + ROW_H))
        steps = int(self.shown_beats() / self.snap) + 1
        for i in range(steps):
            b = i * self.snap
            x = self.x_of(b)
            if x < clip.left() - 1 or x > clip.right() + 1:
                continue
            p.setPen(QPen(QColor("#5a606b" if b % 4 == 0 else "#454b55" if b % 1 == 0 else "#30353c"), 1))
            p.drawLine(int(x), clip.top(), int(x), clip.bottom())
        # past the pattern end: lightly shaded; drawing there makes the pattern longer
        p.fillRect(QRectF(self.x_of(length), clip.top(), self.width(), clip.height()), QColor(0, 0, 0, 45))
        p.setPen(QPen(QColor("#8a6a3a"), 2))
        p.drawLine(int(self.x_of(length)), clip.top(), int(self.x_of(length)), clip.bottom())
        for i, n in enumerate(self.shown_notes()):
            if n is None:
                continue
            r = self.rect(n)
            if not r.intersects(QRectF(clip)):
                continue
            sel = i in self.selected
            p.fillRect(r, QColor.fromHsv(30 if not sel else 12, 120 + n.velocity, 255 if sel else 240))
            p.setPen(QPen(QColor("#ffffff" if sel else "#5a3a10"), 2 if sel else 1))
            p.drawRect(r)
            if r.width() > 30:
                p.setPen(QColor("#2a1a05"))
                p.setFont(QFont("sans", 7))
                p.drawText(r.adjusted(3, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, note_name(n.pitch))
        if self.play_beat is not None and self.rec_beat is None:
            x = int(self.x_of(self.play_beat))
            p.setPen(QPen(QColor("#5ad65a"), 2))
            p.drawLine(x, clip.top(), x, clip.bottom())
        if self.rec_beat is not None:
            x = int(self.x_of(self.rec_beat))
            p.setPen(QPen(QColor("#e04040"), 2))
            p.drawLine(x, clip.top(), x, clip.bottom())
        if self.box is not None:
            p.setPen(QPen(QColor("#8fb8ff"), 1, Qt.PenStyle.DashLine))
            p.fillRect(self.box, QColor(120, 160, 255, 40))
            p.drawRect(self.box)

    # ---- mouse -------------------------------------------------------------
    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if x < KEYS_W:
            if e.button() == Qt.MouseButton.LeftButton:
                self._press_key(x, y)
            return
        if not self.doc.channel or not self.doc.pattern:
            self.doc.message.emit("add a channel and a pattern first (Channel Rack, F6)")
            return
        self.setFocus()
        mods = e.modifiers()
        snap = not (mods & Qt.KeyboardModifier.AltModifier)
        h = self.hit(x, y)
        notes = self.notes()
        if e.button() == Qt.MouseButton.RightButton:
            if h and h[0] in self.selected and len(self.selected) > 1:
                self.delete_indices(self.selected)
                return
            self.drag = {"mode": "erase", "hits": {h[0]} if h else set(), "last": (x, y)}  # sweep: erase what you pass
            self.update()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if h:
            i, mode = h
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self.set_selection(self.selected ^ {i})
                return
            if i not in self.selected:
                self.set_selection({i})
            self.drag = {"mode": mode, "b0": self.beat_at(x, False), "p0": self.pitch_at(y), "anchor": i,
                         "origs": {j: notes[j] for j in self.selected}, "dp": 0}
            self.preview = dict(self.drag["origs"])
            if mode == "resize":
                self.last_length = notes[i].length
            self.audition.emit(notes[i].pitch, notes[i].velocity)
        elif mods & Qt.KeyboardModifier.ControlModifier:
            self.drag = {"mode": "box", "x0": x, "y0": y, "add": bool(mods & Qt.KeyboardModifier.ShiftModifier)}
            self.box = QRectF(x, y, 0, 0)
        else:
            start = self.floor_beat(x, snap)
            note = Note(self.pitch_at(y), start, self.last_length, 100)
            self.audition.emit(note.pitch, note.velocity)
            self._edit_notes(f"add note {note_name(note.pitch)}", lambda ns: ns.append(note), select_after=[note])

    def key_velocity(self, x: float) -> int:
        """Left end of a key = soft (30), right end = hard (127)."""
        return max(1, min(127, round(30 + 97 * max(0.0, min(1.0, x / KEYS_W)))))

    def _press_key(self, x: float, y: float) -> None:
        self.pressed_key = self.pitch_at(y)
        self.update()
        self.keyPressed.emit(self.pressed_key, self.key_velocity(x))

    def _release_key(self) -> None:
        if self.pressed_key is not None:
            self.pressed_key = None
            self.update()
            self.keyReleased.emit()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if self.pressed_key is not None:  # glide over the keys while held
            if x < KEYS_W and self.pitch_at(y) != self.pressed_key:
                self._release_key()
                self._press_key(x, y)
            return
        if not self.drag:
            h = self.hit(x, y)
            self.setCursor(Qt.CursorShape.SizeHorCursor if h and h[1] == "resize"
                           else Qt.CursorShape.OpenHandCursor if h else Qt.CursorShape.ArrowCursor)
            return
        d = self.drag
        if d["mode"] == "erase":
            x0, y0 = d.get("last", (x, y))
            steps = max(1, int(max(abs(x - x0), abs(y - y0)) // 3))  # check the path, not just the end point
            before = len(d["hits"])
            for k in range(1, steps + 1):
                h = self.hit(x0 + (x - x0) * k / steps, y0 + (y - y0) * k / steps)
                if h:
                    d["hits"].add(h[0])
            d["last"] = (x, y)
            if len(d["hits"]) != before:
                self.update()
            return
        if d["mode"] == "box":
            self.box = QRectF(min(d["x0"], x), min(d["y0"], y), abs(x - d["x0"]), abs(y - d["y0"]))
            self.update()
            return
        snap = not (e.modifiers() & Qt.KeyboardModifier.AltModifier)
        origs, a = d["origs"], d["origs"][d["anchor"]]
        db = self.beat_at(x, False) - d["b0"]
        if d["mode"] == "move":
            start = a.start + db
            start = round(start / self.snap) * self.snap if snap else start
            shift = max(start - a.start, -min(n.start for n in origs.values()))
            dp = self.pitch_at(y) - d["p0"]
            dp = max(-min(n.pitch for n in origs.values()), min(127 - max(n.pitch for n in origs.values()), dp))
            self.preview = {j: Note(n.pitch + dp, round(n.start + shift, 6), n.length, n.velocity)
                            for j, n in origs.items()}
            if dp != d["dp"]:
                d["dp"] = dp
                self.audition.emit(a.pitch + dp, a.velocity)
        else:
            end = a.start + a.length + db
            end = round(end / self.snap) * self.snap if snap else end
            dl = end - a.start - a.length
            least = self.snap if snap else 0.01
            self.preview = {j: Note(n.pitch, n.start, round(max(least, n.length + dl), 6), n.velocity)
                            for j, n in origs.items()}
        self.update()

    def mouseReleaseEvent(self, e):
        if self.pressed_key is not None:
            self._release_key()
            return
        d, self.drag = self.drag, None
        if not d:
            return
        if d["mode"] == "erase":
            self.delete_indices(d["hits"])
            self.update()
            return
        if d["mode"] == "box":
            box, self.box = self.box, None
            hits = {i for i, n in enumerate(self.notes()) if box is not None and self.rect(n).intersects(box)}
            self.set_selection((self.selected | hits) if d["add"] else hits)
            return
        new, self.preview = self.preview, {}
        changed = {j: n for j, n in new.items() if n != d["origs"][j]}
        if changed:
            if d["mode"] == "resize":
                self.last_length = new[d["anchor"]].length

            def fn(ns):
                for j, n in changed.items():
                    ns[j] = n
            label = f"{d['mode']} {len(changed)} note{'s' if len(changed) > 1 else ''}"
            self._edit_notes(label, fn, select_after=list(new.values()))
        self.update()

    def wheelEvent(self, e):
        """Alt+wheel over a note = change velocity (of all selected notes if that note is selected)."""
        if not (e.modifiers() & Qt.KeyboardModifier.AltModifier):
            return super().wheelEvent(e)
        pos = e.position()
        h = self.hit(pos.x(), pos.y())
        if not h:
            return
        delta = e.angleDelta().y() or e.angleDelta().x()  # Alt may turn the wheel sideways
        step = 8 if delta > 0 else -8
        idx = self.selected if h[0] in self.selected else {h[0]}
        self.change_velocity(idx, step)
        e.accept()

    # ---- keyboard ------------------------------------------------------------
    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_indices(self.selected)
        elif ctrl and k == Qt.Key.Key_A:
            self.set_selection(set(range(len(self.notes()))))
        elif ctrl and k == Qt.Key.Key_C:
            self.copy()
        elif ctrl and k == Qt.Key.Key_V:
            self.paste()
        elif ctrl and k == Qt.Key.Key_D:
            self.copy()
            self.paste()
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.selected:
            self.move_selected(0.0, (12 if shift else 1) * (1 if k == Qt.Key.Key_Up else -1))
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.selected:
            self.move_selected(self.snap * (1 if k == Qt.Key.Key_Right else -1), 0)
        elif k == Qt.Key.Key_Escape:
            self.set_selection(set())
        else:
            return super().keyPressEvent(e)
        e.accept()

    # ---- edit actions ----------------------------------------------------------
    def delete_indices(self, idx: set[int]) -> None:
        idx = {i for i in idx if i < len(self.notes())}
        if not idx:
            return

        def fn(ns):
            for i in sorted(idx, reverse=True):
                ns.pop(i)
        self._edit_notes(f"delete {len(idx)} note{'s' if len(idx) > 1 else ''}", fn, select_after=[])

    def move_selected(self, beats: float, semis: int) -> None:
        sel = self.selected_notes()
        if not sel:
            return
        beats = max(beats, -min(n.start for n in sel))
        semis = max(-min(n.pitch for n in sel), min(127 - max(n.pitch for n in sel), semis))
        if not beats and not semis:
            return
        moved = {i: Note(n.pitch + semis, round(n.start + beats, 6), n.length, n.velocity)
                 for i, n in zip(sorted(self.selected), sel)}

        def fn(ns):
            for i, n in moved.items():
                ns[i] = n
        what = f"{semis:+d} semitone{'s' if abs(semis) != 1 else ''}" if semis else f"{beats:+g} beat"
        self._edit_notes(f"move {len(moved)} notes {what}", fn, select_after=list(moved.values()))
        if semis and len(moved) == 1:
            n = next(iter(moved.values()))
            self.audition.emit(n.pitch, n.velocity)

    def change_velocity(self, idx: set[int], step: int = 0, value: int | None = None) -> None:
        notes = self.notes()
        idx = {i for i in idx if i < len(notes)}
        if not idx:
            return
        new = {i: Note(notes[i].pitch, notes[i].start, notes[i].length,
                       max(1, min(127, value if value is not None else notes[i].velocity + step))) for i in idx}
        if all(new[i] == notes[i] for i in idx):
            return

        def fn(ns):
            for i, n in new.items():
                ns[i] = n
        v = next(iter(new.values())).velocity
        self._edit_notes(f"velocity {v}" if len(new) == 1 else f"velocity of {len(new)} notes",
                         fn, select_after=[new[i] for i in sorted(self.selected) if i in new] or None)

    def copy(self) -> None:
        sel = self.selected_notes()
        if not sel:
            self.doc.message.emit("select notes first (click, Shift+click, Ctrl+drag or Ctrl+A)")
            return
        origin = min(n.start for n in sel)
        span = max(n.start + n.length for n in sel) - origin
        PianoGrid.clipboard = [Note(n.pitch, n.start - origin, n.length, n.velocity) for n in sel]
        self._clip_step = max(1.0, math.ceil(span - 1e-9))  # whole beats, so pastes line up
        self._paste_at = origin + self._clip_step
        self.doc.message.emit(f"copied {len(sel)} note{'s' if len(sel) > 1 else ''}")

    def paste(self) -> None:
        clip = PianoGrid.clipboard
        if not clip or not self.doc.channel or not self.doc.pattern:
            return
        at = self._paste_at if self._paste_at is not None else 0.0
        new = [Note(n.pitch, round(n.start + at, 6), n.length, n.velocity) for n in clip]
        pat_name = self.doc.pattern

        def fn(ns):
            ns.extend(new)

        def grow(p):  # (kept for clarity: _edit_notes grows the pattern for every edit)
            from ..quantize import pattern_length_for
            pat = p.pattern(pat_name)
            pat.length = max(pat.length, pattern_length_for(pat.notes.get(self.doc.channel, [])))
        if self._edit_notes(f"paste {len(new)} note{'s' if len(new) > 1 else ''}", fn, select_after=new, after=grow):
            self._paste_at = at + getattr(self, "_clip_step", 1.0)

    def _edit_notes(self, label: str, fn, select_after: list[Note] | None = None, after=None) -> bool:
        pat, ch = self.doc.pattern, self.doc.channel

        def apply(p):
            from ..quantize import pattern_length_for
            pt = p.pattern(pat)
            notes = pt.notes.setdefault(ch, [])
            fn(notes)
            notes.sort(key=lambda n: (n.start, n.pitch))
            pt.length = max(pt.length, pattern_length_for(notes))  # notes past the end make it longer
            if after:
                after(p)
        ok = self.doc.edit(label, apply)
        if ok and select_after is not None:
            self.set_selection(match_indices(self.notes(), select_after))
        return ok


class VelocityLane(QWidget):
    """Bars under the grid: height = velocity. Drag over a bar to set it."""

    def __init__(self, grid: PianoGrid):
        super().__init__()
        self.grid = grid
        self.preview: dict[int, int] = {}
        self.setFixedHeight(LANE_H)
        self.setMouseTracking(False)

    def sizeHint(self):
        return QSize(self.grid.width(), LANE_H)

    def _hits(self, x: float) -> set[int]:
        g = self.grid
        idx = set()
        for i, n in enumerate(g.notes()):
            x0 = g.x_of(n.start)
            if x0 - 3 <= x <= x0 + max(8.0, n.length * g.px_per_beat * 0.5):
                idx.add(i)
        sel = idx & g.selected
        return sel or idx

    def _vel(self, y: float) -> int:
        return max(1, min(127, round(127 * (1 - (y - 4) / (LANE_H - 8)))))

    def paintEvent(self, ev):
        p = QPainter(self)
        g = self.grid
        p.fillRect(self.rect(), QColor("#25282d"))
        p.fillRect(QRectF(0, 0, KEYS_W, LANE_H), QColor("#1e2024"))
        p.setPen(QColor("#888"))
        p.setFont(QFont("sans", 8))
        p.drawText(QRectF(6, 0, KEYS_W - 8, LANE_H), Qt.AlignmentFlag.AlignVCenter, "velocity")
        for i, n in enumerate(g.notes()):
            v = self.preview.get(i, n.velocity)
            x = g.x_of(n.start)
            h = (LANE_H - 8) * v / 127
            sel = i in g.selected
            col = QColor("#ffb060" if sel else "#d08a3a")
            p.fillRect(QRectF(x, LANE_H - 4 - h, 5, h), col)
            p.setPen(QPen(col, 1))
            p.drawEllipse(QRectF(x - 1, LANE_H - 4 - h - 3, 7, 7))

    def mousePressEvent(self, e):
        self._drag(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._drag(e)

    def _drag(self, e):
        x, y = e.position().x(), e.position().y()
        for i in self._hits(x):
            self.preview[i] = self._vel(y)
        self.update()

    def mouseReleaseEvent(self, e):
        preview, self.preview = self.preview, {}
        notes = self.grid.notes()
        changed = {i: v for i, v in preview.items() if i < len(notes) and notes[i].velocity != v}
        if not changed:
            self.update()
            return

        def fn(ns):
            for i, v in changed.items():
                n = ns[i]
                ns[i] = Note(n.pitch, n.start, n.length, v)
        keep = [Note(n.pitch, n.start, n.length, changed.get(i, n.velocity))
                for i, n in enumerate(notes) if i in self.grid.selected]
        v = next(iter(changed.values()))
        self.grid._edit_notes(f"velocity {v}" if len(changed) == 1 else f"velocity of {len(changed)} notes",
                              fn, select_after=keep)
        self.update()


class PianoRoll(QWidget):
    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        self.grid = PianoGrid(doc)
        from .preview_client import Previewer
        self.previewer = Previewer(self)
        self.previewer.result.connect(self._preview_result)
        self.grid.keyPressed.connect(self.play_key)
        self.grid.keyReleased.connect(self.stop_key)
        self.grid.audition.connect(self.audition)
        self._key_id: int | None = None
        self._typed: dict[int, int] = {}  # pitch -> preview note id (computer keyboard)
        top = QHBoxLayout()
        self.title = QLabel()
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(QLabel("Snap"))
        self.snap_box = QComboBox()
        for label, v in (("1/4 beat", 0.25), ("1/8 beat", 0.125), ("1/3 beat", 1 / 3), ("1/2 beat", 0.5),
                         ("1 beat", 1.0)):
            self.snap_box.addItem(label, v)
        self.snap_box.currentIndexChanged.connect(lambda _: self._set_snap())
        top.addWidget(self.snap_box)
        self.quant_btn = QPushButton("Quantize")
        self.quant_btn.setToolTip("Snap this channel's notes to the snap grid (Alt+Q)")
        self.quant_btn.clicked.connect(self.quantize)
        top.addWidget(self.quant_btn)
        top.addWidget(QLabel("Zoom"))
        self.zoom_box = QComboBox()
        for z in (40, 80, 120, 160):
            self.zoom_box.addItem(f"{z}px/beat", z)
        self.zoom_box.setCurrentIndex(1)
        self.zoom_box.currentIndexChanged.connect(lambda _: self._set_zoom())
        top.addWidget(self.zoom_box)
        from . import prefs
        self.sound_btn = QPushButton("🔊")
        self.sound_btn.setCheckable(True)
        self.sound_btn.setChecked(bool(prefs.get("piano_sound", True)))
        self.sound_btn.setToolTip("Hear notes while you place or move them")
        self.sound_btn.setFixedWidth(34)
        self.sound_btn.toggled.connect(lambda on: prefs.set_("piano_sound", on))
        top.addWidget(self.sound_btn)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.grid)
        self.scroll.setWidgetResizable(False)
        self.lane = VelocityLane(self.grid)
        self.lane_scroll = QScrollArea()
        self.lane_scroll.setWidget(self.lane)
        self.lane_scroll.setWidgetResizable(False)
        self.lane_scroll.setFixedHeight(LANE_H + 2)
        self.lane_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lane_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lane_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.horizontalScrollBar().valueChanged.connect(self.lane_scroll.horizontalScrollBar().setValue)
        self.grid.selectionChanged.connect(self.lane.update)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.scroll, 1)
        lay.addWidget(self.lane_scroll)
        doc.changed.connect(self.refresh)
        doc.selection.connect(self._new_target)
        doc.selection.connect(self.refresh)
        self.refresh()
        self._centered = False
        doc.selection.connect(self.center_view)

    def center_view(self) -> None:
        """Scroll so the channel's notes (or C4-C5 when empty) sit in the middle."""
        notes = self.grid.notes()
        pitch = round(sum(n.pitch for n in notes) / len(notes)) if notes else 66  # 66 = middle of C4..C5
        h = self.scroll.viewport().height()
        self.scroll.verticalScrollBar().setValue(int(self.grid.y_of(pitch) + ROW_H / 2 - h / 2))

    def showEvent(self, e):
        super().showEvent(e)
        if not self._centered:  # first time the panel is shown: viewport has its real size now
            self._centered = True
            self.center_view()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        vw = self.scroll.viewport().width()
        if vw != self.grid.view_w:  # panel got wider/narrower: grid fills it again
            self.grid.view_w = vw
            self.refresh()
        if self._centered and not self.grid.notes():
            self.center_view()

    def _new_target(self) -> None:
        """Other channel or pattern: forget the selection (indices belong to the old notes)."""
        self.grid.set_selection(set())
        self.grid._paste_at = None
        p = self.doc.project
        if self.doc.channel:
            ch = p.channel(self.doc.channel)
            if ch.instrument == "plugin":  # load Surge & co. now, not on the first key
                self.previewer.warm(ch, p.sample_rate, p.master)

    def audition(self, pitch: int, velocity: int = 100) -> None:
        """Short preview while drawing (when 🔊 is on)."""
        if not self.sound_btn.isChecked() or not self.doc.channel:
            return
        p = self.doc.project
        nid = self.previewer.note_on(p.channel(self.doc.channel), pitch, velocity, p.sample_rate, p.master)
        QTimer.singleShot(250, lambda: self.previewer.note_off(nid))

    CLICK = None  # metronome sound: GM drum kit woodblock (same as the live metronome)

    def click(self, accent: bool) -> None:
        """One metronome click (count-in / recording) through the preview sampler."""
        from ..model import Channel
        if PianoRoll.CLICK is None:
            PianoRoll.CLICK = Channel("click", "gm", program=0, bank=128, volume=1.0)
        p = self.doc.project
        nid = self.previewer.note_on(PianoRoll.CLICK, 76 if accent else 77, 127 if accent else 100,
                                     p.sample_rate, p.master)
        QTimer.singleShot(90, lambda: self.previewer.note_off(nid))

    def play_key(self, pitch: int, velocity: int = 100) -> None:
        """Hear one note with the current channel's sound while the key is held."""
        if not self.doc.channel:
            self.doc.message.emit("add a channel first (Channel Rack, F6)")
            return
        self.stop_key()
        p = self.doc.project
        self._key_id = self.previewer.note_on(p.channel(self.doc.channel), pitch, velocity, p.sample_rate, p.master)
        self.doc.message.emit(f"{note_name(pitch)}  velocity {velocity}  ({self.doc.channel})")

    def typed_on(self, pitch: int, velocity: int) -> None:
        """Computer-keyboard note on (several at once is fine)."""
        if not self.doc.channel:
            self.doc.message.emit("add a channel first (Channel Rack, F6)")
            return
        self.typed_off(pitch)
        p = self.doc.project
        self._typed[pitch] = self.previewer.note_on(p.channel(self.doc.channel), pitch, velocity,
                                                    p.sample_rate, p.master)
        self.grid.held.add(pitch)
        self.grid.update()
        self.doc.message.emit(f"{note_name(pitch)}  velocity {velocity}  ({self.doc.channel})")

    def typed_off(self, pitch: int) -> None:
        nid = self._typed.pop(pitch, None)
        if nid is not None:
            self.previewer.note_off(nid)
        if pitch in self.grid.held:
            self.grid.held.discard(pitch)
            self.grid.update()

    def stop_key(self) -> None:
        if self._key_id is not None:
            self.previewer.note_off(self._key_id)
            self._key_id = None

    def _preview_result(self, line: str) -> None:
        if line.startswith("error"):
            self.doc.message.emit(f"preview: {line[6:]}")

    def quantize(self) -> None:
        from ..quantize import quantize
        ch, grid = self.doc.channel, self.grid.snap
        pat = self.doc.current_pattern()
        if not ch or pat is None or not pat.notes.get(ch):
            self.doc.message.emit("nothing to quantize")
            return

        def fn(p):
            q = p.pattern(pat.name)
            q.notes[ch] = quantize(q.notes[ch], grid)
        self.doc.edit(f"quantize {ch} in {pat.name} to {self.snap_box.currentText()}", fn)

    def _set_snap(self):
        self.grid.snap = self.snap_box.currentData()
        self.grid.update()

    def _set_zoom(self):
        self.grid.px_per_beat = float(self.zoom_box.currentData())
        self.refresh()

    def refresh(self) -> None:
        self.grid.view_w = self.scroll.viewport().width()
        self.title.setText(f"<b>{self.doc.channel or '(no channel)'}</b> in <b>{self.doc.pattern or '(no pattern)'}</b>")
        self.grid.resize_to_pattern()
        self.grid.resize(self.grid.sizeHint())
        self.grid.set_selection(self.grid.selected)  # drops indices that no longer exist
        self.grid.update()
        self.lane.resize(self.grid.width(), LANE_H)
        self.lane.update()
