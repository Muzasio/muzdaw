import hashlib
import os
import shutil

import numpy as np
import pytest

from muzdaw.cli import _gain, main
from muzdaw.effects import make_effect, parse_value
from muzdaw.model import Bus, Channel, Clip, Note, Pattern, Project, ProjectError, load
from muzdaw.plugin_state import juce_b64decode, juce_b64encode, vst3_component, vst3_state
from muzdaw.render import render_audio

SURGE = "/usr/lib/vst3/Surge XT.vst3"
LSP = "/usr/lib/vst3/lsp-plugins.vst3"
has_surge = pytest.mark.skipif(not os.path.exists(SURGE), reason="Surge XT not installed")


def one(inst="sine", **ch) -> Project:
    p = Project(tempo=120)
    p.channels = [Channel("a", inst, **ch)]
    p.patterns = [Pattern("A", 4, {"a": [Note(69, 0, 2)]})]
    p.playlist = [Clip("A", 0)]
    return p


def rms(a):
    return float(np.sqrt(np.mean(a ** 2)))


# ---- values -----------------------------------------------------------------

@pytest.mark.parametrize("txt,val", [("0.5", 0.5), ("-6dB", -6.0), ("2kHz", 2000.0), ("440Hz", 440.0),
                                     ("25%", 0.25), ("30ms", 30.0), ("on", True), ("LPF24", "LPF24")])
def test_parse_value(txt, val):
    assert parse_value(txt) == val


def test_gain_db():
    assert _gain("-6dB") == pytest.approx(0.501187, abs=1e-6)
    assert _gain("0.8") == 0.8


# ---- JUCE state -------------------------------------------------------------

def test_juce_b64_roundtrip():
    for data in (b"", b"\x00", b"sub3 hello \xff\x00\x01", bytes(range(256)) * 3):
        assert juce_b64decode(juce_b64encode(data)) == data
    assert vst3_component(vst3_state(b"abc")) == b"abc"


@has_surge
def test_juce_b64_matches_real_plugin_state():
    import pedalboard as pb
    st = pb.load_plugin(SURGE).raw_state
    comp = vst3_component(st)
    assert comp[:4] == b"sub3"
    assert vst3_state(comp)[8:].split(b"IComponent>")[1] == st[8:].split(b"IComponent>")[1]


# ---- effects ----------------------------------------------------------------

def test_fx_bad_type_and_param():
    with pytest.raises(ValueError):
        make_effect({"type": "nosuch"})
    with pytest.raises(ValueError):
        make_effect({"type": "reverb", "params": {"nope": 1}})
    with pytest.raises(ValueError):
        make_effect({"type": "ladder", "params": {"mode": "XYZ"}})


def test_builtin_fx_changes_sound_and_is_repeatable():
    dry = render_audio(one())
    p = one()
    p.channels[0].fx = [{"type": "reverb", "params": {"room_size": 0.9}}]
    wet1, wet2 = render_audio(p), render_audio(p)
    assert not np.allclose(dry, wet1)
    assert hashlib.sha256(wet1.tobytes()).digest() == hashlib.sha256(wet2.tobytes()).digest()


def test_bypass():
    p = one()
    p.channels[0].fx = [{"type": "gain", "params": {"gain_db": -20}, "bypass": True}]
    assert np.allclose(render_audio(p), render_audio(one()))


@pytest.mark.skipif(not os.path.exists(LSP), reason="LSP not installed")
def test_lsp_plugin_by_name():
    p = one()
    p.master_fx = [{"type": "plugin", "plugin": LSP, "name": "Limiter Stereo"}]
    assert rms(render_audio(p)) > 0.001


# ---- mixer routing ------------------------------------------------------------

def test_bus_volume_and_mute():
    direct = render_audio(one())
    p = one(output="b")
    p.buses = [Bus("b", volume=0.5)]
    assert rms(render_audio(p)) == pytest.approx(rms(direct) * 0.5, rel=1e-3)
    p.buses[0].mute = True
    assert rms(render_audio(p)) == 0.0


