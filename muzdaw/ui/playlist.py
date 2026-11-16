"""Playlist: pattern clips on tracks over time.

Left click empty = place the current pattern (snaps to the bar). Drag a clip =
move it (snaps to the beat). Right click a clip = remove. Click a clip = select its pattern.
Double-click a clip = open its pattern in the Piano Roll.
Audio clips (teal, with a waveform): drag an audio file in from the file manager, drag to move,
right click = remove.
"""
from __future__ import annotations

import zlib

from PySide6.QtCore import QLineF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from pathlib import Path

import numpy as np

from ..model import AudioClip, Clip

HEAD_W = 70
RULER_H = 18
TRACK_H = 34
TRACKS = 12


def pattern_color(name: str) -> QColor:
    return QColor.fromHsv(zlib.crc32(name.encode()) % 360, 110, 200)


class PlaylistGrid(QWidget):
    openPattern = Signal(str)  # double-click on a clip

    def __init__(self, doc):
        super().__init__()
        self._mini: dict[str, list[tuple[float, float, float]]] = {}  # pattern -> (beat0, beat1, y 0..1)
        self.doc = doc
        self.px_per_beat = 16.0
        self.drag = None  # (index, press_beat, press_track, orig Clip)
        self.preview: Clip | None = None
        self.play_beat: float | None = None
        self.adrag = None  # (index, press_beat, press_track, orig AudioClip)
        self.apreview: AudioClip | None = None
        self._wave: dict[str, np.ndarray] = {}  # path -> loudness per 1/100 s (0..1)
        self.setAcceptDrops(True)

    def beats(self) -> float:
        return max(64.0, self.doc.project.song_length() + 32)

    def sizeHint(self):
        return QSize(int(HEAD_W + self.beats() * self.px_per_beat), RULER_H + TRACKS * TRACK_H)

    def x_of(self, b: float) -> float:
        return HEAD_W + b * self.px_per_beat

    def beat_at(self, x: float) -> float:
        return max(0.0, (x - HEAD_W) / self.px_per_beat)

    def track_at(self, y: float) -> int:
        return max(0, min(TRACKS - 1, int((y - RULER_H) // TRACK_H)))

    def clip_rect(self, c: Clip) -> QRectF:
        length = self.doc.project.pattern(c.pattern).length
        return QRectF(self.x_of(c.start) + 1, RULER_H + c.track * TRACK_H + 2,
                      length * self.px_per_beat - 2, TRACK_H - 4)

    def audio_rect(self, a: AudioClip) -> QRectF:
        length = max(0.25, a.length_beats(self.doc.project.tempo))
        return QRectF(self.x_of(a.start) + 1, RULER_H + a.track * TRACK_H + 2,
                      length * self.px_per_beat - 2, TRACK_H - 4)

    def hit_audio(self, x: float, y: float) -> int | None:
        clips = self.doc.project.audio_clips
        for i in reversed(range(len(clips))):
            if self.audio_rect(clips[i]).contains(x, y):
                return i
        return None

    def wave(self, path: str) -> np.ndarray:
        """Loudness every 10 ms, worked out once per file (decoded at a low rate: fast)."""
        if path not in self._wave:
            from ..model import ProjectError
            from ..samples import load
            try:
                a = np.abs(load(path, 8000)).max(axis=0)
                k = 80  # 10 ms at 8 kHz
                n = len(a) // k
                env = a[: n * k].reshape(n, k).max(axis=1) if n else np.zeros(0, np.float32)
                self._wave[path] = env / max(1e-6, float(env.max())) if len(env) else env
            except ProjectError:
                self._wave[path] = np.zeros(0, np.float32)
        return self._wave[path]

    def add_audio(self, path: str, beat: float = 0.0, track: int = 0) -> bool:
        from ..model import ProjectError
        from ..samples import check_path
        try:
            p = check_path(path).resolve()
        except ProjectError as e:
            self.doc.message.emit(str(e))
            return False
        clip = AudioClip(str(p), max(0.0, round(beat)), max(0, min(TRACKS - 1, track)))
        return self.doc.edit(f"add audio {p.name} at bar {int(clip.start // 4) + 1}",
                             lambda q: (q.audio_clips.append(clip), q.audio_clips.sort(key=lambda c: (c.start, c.track))))

    def dragEnterEvent(self, e):
        from ..samples import AUDIO_EXT
        if e.mimeData().hasUrls() and any(Path(u.toLocalFile()).suffix.lower() in AUDIO_EXT
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        pos = e.position()
        for k, u in enumerate(e.mimeData().urls()):
            self.add_audio(u.toLocalFile(), self.beat_at(pos.x()), self.track_at(pos.y()) + k)
        e.acceptProposedAction()

    def hit(self, x: float, y: float) -> int | None:
        for i in reversed(range(len(self.doc.project.playlist))):
            if self.clip_rect(self.doc.project.playlist[i]).contains(x, y):
                return i
        return None

    def mini_lines(self, name: str) -> list[tuple[float, float, float]]:
        """A pattern's notes as (start beat, end beat, height 0..1); worked out once per change."""
        if name not in self._mini:
            pat = self.doc.project.pattern(name)
            notes = [n for ns in pat.notes.values() for n in ns if n.start < pat.length]
            if notes:
                lo, hi = min(n.pitch for n in notes), max(n.pitch for n in notes)
                span = max(1, hi - lo)
                self._mini[name] = [(n.start, min(n.start + n.length, pat.length), (n.pitch - lo) / span)
                                    for n in notes]
            else:
                self._mini[name] = []
        return self._mini[name]

    def invalidate(self) -> None:
        self._mini.clear()

    def paintEvent(self, ev):
        p = QPainter(self)
        w = self.width()
        area = QRectF(ev.rect())
        p.fillRect(self.rect(), QColor("#2b2f35"))
        for t in range(TRACKS):
            y = RULER_H + t * TRACK_H
            p.fillRect(QRectF(HEAD_W, y, w, TRACK_H), QColor("#30353c" if t % 2 else "#2c3036"))
            p.fillRect(QRectF(0, y, HEAD_W - 2, TRACK_H - 1), QColor("#3a3f47"))
            p.setPen(QColor("#aab"))
            p.setFont(QFont("sans", 8))
            p.drawText(QRectF(6, y, HEAD_W, TRACK_H), Qt.AlignmentFlag.AlignVCenter, f"Track {t + 1}")
        beats = int(self.beats())
        for b in range(beats + 1):
            x = self.x_of(b)
            bar = b % 4 == 0
            p.setPen(QPen(QColor("#555c66" if bar else "#383d44"), 1))
            p.drawLine(int(x), RULER_H, int(x), self.height())
            if bar:
                p.setPen(QColor("#ccc"))
                p.setFont(QFont("sans", 8))
                p.drawText(int(x) + 3, RULER_H - 5, str(b // 4 + 1))
        clips = list(self.doc.project.playlist)
        if self.drag and self.preview:
            clips[self.drag[0]] = self.preview
        for c in clips:
            r = self.clip_rect(c)
            if not r.intersects(area):
                continue  # off screen: skip
            col = pattern_color(c.pattern)
            p.fillRect(r, col.darker(130) if c.pattern != self.doc.pattern else col)
            p.setPen(QColor("#111"))
            p.drawRect(r)
            p.setFont(QFont("sans", 8))
            p.drawText(r.adjusted(4, 2, 0, 0), Qt.AlignmentFlag.AlignTop, c.pattern)
            self._mini_notes(p, c, r)
        auds = list(self.doc.project.audio_clips)
        if self.adrag and self.apreview:
            auds[self.adrag[0]] = self.apreview
        for a in auds:
            r = self.audio_rect(a)
            if not r.intersects(area):
                continue
            p.fillRect(r, QColor("#2f7f86"))
            p.setPen(QColor("#111"))
            p.drawRect(r)
            env = self.wave(a.path)
            if len(env):
                mid, half = r.center().y() + 5, (r.height() - 16) / 2
                px_per_s = self.px_per_beat * self.doc.project.tempo / 60.0
                cols = range(int(max(r.left(), area.left())), int(min(r.right(), area.right())))
                lines = []
                for x in cols:
                    i = int((x - r.left()) / px_per_s * 100)
                    if 0 <= i < len(env):
                        h = float(env[i]) * half
                        lines.append(QLineF(x, mid - h, x, mid + h))
                p.setPen(QColor("#bfeef2"))
                if lines:
                    p.drawLines(lines)
            p.setPen(QColor("#e8fbfc"))
            p.setFont(QFont("sans", 8))
            p.drawText(r.adjusted(4, 2, 0, 0), Qt.AlignmentFlag.AlignTop, Path(a.path).name)
        if self.play_beat is not None:
            x = self.x_of(self.play_beat)
            p.setPen(QPen(QColor("#5ad65a"), 2))
            p.drawLine(int(x), 0, int(x), self.height())

    def _mini_notes(self, p: QPainter, c: Clip, r: QRectF) -> None:
        segs = self.mini_lines(c.pattern)
        if not segs:
            return
        top, h, x0, ppb = r.top() + 13, r.height() - 15, r.left(), self.px_per_beat
        p.setPen(QColor(20, 20, 20, 170))
        p.drawLines([QLineF(x0 + b0 * ppb, top + h - yf * h, x0 + b1 * ppb, top + h - yf * h)
                     for b0, b1, yf in segs])

    def mouseDoubleClickEvent(self, e):
        i = self.hit(e.position().x(), e.position().y())
        if i is not None:
            self.openPattern.emit(self.doc.project.playlist[i].pattern)

    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if x < HEAD_W or y < RULER_H:
            return
        ai = self.hit_audio(x, y)
        if ai is not None:
            a = self.doc.project.audio_clips[ai]
            if e.button() == Qt.MouseButton.RightButton:
                self.doc.edit(f"remove audio {Path(a.path).name}", lambda q: q.audio_clips.pop(ai))
            elif e.button() == Qt.MouseButton.LeftButton:
                self.adrag = (ai, self.beat_at(x), self.track_at(y), a)
                self.apreview = AudioClip(a.path, a.start, a.track, a.gain)
            return
        i = self.hit(x, y)
        if e.button() == Qt.MouseButton.RightButton:
            if i is not None:
                c = self.doc.project.playlist[i]
                self.doc.edit(f"remove clip {c.pattern}", lambda p: p.playlist.pop(i))
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if i is not None:
            c = self.doc.project.playlist[i]
            self.doc.select(pattern=c.pattern)
            self.drag = (i, self.beat_at(x), self.track_at(y), c)
            self.preview = Clip(c.pattern, c.start, c.track)
            return
        if not self.doc.pattern:
            self.doc.message.emit("add a pattern first (Channel Rack)")
            return
        start = (self.beat_at(x) // 4) * 4
        clip = Clip(self.doc.pattern, start, self.track_at(y))
        self.doc.edit(f"place {clip.pattern} at bar {int(start // 4) + 1}",
                      lambda p: (p.playlist.append(clip), p.playlist.sort(key=lambda c: (c.start, c.track))))

    def mouseMoveEvent(self, e):
        if self.adrag:
            i, b0, t0, a = self.adrag
            start = max(0.0, round(a.start + self.beat_at(e.position().x()) - b0))
            track = max(0, min(TRACKS - 1, a.track + self.track_at(e.position().y()) - t0))
            self.apreview = AudioClip(a.path, float(start), track, a.gain)
            self.update()
            return
        if not self.drag:
            return
        i, b0, t0, c = self.drag
        start = max(0.0, round(c.start + self.beat_at(e.position().x()) - b0))
        track = max(0, min(TRACKS - 1, c.track + self.track_at(e.position().y()) - t0))
        self.preview = Clip(c.pattern, float(start), track)
        self.update()

    def mouseReleaseEvent(self, e):
        if self.adrag:
            i, _, _, a = self.adrag
            new, self.adrag, self.apreview = self.apreview, None, None
            if new and (new.start, new.track) != (a.start, a.track):
                def fn(q):
                    q.audio_clips[i] = new
                    q.audio_clips.sort(key=lambda c: (c.start, c.track))
                self.doc.edit(f"move audio {Path(a.path).name}", fn)
            self.update()
            return
        if not self.drag:
            return
        i, _, _, c = self.drag
        new = self.preview
        self.drag, self.preview = None, None
        if new and (new.start, new.track) != (c.start, c.track):
            def fn(p):
                p.playlist[i] = new
                p.playlist.sort(key=lambda x: (x.start, x.track))
            self.doc.edit(f"move clip {c.pattern}", fn)
        self.update()


class Playlist(QWidget):
    def __init__(self, doc):
        super().__init__()
        self.grid = PlaylistGrid(doc)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.grid)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.scroll)
        doc.changed.connect(self.refresh)
        doc.selection.connect(self.grid.update)
        self.refresh()

    def refresh(self) -> None:
        self.grid.invalidate()
        self.grid.resize(self.grid.sizeHint())
        self.grid.update()

    def set_play_position(self, beat: float | None) -> None:
        self.grid.play_beat = beat
        self.grid.update()
