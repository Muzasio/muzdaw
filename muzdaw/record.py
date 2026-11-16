"""Record MIDI (keyboard -> notes) and audio (input -> WAV).

MIDI uses mido + python-rtmidi (ALSA). Audio uses sounddevice (PortAudio).
Both take the clock from time.perf_counter(); beat 0 = the moment start() runs.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from .model import Note, ProjectError


def midi_inputs() -> list[str]:
    import mido
    return [n for n in mido.get_input_names() if "Midi Through" not in n]


def pick_input(name: str | None) -> str:
    """Exact name, or the first input whose name contains `name`, or the only real input."""
    names = midi_inputs()
    if name:
        for n in names:
            if n == name or name.lower() in n.lower():
                return n
        raise ProjectError(f"no MIDI input matching {name!r} (found: {', '.join(names) or 'none'})")
    if not names:
        raise ProjectError("no MIDI keyboard found (plug one in, then run `muzdaw midi-inputs`)")
    return names[0]


class MidiRecorder:
    """Collect note on/off from one MIDI input and turn them into Notes (in beats)."""

    def __init__(self, port: str, tempo: float):
        self.port_name, self.tempo = port, tempo
        self._events: list[tuple[float, object]] = []
        self._lock = threading.Lock()
        self._port = None
        self._t0: float | None = None
        self._t_end: float | None = None

    def _on_msg(self, msg) -> None:
        now = time.perf_counter()
        with self._lock:
            self._events.append((now, msg))

    def open(self) -> None:
        """Open the port early (before a count-in) so nothing is missed."""
        import mido
        try:
            self._port = mido.open_input(self.port_name, callback=self._on_msg)
        except (OSError, IOError) as e:
            raise ProjectError(f"cannot open MIDI input {self.port_name}: {e}") from e

    def start(self, at: float | None = None) -> None:
        """Beat 0 is now (or perf_counter time `at`). Earlier events are dropped."""
        self._t0 = time.perf_counter() if at is None else at

    def stop(self) -> list[Note]:
        if self._t_end is None:
            self._t_end = time.perf_counter()
        if self._port is not None:
            self._port.close()
            self._port = None
        return self.notes()

    def notes(self) -> list[Note]:
        if self._t0 is None:
            return []
        bps = self.tempo / 60.0
        end = self._t_end if self._t_end is not None else time.perf_counter()
        with self._lock:
            events = list(self._events)
        open_: dict[tuple[int, int], list[tuple[float, int]]] = {}
        out: list[Note] = []

        def close(key, t_off):
            t_on, vel = open_[key].pop(0)
            s = max(0.0, (t_on - self._t0) * bps)
            e = (t_off - self._t0) * bps
            if e > 0:
                out.append(Note(key[1], round(s, 6), round(max(e - s, 0.01), 6), vel))

        for t, m in events:
            if m.type == "note_on" and m.velocity > 0:
                open_.setdefault((m.channel, m.note), []).append((t, m.velocity))
            elif m.type in ("note_on", "note_off") and open_.get((m.channel, m.note)):
                close((m.channel, m.note), t)
        for key, stack in open_.items():  # keys still held at stop
            while stack:
                close(key, end)
        return sorted(out, key=lambda n: (n.start, n.pitch))


def audio_inputs() -> list[str]:
    import sounddevice as sd
    return [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


def write_wav(path: str | Path, data: np.ndarray, sample_rate: int) -> Path:
    """float -1..1 (frames x channels) -> 16-bit PCM WAV."""
    from scipy.io import wavfile
    path = Path(path)
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
    wavfile.write(str(path), sample_rate, pcm)
    return path


def record_audio(out: str | Path, seconds: float, sample_rate: int = 44100, channels: int = 1,
                 device: str | int | None = None, rec=None) -> tuple[Path, float]:
    """Record `seconds` from an input device into a WAV. Returns (path, peak).

    rec: function(frames, sample_rate, channels, device) -> float array; tests pass a fake.
    """
    if not 0.1 <= seconds <= 3600:
        raise ProjectError(f"seconds must be 0.1-3600, got {seconds}")
    if channels not in (1, 2):
        raise ProjectError("channels must be 1 or 2")
    frames = int(seconds * sample_rate)
    if rec is None:
        import sounddevice as sd

        def rec(frames, sr, ch, dev):
            try:
                data = sd.rec(frames, samplerate=sr, channels=ch, dtype="float32", device=dev)
                sd.wait()
            except (sd.PortAudioError, ValueError) as e:
                raise ProjectError(f"cannot record from input {dev!r}: {e}") from e
            return data
    if isinstance(device, str) and device.isdigit():
        device = int(device)
    data = np.asarray(rec(frames, sample_rate, channels, device), dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    path = write_wav(out, data, sample_rate)
    return path, float(np.max(np.abs(data))) if data.size else 0.0
