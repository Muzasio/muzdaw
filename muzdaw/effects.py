"""Effects: pedalboard built-ins and VST3/VST2 plugins, params set by name.

An effect in the project file:
    {"type": "reverb", "params": {"room_size": 0.6}, "bypass": false}
    {"type": "plugin", "plugin": "/usr/lib/vst3/lsp-plugins.vst3",
     "name": "Compressor Stereo", "params": {"threshold_db": -18}}
"""
from __future__ import annotations

import re
from pathlib import Path

BUILTINS = {
    "reverb": "Reverb", "delay": "Delay", "chorus": "Chorus", "phaser": "Phaser",
    "distortion": "Distortion", "compressor": "Compressor", "limiter": "Limiter",
    "gate": "NoiseGate", "gain": "Gain", "highpass": "HighpassFilter", "lowpass": "LowpassFilter",
    "eq-peak": "PeakFilter", "eq-low": "LowShelfFilter", "eq-high": "HighShelfFilter",
    "ladder": "LadderFilter", "bitcrush": "Bitcrush", "clipping": "Clipping", "pitchshift": "PitchShift",
}

_UNIT = re.compile(r"^\s*([-+]?\d*\.?\d+(?:e[-+]?\d+)?)\s*(db|hz|khz|ms|s|%|x)?\s*$", re.I)


def parse_value(text: str) -> float | bool | str:
    """'0.75' -> 0.75, '-6dB' -> -6.0, '2kHz' -> 2000.0, '50%' -> 0.5, 'true' -> True, else the text.

    Units are only a convenience: the number goes to the param as written
    (dB stays dB, Hz stays Hz, ms stays ms); kHz becomes Hz, % becomes 0-1.
    """
    if isinstance(text, (int, float, bool)):
        return text
    low = text.strip().lower()
    if low in ("true", "on", "yes"):
        return True
    if low in ("false", "off", "no"):
        return False
    m = _UNIT.match(text)
    if not m:
        return text.strip()
    num, unit = float(m.group(1)), (m.group(2) or "").lower()
    return num * 1000 if unit == "khz" else num / 100 if unit == "%" else num


def plugin_names(path: str) -> list[str]:
    import pedalboard as pb
    return list(pb.VST3Plugin.get_plugin_names_for_file(path)) if path.endswith(".vst3") else []


def make_effect(fx: dict):
    """Build a fresh pedalboard plugin from an effect dict. Raises ValueError on bad input."""
    import pedalboard as pb
    kind = fx.get("type")
    if kind == "plugin":
        path = str(Path(fx.get("plugin") or "").expanduser())
        if not Path(path).exists():
            raise ValueError(f"effect plugin not found: {path}")
        try:
            # a fresh instance per render, so plugin state never leaks between renders
            name = fx.get("name")
            proc = pb.load_plugin(path, plugin_name=name) if name else pb.load_plugin(path)
        except (ValueError, ImportError, RuntimeError) as e:
            raise ValueError(f"cannot load effect {path} {fx.get('name') or ''}: {e}") from e
        known = proc.parameters
    elif kind in BUILTINS:
        proc = getattr(pb, BUILTINS[kind])()
        known = {k: None for k in dir(proc) if not k.startswith("_") and _is_param(proc, k)}
    else:
        raise ValueError(f"unknown effect {kind!r}; choose from {', '.join(sorted(BUILTINS))}, plugin")
    for k, v in (fx.get("params") or {}).items():
        if k not in known:
            raise ValueError(f"effect {kind}: no param {k!r}; params: {', '.join(sorted(known))}")
        val = parse_value(v) if isinstance(v, str) else v
        cur = getattr(proc, k, None)
        if kind != "plugin" and type(cur).__name__ == "Mode" and isinstance(val, str):
            if not hasattr(type(cur), val):
                raise ValueError(f"effect {kind}: {k} must be one of "
                                 f"{', '.join(m for m in dir(type(cur)) if m.isupper())}")
            val = getattr(type(cur), val)
        try:
            setattr(proc, k, val)
        except (ValueError, TypeError) as e:
            raise ValueError(f"effect {kind}: param {k}={v!r}: {e}") from e
    return proc


def _is_param(proc, name: str) -> bool:
    if name in ("is_effect", "is_instrument", "reset", "process") or name.isupper():
        return False
    try:
        val = getattr(proc, name)
    except Exception:
        return False
    return isinstance(val, (int, float, bool)) or type(val).__name__ == "Mode"


def describe(fx: dict) -> list[str]:
    """Lines 'name = value  (range)' for an effect type or plugin, for `fx-params`."""
    proc = make_effect({**fx, "params": {}})
    lines = []
    if fx.get("type") == "plugin":
        for k, p in proc.parameters.items():
            rng = ""
            try:
                rng = f"{p.min_value} .. {p.max_value}" if p.min_value is not None else ""
            except Exception:
                pass
            vals = getattr(p, "valid_values", None)
            if vals and len(vals) <= 12 and not rng:
                rng = " | ".join(map(str, vals))
            lines.append(f"{k} = {getattr(proc, k)!r}  {rng}".rstrip())
    else:
        for k in sorted(dir(proc)):
            if not k.startswith("_") and _is_param(proc, k):
                lines.append(f"{k} = {getattr(proc, k)!r}")
    return lines


def check_effect(fx: dict) -> None:
    """Cheap validation used by the project model (no plugin load)."""
    if not isinstance(fx, dict):
        raise ValueError("effect must be an object")
    kind = fx.get("type")
    if kind == "plugin":
        if not fx.get("plugin"):
            raise ValueError("plugin effect needs a plugin path")
    elif kind not in BUILTINS:
        raise ValueError(f"unknown effect {kind!r}; choose from {', '.join(sorted(BUILTINS))}, plugin")
    if not isinstance(fx.get("params", {}), dict):
        raise ValueError("effect params must be an object")
