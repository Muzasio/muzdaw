"""Recording (WAV) -> notes with basic-pitch, then into a pattern.

basic-pitch needs Python 3.12 (its own venv), so it runs as a subprocess.
Which Python: env MUZDAW_PITCH_PYTHON, else `<repo>/.venv-pitch/bin/python`.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .model import Note, Pattern, Project, ProjectError
from .quantize import pattern_length_for, quantize

WORKER = Path(__file__).with_name("pitch_worker.py")
TIMEOUT = 600  # seconds


def pitch_python() -> Path:
    env = os.environ.get("MUZDAW_PITCH_PYTHON")
    p = Path(env) if env else Path(__file__).resolve().parents[1] / ".venv-pitch" / "bin" / "python"
    if not p.is_file():
        raise ProjectError(f"basic-pitch Python not found at {p} "
                           "(make the .venv-pitch venv or set MUZDAW_PITCH_PYTHON)")
    return p


def detect(wav: str | Path, onset: float = 0.5, frame: float = 0.3, min_ms: float = 58.0,
           min_hz: float = 0.0, max_hz: float = 0.0) -> list[tuple[float, float, int, float]]:
    """Return (start_s, end_s, pitch, amplitude) for each note found in the WAV."""
    wav = Path(wav)
    if not wav.is_file():
        raise ProjectError(f"audio file not found: {wav}")
    for name, v in (("onset", onset), ("frame", frame)):
        if not 0.05 <= v <= 0.95:
            raise ProjectError(f"{name} threshold must be 0.05-0.95, got {v}")
    args = [str(pitch_python()), "-I", str(WORKER), str(wav.resolve()),
            str(onset), str(frame), str(min_ms), str(min_hz), str(max_hz)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise ProjectError(f"note detection took over {TIMEOUT} s") from None
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["no error text"])[-1]
        raise ProjectError(f"note detection failed: {tail}")
    try:
        return [tuple(n) for n in json.loads(r.stdout)["notes"]]
    except (ValueError, KeyError, TypeError) as e:
        raise ProjectError(f"note detection gave bad output: {e}") from e


def to_notes(events, tempo: float, offset_s: float = 0.0) -> list[Note]:
    """Seconds -> beats. offset_s: seconds of audio before pattern beat 0 (e.g. count-in)."""
    bps = tempo / 60.0
    out = []
    for s, e, pitch, amp in events:
        s -= offset_s
        e -= offset_s
        if e <= 0:
            continue
        s = max(0.0, s)
        vel = max(1, min(127, round(40 + amp * 87)))  # amplitude 0..1 -> velocity 40..127
        out.append(Note(int(pitch), round(s * bps, 6), round(max(e - s, 0.01) * bps, 6), vel))
    return sorted(out, key=lambda n: (n.start, n.pitch))


def put_notes(p: Project, pattern: str, channel: str, notes: list[Note], replace_old: bool = False) -> Pattern:
    """Add notes to a channel in a pattern (made if missing; grown to fit)."""
    p.channel(channel)  # must exist
    try:
        pat = p.pattern(pattern)
    except ProjectError:
        pat = Pattern(name=pattern)
        p.patterns.append(pat)
    old = [] if replace_old else pat.notes.get(channel, [])
    pat.notes[channel] = sorted(old + notes, key=lambda n: (n.start, n.pitch))
    pat.length = max(pat.length, pattern_length_for(pat.notes[channel]))
    return pat


def rec2midi(p: Project, wav: str | Path, channel: str, pattern: str, grid: float | None = None,
             strength: float = 1.0, replace_old: bool = False, offset_s: float = 0.0, **detect_args) -> int:
    """Detect notes in WAV and put them into the project. Returns the note count."""
    notes = to_notes(detect(wav, **detect_args), p.tempo, offset_s)
    if grid:
        notes = quantize(notes, grid, strength)
    put_notes(p, pattern, channel, notes, replace_old)
    return len(notes)
