"""Live engine: play the project in realtime with Carla (C++ audio, Python only controls).

Graph (Carla patchbay mode, nothing is connected unless we connect it):

  per channel: MIDI file player -> instrument -> live fx... -> strip (vol/pan) -> bus input / master input
                                                                    \\-> send gain -> bus input
  per bus:     input gain -> live fx... -> strip -> its output
  master:      input gain -> live fx... -> master strip -> speakers (only when audible=True)
  metronome:   MIDI file player -> soundfont (GM drums) -> master strip

Live sound is a close preview of the offline render, not a copy:
- built-in Faust synths -> Surge XT / GM drum stand-ins
- built-in effects -> LSP / Dragonfly LV2 equivalents (bitcrush, ladder, pitchshift: skipped live)
- plugin effects: same plugin, params copied by name (LSP bundle -> LSP LV2)
"""
from __future__ import annotations

import base64
import math
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .effects import make_effect
from .events import song_events
from .midi_io import channel_midi
from .model import Channel, Project, ProjectError
from .plugin_state import find_surge_patch, surge_fxp_to_state

CARLA_PY = Path("/usr/share/carla")
CARLA_LIB = Path("/usr/lib/carla/libcarla_standalone2.so")
CARLA_BIN = Path("/usr/lib/carla")
SOUNDFONT = Path("/usr/share/soundfonts/FluidR3_GM.sf2")
SURGE = Path("/usr/lib/vst3/Surge XT.vst3")
LSP_LV2 = Path("/usr/lib/lv2/lsp-plugins.lv2")
LSP = "http://lsp-plug.in/plugins/lv2/"
SETTLE_TICKS = 30  # x 10 ms after building the graph

# built-in Faust synth -> live stand-in
FAUST_LIVE = {
    "sine": {"patch": "Templates/Init Sine"}, "saw": {"patch": "Templates/Init Saw"},
    "square": {"patch": "Templates/Init Square"}, "bass": {"patch": "Basses/Attacky"},
    "pluck": {"patch": "Templates/Init Karplus-Strong"}, "pad": {"patch": "Pads/Primes"},
    "kick": {"drum": 36}, "hat": {"drum": 42},
}


class LiveError(ProjectError):
    pass


def _g(db: float) -> float:
    return 10 ** (db / 20)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def live_fx(fx: dict) -> tuple[str, str, dict] | None:
    """Built-in effect -> (kind, uri/label, {symbol: value}) for Carla, or None if no live version.

    kind: 'lv2' or 'internal'. Values come from the pedalboard object, so
    defaults match the offline effect.
    """
    p = make_effect(fx)
    t = fx["type"]
    eq = {"highpass": 2, "lowpass": 4, "eq-peak": 1, "eq-low": 5, "eq-high": 3}
    if t == "compressor":
        return "lv2", LSP + "compressor_stereo", {
            "al": _clamp(_g(p.threshold_db), 0.001, 1), "cr": _clamp(p.ratio, 1, 100),
            "at": _clamp(p.attack_ms, 0, 2000), "rt": _clamp(p.release_ms, 0, 5000)}
    if t == "limiter":
        return "lv2", LSP + "limiter_stereo", {
            "th": _clamp(_g(p.threshold_db), 0.003981, 1), "rt": _clamp(p.release_ms, 0.25, 20)}
    if t == "gate":
        return "lv2", LSP + "gate_stereo", {
            "gt": _clamp(_g(p.threshold_db), 1.6e-05, 1), "at": _clamp(p.attack_ms, 0, 2000),
            "rt": _clamp(p.release_ms, 0, 5000)}
    if t == "gain":
        return "internal", "audiogain_s", {0: _clamp(_g(p.gain_db), 0, 4)}
    if t in eq:
        vals = {"ft_0": eq[t], "f_0": _clamp(p.cutoff_frequency_hz, 10, 24000)}
        if t in ("eq-peak", "eq-low", "eq-high"):
            vals.update({"g_0": _clamp(_g(p.gain_db), 0.01585, 63.0957), "q_0": _clamp(p.q, 0, 100)})
        return "lv2", LSP + "para_equalizer_x8_stereo", vals
    if t == "delay":
        return "lv2", LSP + "slap_delay_stereo", {
            "dm0": 1, "dt0": _clamp(p.delay_seconds * 1000, 0, 1000), "dfb0": _clamp(p.feedback, 0, 1),
            "dry": 1 - p.mix, "wet": p.mix}
    if t == "chorus":
        return "lv2", LSP + "chorus_stereo", {
            "rate": _clamp(p.rate_hz, 0.01, 20), "depth": _clamp(p.centre_delay_ms * p.depth * 2, 0.1, 20),
            "dry": 1 - p.mix, "wet": p.mix}
    if t == "phaser":
        return "lv2", LSP + "phaser_stereo", {
            "rate": _clamp(p.rate_hz, 0.01, 20), "depth": _clamp(p.depth * 2, 0, 10),
            "dry": 1 - p.mix, "wet": p.mix}
    if t == "reverb":
        return "lv2", "urn:dragonfly:room", {
            "size": 8 + p.room_size * 24, "decay": 0.3 + p.room_size * 4,
            "dry_level": p.dry_level * 100, "late_level": p.wet_level * 100, "early_level": p.wet_level * 50,
            "late_damp": 16000 - p.damping * 12000}
    if t == "distortion":
        return "lv2", LSP + "clipper_stereo", {"g_in": _clamp(_g(p.drive_db), 0, 1000)}
    if t == "clipping":
        return "lv2", LSP + "clipper_stereo", {"thresh": _clamp(p.threshold_db, -48, 0)}
    return None  # bitcrush, ladder, pitchshift


