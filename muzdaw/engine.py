"""Playback engine for the window (Space): plays exactly what the export renders.

Runs as its own process (`python -m muzdaw.engine`), started without a screen.
It builds the audio with the same code as `render` (render_channel + mix), so
what you hear is what you export. Each channel's dry audio is cached; after an
edit only changed channels are rendered again and the new audio is swapped in
at the same position, so you hear changes while it plays.

JSON line commands on stdin:
    {"cmd": "play", "path": SONG, "mode": "pattern"|"song", "pattern": NAME,
     "from": BEAT, "loop": true, "metronome": false, "count_in": 0}
    {"cmd": "update"}     song file changed: rebuild what changed, keep playing
    {"cmd": "stop"}
Lines on stdout:
    preparing
    started T0 BEAT0 BEATS_PER_SEC LOOP_BEATS COUNT_IN   (T0 = time.monotonic() when BEAT0 is heard)
    updated MS RENDERED
    stopped
    error TEXT
Env MUZDAW_SILENT=1: builds everything but never opens the sound output (tests).
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .events import Event, pattern_events, song_events
from .model import Channel, Project, ProjectError, load
from .render import TAIL_SEC, mix, render_channel

DRY_FIELDS = ("instrument", "plugin", "patch", "program", "bank", "params", "sample", "root")
CLICK = Channel("metronome", "gm", program=0, bank=128, volume=1.0)


@dataclass
class Take:
    """What to play: built audio + where beat numbers sit in it."""
    pre: np.ndarray        # count-in clicks (2, p), may be empty
    buf: np.ndarray        # the music (2, n)
    loop: bool
    beat0: float           # song/pattern beat at buf[0]
    loop_beats: float      # length of buf in beats (0 = not looping)
    bps: float             # beats per second
    count_in: int
    sr: int


class Builder:
    """Renders takes; keeps every channel's dry audio so edits re-render only what changed."""

    def __init__(self):
        self.stems: dict[str, np.ndarray] = {}
        self.renders = 0  # how many channel renders actually ran (tests check the cache)

    def _stem(self, p: Project, ch: Channel, events: list[Event], seconds: float, tmp: Path) -> np.ndarray:
        d = asdict(ch)
        key = hashlib.sha1(json.dumps([{k: d[k] for k in DRY_FIELDS}, [list(map(float, (e.start, e.pitch, e.length, e.velocity)))
                                                                         for e in events],
                                       p.tempo, p.sample_rate, round(seconds, 6)],
                                      sort_keys=True).encode()).hexdigest()
        if key not in self.stems:
            self.stems[key] = render_channel(p, ch, events, seconds, tmp)
            self.renders += 1
            if len(self.stems) > 256:
                self.stems.pop(next(iter(self.stems)))
        return self.stems[key]

    def clicks(self, p: Project, beats: int, tmp: Path, accent_every: int = 4) -> np.ndarray:
        evs = [Event(float(b), 76 if b % accent_every == 0 else 77, 0.1, 120 if b % accent_every == 0 else 90)
               for b in range(beats)]
        return self._stem(p, CLICK, evs, p.beats_to_sec(beats), tmp)

    def build(self, p: Project, mode: str, pattern: str | None, start: float, loop: bool,
              metronome: bool, count_in: int) -> Take:
        p.validate()
        sr = p.sample_rate
        if mode == "pattern":
            if not pattern:
                raise ProjectError("no pattern selected")
            events = pattern_events(p, pattern)
            length = p.pattern(pattern).length
            loop = True
            beat0 = 0.0
        else:
            events = song_events(p)
            length = p.song_length()
            if length <= 0:
                raise ProjectError("the playlist is empty: place a pattern first (F5), or use PAT mode")
            beat0 = 0.0
        seconds = p.beats_to_sec(length) + TAIL_SEC
        n_loop = round(p.beats_to_sec(length) * sr)
        n = round(seconds * sr)
        with tempfile.TemporaryDirectory(prefix="muzdaw-engine-") as td:
            dry = {ch.name: self._stem(p, ch, events[ch.name], seconds, Path(td))
                   for ch in p.channels if not ch.mute and events[ch.name]}
            clips = None
            if mode == "song" and p.audio_clips:
                from .samples import add_audio_clips
                clips = np.zeros((2, n), np.float32)
                add_audio_clips(p, clips)
            wet = mix(p, dry, n, clips) if (dry or clips is not None) else np.zeros((2, n), np.float32)
            if metronome:
                wet[:, :n_loop] += self.clicks(p, int(np.ceil(length)), Path(td))[:, :n_loop] * 0.6
            pre = (self.clicks(p, count_in, Path(td)) if count_in > 0 else np.zeros((2, 0), np.float32))
        wet = wet.astype(np.float32)
        if loop:
            # fold the tail (release, reverb) back onto the start, so the loop joins without a gap
            buf = wet[:, :n_loop].copy()
            tail = wet[:, n_loop:]
            k = min(tail.shape[1], n_loop)
            buf[:, :k] += tail[:, :k]
        else:
            buf = wet
        return Take(pre.astype(np.float32), buf, loop, beat0, length if loop else 0.0, p.tempo / 60.0, count_in, sr)


