"""Turn playlist + patterns into a flat list of note events per channel."""
from __future__ import annotations

from dataclasses import dataclass

from .model import Project


@dataclass(frozen=True, order=True)
class Event:
    start: float     # beats from song start
    pitch: int
    length: float    # beats
    velocity: int


def song_events(project: Project) -> dict[str, list[Event]]:
    """Notes that start past a pattern's length are cut (like FL pattern length)."""
    out: dict[str, list[Event]] = {c.name: [] for c in project.channels}
    for clip in project.playlist:
        pat = project.pattern(clip.pattern)
        for ch, notes in pat.notes.items():
            for n in notes:
                if n.start >= pat.length:
                    continue
                out[ch].append(Event(clip.start + n.start, n.pitch, n.length, n.velocity))
    for evs in out.values():
        evs.sort()
    return out


def pattern_events(project: Project, name: str) -> dict[str, list[Event]]:
    """One pattern's notes per channel (beats from the pattern start), for pattern-mode playback."""
    pat = project.pattern(name)
    out: dict[str, list[Event]] = {c.name: [] for c in project.channels}
    for ch, notes in pat.notes.items():
        for n in notes:
            if n.start < pat.length:
                out[ch].append(Event(n.start, n.pitch, n.length, n.velocity))
    for evs in out.values():
        evs.sort()
    return out
