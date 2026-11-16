"""Built-in instruments (Faust code, compiled inside DawDreamer).

They need no system plugins, so a project always renders. Every preset
reads freq/gain/gate from MIDI, plus named params a channel can set with
`params` (e.g. {"cutoff": 2000}). Params are baked into the code as slider
defaults, so the value is exact and the render is repeatable.
"""
from __future__ import annotations

# name: (default, min, max)
PARAMS: dict[str, tuple[float, float, float]] = {
    "attack":  (0.005, 0.001, 5.0),
    "decay":   (0.1, 0.001, 5.0),
    "sustain": (0.7, 0.0, 1.0),
    "release": (0.2, 0.001, 10.0),
    "cutoff":  (4000.0, 20.0, 20000.0),
}

# body must define `voice` (mono); output is duplicated to stereo
_BODIES = {
    "sine":   "voice = os.osc(freq) * env;",
    "saw":    "voice = os.sawtooth(freq) : fi.lowpass(2, cutoff) : *(env);",
    "square": "voice = os.square(freq) : fi.lowpass(2, cutoff) : *(env);",
    "bass":   "voice = (os.sawtooth(freq) + os.square(freq/2)) * 0.5 : fi.resonlp(cutoff*0.25, 2, 1) : *(env);",
    "pluck":  "voice = sy.combString(freq, 0.6, gate : ba.impulsify);",
    "pad":    "voice = (os.sawtooth(freq) + os.sawtooth(freq*1.006) + os.sawtooth(freq*0.994)) / 3"
              " : fi.lowpass(2, cutoff) : *(en.adsr(max(attack,0.4), decay, sustain, max(release,0.8), gate));",
    "kick":   "voice = os.osc(45 + 120*en.ar(0.001, 0.05, gate)) * en.ar(0.001, 0.35, gate);",
    "hat":    "voice = no.noise : fi.highpass(2, 7000) : *(en.ar(0.001, 0.05, gate));",
}

PRESETS = sorted(_BODIES)


def check_params(params: dict[str, float]) -> None:
    for k, v in params.items():
        if k not in PARAMS:
            raise ValueError(f"unknown param {k!r}; choose from {', '.join(PARAMS)}")
        _, lo, hi = PARAMS[k]
        if not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"param {k} must be {lo}-{hi}, got {v}")


def faust_code(preset: str, params: dict[str, float] | None = None) -> str:
    if preset not in _BODIES:
        raise ValueError(f"unknown instrument {preset!r}; choose from {', '.join(PRESETS)}")
    params = params or {}
    check_params(params)
    lines = ['import("stdfaust.lib");',
             'freq = hslider("freq", 440, 20, 20000, 0.01);',
             'gain = hslider("gain", 0.5, 0, 1, 0.01);',
             'gate = button("gate");']
    for name, (default, lo, hi) in PARAMS.items():
        val = float(params.get(name, default))
        lines.append(f'{name} = hslider("{name}", {val!r}, {lo!r}, {hi!r}, 0.001);')
    lines.append("env = en.adsr(attack, decay, sustain, release, gate);")
    lines.append(_BODIES[preset])
    lines.append("process = voice * gain * 0.3 <: _, _;")
    return "\n".join(lines) + "\n"
