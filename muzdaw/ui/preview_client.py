"""Talks to the note preview server (muzdaw.preview) running in its own process."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from ..model import Channel


def headless_env() -> QProcessEnvironment:
    """Environment for helper processes: no screen, so no plugin (e.g. Surge) can open a window."""
    env = QProcessEnvironment.systemEnvironment()
    for var in ("DISPLAY", "WAYLAND_DISPLAY"):
        env.remove(var)
    env.insert("QT_QPA_PLATFORM", "offscreen")
    return env


class Previewer(QObject):
    result = Signal(str)  # "ok ..." / "error ..." line from the server

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self._buf = b""
        self._id = 0

    def _ensure(self) -> QProcess:
        if self.proc is None or self.proc.state() == QProcess.ProcessState.NotRunning:
            self.proc = QProcess(self)
            self.proc.setProcessEnvironment(headless_env())
            self.proc.readyReadStandardOutput.connect(self._read)
            self.proc.start(sys.executable, ["-m", "muzdaw.preview"])
        return self.proc

    def note_on(self, ch: Channel, pitch: int, velocity: int = 100, sr: int = 44100, master: float = 0.8) -> int:
        """Start a note; it sounds until note_off(id) (or about 4 s). Returns the note id."""
        self._id += 1
        req = {"cmd": "on", "id": self._id, "channel": asdict(ch), "pitch": int(pitch),
               "velocity": int(velocity), "sr": sr, "master": master}
        self._ensure().write((json.dumps(req) + "\n").encode())
        return self._id

    def warm(self, ch: Channel, sr: int = 44100, master: float = 0.8) -> None:
        """Load a plugin instrument now, so the first key is instant."""
        req = {"cmd": "warm", "channel": asdict(ch), "sr": sr, "master": master}
        self._ensure().write((json.dumps(req) + "\n").encode())

    def note_off(self, note_id: int) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.write((json.dumps({"cmd": "off", "id": int(note_id)}) + "\n").encode())

    def _read(self) -> None:
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for ln in lines:
            text = ln.decode(errors="replace").strip()
            if text.startswith(("ok ", "error")):
                self.result.emit(text)

    def close(self) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.closeWriteChannel()  # server ends at end of stdin
            if not self.proc.waitForFinished(2000):
                self.proc.kill()
                self.proc.waitForFinished(1000)
