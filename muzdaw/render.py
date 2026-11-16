"""Offline render: project -> stereo WAV.

Pipeline (FL style):
  each channel: instrument (DawDreamer / FluidSynth) -> fx chain (pedalboard)
                -> volume + pan -> its output bus (+ post-fader sends)
  each bus (feeders first): fx -> volume + pan -> its output
  master: fx -> master volume
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .effects import make_effect
from .events import Event, song_events
from .instruments import faust_code
from .midi_io import channel_midi
from .model import Channel, Project, ProjectError

BLOCK = 512
TAIL_SEC = 2.0   # room for release / reverb after the last note
PRE_SEC = 0.25   # pre-roll: plugins (e.g. Surge) load patches in the first block and drop notes there


def find_soundfont() -> Path:
    """General MIDI soundfont: $MUZDAW_SOUNDFONT, else the usual places (Arch/Fedora, Debian/Ubuntu)."""
    import os
    env = os.environ.get("MUZDAW_SOUNDFONT")
    candidates = [Path(env).expanduser()] if env else []
    candidates += [Path("/usr/share/soundfonts/FluidR3_GM.sf2"), Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
                   Path("/usr/share/soundfonts/default.sf2"), Path("/usr/share/sounds/sf2/default-GM.sf2")]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


SOUNDFONT = find_soundfont()


@dataclass
class RenderResult:
    path: Path
    seconds: float
    peak: float
    rms: float


def pan_gains(pan: float) -> tuple[float, float]:
    """Constant-power pan, unity at center."""
    a = (pan + 1) * math.pi / 4
    return math.sqrt(2) * math.cos(a), math.sqrt(2) * math.sin(a)


def _fit(audio: np.ndarray, n: int) -> np.ndarray:
    """Force shape (2, n): mono -> stereo, pad or cut."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[None, :]
    if audio.shape[0] == 1:
        audio = np.repeat(audio, 2, axis=0)
    audio = audio[:2, :n]
    if audio.shape[1] < n:
        audio = np.pad(audio, ((0, 0), (0, n - audio.shape[1])))
    return audio


# ---- instruments -------------------------------------------------------------

def _load_patch(proc, ch: Channel, tmp: Path) -> None:
    from .plugin_state import find_surge_patch, surge_fxp_to_state
    if "surge" not in Path(ch.plugin).name.lower():
        raise ProjectError(f"channel {ch.name}: patches are only supported for Surge XT so far")
    try:
        state = surge_fxp_to_state(find_surge_patch(ch.patch).read_bytes())
    except ValueError as e:
        raise ProjectError(f"channel {ch.name}: {e}") from e
    f = tmp / f"{ch.name}.state"
    f.write_bytes(state)
    proc.load_state(str(f))


def _render_daw(project: Project, ch: Channel, events: list[Event], seconds: float, tmp: Path) -> np.ndarray:
    import dawdreamer as daw
    sr = project.sample_rate
    engine = daw.RenderEngine(sr, BLOCK)
    engine.set_bpm(project.tempo)
    if ch.instrument == "plugin":
        path = Path(ch.plugin).expanduser()
        if not path.exists():
            raise ProjectError(f"channel {ch.name}: plugin not found: {path}")
        try:
            proc = engine.make_plugin_processor("inst", str(path))
        except RuntimeError as e:
            raise ProjectError(f"channel {ch.name}: cannot load {path} (VST3/VST2 only): {e}") from e
        if ch.patch:
            _load_patch(proc, ch, tmp)
        if ch.params:
            index = {p["name"].lower(): p["index"] for p in proc.get_parameters_description()}
            for k, v in ch.params.items():
                if k.lower() not in index:
                    raise ProjectError(f"channel {ch.name}: plugin has no param {k!r}")
                proc.set_parameter(index[k.lower()], float(v))
    else:
        proc = engine.make_faust_processor("inst")
        try:
            proc.set_dsp_string(faust_code(ch.instrument, ch.params))
        except ValueError as e:
            raise ProjectError(f"channel {ch.name}: {e}") from e
        proc.num_voices = 16
        if not proc.compile():
            raise ProjectError(f"channel {ch.name}: Faust code did not compile")
    for e in events:
        proc.add_midi_note(e.pitch, e.velocity, PRE_SEC + project.beats_to_sec(e.start),
                           project.beats_to_sec(e.length))
    engine.load_graph([(proc, [])])
    engine.render(PRE_SEC + seconds)
    pre = round(PRE_SEC * sr)
    return _fit(engine.get_audio()[:, pre:], round(seconds * sr))


def _set_program(mf: Path, program: int, bank: int, mch: int) -> None:
    """Insert bank select + program change at the start of a MIDI file."""
    import mido
    mid = mido.MidiFile(str(mf))
    tr = mid.tracks[0]
    tr.insert(1, mido.Message("program_change", program=program, channel=mch, time=0))
    tr.insert(1, mido.Message("control_change", control=0, value=min(bank, 127), channel=mch, time=0))
    mid.save(str(mf))


