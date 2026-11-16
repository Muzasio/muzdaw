"""Command line: every edit is one command, saved to the project with undo.

Run: python -m muzdaw <command> ...   (python -m muzdaw -h for the list)
"""
from __future__ import annotations

import argparse
import os
import copy
import re
import sys
from pathlib import Path

from . import history
from .effects import BUILTINS, make_effect, parse_value
from .model import Bus, Channel, Clip, Note, Pattern, Project, ProjectError, load, save
from .presets import PRESETS

_NOTE_RE = re.compile(r"^([A-Ga-g])([#b]?)(-?\d)$")
_SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_pitch(text: str) -> int:
    """'60', 'C4' (=60), 'F#3', 'Bb2'."""
    if text.lstrip("-").isdigit():
        val = int(text)
    else:
        m = _NOTE_RE.match(text)
        if not m:
            raise ProjectError(f"bad note {text!r}: use a number 0-127 or a name like C4, F#3, Bb2")
        val = (int(m.group(3)) + 1) * 12 + _SEMI[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    if not 0 <= val <= 127:
        raise ProjectError(f"note {text!r} is out of range 0-127")
    return val


def parse_params(items: list[str] | None, numeric: bool = True) -> dict:
    """name=value pairs. Instrument params must be numbers; effect params may be text/bool."""
    out = {}
    for it in items or []:
        key, sep, val = it.partition("=")
        if not sep or not key.strip():
            raise ProjectError(f"bad --param {it!r}: use name=value")
        v = parse_value(val)
        if numeric and (isinstance(v, (str, bool))):
            raise ProjectError(f"bad --param {it!r}: value must be a number")
        out[key.strip()] = float(v) if numeric else v
    return out


def _gain(text: str) -> float:
    """Volume as linear gain or dB: '0.8', '-6dB'."""
    v = parse_value(text)
    if isinstance(v, (bool, str)):
        raise argparse.ArgumentTypeError(f"bad volume {text!r}")
    return round(10 ** (v / 20), 6) if text.strip().lower().endswith("db") else float(v)


# ---- edit commands (each returns a label for the undo list) ---------------

def _apply_inst(c: Channel, a) -> None:
    if getattr(a, "inst", None):
        preset = PRESETS[a.inst]
        c.instrument, c.plugin, c.patch = preset["instrument"], preset.get("plugin"), preset.get("patch")
        c.program, c.bank = preset.get("program", 0), preset.get("bank", 0)
    if getattr(a, "plugin", None):
        c.instrument, c.plugin, c.patch = "plugin", a.plugin, None
    if getattr(a, "gm", None) is not None:
        c.instrument, c.plugin, c.patch, c.program, c.bank = "gm", None, None, a.gm, 0
    if getattr(a, "drums", False):
        c.instrument, c.plugin, c.patch, c.bank = "gm", None, None, 128
    if getattr(a, "sample", None):
        from .samples import check_path
        c.instrument, c.plugin, c.patch = "sample", None, None
        c.sample = str(check_path(a.sample).resolve())
    if getattr(a, "root", None) is not None:
        if c.instrument != "sample":
            raise ProjectError("--root is for sample channels (--sample FILE)")
        c.root = parse_pitch(a.root)
    if getattr(a, "patch", None):
        if c.instrument != "plugin":
            raise ProjectError("--patch needs a plugin instrument (e.g. --inst surge-pad or --plugin ...)")
        c.patch = a.patch


def cmd_add_channel(p: Project, a) -> str:
    if any(c.name == a.name for c in p.channels):
        raise ProjectError(f"channel {a.name} already exists")
    c = Channel(name=a.name, params=parse_params(a.param), volume=a.vol, pan=a.pan, output=a.out)
    _apply_inst(c, a)
    p.channels.append(c)
    return f"add channel {a.name}"


def cmd_set_channel(p: Project, a) -> str:
    c = p.channel(a.name)
    _apply_inst(c, a)
    for attr in ("vol", "pan", "mute", "out"):
        val = getattr(a, attr)
        if val is not None:
            setattr(c, {"vol": "volume", "out": "output"}.get(attr, attr), val)
    c.params.update(parse_params(a.param))
    return f"set channel {a.name}"


def cmd_add_audio(p: Project, a) -> str:
    from .model import AudioClip
    from .samples import check_path
    path = check_path(a.file).resolve()
    gain = _gain(a.gain) if a.gain is not None else 1.0
    p.audio_clips.append(AudioClip(str(path), a.start, a.track, gain))
    p.audio_clips.sort(key=lambda c: (c.start, c.track))
    return f"add audio {path.name} at beat {a.start:g}"


def cmd_del_audio(p: Project, a) -> str:
    if not 0 <= a.index < len(p.audio_clips):
        raise ProjectError(f"no audio clip {a.index} (there are {len(p.audio_clips)}; see `info`)")
    c = p.audio_clips.pop(a.index)
    return f"delete audio clip {a.index} ({Path(c.path).name})"


def cmd_del_channel(p: Project, a) -> str:
    c = p.channel(a.name)
    p.channels.remove(c)
    for pat in p.patterns:
        pat.notes.pop(a.name, None)
    return f"delete channel {a.name}"


def cmd_add_pattern(p: Project, a) -> str:
    if any(x.name == a.name for x in p.patterns):
        raise ProjectError(f"pattern {a.name} already exists")
    p.patterns.append(Pattern(name=a.name, length=a.length))
    return f"add pattern {a.name}"


def cmd_add_note(p: Project, a) -> str:
    pat = p.pattern(a.pattern)
    p.channel(a.channel)  # must exist
    n = Note(parse_pitch(a.pitch), a.start, a.length, a.vel)
    n.validate()
    pat.notes.setdefault(a.channel, []).append(n)
    pat.notes[a.channel].sort(key=lambda x: (x.start, x.pitch))
    return f"add note {a.pitch} at {a.start}"


def cmd_del_note(p: Project, a) -> str:
    pat = p.pattern(a.pattern)
    pitch = parse_pitch(a.pitch)
    notes = pat.notes.get(a.channel, [])
    keep = [n for n in notes if not (n.pitch == pitch and abs(n.start - a.start) < 1e-9)]
    if len(keep) == len(notes):
        raise ProjectError(f"no note {a.pitch} at {a.start} in {a.pattern}/{a.channel}")
    pat.notes[a.channel] = keep
    return f"delete note {a.pitch} at {a.start}"


def cmd_place(p: Project, a) -> str:
    p.pattern(a.pattern)
    p.playlist.append(Clip(pattern=a.pattern, start=a.start, track=a.track))
    p.playlist.sort(key=lambda c: (c.start, c.track))
    return f"place {a.pattern} at {a.start}"


def cmd_tempo(p: Project, a) -> str:
    p.tempo = a.bpm
    return f"tempo {a.bpm}"


def cmd_master(p: Project, a) -> str:
    p.master = a.vol
    return f"master {a.vol}"


def _chain(p: Project, target: str) -> list[dict]:
    if target == "master":
        return p.master_fx
    for c in p.channels:
        if c.name == target:
            return c.fx
    for b in p.buses:
        if b.name == target:
            return b.fx
    raise ProjectError(f"no channel or bus named {target} (or use 'master')")


def _fx_index(chain: list, idx: int, target: str) -> int:
    if not 0 <= idx < len(chain):
        raise ProjectError(f"{target} has no fx {idx} (it has {len(chain)}: 0..{len(chain) - 1})")
    return idx


def _check_fx_now(fx: dict) -> None:
    try:
        make_effect(fx)  # loads it once so bad names/params fail now, not at render
    except ValueError as e:
        raise ProjectError(str(e)) from e


def cmd_add_fx(p: Project, a) -> str:
    chain = _chain(p, a.target)
    fx = {"type": a.type, "params": parse_params(a.param, numeric=False), "bypass": False}
    if a.type == "plugin":
        if not a.plugin:
            raise ProjectError("add-fx plugin needs --plugin PATH")
        fx["plugin"] = a.plugin
        if a.name:
            fx["name"] = a.name
    _check_fx_now(fx)
    pos = len(chain) if a.at is None else max(0, min(a.at, len(chain)))
    chain.insert(pos, fx)
    return f"add fx {a.type} to {a.target} slot {pos}"


def cmd_set_fx(p: Project, a) -> str:
    chain = _chain(p, a.target)
    fx = chain[_fx_index(chain, a.index, a.target)]
    fx.setdefault("params", {}).update(parse_params(a.param, numeric=False))
    if a.bypass is not None:
        fx["bypass"] = a.bypass
    _check_fx_now({**fx, "bypass": False})
    return f"set fx {a.target} slot {a.index}"


def cmd_del_fx(p: Project, a) -> str:
    chain = _chain(p, a.target)
    fx = chain.pop(_fx_index(chain, a.index, a.target))
    return f"delete fx {fx['type']} from {a.target} slot {a.index}"


def cmd_move_fx(p: Project, a) -> str:
    chain = _chain(p, a.target)
    fx = chain.pop(_fx_index(chain, a.index, a.target))
    chain.insert(max(0, min(a.to, len(chain))), fx)
    return f"move fx {a.target} slot {a.index} -> {a.to}"


def cmd_add_bus(p: Project, a) -> str:
    if any(b.name == a.name for b in p.buses) or a.name == "master":
        raise ProjectError(f"bus {a.name} already exists")
    p.buses.append(Bus(name=a.name, volume=a.vol, pan=a.pan, output=a.out))
    return f"add bus {a.name}"


def cmd_set_bus(p: Project, a) -> str:
    b = p.bus(a.name)
    for attr in ("vol", "pan", "mute", "out"):
        val = getattr(a, attr)
        if val is not None:
            setattr(b, {"vol": "volume", "out": "output"}.get(attr, attr), val)
    return f"set bus {a.name}"


def cmd_del_bus(p: Project, a) -> str:
    b = p.bus(a.name)
    p.buses.remove(b)
    for c in p.channels:
        if c.output == a.name:
            c.output = "master"
        c.sends.pop(a.name, None)
    for other in p.buses:
        if other.output == a.name:
            other.output = "master"
    return f"delete bus {a.name} (its inputs now go to master)"


def cmd_send(p: Project, a) -> str:
    c = p.channel(a.channel)
    p.bus(a.bus)
    if a.level == 0:
        c.sends.pop(a.bus, None)
        return f"remove send {a.channel} -> {a.bus}"
    c.sends[a.bus] = a.level
    return f"send {a.channel} -> {a.bus} {a.level}"


def cmd_quantize(p: Project, a) -> str:
    from .quantize import parse_grid, quantize
    pat = p.pattern(a.pattern)
    chans = [a.channel] if a.channel else list(pat.notes)
    grid = parse_grid(a.grid)
    count = 0
    for ch in chans:
        if ch not in pat.notes:
            raise ProjectError(f"pattern {pat.name} has no notes for channel {ch}")
        pat.notes[ch] = quantize(pat.notes[ch], grid, a.strength, a.swing, a.ends)
        count += len(pat.notes[ch])
    return f"quantize {pat.name} ({', '.join(chans) or 'no channels'}) to {a.grid}: {count} notes"


def _detect_args(a) -> dict:
    return {"onset": a.onset, "frame": a.frame, "min_ms": a.min_ms, "min_hz": a.min_hz, "max_hz": a.max_hz}


def cmd_rec2midi(p: Project, a) -> str:
    from .quantize import parse_grid
    from .rec2midi import rec2midi
    print("finding notes (basic-pitch)...", flush=True)
    n = rec2midi(p, a.wav, a.channel, a.pattern, parse_grid(a.quantize) if a.quantize else None,
                 a.strength, a.replace, a.offset, **_detect_args(a))
    if n == 0:
        raise ProjectError("no notes found (try a lower --onset, e.g. 0.3)")
    return f"rec2midi {Path(a.wav).name} -> {a.pattern}/{a.channel}: {n} notes"


def _count_in(beats: int, tempo: float) -> float:
    """Print a count-in; returns the perf_counter time of the first recorded beat."""
    import time
    step = 60.0 / tempo
    t = time.perf_counter()
    for i in range(beats, 0, -1):
        print(f"\r  {i}...", end="", flush=True)
        t += step
        time.sleep(max(0.0, t - time.perf_counter()))
    print("\r  REC  ", flush=True)
    return t


def cmd_record_midi(p: Project, a) -> str:
    import time

    from .quantize import parse_grid, quantize
    from .rec2midi import put_notes
    from .record import MidiRecorder, pick_input
    p.channel(a.channel)
    grid = parse_grid(a.quantize) if a.quantize else None
    port = pick_input(a.port)
    beats = a.bars * 4 if a.bars else None
    rec = MidiRecorder(port, p.tempo)
    rec.open()
    sess = None
    try:
        if a.play:
            from .live import LiveSession
            print("starting Carla...", flush=True)
            sess = LiveSession(p, audible=not (a.silent or os.environ.get("MUZDAW_SILENT")), metronome=a.metronome, keyboard=a.channel, loop=True)
            sess.start()
        print(f"recording from {port} into {a.pattern}/{a.channel} "
              f"({f'{a.bars} bars' if beats else 'Ctrl+C stops'})", flush=True)
        t0 = _count_in(a.count_in, p.tempo)
        if sess:
            sess.play(a.start)
        rec.start(t0)
        try:
            while beats is None or (time.perf_counter() - t0) * p.tempo / 60 < beats:
                b = int((time.perf_counter() - t0) * p.tempo / 60)
                print(f"\r  bar {b // 4 + 1:>3} beat {b % 4 + 1}  notes {len(rec.notes()):>3}", end="", flush=True)
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        print()
        notes = rec.stop()
    finally:
        rec.stop()
        if sess:
            sess.close()
    if beats:
        notes = [n for n in notes if n.start < beats]
    if not notes:
        raise ProjectError("no notes recorded (project not changed)")
    if grid:
        notes = quantize(notes, grid, a.strength)
    put_notes(p, a.pattern, a.channel, notes, a.replace)
    return f"record-midi -> {a.pattern}/{a.channel}: {len(notes)} notes"


EDITS = {
    "add-channel": cmd_add_channel, "set-channel": cmd_set_channel, "del-channel": cmd_del_channel,
    "add-pattern": cmd_add_pattern, "add-note": cmd_add_note, "del-note": cmd_del_note,
    "place": cmd_place, "tempo": cmd_tempo, "master": cmd_master,
    "add-audio": cmd_add_audio, "del-audio": cmd_del_audio,
    "add-fx": cmd_add_fx, "set-fx": cmd_set_fx, "del-fx": cmd_del_fx, "move-fx": cmd_move_fx,
    "add-bus": cmd_add_bus, "set-bus": cmd_set_bus, "del-bus": cmd_del_bus, "send": cmd_send,
    "quantize": cmd_quantize, "rec2midi": cmd_rec2midi, "record-midi": cmd_record_midi,
}


# ---- other commands -------------------------------------------------------

def _fx_text(chain: list[dict]) -> str:
    out = []
    for i, fx in enumerate(chain):
        name = fx.get("name") or (Path(fx["plugin"]).stem if fx["type"] == "plugin" else fx["type"])
        params = " ".join(f"{k}={v}" for k, v in fx.get("params", {}).items())
        out.append(f"[{i}] {name}{' (bypass)' if fx.get('bypass') else ''}{' ' + params if params else ''}")
    return "  ".join(out)


def show_info(p: Project) -> None:
    print(f"{p.name}  tempo {p.tempo}  length {p.song_length()} beats "
          f"({p.beats_to_sec(p.song_length()):.2f} s)  master {p.master}")
    if p.master_fx:
        print(f"  master fx: {_fx_text(p.master_fx)}")
    print("channels:")
    for c in p.channels:
        src = {"plugin": f"{Path(c.plugin or '').stem}{' / ' + c.patch if c.patch else ''}",
               "gm": f"GM {'drums' if c.bank == 128 else 'prog'} {c.program}",
               "sample": f"sample {Path(c.sample or '').name} root {c.root}"}.get(c.instrument, c.instrument)
        route = f" -> {c.output}" if c.output != "master" else ""
        sends = "".join(f" send {b} {v}" for b, v in c.sends.items())
        print(f"  {c.name:<12} {src:<24} vol {c.volume:<5} pan {c.pan:<5}{route}{sends}"
              f"{' MUTED' if c.mute else ''} {c.params or ''}".rstrip())
        if c.fx:
            print(f"      fx: {_fx_text(c.fx)}")
    if p.buses:
        print("buses:")
        for b in p.buses:
            print(f"  {b.name:<12} vol {b.volume:<5} pan {b.pan:<5} -> {b.output}{' MUTED' if b.mute else ''}")
            if b.fx:
                print(f"      fx: {_fx_text(b.fx)}")
    print("patterns:")
    for pt in p.patterns:
        counts = ", ".join(f"{ch}:{len(ns)}" for ch, ns in pt.notes.items()) or "empty"
        print(f"  {pt.name:<12} {pt.length} beats  notes {counts}")
    print("playlist:")
    for c in p.playlist:
        print(f"  {c.start:>6} track {c.track}  {c.pattern}")
    if p.audio_clips:
        print("audio clips:")
        for i, a in enumerate(p.audio_clips):
            print(f"  {i}: {a.start:>6} track {a.track}  {Path(a.path).name}  gain {a.gain}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="muzdaw", description="Fast DAW you drive from the command line.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    presets = sorted(PRESETS)

    s = sub.add_parser("new", help="create a project file")
    s.add_argument("project"); s.add_argument("--tempo", type=float, default=120.0)
    s.add_argument("--force", action="store_true", help="overwrite if it exists")

    sub.add_parser("info", help="show the project").add_argument("project")
    sub.add_parser("instruments", help="list instrument presets")
    s = sub.add_parser("patches", help="list Surge XT patches")
    s.add_argument("filter", nargs="?", default="")
    sub.add_parser("effects", help="list built-in effects")
    s = sub.add_parser("fx-params", help="list an effect's params and current values")
    s.add_argument("type", help="built-in effect name or 'plugin'")
    s.add_argument("--plugin"); s.add_argument("--name", help="plugin inside a bundle (e.g. LSP)")
    s = sub.add_parser("plugin-names", help="list plugins inside a VST3 bundle")
    s.add_argument("plugin")

    def inst_args(s, default_inst):
        s.add_argument("--inst", default=default_inst, choices=presets, metavar="PRESET",
                       help="instrument preset (see `instruments`)")
        s.add_argument("--plugin", help="path to a VST3/VST2 instrument")
        s.add_argument("--patch", help="plugin patch, e.g. Surge 'Pads/Primes' (see `patches`)")
        s.add_argument("--gm", type=int, metavar="PROGRAM", help="General MIDI program 0-127")
        s.add_argument("--drums", action="store_true", help="General MIDI drum kit")
        s.add_argument("--param", action="append", metavar="NAME=VALUE")
        s.add_argument("--sample", metavar="FILE", help="audio file: a sample channel (one-shot per note)")
        s.add_argument("--root", metavar="NOTE", help="sample channels: note that plays the file at its own pitch (C4)")

    s = sub.add_parser("add-channel", help="add an instrument channel")
    s.add_argument("project"); s.add_argument("name")
    inst_args(s, "saw")
    s.add_argument("--vol", type=_gain, default=0.8); s.add_argument("--pan", type=float, default=0.0)
    s.add_argument("--out", default="master", help="mixer bus to route into")

    def mix_args(s):
        s.add_argument("--vol", type=_gain); s.add_argument("--pan", type=float); s.add_argument("--out")
        g = s.add_mutually_exclusive_group()
        g.add_argument("--mute", dest="mute", action="store_true", default=None)
        g.add_argument("--unmute", dest="mute", action="store_false")

    s = sub.add_parser("set-channel", help="change a channel")
    s.add_argument("project"); s.add_argument("name")
    inst_args(s, None)
    mix_args(s)

    s = sub.add_parser("del-channel", help="delete a channel and its notes")
    s.add_argument("project"); s.add_argument("name")

    s = sub.add_parser("add-pattern", help="add an empty pattern")
    s.add_argument("project"); s.add_argument("name"); s.add_argument("--length", type=float, default=4.0)

    for cmd in ("add-note", "del-note"):
        s = sub.add_parser(cmd, help=f"{cmd.split('-')[0]} a note in a pattern (times in beats)")
        s.add_argument("project"); s.add_argument("pattern"); s.add_argument("channel")
        s.add_argument("pitch", help="60 or C4"); s.add_argument("start", type=float)
        if cmd == "add-note":
            s.add_argument("length", type=float); s.add_argument("--vel", type=int, default=100)

    s = sub.add_parser("add-audio", help="put an audio file (music, vocals, SFX) on the playlist")
    s.add_argument("project"); s.add_argument("file"); s.add_argument("start", type=float, help="beat")
    s.add_argument("--track", type=int, default=0); s.add_argument("--gain", help="0.5 or -6dB")
    s = sub.add_parser("del-audio", help="remove an audio clip (number from `info`)")
    s.add_argument("project"); s.add_argument("index", type=int)

    s = sub.add_parser("place", help="put a pattern on the playlist")
    s.add_argument("project"); s.add_argument("pattern"); s.add_argument("start", type=float)
    s.add_argument("--track", type=int, default=0)

    s = sub.add_parser("tempo", help="set tempo (BPM)")
    s.add_argument("project"); s.add_argument("bpm", type=float)
    s = sub.add_parser("master", help="set master volume (0.8 or -2dB)")
    s.add_argument("project"); s.add_argument("vol", type=_gain)

    s = sub.add_parser("add-fx", help="add an effect to a channel, bus or 'master'")
    s.add_argument("project"); s.add_argument("target")
    s.add_argument("type", choices=sorted(BUILTINS) + ["plugin"])
    s.add_argument("--plugin", help="VST3/VST2 effect path (type plugin)")
    s.add_argument("--name", help="plugin inside a bundle, e.g. 'Compressor Stereo'")
    s.add_argument("--param", action="append", metavar="NAME=VALUE", help="e.g. threshold_db=-18dB")
    s.add_argument("--at", type=int, help="slot to insert at (default: end)")

    s = sub.add_parser("set-fx", help="change an effect's params / bypass")
    s.add_argument("project"); s.add_argument("target"); s.add_argument("index", type=int)
    s.add_argument("--param", action="append", metavar="NAME=VALUE")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--bypass", dest="bypass", action="store_true", default=None)
    g.add_argument("--on", dest="bypass", action="store_false")

    s = sub.add_parser("del-fx", help="remove an effect")
    s.add_argument("project"); s.add_argument("target"); s.add_argument("index", type=int)
    s = sub.add_parser("move-fx", help="move an effect to another slot")
    s.add_argument("project"); s.add_argument("target"); s.add_argument("index", type=int)
    s.add_argument("to", type=int)

    s = sub.add_parser("add-bus", help="add a mixer bus")
    s.add_argument("project"); s.add_argument("name")
    s.add_argument("--vol", type=_gain, default=1.0); s.add_argument("--pan", type=float, default=0.0)
    s.add_argument("--out", default="master")
    s = sub.add_parser("set-bus", help="change a mixer bus")
    s.add_argument("project"); s.add_argument("name")
    mix_args(s)
    s = sub.add_parser("del-bus", help="delete a bus (its inputs go to master)")
    s.add_argument("project"); s.add_argument("name")
    s = sub.add_parser("send", help="post-fader send from a channel to a bus (level 0 removes)")
    s.add_argument("project"); s.add_argument("channel"); s.add_argument("bus")
    s.add_argument("level", type=_gain)

    for cmd in ("undo", "redo"):
        sub.add_parser(cmd, help=f"{cmd} the last edit").add_argument("project")

    s = sub.add_parser("play", help="play live through Carla (Ctrl+C stops)")
    s.add_argument("project")
    s.add_argument("--from", dest="start", type=float, default=0.0, help="start beat")
    s.add_argument("--loop", action="store_true", help="loop the whole song")
    s.add_argument("--metronome", action="store_true")
    s.add_argument("--keyboard", metavar="CHANNEL", help="MIDI keyboard plays this channel")
    s.add_argument("--seconds", type=float, help="stop after N seconds")
    s.add_argument("--silent", action="store_true", help="don't connect to the speakers (test mode)")

    s = sub.add_parser("render", help="render to WAV")
    s.add_argument("project"); s.add_argument("out")

    s = sub.add_parser("export-midi", help="write a MIDI file")
    s.add_argument("project"); s.add_argument("out")

    s = sub.add_parser("import-midi", help="make a new project from a MIDI file")
    s.add_argument("midi"); s.add_argument("project"); s.add_argument("--force", action="store_true")
    s.add_argument("--inst", default="saw", choices=presets, metavar="PRESET")

    def q_args(s, opt: bool):
        if opt:
            s.add_argument("--quantize", metavar="GRID", help="quantize after: 1/16, 1/8t, beat, bar")
        s.add_argument("--strength", type=float, default=1.0, help="0-1 (1 = snap fully)")

    s = sub.add_parser("quantize", help="snap notes in a pattern to a grid")
    s.add_argument("project"); s.add_argument("pattern")
    s.add_argument("--channel", help="only this channel (default: all)")
    s.add_argument("--grid", default="1/16", help="1/16 (default), 1/8, 1/8t, 1/4., beat, bar, 0.25")
    q_args(s, False)
    s.add_argument("--swing", type=float, default=0.0, help="0-1: push every 2nd step later")
    s.add_argument("--ends", action="store_true", help="also snap note ends")

    s = sub.add_parser("rec2midi", help="turn a recording (WAV) into notes in a pattern")
    s.add_argument("project"); s.add_argument("wav"); s.add_argument("channel"); s.add_argument("pattern")
    q_args(s, True)
    s.add_argument("--replace", action="store_true", help="remove the channel's old notes in the pattern")
    s.add_argument("--offset", type=float, default=0.0, help="seconds of audio before beat 0 (e.g. count-in)")
    s.add_argument("--onset", type=float, default=0.5, help="note start sensitivity 0.05-0.95 (lower = more notes)")
    s.add_argument("--frame", type=float, default=0.3, help="note hold sensitivity 0.05-0.95")
    s.add_argument("--min-ms", type=float, default=58.0, help="shortest note in ms")
    s.add_argument("--min-hz", type=float, default=0.0, help="ignore notes below this (0 = off)")
    s.add_argument("--max-hz", type=float, default=0.0, help="ignore notes above this (0 = off)")

    s = sub.add_parser("record-midi", help="record a MIDI keyboard into a pattern")
    s.add_argument("project"); s.add_argument("channel"); s.add_argument("pattern")
    s.add_argument("--port", help="MIDI input name (part is enough; see `midi-inputs`)")
    s.add_argument("--bars", type=int, help="stop after N bars (default: Ctrl+C stops)")
    s.add_argument("--count-in", type=int, default=4, help="beats to count before recording (0 = none)")
    s.add_argument("--play", action="store_true", help="play the song (and your keys) through Carla while recording")
    s.add_argument("--from", dest="start", type=float, default=0.0, help="song beat to play from (with --play)")
    s.add_argument("--metronome", action="store_true", help="click while recording (with --play)")
    s.add_argument("--silent", action="store_true", help="with --play: don't connect to speakers")
    s.add_argument("--replace", action="store_true", help="remove the channel's old notes in the pattern")
    q_args(s, True)

    s = sub.add_parser("record-audio", help="record from an audio input into a WAV")
    s.add_argument("out"); s.add_argument("--seconds", type=float, required=True)
    s.add_argument("--device", help="input number or name (see `audio-inputs`)")
    s.add_argument("--channels", type=int, default=1, choices=(1, 2))
    s.add_argument("--rate", type=int, default=44100)
    s.add_argument("--count-in", type=int, default=0, help="beats to count first")
    s.add_argument("--tempo", type=float, default=120.0, help="for the count-in")
    s.add_argument("--force", action="store_true", help="overwrite if it exists")

    sub.add_parser("midi-inputs", help="list MIDI keyboards")
    sub.add_parser("audio-inputs", help="list audio inputs (mics, line in)")
    return ap


def _refuse_overwrite(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise ProjectError(f"{path} exists (use --force to overwrite)")


def _list_info(a) -> None:
    if a.cmd == "instruments":
        from .presets import available, kind
        for name in sorted(PRESETS, key=lambda n: (kind(n), n)):
            print(f"{name:<15} {kind(name):<13}{'' if available(name) else ' (not installed)'}")
        print("\nAlso: --plugin PATH (any VST3/VST2), --gm 0-127, --drums, --patch (Surge XT)")
    elif a.cmd == "patches":
        from .plugin_state import list_surge_patches
        names = list_surge_patches(a.filter)
        print("\n".join(names) if names else "no Surge XT patches found")
    elif a.cmd == "effects":
        print("\n".join(sorted(BUILTINS)) + "\nplugin (--plugin PATH [--name NAME])")
    elif a.cmd == "fx-params":
        from .effects import describe
        fx = {"type": a.type, "plugin": a.plugin, "name": a.name}
        try:
            print("\n".join(describe(fx)))
        except ValueError as e:
            raise ProjectError(str(e)) from e
    elif a.cmd == "plugin-names":
        from .effects import plugin_names
        print("\n".join(plugin_names(a.plugin)) or "(single plugin, no --name needed)")


def _play(a) -> int:
    from .live import LiveSession
    p = load(a.project)
    sess = LiveSession(p, audible=not (a.silent or os.environ.get("MUZDAW_SILENT")), metronome=a.metronome, keyboard=a.keyboard, loop=a.loop)
    print("starting Carla...", flush=True)
    sess.start()
    try:
        for w in sess.warnings:
            print(f"note: {w}")
        mode = "silent (not connected to speakers)" if a.silent else "playing"
        print(f"{mode}: {p.song_length()} beats at {p.tempo} BPM{' (loop)' if a.loop else ''}. Ctrl+C stops.")
        last = [-1]

        def tick(s):
            beat = int(s.position_beats())
            if beat != last[0]:
                last[0] = beat
                print(f"\rbeat {beat + 1:>4}  bar {beat // 4 + 1:>3}", end="", flush=True)

        sess.play(a.start)
        try:
            peaks = sess.run(seconds=a.seconds, on_tick=tick)
        except KeyboardInterrupt:
            peaks = None
        print()
        if peaks:
            print("peaks: " + "  ".join(f"{k} {v:.2f}" for k, v in peaks.items()))
    finally:
        sess.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if a.cmd in ("instruments", "patches", "effects", "fx-params", "plugin-names"):
            _list_info(a)
        elif a.cmd == "new":
            path = Path(a.project)
            _refuse_overwrite(path, a.force)
            save(Project(name=path.name.split(".")[0], tempo=a.tempo), path)
            print(f"created {path}")
        elif a.cmd == "info":
            show_info(load(a.project))
        elif a.cmd in EDITS:
            before = load(a.project)
            after = copy.deepcopy(before)
            label = EDITS[a.cmd](after, a)
            after.validate()
            history.commit(a.project, before, after, label)
            print(f"ok: {label}")
        elif a.cmd in ("undo", "redo"):
            label = getattr(history, a.cmd)(a.project)
            print(f"{a.cmd}: {label}")
        elif a.cmd == "play":
            return _play(a)
        elif a.cmd in ("midi-inputs", "audio-inputs"):
            from .record import audio_inputs, midi_inputs
            names = midi_inputs() if a.cmd == "midi-inputs" else audio_inputs()
            print("\n".join(names) if names else "none found")
        elif a.cmd == "record-audio":
            from .record import record_audio
            out = Path(a.out)
            _refuse_overwrite(out, a.force)
            if a.count_in:
                _count_in(a.count_in, a.tempo)
            print(f"recording {a.seconds} s...", flush=True)
            path, peak = record_audio(out, a.seconds, a.rate, a.channels, a.device)
            warn = "  WARNING: silent (check the input)" if peak < 0.001 else (
                "  WARNING: clipping (turn the input down)" if peak >= 0.999 else "")
            print(f"wrote {path}  peak {peak:.3f}{warn}")
        elif a.cmd == "render":
            from .render import render
            r = render(load(a.project), a.out)
            warn = "  WARNING: clipping" if r.peak > 1.0 else ""
            print(f"wrote {r.path}  {r.seconds:.2f} s  peak {r.peak:.3f}  rms {r.rms:.3f}{warn}")
        elif a.cmd == "export-midi":
            from .midi_io import export_midi
            n = export_midi(load(a.project), a.out)
            print(f"wrote {a.out}  {n} notes")
        elif a.cmd == "import-midi":
            from .midi_io import import_midi
            path = Path(a.project)
            _refuse_overwrite(path, a.force)
            p = import_midi(a.midi, instrument="saw")
            for c in p.channels:
                _apply_inst(c, a)
            save(p, path)
            print(f"created {path} from {a.midi}: {len(p.channels)} channels")
    except ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