def test_send_adds_signal():
    direct = render_audio(one())
    p = one(sends={"b": 1.0})
    p.buses = [Bus("b")]
    assert rms(render_audio(p)) == pytest.approx(rms(direct) * 2, rel=1e-3)


def test_bus_chain_and_loop():
    p = one(output="a1")
    p.buses = [Bus("a2", volume=0.5), Bus("a1", output="a2")]
    assert [b.name for b in p.bus_order()] == ["a1", "a2"]
    assert rms(render_audio(p)) == pytest.approx(rms(render_audio(one())) * 0.5, rel=1e-3)
    p.buses[0].output = "a1"
    with pytest.raises(ProjectError):
        p.validate()


def test_routing_validation():
    with pytest.raises(ProjectError):
        one(output="nobus").validate()
    with pytest.raises(ProjectError):
        one(sends={"nobus": 0.5}).validate()


# ---- instruments --------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("fluidsynth"), reason="fluidsynth not installed")
def test_gm_piano_and_drums():
    assert rms(render_audio(one("gm", program=0))) > 0.001
    p = one("gm", bank=128)
    p.patterns[0].notes["a"] = [Note(36, 0, 0.5), Note(38, 1, 0.5)]
    assert rms(render_audio(p)) > 0.001


@has_surge
def test_surge_patch_loads_and_differs():
    default = render_audio(one("plugin", plugin=SURGE))
    patched = render_audio(one("plugin", plugin=SURGE, patch="Basses/Attacky"))
    assert rms(default) > 0.001 and rms(patched) > 0.001
    assert abs(rms(patched) - rms(default)) / rms(default) > 0.2


@has_surge
def test_note_at_zero_survives_patch_load():
    # Surge drops notes in the first block after a patch load; the pre-roll must fix it
    a = render_audio(one("plugin", plugin=SURGE, patch="Basses/Attacky"))
    assert np.abs(a[:, :4410]).max() > 0.01


@has_surge
def test_patch_not_found():
    with pytest.raises(ProjectError):
        render_audio(one("plugin", plugin=SURGE, patch="No/Such Patch"))


# ---- CLI ------------------------------------------------------------------------

def test_cli_fx_bus_flow(tmp_path):
    f = str(tmp_path / "s.muz.json")
    main(["new", f])
    assert main(["add-channel", f, "keys", "--inst", "piano", "--vol", "-6dB"]) == 0
    assert main(["add-bus", f, "verb"]) == 0
    assert main(["add-fx", f, "verb", "reverb", "--param", "wet_level=1", "--param", "dry_level=0"]) == 0
    assert main(["send", f, "keys", "verb", "0.3"]) == 0
    assert main(["add-fx", f, "keys", "gain", "--param", "gain_db=-3dB"]) == 0
    assert main(["add-fx", f, "keys", "highpass", "--param", "cutoff_frequency_hz=100Hz", "--at", "0"]) == 0
    p = load(f)
    assert [x["type"] for x in p.channel("keys").fx] == ["highpass", "gain"]
    assert main(["move-fx", f, "keys", "0", "1"]) == 0
    assert main(["set-fx", f, "keys", "0", "--bypass"]) == 0
    p = load(f)
    assert [x["type"] for x in p.channel("keys").fx] == ["gain", "highpass"]
    assert p.channel("keys").fx[0]["bypass"] is True
    assert p.channel("keys").volume == pytest.approx(0.501187, abs=1e-6)
    assert main(["set-fx", f, "keys", "5", "--on"]) == 1
    assert main(["add-fx", f, "keys", "reverb", "--param", "nope=1"]) == 1
    assert main(["set-bus", f, "verb", "--out", "verb"]) == 1
    assert main(["del-bus", f, "verb"]) == 0
    assert load(f).channel("keys").sends == {}
    assert main(["undo", f]) == 0
    assert load(f).channel("keys").sends == {"verb": 0.3}


def test_cli_lists(capsys):
    assert main(["instruments"]) == 0
    assert "piano" in capsys.readouterr().out
    assert main(["effects"]) == 0
    assert main(["fx-params", "compressor"]) == 0
    assert "threshold_db" in capsys.readouterr().out