def _render_gm(project: Project, ch: Channel, events: list[Event], seconds: float, tmp: Path) -> np.ndarray:
    from scipy.io import wavfile
    exe = shutil.which("fluidsynth")
    if not exe:
        raise ProjectError(f"channel {ch.name}: GM sounds need fluidsynth (not found)")
    if not SOUNDFONT.exists():
        raise ProjectError(f"channel {ch.name}: GM soundfont not found: {SOUNDFONT}")
    mch = 9 if ch.bank == 128 else 0
    mf, wf = tmp / f"{ch.name}.mid", tmp / f"{ch.name}.wav"
    channel_midi(project, events, mf, channel=mch)
    _set_program(mf, ch.program, ch.bank, mch)
    cmd = [exe, "-ni", "-q", "-R", "0", "-C", "0", "-g", "0.5", "-r", str(project.sample_rate),
           "-F", str(wf), str(SOUNDFONT), str(mf)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not wf.exists():
        raise ProjectError(f"channel {ch.name}: fluidsynth failed: {r.stderr.strip()[:300]}")
    _, audio = wavfile.read(wf)
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    return _fit(audio.T, round(seconds * project.sample_rate))


def render_channel(project: Project, ch: Channel, events: list[Event], seconds: float, tmp: Path) -> np.ndarray:
    """Dry instrument audio, shape (2, n)."""
    if ch.instrument == "sample":
        from .samples import render_sample_channel
        return render_sample_channel(project, ch, events, seconds)
    if ch.instrument == "gm":
        return _render_gm(project, ch, events, seconds, tmp)
    return _render_daw(project, ch, events, seconds, tmp)


# ---- mixer -------------------------------------------------------------------

def apply_fx(chain: list[dict], audio: np.ndarray, sr: int, owner: str) -> np.ndarray:
    active = [fx for fx in chain if not fx.get("bypass")]
    if not active:
        return audio
    import pedalboard as pb
    try:
        board = pb.Pedalboard([make_effect(fx) for fx in active])
    except ValueError as e:
        raise ProjectError(f"{owner}: {e}") from e
    return _fit(board(audio, sr), audio.shape[1])


def mix(project: Project, dry: dict[str, np.ndarray], n: int, master_in: np.ndarray | None = None) -> np.ndarray:
    """Channels -> fx -> volume/pan -> buses -> master. master_in: extra audio into the master (audio clips)."""
    sr = project.sample_rate
    inputs = {b.name: np.zeros((2, n), np.float32) for b in project.buses}
    inputs["master"] = np.zeros((2, n), np.float32) if master_in is None else master_in[:, :n].astype(np.float32)
    for ch in project.channels:
        if ch.name not in dry:
            continue
        y = apply_fx(ch.fx, dry[ch.name], sr, f"channel {ch.name}")
        gl, gr = pan_gains(ch.pan)
        y = y * np.array([[gl], [gr]], np.float32) * ch.volume
        inputs[ch.output] += y
        for bus, level in ch.sends.items():
            inputs[bus] += y * level
    for bus in project.bus_order():
        if bus.mute:
            continue
        y = apply_fx(bus.fx, inputs[bus.name], sr, f"bus {bus.name}")
        gl, gr = pan_gains(bus.pan)
        inputs[bus.output] += y * np.array([[gl], [gr]], np.float32) * bus.volume
    return apply_fx(project.master_fx, inputs["master"], sr, "master") * project.master


def render_audio(project: Project) -> np.ndarray:
    """Return float32 audio, shape (2, samples)."""
    project.validate()
    beats = project.song_length()
    if beats <= 0:
        raise ProjectError("playlist is empty: nothing to render (use `place`)")
    events = song_events(project)
    seconds = project.beats_to_sec(beats) + TAIL_SEC
    n = round(seconds * project.sample_rate)
    with tempfile.TemporaryDirectory(prefix="muzdaw-") as td:
        dry = {ch.name: render_channel(project, ch, events[ch.name], seconds, Path(td))
               for ch in project.channels if not ch.mute and events[ch.name]}
    if not dry and not project.audio_clips:
        raise ProjectError("all channels are muted or have no notes")
    clips = None
    if project.audio_clips:
        from .samples import add_audio_clips
        clips = np.zeros((2, n), np.float32)
        add_audio_clips(project, clips)
    return mix(project, dry, n, clips).astype(np.float32)


def render(project: Project, out: str | Path) -> RenderResult:
    from scipy.io import wavfile

    out = Path(out)
    if out.suffix.lower() != ".wav":
        raise ProjectError("output must be a .wav file")
    audio = render_audio(project)
    wavfile.write(out, project.sample_rate, audio.T)
    return RenderResult(out, audio.shape[1] / project.sample_rate,
                        float(np.abs(audio).max()), float(np.sqrt(np.mean(audio ** 2))))
