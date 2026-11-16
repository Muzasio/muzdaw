"""Note preview server: a tiny sampler for the piano keys in the UI.

Runs as its own process (`python -m muzdaw.preview`) so a plugin crash or a
slow render never freezes the UI. One JSON request per line on stdin:
    {"cmd": "on", "id": 7, "channel": {...Channel...}, "pitch": 60, "velocity": 100, "sr": 44100, "master": 0.8}
    {"cmd": "off", "id": 7}
"on" starts the note (it keeps sounding while held, up to HOLD_SEC);
"off" lets it go with a short fade (like lifting a finger).
    {"cmd": "warm", "channel": {...}}   (get a plugin instrument ready before the first key)
Plugin instruments (Surge, Odin2, Dexed, Cardinal, Zyn...) play LIVE through Carla: notes are
instant and sustain for real (no pre-render). Built-in and GM sounds use the fast sampler.
Writes one line per request: "ok on PITCH VEL MS PEAK cached|new", "ok off ID" or "error TEXT".
Rendered notes are cached per (channel, pitch, velocity level).
Env MUZDAW_SILENT=1: render but never open the sound output (tests).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .events import Event
from .model import Channel, Project, ProjectError
from .render import apply_fx, pan_gains, render_channel

HOLD_SEC = 4.0       # longest a held key sounds
TAIL_SEC = 1.0       # instrument release after the hold
FADE_SEC = 0.25      # fade when the key is let go
VEL_LEVELS = 16      # velocity is rounded to 16 levels (8, 16, ... 127) for the cache
CACHE_MAX = 256


def vel_level(velocity: int) -> int:
    step = 128 // VEL_LEVELS
    return max(step, min(127, round(velocity / step) * step))


def render_note(ch: Channel, pitch: int, sr: int = 44100, master: float = 0.8, velocity: int = 100,
                hold_sec: float = HOLD_SEC) -> np.ndarray:
    """One held note through the channel's instrument, fx, volume and pan. Shape (2, n)."""
    ch.validate()
    if not 0 <= pitch <= 127:
        raise ProjectError(f"pitch must be 0-127, got {pitch}")
    if not 1 <= velocity <= 127:
        raise ProjectError(f"velocity must be 1-127, got {velocity}")
    p = Project(tempo=60.0, sample_rate=sr, master=master, channels=[ch])  # 60 BPM: 1 beat = 1 s
    with tempfile.TemporaryDirectory(prefix="muzdaw-preview-") as td:
        dry = render_channel(p, ch, [Event(0.0, pitch, hold_sec, velocity)], hold_sec + TAIL_SEC, Path(td))
    y = apply_fx(ch.fx, dry, sr, f"channel {ch.name}")
    gl, gr = pan_gains(ch.pan)
    return (y * np.array([[gl], [gr]], np.float32) * ch.volume * master).astype(np.float32)


class Voice:
    def __init__(self, audio: np.ndarray, sr: int):
        self.audio = np.ascontiguousarray(audio.T)  # (n, 2)
        self.pos = 0
        self.fade_len = max(1, int(FADE_SEC * sr))
        self.fade_pos: int | None = None  # set when released

    def release(self) -> None:
        if self.fade_pos is None:
            self.fade_pos = 0

    def done(self) -> bool:
        return self.pos >= len(self.audio) or (self.fade_pos is not None and self.fade_pos >= self.fade_len)

    def take(self, n: int) -> np.ndarray:
        chunk = self.audio[self.pos:self.pos + n]
        self.pos += len(chunk)
        if self.fade_pos is not None and len(chunk):
            k = np.arange(self.fade_pos, self.fade_pos + len(chunk))
            g = np.clip(1.0 - k / self.fade_len, 0.0, 1.0).astype(np.float32)
            chunk = chunk * g[:, None]
            self.fade_pos += len(chunk)
        return chunk


class Mixer:
    """Plays voices on one sounddevice output stream (polyphonic, fade on release)."""

    def __init__(self, play: bool):
        self.play = play
        self.voices: dict[int, Voice] = {}
        self.lock = threading.Lock()
        self.stream = None
        self.sr = None

    def _callback(self, out, frames, _time, _status) -> None:
        out.fill(0)
        with self.lock:
            for vid in list(self.voices):
                v = self.voices[vid]
                chunk = v.take(frames)
                out[:len(chunk)] += chunk
                if v.done():
                    del self.voices[vid]
        np.clip(out, -1.0, 1.0, out=out)

    def _ensure_stream(self, sr: int) -> None:
        if not self.play or (self.stream is not None and self.sr == sr):
            return
        import sounddevice as sd
        if self.stream is not None:
            self.stream.close()
        self.stream = sd.OutputStream(samplerate=sr, channels=2, dtype="float32", callback=self._callback,
                                      latency="low")
        self.stream.start()
        self.sr = sr

    def start(self, vid: int, audio: np.ndarray, sr: int) -> None:
        self._ensure_stream(sr)
        with self.lock:
            self.voices[vid] = Voice(audio, sr)

    def release(self, vid: int) -> bool:
        with self.lock:
            v = self.voices.get(vid)
            if v:
                v.release()
            return v is not None

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()


