"""Quantize notes to a grid (FL-style: grid, strength, swing, note ends).

Pure functions on Note lists; times are in beats (1 beat = quarter note).
"""
from __future__ import annotations

import re
from dataclasses import replace

from .model import Note, ProjectError

_NAMED = {"bar": 4.0, "beat": 1.0, "half": 2.0}


def parse_grid(text: str) -> float:
    """'1/16' -> 0.25 beat, '1/8t' -> 1/3 beat (triplet), '1/4.' -> 1.5 beat (dotted), 'beat', 'bar', '0.5'."""
    t = str(text).strip().lower()
    if t in _NAMED:
        return _NAMED[t]
    m = re.fullmatch(r"1/(\d+)([t.]?)", t)
    if m:
        den = int(m.group(1))
        if den not in (1, 2, 4, 8, 16, 32, 64):
            raise ProjectError(f"grid 1/{den}: use 1/1 .. 1/64")
        g = 4.0 / den
        if m.group(2) == "t":
            g *= 2.0 / 3.0
        elif m.group(2) == ".":
            g *= 1.5
        return g
    try:
        g = float(t)
    except ValueError:
        raise ProjectError(f"bad grid {text!r} (try 1/16, 1/8t, beat, bar or 0.25)") from None
    if not 1 / 64 <= g <= 16:
        raise ProjectError(f"grid {g} beats is out of range")
    return g


def quantize(notes: list[Note], grid: float, strength: float = 1.0, swing: float = 0.0,
             ends: bool = False) -> list[Note]:
    """Move note starts toward the grid.

    strength 0..1: 1 = snap fully, 0.5 = halfway (keeps some human feel).
    swing 0..1: push every 2nd grid step later by up to half a step.
    ends: also snap note ends (length >= one grid step).
    """
    if grid <= 0:
        raise ProjectError("grid must be > 0")
    if not 0.0 <= strength <= 1.0:
        raise ProjectError(f"strength must be 0-1, got {strength}")
    if not 0.0 <= swing <= 1.0:
        raise ProjectError(f"swing must be 0-1, got {swing}")
    out = []
    for n in notes:
        step = round(n.start / grid)
        target = step * grid + (swing * grid * 0.5 if step % 2 else 0.0)
        start = max(0.0, n.start + (target - n.start) * strength)
        length = n.length
        if ends:
            end = n.start + n.length
            end_target = max(round(end / grid), step + 1) * grid
            length = max(grid * 0.25, (end + (end_target - end) * strength) - start)
        out.append(replace(n, start=round(start, 6), length=round(max(length, 1e-3), 6)))
    return sorted(out, key=lambda x: (x.start, x.pitch))


def pattern_length_for(notes: list[Note], minimum: float = 4.0) -> float:
    """Whole bars (4 beats) that hold all notes."""
    end = max((n.start + n.length for n in notes), default=0.0)
    return max(minimum, -(-end // 4) * 4)
