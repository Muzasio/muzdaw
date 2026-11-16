"""Project model: one JSON file holds the whole song.

Times are in beats (quarter notes). Tempo is constant per project for now.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

SCHEMA_VERSION = 1


class ProjectError(ValueError):
    """Bad input or a broken project file."""


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ProjectError(msg)


@dataclass(slots=True)
class Note:
    pitch: int          # MIDI 0-127
    start: float        # beats from pattern start
    length: float       # beats
    velocity: int = 100  # 1-127

    def validate(self) -> None:
        _check(isinstance(self.pitch, int) and 0 <= self.pitch <= 127, f"pitch must be 0-127, got {self.pitch}")
        _check(self.start >= 0, f"start must be >= 0, got {self.start}")
        _check(self.length > 0, f"length must be > 0, got {self.length}")
        _check(isinstance(self.velocity, int) and 1 <= self.velocity <= 127,
               f"velocity must be 1-127, got {self.velocity}")


def _check_fx(owner: str, fx: list) -> None:
    from .effects import check_effect
    _check(isinstance(fx, list), f"{owner}: fx must be a list")
    for i, e in enumerate(fx):
        try:
            check_effect(e)
        except ValueError as err:
            raise ProjectError(f"{owner}: fx {i}: {err}") from err


@dataclass
class Channel:
    name: str
    instrument: str = "saw"   # built-in Faust preset, "plugin", "gm" or "sample"
    plugin: str | None = None  # path to a VST3/VST2 file when instrument == "plugin"
    patch: str | None = None   # plugin patch (Surge XT: "Pads/Primes" or a .fxp path)
    program: int = 0           # GM program 0-127 when instrument == "gm"
    bank: int = 0              # GM bank (128 = drum kits)
    params: dict[str, float] = field(default_factory=dict)
    volume: float = 0.8       # 0-2 (linear gain)
    pan: float = 0.0          # -1 left .. 1 right
    mute: bool = False
    fx: list[dict] = field(default_factory=list)   # effect chain, in order
    output: str = "master"    # bus name or "master"
    sends: dict[str, float] = field(default_factory=dict)  # bus -> level 0-2 (post-fader)
    sample: str | None = None  # audio file when instrument == "sample" (one-shot, read in place)
    root: int = 60             # sample: the note that plays it at its own pitch

    def validate(self) -> None:
        _check(bool(self.name), "channel name is empty")
        _check(0.0 <= self.volume <= 2.0, f"volume must be 0-2, got {self.volume}")
        _check(-1.0 <= self.pan <= 1.0, f"pan must be -1..1, got {self.pan}")
        _check_fx(f"channel {self.name}", self.fx)
        for b, lvl in self.sends.items():
            _check(0.0 <= lvl <= 2.0, f"channel {self.name}: send to {b} must be 0-2, got {lvl}")
        if self.instrument == "plugin":
            _check(bool(self.plugin), f"channel {self.name}: plugin path missing")
        elif self.instrument == "sample":
            _check(bool(self.sample), f"channel {self.name}: sample file missing")
            from .samples import AUDIO_EXT
            _check(Path(self.sample).suffix.lower() in AUDIO_EXT,
                   f"channel {self.name}: not an audio file: {Path(self.sample).name}")
            _check(isinstance(self.root, int) and 0 <= self.root <= 127, f"channel {self.name}: root must be 0-127")
        elif self.instrument == "gm":
            _check(0 <= self.program <= 127, f"channel {self.name}: GM program must be 0-127")
            _check(self.bank in (0, 128) or 0 <= self.bank <= 127, f"channel {self.name}: bad GM bank")
        else:
            from .instruments import PRESETS, check_params
            _check(self.instrument in PRESETS,
                   f"channel {self.name}: unknown instrument {self.instrument!r} "
                   f"(choose: {', '.join(PRESETS)}, plugin, gm)")
            try:
                check_params(self.params)
            except ValueError as e:
                raise ProjectError(f"channel {self.name}: {e}") from e


@dataclass
class Bus:
    """Mixer insert track (FL style): channels route or send into it."""
    name: str
    volume: float = 1.0
    pan: float = 0.0
    mute: bool = False
    fx: list[dict] = field(default_factory=list)
    output: str = "master"

    def validate(self) -> None:
        _check(bool(self.name) and self.name != "master", f"bad bus name {self.name!r}")
        _check(0.0 <= self.volume <= 2.0, f"bus {self.name}: volume must be 0-2")
        _check(-1.0 <= self.pan <= 1.0, f"bus {self.name}: pan must be -1..1")
        _check_fx(f"bus {self.name}", self.fx)


@dataclass
class Pattern:
    name: str
    length: float = 4.0  # beats
    notes: dict[str, list[Note]] = field(default_factory=dict)  # channel name -> notes

    def validate(self, channel_names: set[str]) -> None:
        _check(bool(self.name), "pattern name is empty")
        _check(self.length > 0, f"pattern {self.name}: length must be > 0")
        for ch, notes in self.notes.items():
            _check(ch in channel_names, f"pattern {self.name}: unknown channel {ch}")
            for n in notes:
                n.validate()


@dataclass
class Clip:
    pattern: str
    start: float  # beats in song
    track: int = 0

    def validate(self, pattern_names: set[str]) -> None:
        _check(self.pattern in pattern_names, f"clip uses unknown pattern {self.pattern}")
        _check(self.start >= 0, f"clip start must be >= 0, got {self.start}")
        _check(self.track >= 0, f"clip track must be >= 0, got {self.track}")


@dataclass
class AudioClip:
    """An audio file placed on the playlist (music, vocals, SFX). Goes to the master."""
    path: str
    start: float          # beats in song
    track: int = 0
    gain: float = 1.0     # 0-4 (linear)

    def validate(self) -> None:
        from .samples import AUDIO_EXT
        _check(bool(self.path) and Path(self.path).suffix.lower() in AUDIO_EXT,
               f"audio clip: not an audio file: {Path(self.path).name if self.path else '(none)'}")
        _check(self.start >= 0, f"audio clip start must be >= 0, got {self.start}")
        _check(self.track >= 0, f"audio clip track must be >= 0, got {self.track}")
        _check(0.0 <= self.gain <= 4.0, f"audio clip gain must be 0-4, got {self.gain}")

    def length_beats(self, tempo: float) -> float:
        from .samples import duration
        return duration(self.path) * tempo / 60.0


@dataclass
class Project:
    name: str = "untitled"
    tempo: float = 120.0
    sample_rate: int = 44100
    master: float = 0.8
    master_fx: list[dict] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)
    buses: list[Bus] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    playlist: list[Clip] = field(default_factory=list)
    audio_clips: list[AudioClip] = field(default_factory=list)
    version: int = SCHEMA_VERSION

    # ---- lookups -------------------------------------------------------
    def channel(self, name: str) -> Channel:
        for c in self.channels:
            if c.name == name:
                return c
        raise ProjectError(f"no channel named {name}")

    def bus(self, name: str) -> Bus:
        for b in self.buses:
            if b.name == name:
                return b
        raise ProjectError(f"no bus named {name}")

    def bus_order(self) -> list[Bus]:
        """Buses so that every bus comes after all buses that feed it. Raises on loops."""
        by = {b.name: b for b in self.buses}
        order, state = [], {}

        def visit(name: str, path: tuple) -> None:
            if state.get(name) == 2:
                return
            _check(state.get(name) != 1, f"mixer loop: {' -> '.join(path + (name,))}")
            state[name] = 1
            for b in self.buses:  # feeders first
                if b.output == name:
                    visit(b.name, path + (name,))
            state[name] = 2
            order.append(by[name])

        for b in self.buses:
            visit(b.name, ())
        return order

    def pattern(self, name: str) -> Pattern:
        for p in self.patterns:
            if p.name == name:
                return p
        raise ProjectError(f"no pattern named {name}")

    def song_length(self) -> float:
        """Song length in beats (end of the last clip)."""
        lengths = {p.name: p.length for p in self.patterns}
        ends = [c.start + lengths[c.pattern] for c in self.playlist]
        ends += [a.start + a.length_beats(self.tempo) for a in self.audio_clips]
        return max(ends, default=0.0)

    def beats_to_sec(self, beats: float) -> float:
        return beats * 60.0 / self.tempo

    # ---- checks --------------------------------------------------------
    def validate(self) -> None:
        _check(self.version == SCHEMA_VERSION, f"unsupported project version {self.version}")
        _check(20 <= self.tempo <= 999, f"tempo must be 20-999, got {self.tempo}")
        _check(self.sample_rate in (22050, 44100, 48000, 88200, 96000), f"bad sample rate {self.sample_rate}")
        _check(0.0 <= self.master <= 2.0, f"master must be 0-2, got {self.master}")
        names = [c.name for c in self.channels]
        _check(len(names) == len(set(names)), "channel names must be unique")
        for c in self.channels:
            c.validate()
        _check_fx("master", self.master_fx)
        bnames = [b.name for b in self.buses]
        _check(len(bnames) == len(set(bnames)), "bus names must be unique")
        targets = set(bnames) | {"master"}
        for b in self.buses:
            b.validate()
            _check(b.output in targets and b.output != b.name, f"bus {b.name}: bad output {b.output}")
        for c in self.channels:
            _check(c.output in targets, f"channel {c.name}: unknown output {c.output}")
            for t in c.sends:
                _check(t in bnames, f"channel {c.name}: send to unknown bus {t}")
        self.bus_order()
        pnames = [p.name for p in self.patterns]
        _check(len(pnames) == len(set(pnames)), "pattern names must be unique")
        for p in self.patterns:
            p.validate(set(names))
        for c in self.playlist:
            c.validate(set(pnames))
        for a in self.audio_clips:
            a.validate()

    # ---- copy / JSON ---------------------------------------------------
    def clone(self) -> Project:
        """Fast deep copy (same result as copy.deepcopy, ~5x faster for many notes)."""
        import copy
        c = copy.copy(self)
        c.master_fx = copy.deepcopy(self.master_fx)
        c.channels = [copy.deepcopy(ch) for ch in self.channels]
        c.buses = [copy.deepcopy(b) for b in self.buses]
        c.patterns = [Pattern(p.name, p.length, {ch: [Note(n.pitch, n.start, n.length, n.velocity) for n in ns]
                                                 for ch, ns in p.notes.items()}) for p in self.patterns]
        c.playlist = [Clip(x.pattern, x.start, x.track) for x in self.playlist]
        c.audio_clips = [AudioClip(a.path, a.start, a.track, a.gain) for a in self.audio_clips]
        return c

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(replace(self, patterns=[], playlist=[], audio_clips=[])).items()}
        d["patterns"] = [{"name": p.name, "length": p.length,
                          "notes": {ch: [{"pitch": n.pitch, "start": n.start, "length": n.length,
                                          "velocity": n.velocity} for n in ns] for ch, ns in p.notes.items()}}
                         for p in self.patterns]
        d["playlist"] = [{"pattern": x.pattern, "start": x.start, "track": x.track} for x in self.playlist]
        d["audio_clips"] = [{"path": a.path, "start": a.start, "track": a.track, "gain": a.gain}
                            for a in self.audio_clips]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Project:
        try:
            p = cls(
                name=str(d.get("name", "untitled")),
                tempo=float(d.get("tempo", 120.0)),
                sample_rate=int(d.get("sample_rate", 44100)),
                master=float(d.get("master", 0.8)),
                master_fx=list(d.get("master_fx", [])),
                buses=[Bus(**b) for b in d.get("buses", [])],
                version=int(d.get("version", SCHEMA_VERSION)),
                channels=[Channel(**c) for c in d.get("channels", [])],
                patterns=[
                    Pattern(name=pt["name"], length=float(pt.get("length", 4.0)),
                            notes={ch: [Note(**n) for n in ns] for ch, ns in pt.get("notes", {}).items()})
                    for pt in d.get("patterns", [])
                ],
                playlist=[Clip(**c) for c in d.get("playlist", [])],
                audio_clips=[AudioClip(**a) for a in d.get("audio_clips", [])],
            )
        except (TypeError, KeyError, ValueError) as e:
            raise ProjectError(f"broken project data: {e}") from e
        p.validate()
        return p


def load(path: str | Path) -> Project:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProjectError(f"project not found: {path}") from None
    except json.JSONDecodeError as e:
        raise ProjectError(f"not valid JSON: {path}: {e}") from e
    _check(isinstance(data, dict), "project file must hold a JSON object")
    return Project.from_dict(data)


def dumps(project: Project) -> str:
    """The project as the JSON text that save() writes."""
    return json.dumps(project.to_dict(), indent=2, sort_keys=True) + "\n"


def save(project: Project, path: str | Path) -> str:
    """Validate, then write atomically (temp file + rename) so a crash never leaves half a file.

    Returns the written text."""
    project.validate()
    path = Path(path)
    text = dumps(project)
    fd, tmp = tempfile.mkstemp(dir=path.parent or ".", prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return text