class LiveVoices:
    """One plugin channel loaded in Carla; notes go straight in as MIDI (instant, real sustain)."""

    def __init__(self, play: bool):
        self.play = play
        self.sess = None
        self.key: str | None = None
        self.notes: dict[int, tuple[int, int]] = {}  # voice id -> (midi channel, pitch)

    @staticmethod
    def wanted(ch: Channel) -> bool:
        from .live import CARLA_LIB
        return ch.instrument == "plugin" and CARLA_LIB.exists()

    def ensure(self, ch_dict: dict, master: float) -> None:
        key = json.dumps([ch_dict, master], sort_keys=True)
        if key == self.key and self.sess is not None:
            return
        self.close()
        from .live import LiveSession
        from .model import Clip, Pattern
        ch = Channel(**ch_dict)
        ch.mute = False
        ch.output, ch.sends = "master", {}
        p = Project(master=master, channels=[ch], patterns=[Pattern("p", 4.0)], playlist=[Clip("p", 0.0)])
        sess = LiveSession(p, audible=self.play)
        sess.start()  # loads the plugin + patch + fx (~0.5-2 s once per channel)
        self.sess, self.key = sess, key

    def on(self, vid: int, pitch: int, velocity: int) -> float:
        s = self.sess
        node = s.instruments[next(iter(s.instruments))]
        s.h.send_midi_note(node.pid, 0, pitch, velocity)
        self.notes[vid] = (node.pid, pitch)
        return 0.0

    def off(self, vid: int) -> bool:
        n = self.notes.pop(vid, None)
        if n is None or self.sess is None:
            return False
        self.sess.h.send_midi_note(n[0], 0, n[1], 0)  # velocity 0 = note off
        return True

    def peak(self) -> float:
        s = self.sess
        return s.peak(s.master_strip) if s else 0.0

    def idle(self) -> None:
        if self.sess is not None:
            self.sess.h.engine_idle()

    def close(self) -> None:
        if self.sess is not None:
            for vid in list(self.notes):
                self.off(vid)
            self.sess.close()
        self.sess, self.key, self.notes = None, None, {}


class Server:
    def __init__(self, play: bool = True):
        self.mixer = Mixer(play)
        self.live = LiveVoices(play)
        self.cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._next_id = 1_000_000  # ids for requests without one

    def _note(self, req: dict) -> tuple[np.ndarray, str, int, int]:
        ch = Channel(**req["channel"])
        pitch, sr = int(req["pitch"]), int(req.get("sr", 44100))
        master = float(req.get("master", 0.8))
        vel = vel_level(int(req.get("velocity", 100)))
        key = json.dumps([req["channel"], pitch, vel, sr, master], sort_keys=True)
        audio = self.cache.get(key)
        if audio is not None:
            self.cache.move_to_end(key)
            return audio, "cached", sr, vel
        audio = render_note(ch, pitch, sr, master, vel)
        self.cache[key] = audio
        if len(self.cache) > CACHE_MAX:
            self.cache.popitem(last=False)
        return audio, "new", sr, vel

    def handle(self, line: str) -> str:
        try:
            req = json.loads(line)
            cmd = req.get("cmd", "on")
            if cmd == "off":
                vid = int(req["id"])
                done = self.live.off(vid) or self.mixer.release(vid)
                return f"ok off {vid}" if done else f"ok off {vid} (already ended)"
            if cmd not in ("on", "warm"):
                return f"error unknown cmd {cmd!r}"
            t = time.perf_counter()
            ch = Channel(**req["channel"])
            ch.validate()
            if LiveVoices.wanted(ch):
                self.live.ensure(req["channel"], float(req.get("master", 0.8)))
                if cmd == "warm":
                    return f"ok warm live {(time.perf_counter() - t) * 1000:.0f}"
                pitch, vel = int(req["pitch"]), max(1, min(127, int(req.get("velocity", 100))))
                if not 0 <= pitch <= 127:
                    raise ProjectError(f"pitch must be 0-127, got {pitch}")
                vid = int(req["id"]) if "id" in req else self._next_id
                self._next_id += 1
                self.live.on(vid, pitch, vel)
                return f"ok on {pitch} {vel} {(time.perf_counter() - t) * 1000:.0f} 0.000 live"
            if cmd == "warm":
                return "ok warm sampler 0"
            audio, state, sr, vel = self._note(req)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            return f"error bad request: {e}"
        except (ProjectError, RuntimeError) as e:
            return f"error {e}"
        vid = int(req["id"]) if "id" in req else self._next_id
        self._next_id += 1
        try:
            self.mixer.start(vid, audio, sr)
        except Exception as e:  # sound device problems (PortAudioError) must not kill the server
            return f"error sound output: {e}"
        ms = (time.perf_counter() - t) * 1000
        return f"ok on {req['pitch']} {vel} {ms:.0f} {float(np.abs(audio).max()):.3f} {state}"


def main() -> int:
    import selectors
    srv = Server(play=not os.environ.get("MUZDAW_SILENT"))
    fd = sys.stdin.fileno()
    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ)
    buf = b""
    try:
        while True:
            # wake at least every 50 ms to keep Carla's host idle running
            if sel.select(timeout=0.05):
                chunk = os.read(fd, 65536)  # raw read: no hidden Python buffer, so select() stays truthful
                if not chunk:
                    break  # UI closed our stdin
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    if line.strip():
                        print(srv.handle(line.decode("utf-8", "replace")), flush=True)
            srv.live.idle()
    finally:
        srv.live.close()
        srv.mixer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