class Player:
    """Plays a Take on one sound stream; a new Take can replace the old one at the same place."""

    def __init__(self, play: bool):
        self.play_sound = play
        self.lock = threading.Lock()
        self.take: Take | None = None
        self.pos = 0            # frames into pre + buf
        self.playing = False
        self.ended = False
        self.stream = None
        self.sr = None
        self.latency = 0.0

    def _callback(self, out, frames, _time, _status) -> None:
        out.fill(0)
        with self.lock:
            t = self.take
            if not self.playing or t is None:
                return
            i = 0
            p = t.pre.shape[1]
            n = t.buf.shape[1]
            while i < frames:
                if self.pos < p:
                    k = min(frames - i, p - self.pos)
                    out[i:i + k] = t.pre[:, self.pos:self.pos + k].T
                else:
                    j = self.pos - p
                    if j >= n:
                        if t.loop and n > 0:
                            self.pos = p
                            continue
                        self.playing, self.ended = False, True
                        break
                    k = min(frames - i, n - j)
                    out[i:i + k] = t.buf[:, j:j + k].T
                i += k
                self.pos += k
        np.clip(out, -1.0, 1.0, out=out)

    def _ensure_stream(self, sr: int) -> None:
        if not self.play_sound:
            self.latency = 0.0
            return
        if self.stream is not None and self.sr == sr:
            return
        import sounddevice as sd
        if self.stream is not None:
            self.stream.close()
        self.stream = sd.OutputStream(samplerate=sr, channels=2, dtype="float32", callback=self._callback,
                                      latency="high")  # ~0.1 s: background renders never cause clicks
        self.stream.start()
        self.sr = sr
        self.latency = float(self.stream.latency)

    def start(self, take: Take, from_beat: float = 0.0) -> float:
        """Start playing. Returns T0: monotonic time when beat `from_beat` is heard."""
        self._ensure_stream(take.sr)
        j = round(max(0.0, from_beat - take.beat0) / take.bps * take.sr)
        if take.loop and take.buf.shape[1]:
            j %= take.buf.shape[1]
        with self.lock:
            self.take = take
            self.pos = take.pre.shape[1] + min(j, take.buf.shape[1])
            self.playing, self.ended = True, False
        return time.monotonic() + self.latency + take.pre.shape[1] / take.sr

    def swap(self, take: Take) -> tuple[float, float]:
        """Replace the audio but keep the musical position. Returns (T0, beat heard at T0)."""
        empty = np.zeros((2, 0), np.float32)
        with self.lock:
            old = self.take
            p_old = old.pre.shape[1] if old is not None else 0
            if old is not None and self.pos < p_old:
                # still counting in: keep the clicks that are left, the music starts after them
                remaining = old.pre[:, self.pos:]
                self.take = Take(remaining, take.buf, take.loop, take.beat0, take.loop_beats, take.bps,
                                 take.count_in, take.sr)
                self.pos = 0
                pre_left, beat = remaining.shape[1], take.beat0
            else:
                beat = (old.beat0 + (self.pos - p_old) / old.sr * old.bps) if old is not None else take.beat0
                j = round((beat - take.beat0) / take.bps * take.sr)
                if take.loop and take.buf.shape[1]:
                    j %= take.buf.shape[1]
                self.take = Take(empty, take.buf, take.loop, take.beat0, take.loop_beats, take.bps,
                                 take.count_in, take.sr)
                self.pos = max(0, min(j, take.buf.shape[1]))
                pre_left = 0
                if take.loop and take.loop_beats:
                    beat = take.beat0 + (self.pos / take.sr * take.bps)
        return time.monotonic() + self.latency + pre_left / take.sr, beat

    def stop(self) -> None:
        with self.lock:
            self.playing = False

    def close(self) -> None:
        self.stop()
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class Engine:
    """IO on the main thread, ALL rendering on one worker thread.

    DawDreamer hangs when one process renders on two different threads (tested 2026-11-16:
    a Faust render on the main thread, then one on another thread never returns). So the
    engine process never renders anywhere else than in `_work`.
    """

    def __init__(self, play: bool = True, out=print):
        self.builder = Builder()
        self.player = Player(play)
        self.out = out
        self.req: dict | None = None
        self.jobs: queue.Queue = queue.Queue()
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        self.pending_update = False
        self._gen = 0  # newest request number; older results are dropped
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()

    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            gen, kind, req = job
            t = time.perf_counter()
            n0 = self.builder.renders
            try:
                p = load(req["path"])
                take = self.builder.build(p, req.get("mode", "pattern"), req.get("pattern"),
                                          float(req.get("from", 0.0)), bool(req.get("loop", True)),
                                          bool(req.get("metronome", False)),
                                          int(req.get("count_in", 0)) if kind == "play" else 0)
                self.results.put((gen, kind, req, take, (time.perf_counter() - t) * 1000,
                                  self.builder.renders - n0, None))
            except (ProjectError, OSError, ValueError, KeyError, RuntimeError) as e:
                self.results.put((gen, kind, req, None, 0, 0, str(e)))

    def _submit(self, kind: str, req: dict) -> None:
        self._gen += 1
        self.busy = True
        self.jobs.put((self._gen, kind, dict(req)))

    def handle(self, line: str) -> None:
        try:
            req = json.loads(line)
            cmd = req.get("cmd")
        except (ValueError, AttributeError) as e:
            self.out(f"error bad request: {e}")
            return
        if cmd == "play":
            if not req.get("path"):
                self.out("error play needs a song path")
                return
            self.req = req
            self.out("preparing")
            self._submit("play", req)
        elif cmd == "update":
            if self.req is None or not self.player.playing:
                return
            if self.busy:
                self.pending_update = True  # do it right after the running build
            else:
                self._submit("update", self.req)
        elif cmd == "stop":
            self._gen += 1  # drop builds still running
            self.req = None
            self.pending_update = False
            self.player.stop()
            self.out("stopped")
        else:
            self.out(f"error unknown cmd {cmd!r}")

    def poll(self) -> None:
        """Main thread: take finished builds, start/swap audio, report."""
        if self.player.ended:
            self.player.ended = False
            self.req = None
            self.out("stopped")
        while True:
            try:
                gen, kind, req, take, ms, rendered, err = self.results.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            if gen != self._gen:
                continue  # stopped or replaced meanwhile
            if err:
                self.out(f"error {err}")
                if kind == "play":
                    self.req = None
                continue
            if kind == "play":
                t0 = self.player.start(take, float(req.get("from", 0.0)) if req.get("mode") == "song" else 0.0)
                beat = float(req.get("from", 0.0)) if req.get("mode") == "song" else 0.0
                self.out(f"started {t0:.6f} {beat:.6f} {take.bps:.6f} {take.loop_beats:.6f} {take.count_in}")
            else:
                if not self.player.playing:
                    continue
                t0, beat = self.player.swap(take)
                self.out(f"updated {ms:.0f} {rendered}")
                self.out(f"started {t0:.6f} {beat:.6f} {take.bps:.6f} {take.loop_beats:.6f} 0")
            if self.pending_update and self.req is not None:
                self.pending_update = False
                self._submit("update", self.req)

    def close(self) -> None:
        self.jobs.put(None)
        self.player.close()


def main() -> int:
    import selectors

    def out(text: str) -> None:
        print(text, flush=True)

    eng = Engine(play=not os.environ.get("MUZDAW_SILENT"), out=out)
    fd = sys.stdin.fileno()
    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ)
    buf = b""
    try:
        while True:
            if sel.select(timeout=0.02):
                chunk = os.read(fd, 65536)  # raw read: select() stays truthful
                if not chunk:
                    break
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    if line.strip():
                        eng.handle(line.decode("utf-8", "replace"))
            eng.poll()
    finally:
        eng.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
