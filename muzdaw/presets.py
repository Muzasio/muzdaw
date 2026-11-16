"""Named instrument presets: `add-channel NAME --inst <preset>`.

Each preset is a set of Channel fields. Faust presets always work; GM needs
fluidsynth + a GM soundfont; plugin presets need the plugin installed.
"""
from __future__ import annotations

from pathlib import Path

from .instruments import PRESETS as FAUST

VST3 = Path("/usr/lib/vst3")
VST2 = Path("/usr/lib/vst")

_GM = {
    "piano": 0, "epiano": 4, "organ": 19, "nylon-guitar": 24, "clean-guitar": 27,
    "acoustic-bass": 32, "strings": 48, "choir": 52, "brass": 61, "sax": 65, "flute": 73,
}
_SURGE = {
    "surge-pad": "Pads/Primes", "surge-bass": "Basses/Attacky",
    "surge-lead": "Leads/FM Is Growing On Me", "surge-poly": "Polysynths/Anthemish 3",
}
_PLUGINS = {
    "dexed": VST3 / "Dexed.vst3", "odin2": VST3 / "Odin2.vst3",
    "zyn": VST2 / "ZynAddSubFX.so", "cardinal": VST3 / "CardinalSynth.vst3",
}

PRESETS: dict[str, dict] = {name: {"instrument": name} for name in FAUST}
PRESETS.update({k: {"instrument": "gm", "program": v} for k, v in _GM.items()})
PRESETS["gm-drums"] = {"instrument": "gm", "program": 0, "bank": 128}
PRESETS.update({k: {"instrument": "plugin", "plugin": str(VST3 / "Surge XT.vst3"), "patch": v}
                for k, v in _SURGE.items()})
PRESETS.update({k: {"instrument": "plugin", "plugin": str(v)} for k, v in _PLUGINS.items()})


def kind(name: str) -> str:
    inst = PRESETS[name]["instrument"]
    return "built-in" if inst in FAUST else "GM soundfont" if inst == "gm" else "plugin"


def available(name: str) -> bool:
    p = PRESETS[name].get("plugin")
    return p is None or Path(p).exists()