def lsp_lv2_uri(name: str) -> str | None:
    """'Compressor Stereo' (LSP VST3 bundle name) -> its LV2 URI, from the LV2 .ttl files."""
    want = name.lower().removeprefix("lsp ").strip()
    for ttl in LSP_LV2.glob("*.ttl"):
        text = ttl.read_text(errors="replace")
        m = re.search(r'doap:name\s+"(?:LSP\s+)?([^"]+)"', text)
        if m and m.group(1).lower() == want:
            return LSP + ttl.stem  # each plugin's .ttl is named after its URI tail
    return None


@dataclass
class _Node:
    pid: int
    name: str


@dataclass
class LiveSession:
    project: Project
    audible: bool = False
    metronome: bool = False
    keyboard: str | None = None   # channel name that gets MIDI keyboard input
    loop: bool = False
    driver: str = "PulseAudio"
    warnings: list[str] = field(default_factory=list)

    # ---- setup ---------------------------------------------------------------
    def start(self) -> None:
        if not CARLA_LIB.exists():
            raise LiveError("Carla is not installed (needs /usr/lib/carla/libcarla_standalone2.so)")
        if str(CARLA_PY) not in sys.path:
            sys.path.insert(0, str(CARLA_PY))
        import carla_backend as cb  # noqa: E402  (Carla's own Python API)
        self.cb = cb
        self.project.validate()
        self._tmp = tempfile.TemporaryDirectory(prefix="muzdaw-live-")
        self._groups: dict[int, tuple[str, int]] = {}
        self._ports: dict[tuple[int, int], tuple[str, int]] = {}
        self.connections: list[str] = []
        self._npid = 0
        h = cb.CarlaHostDLL(str(CARLA_LIB), False)
        self.h = h
        h.set_engine_callback(self._callback)
        h.set_engine_option(cb.ENGINE_OPTION_PATH_BINARIES, 0, str(CARLA_BIN))
        h.set_engine_option(cb.ENGINE_OPTION_PATH_RESOURCES, 0, str(CARLA_PY / "resources"))
        h.set_engine_option(cb.ENGINE_OPTION_PROCESS_MODE, cb.ENGINE_PROCESS_MODE_PATCHBAY, "")
        if not h.engine_init(self.driver, "muzdaw"):
            raise LiveError(f"Carla engine did not start ({self.driver}): {h.get_last_error()}")
        self.sr = h.get_sample_rate()
        h.transport_bpm(self.project.tempo)
        try:
            self._build()
        except BaseException:
            self.close()
            raise
        # let the audio thread run a while: Surge applies a loaded patch inside its
        # next audio blocks and drops notes that arrive before that
        self._idle(SETTLE_TICKS)

    def _callback(self, handle, action, pid, v1, v2, v3, vf, vs) -> None:
        cb = self.cb
        s = (vs or b"").decode(errors="replace")
        if action == cb.ENGINE_CALLBACK_PATCHBAY_CLIENT_ADDED:
            self._groups[pid] = (s, v2)
        elif action == cb.ENGINE_CALLBACK_PATCHBAY_PORT_ADDED:
            self._ports[(pid, v1)] = (s, v2)
        elif action == cb.ENGINE_CALLBACK_PATCHBAY_CONNECTION_ADDED:
            self.connections.append(s)  # "groupA:portA:groupB:portB"

    def _idle(self, n: int = 3) -> None:
        for _ in range(n):
            self.h.engine_idle()
            time.sleep(0.01)

    def _add(self, ptype, filename: str, label: str, name: str) -> _Node:
        cb = self.cb
        if not self.h.add_plugin(cb.BINARY_NATIVE, ptype, filename, "", label, 0, None, 0):
            raise LiveError(f"{name}: Carla could not load {filename or label}: {self.h.get_last_error()}")
        node = _Node(self._npid, name)
        self._npid += 1
        self._idle(1)
        return node

    def _gain(self, name: str, gain: float = 1.0, pan: float = 0.0) -> _Node:
        n = self._add(self.cb.PLUGIN_INTERNAL, "", "audiogain_s", name)
        self.h.set_parameter_value(n.pid, 0, _clamp(gain, 0, 4))
        if pan:
            # Carla balance: left/right edges; pan -1..1 moves both edges
            self.h.set_balance_left(n.pid, _clamp(-1 + 2 * max(pan, 0), -1, 1))
            self.h.set_balance_right(n.pid, _clamp(1 + 2 * min(pan, 0), -1, 1))
        return n

    def _group(self, pid: int) -> int:
        for g, (_, plugin) in self._groups.items():
            if plugin == pid:
                return g
        raise LiveError(f"no patchbay group for plugin {pid}")

    def _port_list(self, group: int, is_input: bool, kind: int) -> list[int]:
        cb = self.cb
        return sorted(p for (g, p), (_, fl) in self._ports.items()
                      if g == group and bool(fl & cb.PATCHBAY_PORT_IS_INPUT) == is_input and fl & kind)

    def _connect(self, a: _Node, b: _Node, midi: bool = False) -> None:
        cb = self.cb
        kind = cb.PATCHBAY_PORT_TYPE_MIDI if midi else cb.PATCHBAY_PORT_TYPE_AUDIO
        ga, gb = self._group(a.pid), self._group(b.pid)
        outs, ins = self._port_list(ga, False, kind)[:2], self._port_list(gb, True, kind)[:2]
        if not outs or not ins:
            raise LiveError(f"cannot connect {a.name} -> {b.name} (missing {'MIDI' if midi else 'audio'} ports)")
        pairs = [(outs[0], ins[0])] if midi else (
            [(outs[0], i) for i in ins] if len(outs) == 1 else list(zip(outs, ins)))
        for o, i in pairs:
            self.h.patchbay_connect(False, ga, o, gb, i)

    def speakers_connected(self) -> bool:
        """True if anything is connected to the system audio output."""
        g = self._system("Audio Output")
        return any(c.split(":")[2] == str(g) for c in self.connections if c.count(":") == 3)

    def _system(self, name: str) -> int:
        for g, (n, plugin) in self._groups.items():
            if n == name and plugin < 0:
                return g
        raise LiveError(f"no system group {name}")

    def _midi_player(self, name: str, mid: Path) -> _Node:
        n = self._add(self.cb.PLUGIN_INTERNAL, "", "midifile", f"{name}/player")
        self.h.set_custom_data(n.pid, self.cb.CUSTOM_DATA_TYPE_STRING, "file", str(mid))
        self.h.set_parameter_value(n.pid, 0, 1.0 if self.loop else 0.0)  # Repeat Mode
        return n

    def _set_params_by_symbol(self, node: _Node, values: dict) -> None:
        h = self.h
        by_sym = {}
        for k in range(h.get_parameter_count(node.pid)):
            info = h.get_parameter_info(node.pid, k)
            by_sym[info["symbol"]] = k
        for sym, v in values.items():
            k = sym if isinstance(sym, int) else by_sym.get(sym)
            if k is None:
                self.warnings.append(f"{node.name}: live param {sym} not found")
                continue
            h.set_parameter_value(node.pid, k, float(v))

    def _instrument(self, ch: Channel) -> tuple[_Node, dict]:
        cb, h = self.cb, self.h
        extra = {}
        inst, plugin, patch = ch.instrument, ch.plugin, ch.patch
        if inst in FAUST_LIVE:
            stand = FAUST_LIVE[inst]
            self.warnings.append(f"channel {ch.name}: built-in '{inst}' plays a stand-in live "
                                 f"({stand.get('patch') or 'GM drum'}); render uses the real sound")
            if "drum" in stand:
                inst, extra = "gm", {"drum": stand["drum"]}
            else:
                inst, plugin, patch = "plugin", str(SURGE), stand["patch"]
        if inst == "gm":
            if not SOUNDFONT.exists():
                raise LiveError(f"channel {ch.name}: soundfont not found: {SOUNDFONT}")
            n = self._add(cb.PLUGIN_SF2, str(SOUNDFONT), "FluidR3_GM", ch.name)
            drums = ch.bank == 128 or "drum" in extra
            if not drums:
                for i in range(h.get_midi_program_count(n.pid)):
                    d = h.get_midi_program_data(n.pid, i)
                    if d["bank"] == ch.bank and d["program"] == ch.program:
                        h.set_midi_program(n.pid, i)
                        break
            extra["midi_channel"] = 9 if drums else 0
            return n, extra
        path = Path(plugin).expanduser()
        if not path.exists():
            raise LiveError(f"channel {ch.name}: plugin not found: {path}")
        ptype = {".vst3": cb.PLUGIN_VST3, ".so": cb.PLUGIN_VST2, ".lv2": cb.PLUGIN_LV2}.get(path.suffix)
        if ptype is None:
            raise LiveError(f"channel {ch.name}: unknown plugin type {path.suffix}")
        n = self._add(ptype, str(path), "", ch.name)
        if patch:
            state = surge_fxp_to_state(find_surge_patch(patch).read_bytes())
            h.set_chunk_data(n.pid, base64.b64encode(state).decode())
        if ch.params:
            names = {h.get_parameter_info(n.pid, k)["name"].lower(): k for k in range(h.get_parameter_count(n.pid))}
            for k, v in ch.params.items():
                if k.lower() in names:
                    h.set_parameter_value(n.pid, names[k.lower()], float(v))
                else:
                    self.warnings.append(f"channel {ch.name}: param {k} not found live")
        return n, extra

    def _fx_chain(self, owner: str, chain: list[dict], src: _Node) -> _Node:
        cb, h = self.cb, self.h
        node = src
        for i, fx in enumerate(chain):
            if fx.get("bypass"):
                continue
            label = f"{owner}/fx{i}"
            if fx["type"] == "plugin":
                path = str(Path(fx["plugin"]).expanduser())
                pb = make_effect(fx)
                if fx.get("name") and "lsp-plugins" in path:
                    uri = lsp_lv2_uri(fx["name"])
                    if not uri:
                        self.warnings.append(f"{label}: no LSP LV2 for {fx['name']}, skipped live")
                        continue
                    n = self._add(cb.PLUGIN_LV2, "", uri, label)  # LSP VST3 crashes Carla headless
                    lv2_by_name = {h.get_parameter_info(n.pid, k)["name"].lower(): k
                                   for k in range(h.get_parameter_count(n.pid))}
                    for key in (fx.get("params") or {}):
                        p = pb.parameters[key]
                        k = lv2_by_name.get(p.name.lower())
                        if k is None:
                            self.warnings.append(f"{label}: param {key} not found live")
                            continue
                        val = float(getattr(pb, key))
                        if h.get_parameter_info(n.pid, k)["unit"] == "G" and p.label == "dB":
                            val = _g(val)
                        h.set_parameter_value(n.pid, k, val)
                else:
                    ptype = cb.PLUGIN_VST3 if path.endswith(".vst3") else cb.PLUGIN_VST2
                    n = self._add(ptype, path, "", label)
                    by_name = {h.get_parameter_info(n.pid, k)["name"].lower(): k
                               for k in range(h.get_parameter_count(n.pid))}
                    for key in (fx.get("params") or {}):
                        p = pb.parameters[key]
                        k = by_name.get(p.name.lower())
                        if k is None:
                            self.warnings.append(f"{label}: param {key} not found live")
                            continue
                        r = h.get_parameter_ranges(n.pid, k)
                        val = p.raw_value if (r["min"], r["max"]) == (0.0, 1.0) else float(getattr(pb, key))
                        h.set_parameter_value(n.pid, k, val)
            else:
                spec = live_fx(fx)
                if spec is None:
                    self.warnings.append(f"{label}: '{fx['type']}' has no live version, skipped live")
                    continue
                kind, uri, values = spec
                ptype = cb.PLUGIN_LV2 if kind == "lv2" else cb.PLUGIN_INTERNAL
                n = self._add(ptype, "", uri, label)
                self._set_params_by_symbol(n, values)
            self._idle(1)
            self._pending.append((node, n))
            node = n
        return node

    def _build(self) -> None:
        p = self.project
        tmp = Path(self._tmp.name)
        self._pending: list[tuple[_Node, _Node]] = []   # audio connections, made after refresh
        midi_links: list[tuple[_Node, _Node]] = []
        self.song_beats = p.song_length()
        if self.song_beats <= 0:
            raise LiveError("playlist is empty: nothing to play (use `place`)")
        events = song_events(p)
        if p.audio_clips:
            self.warnings.append("audio clips play in the window (Space) and in the export, not in terminal live play")

        # master
        self.master_in = self._gain("master/in")
        master_last = self._fx_chain("master", p.master_fx, self.master_in)
        self.master_strip = self._gain("master/strip", p.master)
        self._pending.append((master_last, self.master_strip))
        targets = {"master": self.master_in}

        # buses (inputs first, so channels can connect to them)
        bus_strips = {}
        for b in p.buses:
            targets[b.name] = self._gain(f"{b.name}/in")
        for b in p.bus_order():
            last = self._fx_chain(b.name, b.fx, targets[b.name])
            strip = self._gain(f"{b.name}/strip", 0.0 if b.mute else b.volume, b.pan)
            self._pending.append((last, strip))
            bus_strips[b.name] = strip
        for b in p.buses:
            self._pending.append((bus_strips[b.name], targets[b.output]))

        # channels
        self.strips: dict[str, _Node] = {}
        self.instruments: dict[str, _Node] = {}
        for ch in p.channels:
            if ch.mute:
                continue
            if ch.instrument == "sample":
                self.warnings.append(f"channel {ch.name}: sample channels play in the window (Space) and in "
                                     "the export, not in terminal live play")
                continue
            inst, extra = self._instrument(ch)
            self.instruments[ch.name] = inst
            mid = channel_midi(p, events[ch.name], tmp / f"{len(self.strips)}.mid",
                               channel=extra.get("midi_channel", 0), end_beats=self.song_beats,
                               remap_pitch=extra.get("drum"))
            player = self._midi_player(ch.name, mid)
            midi_links.append((player, inst))
            last = self._fx_chain(ch.name, ch.fx, inst)
            strip = self._gain(f"{ch.name}/strip", ch.volume, ch.pan)
            self._pending.append((last, strip))
            self._pending.append((strip, targets[ch.output]))
            for bus, level in ch.sends.items():
                send = self._gain(f"{ch.name}/send-{bus}", level)
                self._pending += [(strip, send), (send, targets[bus])]
            self.strips[ch.name] = strip

        # metronome: one click per beat, accent on bar start
        if self.metronome:
            from .events import Event
            clicks = [Event(float(b), 76 if b % 4 == 0 else 77, 0.1, 110 if b % 4 == 0 else 80)
                      for b in range(math.ceil(self.song_beats))]
            click = self._add(self.cb.PLUGIN_SF2, str(SOUNDFONT), "FluidR3_GM", "metronome")
            player = self._midi_player("metronome", channel_midi(p, clicks, tmp / "click.mid", channel=9,
                                                                 end_beats=self.song_beats))
            midi_links.append((player, click))
            self._pending.append((click, self.master_strip))

        self.h.patchbay_refresh(False)
        self._idle(10)
        for a, b in midi_links:
            self._connect(a, b, midi=True)
        for a, b in self._pending:
            self._connect(a, b)
        if self.keyboard:
            inst = self.instruments.get(self.keyboard)
            if inst is None:
                raise LiveError(f"--keyboard: no (unmuted) channel named {self.keyboard}")
            g = self._system("Midi Input")
            for port in self._port_list(g, False, self.cb.PATCHBAY_PORT_TYPE_MIDI):
                gi = self._group(inst.pid)
                self.h.patchbay_connect(False, g, port, gi, self._port_list(gi, True, self.cb.PATCHBAY_PORT_TYPE_MIDI)[0])
        if self.audible:
            g = self._system("Audio Output")
            gm = self._group(self.master_strip.pid)
            for o, i in zip(self._port_list(gm, False, self.cb.PATCHBAY_PORT_TYPE_AUDIO),
                            self._port_list(g, True, self.cb.PATCHBAY_PORT_TYPE_AUDIO)):
                self.h.patchbay_connect(False, gm, o, g, i)
        self._idle(5)

    # ---- transport -------------------------------------------------------------
    def play(self, from_beat: float = 0.0) -> None:
        self.h.transport_relocate(round(self.project.beats_to_sec(from_beat) * self.sr))
        self.h.transport_play()

    def stop(self) -> None:
        self.h.transport_pause()

    def position_beats(self) -> float:
        return self.h.get_current_transport_frame() / self.sr * self.project.tempo / 60.0

    def peak(self, node: _Node) -> float:
        return max(self.h.get_output_peak_value(node.pid, True), self.h.get_output_peak_value(node.pid, False))

    def run(self, seconds: float | None = None, on_tick=None) -> dict[str, float]:
        """Keep the engine running; stop at song end (+1 s tail) unless looping.

        Returns the highest peak seen per channel strip and 'master'.
        """
        peaks = {name: 0.0 for name in self.strips} | {"master": 0.0}
        end = self.project.beats_to_sec(self.song_beats) + 1.0
        t0 = time.monotonic()
        try:
            while True:
                self.h.engine_idle()
                time.sleep(0.02)
                for name, node in self.strips.items():
                    peaks[name] = max(peaks[name], self.peak(node))
                peaks["master"] = max(peaks["master"], self.peak(self.master_strip))
                if on_tick:
                    on_tick(self)
                elapsed = time.monotonic() - t0
                if seconds is not None and elapsed >= seconds:
                    break
                if seconds is None and not self.loop and elapsed >= end:
                    break
        finally:
            self.stop()
        return peaks

    def close(self) -> None:
        h = getattr(self, "h", None)
        if h is not None:
            try:
                if h.is_engine_running():
                    h.engine_close()
            finally:
                self.h = None
        if getattr(self, "_tmp", None):
            self._tmp.cleanup()
            self._tmp = None

    def __enter__(self) -> LiveSession:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
