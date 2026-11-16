"""Talks to the playback engine (muzdaw.engine) running in its own process, without a screen."""
from __future__ import annotations

import json
import sys

from PySide6.QtCore import QObject, QProcess, Signal

from .preview_client import headless_env


class EngineClient(QObject):
    started = Signal(float, float, float, float, int)  # t0 (monotonic), beat at t0, beats/s, loop beats, count-in
    stopped = Signal()
    preparing = Signal()
    failed = Signal(str)
    updated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self._buf = b""
        self.state = "idle"  # idle | preparing | playing

    @property
    def active(self) -> bool:
        return self.state != "idle"

    def _ensure(self) -> QProcess:
        if self.proc is None or self.proc.state() == QProcess.ProcessState.NotRunning:
            self.proc = QProcess(self)
            self.proc.setProcessEnvironment(headless_env())  # plugins can't open windows
            self.proc.readyReadStandardOutput.connect(self._read)
            self.proc.finished.connect(self._died)
            self.proc.start(sys.executable, ["-m", "muzdaw.engine"])
        return self.proc

    def _send(self, obj: dict) -> None:
        self._ensure().write((json.dumps(obj) + "\n").encode())

    def play(self, path: str, mode: str, pattern: str | None, from_beat: float = 0.0, loop: bool = True,
             metronome: bool = False, count_in: int = 0) -> None:
        self.state = "preparing"
        self._send({"cmd": "play", "path": path, "mode": mode, "pattern": pattern, "from": from_beat,
                    "loop": loop, "metronome": metronome, "count_in": count_in})

    def update(self) -> None:
        if self.state == "playing":
            self._send({"cmd": "update"})

    def stop(self) -> None:
        if self.active:
            self._send({"cmd": "stop"})
        self.state = "idle"

    def _read(self) -> None:
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            line = raw.decode(errors="replace").strip()
            if line == "preparing":
                self.preparing.emit()
            elif line.startswith("started "):
                try:
                    t0, beat, bps, loop_beats, cnt = line.split()[1:6]
                    vals = (float(t0), float(beat), float(bps), float(loop_beats), int(cnt))
                except ValueError:
                    continue
                if self.state != "idle":
                    self.state = "playing"
                    self.started.emit(*vals)
            elif line.startswith("updated "):
                self.updated.emit(line)
            elif line == "stopped":
                if self.state != "idle":
                    self.state = "idle"
                    self.stopped.emit()
            elif line.startswith("error"):
                self.state = "idle"
                self.failed.emit(line[6:].strip() or "engine error")

    def _died(self, *_):
        if self.state != "idle":
            self.state = "idle"
            self.failed.emit("playback engine stopped unexpectedly")
            self.stopped.emit()

    def close(self) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.closeWriteChannel()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()
                self.proc.waitForFinished(1000)
