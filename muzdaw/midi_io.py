"""MIDI export / import (one MIDI track per channel)."""
from __future__ import annotations

from pathlib import Path

import mido

from .events import song_events
from .model import Channel, Clip, Note, Pattern, Project, ProjectError

PPQ = 480


def channel_midi(project: Project, events, path: str | Path, channel: int = 0,
                 end_beats: float | None = None, remap_pitch: int | None = None) -> Path:
    """One channel's events as a type-0 MIDI file (tempo included).

    end_beats: put an 'all notes off' at this beat so every file has the same
    length (needed to loop several files in sync). remap_pitch: play every
    note at this pitch (e.g. a drum hit).
    """
    mid = mido.MidiFile(type=0, ticks_per_beat=PPQ)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(project.tempo), time=0))
    msgs = []
    for e in events:
        pitch = e.pitch if remap_pitch is None else remap_pitch
        on = round(e.start * PPQ)
        msgs.append((on, 1, mido.Message("note_on", note=pitch, velocity=e.velocity, channel=channel)))
        msgs.append((max(on + 1, round((e.start + e.length) * PPQ)), 0,
                     mido.Message("note_off", note=pitch, velocity=0, channel=channel)))
    if end_beats is not None:
        msgs.append((round(end_beats * PPQ), 2, mido.Message("control_change", control=123, value=0, channel=channel)))
    last = 0
    for tick, _, m in sorted(msgs, key=lambda x: (x[0], x[1])):
        tr.append(m.copy(time=tick - last))
        last = tick
    mid.save(str(path))
    return Path(path)


def export_midi(project: Project, out: str | Path) -> int:
    """Write the whole song as a type-1 MIDI file. Returns the note count."""
    project.validate()
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(project.tempo), time=0))
    mid.tracks.append(meta)
    count = 0
    for ch_idx, (name, events) in enumerate(song_events(project).items()):
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("track_name", name=name, time=0))
        msgs = []  # (tick, order, msg) ; note_off before note_on at the same tick
        midi_ch = ch_idx % 16
        for e in events:
            on = round(e.start * PPQ)
            off = max(on + 1, round((e.start + e.length) * PPQ))
            msgs.append((on, 1, mido.Message("note_on", note=e.pitch, velocity=e.velocity, channel=midi_ch)))
            msgs.append((off, 0, mido.Message("note_off", note=e.pitch, velocity=0, channel=midi_ch)))
            count += 1
        msgs.sort(key=lambda m: (m[0], m[1]))
        last = 0
        for tick, _, msg in msgs:
            tr.append(msg.copy(time=tick - last))
            last = tick
        mid.tracks.append(tr)
    mid.save(str(out))
    return count


def import_midi(path: str | Path, pattern_name: str = "imported", instrument: str = "saw") -> Project:
    """Read a MIDI file into a new project: one channel per track with notes, one pattern, one clip."""
    try:
        mid = mido.MidiFile(str(path))
    except (OSError, ValueError, EOFError) as e:
        raise ProjectError(f"cannot read MIDI {path}: {e}") from e
    tempo = 120.0
    proj = Project(name=Path(path).stem)
    pat = Pattern(name=pattern_name)
    end = 0.0
    for t_idx, tr in enumerate(mid.tracks):
        name, tick, open_notes, notes = f"track{t_idx}", 0, {}, []
        for msg in tr:
            tick += msg.time
            if msg.type == "set_tempo":
                tempo = round(mido.tempo2bpm(msg.tempo), 3)
            elif msg.type == "track_name":
                name = msg.name or name
            elif msg.type == "note_on" and msg.velocity > 0:
                open_notes.setdefault((msg.channel, msg.note), []).append((tick, msg.velocity))
            elif msg.type in ("note_off", "note_on"):
                stack = open_notes.get((msg.channel, msg.note))
                if stack:
                    on, vel = stack.pop(0)
                    s, ln = on / mid.ticks_per_beat, (tick - on) / mid.ticks_per_beat
                    notes.append(Note(msg.note, s, max(ln, 1 / mid.ticks_per_beat), vel))
                    end = max(end, s + ln)
        if notes:
            base, i = name, 2
            while any(c.name == name for c in proj.channels):
                name, i = f"{base}-{i}", i + 1
            proj.channels.append(Channel(name=name, instrument=instrument))
            pat.notes[name] = sorted(notes, key=lambda n: (n.start, n.pitch))
    proj.tempo = tempo
    pat.length = max(4.0, -(-end // 4) * 4)  # round up to whole bars of 4 beats
    proj.patterns.append(pat)
    proj.playlist.append(Clip(pattern=pattern_name, start=0.0))
    proj.validate()
    return proj
