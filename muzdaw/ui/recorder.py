"""Record the typing keyboard into a pattern (loop recording, FL style).

Flow: start() -> count-in (clicks) -> recording: every note you play is stored at its
beat inside the pattern; when the pattern ends, recording wraps to its start and keeps
adding notes. stop() returns the take as Notes (optionally quantized).

The clock is injectable (tests use a fake clock); clicks are sent through a callback,
so the UI decides how they sound.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..model import Note
from ..quantize import quantize


class TakeRecorder(QObject):
    state = Signal(str)        # "count-in 3", "rec bar 2 beat 1", "idle"
    click = Signal(bool)       # True = first beat of a bar (accent)

    def __init__(self, parent=None, clock=time.perf_counter):
        super().__init__(parent)
        self.clock = clock
        self.timer = QTimer(self)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.tick)
        self.active = False
        self.waiting = False
        self._reset()

    def _reset(self) -> None:
        self.t0 = 0.0            # clock time of recorded beat 0 (after the count-in)
        self.bps = 2.0           # beats per second
        self.length = 4.0        # pattern length in beats
        self.count_in = 4
        self.metronome = False
        self._last_beat = None
        self._open: dict[int, tuple[float, int]] = {}   # pitch -> (beat, velocity)
        self.notes: list[Note] = []

    # ---- control ---------------------------------------------------------------
    def wait_for_start(self) -> None:
        """Armed: the playback engine will tell us when beat 0 is heard (start_at)."""
        self._reset()
        self.active, self.waiting = True, True

    def start_at(self, t0: float, tempo: float, length_beats: float) -> None:
        """Beat 0 is heard at time.monotonic() == t0 (from the engine; count-in before it)."""
        self.clock = time.monotonic
        self.bps = tempo / 60.0
        self.length = max(0.25, float(length_beats))
        self.count_in, self.metronome = 0, False  # the engine plays clicks and count-in
        self.t0 = t0
        self.active, self.waiting = True, False
        self.timer.start()

    def start(self, tempo: float, length_beats: float, count_in: int = 4, metronome: bool = False) -> None:
        self._reset()
        self.bps = tempo / 60.0
        self.length = max(0.25, float(length_beats))
        self.count_in = max(0, int(count_in))
        self.metronome = metronome
        self.t0 = self.clock() + self.count_in / self.bps
        self.active = True
        self.timer.start()
        self.tick()

    def beat_now(self) -> float:
        """Beats since recorded beat 0 (negative during the count-in)."""
        return (self.clock() - self.t0) * self.bps

    def tick(self) -> None:
        if not self.active:
            return
        b = self.beat_now()
        whole = int(b // 1)
        if whole != self._last_beat:
            self._last_beat = whole
            if whole < 0:
                self.click.emit(whole % 4 == 0)
                self.state.emit(f"count-in {-whole}")
            else:
                pos = whole % int(max(1, round(self.length)))
                if self.metronome:
                    self.click.emit(pos % 4 == 0)
                self.state.emit(f"REC  bar {pos // 4 + 1}  beat {pos % 4 + 1}")

    def note_on(self, pitch: int, velocity: int) -> None:
        if not self.active or self.waiting:
            return
        b = self.beat_now()
        if b < -0.25:  # played during the count-in (a little early is fine)
            return
        self._close(pitch, b)
        self._open[pitch] = (max(0.0, b), velocity)

    def note_off(self, pitch: int) -> None:
        if self.active and not self.waiting:
            self._close(pitch, self.beat_now())

    def _close(self, pitch: int, b_end: float) -> None:
        if pitch not in self._open:
            return
        b0, vel = self._open.pop(pitch)
        start = b0 % self.length                         # loop recording: wrap into the pattern
        length = max(0.01, min(b_end - b0, self.length - start))  # never past the pattern end
        self.notes.append(Note(pitch, round(start, 6), round(length, 6), vel))

    def stop(self, grid: float | None = None) -> list[Note]:
        """End the take. grid (beats): quantize starts (and keep them inside the pattern)."""
        if not self.active:
            return []
        if self.waiting:  # stopped before the engine started: nothing recorded
            self.active = self.waiting = False
            self.state.emit("idle")
            return []
        b = self.beat_now()
        for pitch in list(self._open):
            self._close(pitch, b)
        self.active = False
        self.timer.stop()
        self.state.emit("idle")
        notes = self.notes
        if grid:
            notes = [Note(n.pitch, n.start % self.length, n.length, n.velocity)
                     for n in quantize(notes, grid)]
            notes = [Note(n.pitch, n.start, max(0.01, min(n.length, self.length - n.start)), n.velocity)
                     for n in notes]
        return sorted(notes, key=lambda n: (n.start, n.pitch))
