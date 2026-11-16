"""Audio files: sample instruments (one-shots triggered by notes) and audio clips on the playlist.

Files are read in place (never copied or changed) with pedalboard's AudioFile
(WAV, FLAC, MP3, OGG, AIFF), resampled to the project rate and cached in memory.
"""
from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .model import ProjectError

AUDIO_EXT = {".wav", ".flac", ".mp3", ".ogg", ".aiff", ".aif"}
_CACHE: OrderedDict[tuple, np.ndarray] = OrderedDict()
_CACHE_MAX_BYTES = 1_500_000_000  # ~1.5 GB of decoded audio, oldest dropped first
_DUR: dict[tuple, float] = {}


def check_path(path: str | Path) -> Path:
    """An existing, readable audio file (by extension). Raises ProjectError with a clear message."""
    p = Path(path).expanduser()
    if p.suffix.lower() not in AUDIO_EXT:
        raise ProjectError(f"not an audio file: {p.name} (use {', '.join(sorted(AUDIO_EXT))})")
    if not p.is_file():
        raise ProjectError(f"audio file not found: {p}")
    if not os.access(p, os.R_OK):
        raise ProjectError(f"audio file not readable: {p}")
    return p


def _key(p: Path, *extra) -> tuple:
    st = p.stat()
    return (str(p.resolve()), st.st_mtime_ns, st.st_size, *extra)


def load(path: str | Path, sr: int) -> np.ndarray:
    """Whole file as float32 (2, n) at sample rate sr (mono becomes stereo)."""
    p = check_path(path)
    key = _key(p, sr)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    from pedalboard.io import AudioFile
    try:
        with AudioFile(str(p)) as f:
            src = f.resampled_to(float(sr)) if f.samplerate != sr else f
            audio = src.read(src.frames)
    except (OSError, ValueError, RuntimeError) as e:
        raise ProjectError(f"cannot read audio file {p.name}: {e}") from e
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[None, :]
    if audio.shape[0] == 1:
        audio = np.repeat(audio, 2, axis=0)
    audio = np.ascontiguousarray(audio[:2])
    _CACHE[key] = audio
    while sum(a.nbytes for a in _CACHE.values()) > _CACHE_MAX_BYTES and len(_CACHE) > 1:
        _CACHE.popitem(last=False)
    return audio


def duration(path: str | Path) -> float:
    """Length in seconds (0 if the file is missing, so a song still opens)."""
    try:
        p = check_path(path)
        key = _key(p)
    except (ProjectError, OSError):
        return 0.0
    if key not in _DUR:
        from pedalboard.io import AudioFile
        try:
            with AudioFile(str(p)) as f:
                _DUR[key] = f.frames / f.samplerate
        except (OSError, ValueError, RuntimeError):
            _DUR[key] = 0.0
    return _DUR[key]


def pitched(audio: np.ndarray, semitones: float) -> np.ndarray:
    """Classic sampler pitch: play faster/slower (+12 = one octave up = half as long)."""
    if abs(semitones) < 1e-9:
        return audio
    ratio = 2.0 ** (semitones / 12.0)
    n = audio.shape[1]
    m = max(1, int(n / ratio))
    pos = np.arange(m, dtype=np.float64) * ratio
    return np.stack([np.interp(pos, np.arange(n), audio[c]) for c in range(audio.shape[0])]).astype(np.float32)


def render_sample_channel(project, ch, events, seconds: float) -> np.ndarray:
    """A sample channel: every note plays the whole sample (one-shot) at its time.

    Pitch = note - root (semitones), loudness = velocity / 127.
    """
    sr = project.sample_rate
    n = round(seconds * sr)
    out = np.zeros((2, n), np.float32)
    base = load(ch.sample, sr)
    cache: dict[int, np.ndarray] = {}
    for e in events:
        if e.pitch not in cache:
            cache[e.pitch] = pitched(base, e.pitch - ch.root)
        s = cache[e.pitch]
        at = round(project.beats_to_sec(e.start) * sr)
        if at >= n:
            continue
        k = min(s.shape[1], n - at)
        out[:, at:at + k] += s[:, :k] * (e.velocity / 127.0)
    return out


def add_audio_clips(project, buf: np.ndarray, beat_offset: float = 0.0) -> None:
    """Mix the playlist's audio clips into buf (2, n) in place. beat_offset: song beat at buf[0]."""
    sr = project.sample_rate
    n = buf.shape[1]
    for c in project.audio_clips:
        audio = load(c.path, sr)
        at = round(project.beats_to_sec(c.start - beat_offset) * sr)
        src0 = max(0, -at)
        dst0 = max(0, at)
        k = min(audio.shape[1] - src0, n - dst0)
        if k > 0:
            buf[:, dst0:dst0 + k] += audio[:, src0:src0 + k] * c.gain
