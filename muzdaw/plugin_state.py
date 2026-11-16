"""Build JUCE VST3 plugin state blobs (to load a patch file into a plugin).

JUCE-hosted VST3 state = b"VC2!" + uint32 LE length + XML + b"\\0", where the
XML holds <IComponent>SIZE.DATA</IComponent> in JUCE's own base64.
DawDreamer's `load_state(file)` and pedalboard's `raw_state` both take this.

Surge XT: the IComponent data is the `.fxp` patch chunk (bytes from 60 on).
Note: Surge loads a new patch inside the first audio block and stops notes
there, so renders need a short pre-roll before the first note.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

_CHARS = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
_INDEX = {c: i for i, c in enumerate(_CHARS)}

SURGE_PATCH_DIRS = [Path("/usr/share/surge-xt/patches_factory"), Path("/usr/share/surge-xt/patches_3rdparty")]


def juce_b64encode(data: bytes) -> str:
    n = int.from_bytes(data, "little")
    out = []
    for i in range((len(data) * 8 + 5) // 6):
        out.append(_CHARS[(n >> (6 * i)) & 63])
    return f"{len(data)}." + "".join(out)


def juce_b64decode(text: str) -> bytes:
    size_s, sep, body = text.partition(".")
    if not sep or not size_s.isdigit():
        raise ValueError("not JUCE base64")
    n = 0
    for i, c in enumerate(body):
        n |= _INDEX[c] << (6 * i)
    size = int(size_s)
    return (n & ((1 << (size * 8)) - 1)).to_bytes(size, "little")


def vst3_state(component: bytes) -> bytes:
    xml = ('<?xml version="1.0" encoding="UTF-8"?> <VST3PluginState><IComponent>'
           + juce_b64encode(component) + "</IComponent></VST3PluginState>").encode()
    return b"VC2!" + struct.pack("<I", len(xml)) + xml + b"\0"


def vst3_component(state: bytes) -> bytes:
    if state[:4] != b"VC2!":
        raise ValueError("not a JUCE VST3 state")
    (length,) = struct.unpack("<I", state[4:8])
    m = re.search(rb"<IComponent>(.*?)</IComponent>", state[8:8 + length])
    if not m:
        raise ValueError("state has no IComponent")
    return juce_b64decode(m.group(1).decode())


def surge_fxp_to_state(fxp: bytes) -> bytes:
    if fxp[:4] != b"CcnK" or fxp[8:12] != b"FPCh" or fxp[60:64] != b"sub3":
        raise ValueError("not a Surge XT .fxp patch")
    (size,) = struct.unpack(">I", fxp[56:60])
    return vst3_state(fxp[60:60 + size])


def find_surge_patch(name: str) -> Path:
    """'Pads/Primes' or 'Primes' (case-insensitive) or a full .fxp path."""
    p = Path(name).expanduser()
    if p.suffix == ".fxp" and p.is_file():
        return p
    want = name.lower().removesuffix(".fxp")
    hits = []
    for root in SURGE_PATCH_DIRS:
        for f in root.rglob("*.fxp") if root.is_dir() else []:
            rel = f.relative_to(root).with_suffix("").as_posix().lower()
            if rel == want or f.stem.lower() == want:
                hits.append(f)
    if not hits:
        raise ValueError(f"Surge patch not found: {name} (see `muzdaw patches`)")
    return sorted(hits)[0]


def list_surge_patches(filt: str = "") -> list[str]:
    out = []
    for root in SURGE_PATCH_DIRS:
        if root.is_dir():
            out += [f.relative_to(root).with_suffix("").as_posix() for f in root.rglob("*.fxp")]
    return sorted(x for x in set(out) if filt.lower() in x.lower())
