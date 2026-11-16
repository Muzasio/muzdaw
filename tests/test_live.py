"""Live engine tests. Always silent (audible=False): nothing goes to the speakers.

Each live session runs in a fresh process so Carla engines never share state
between tests.
"""
import multiprocessing as mp
import os
import time

import pytest

from muzdaw.live import LiveError, LiveSession, live_fx, lsp_lv2_uri
from muzdaw.model import Bus, Channel, Clip, Note, Pattern, Project

pytestmark = pytest.mark.skipif(not os.path.exists("/usr/lib/carla/libcarla_standalone2.so"),
                                reason="Carla not installed")
SURGE = "/usr/lib/vst3/Surge XT.vst3"


def song(**ch) -> Project:
    p = Project(tempo=120)
    p.channels = [Channel("a", **({"instrument": "gm", "program": 0} | ch))]
    p.patterns = [Pattern("A", 2, {"a": [Note(60, 0, 0.5), Note(64, 1, 0.5)]})]
    p.playlist = [Clip("A", 0)]
    return p


def isolated(fn, *args):
    with mp.get_context("spawn").Pool(1) as pool:
        return pool.apply(fn, args)


def test_live_fx_mapping():
    t, uri, v = live_fx({"type": "compressor", "params": {"threshold_db": -18, "ratio": 4}})
    assert uri.endswith("compressor_stereo") and v["al"] == pytest.approx(0.1259, abs=1e-3) and v["cr"] == 4
    assert live_fx({"type": "eq-high", "params": {"gain_db": 6}})[2]["g_0"] == pytest.approx(1.995, abs=1e-3)
    assert live_fx({"type": "reverb"})[1] == "urn:dragonfly:room"
    assert live_fx({"type": "bitcrush"}) is None


def test_lsp_uri():
    assert lsp_lv2_uri("Limiter Stereo").endswith("/limiter_stereo")
    assert lsp_lv2_uri("No Such Plugin") is None


def _play_silent():
    p = song(fx=[{"type": "reverb"}], sends={"b": 0.5})
    p.buses = [Bus("b", fx=[{"type": "compressor", "params": {"threshold_db": -12}}])]
    p.master_fx = [{"type": "plugin", "plugin": "/usr/lib/vst3/lsp-plugins.vst3", "name": "Limiter Stereo"}]
    with LiveSession(p, audible=False) as s:
        speakers, warnings = s.speakers_connected(), list(s.warnings)
        s.play()
        return speakers, warnings, s.run()


def test_play_silent_has_signal():
    speakers, warnings, peaks = isolated(_play_silent)
    assert not speakers and warnings == []
    assert peaks["a"] > 0.01 and peaks["master"] > 0.01


def _loop():
    p = song()
    late = []
    with LiveSession(p, audible=False, loop=True) as s:
        s.play()
        t0 = time.monotonic()

        def tick(sess):
            if time.monotonic() - t0 > 1.3:  # song is 1 s: this is the second pass
                late.append(sess.peak(sess.strips["a"]))
        s.run(seconds=2.0, on_tick=tick)
    return max(late)


def test_loop_plays_again():
    assert isolated(_loop) > 0.01


def _metronome():
    p = song()
    p.channels[0].mute = True
    with LiveSession(p, audible=False, metronome=True) as s:
        s.play()
        return s.run()["master"]


def test_metronome_only():
    assert isolated(_metronome) > 0.01


def _standin():
    p = song(instrument="saw")
    p.channels.append(Channel("b", "plugin", plugin=SURGE, patch="Basses/Attacky"))
    p.patterns[0].notes["b"] = [Note(36, 0, 1)]
    with LiveSession(p, audible=False) as s:
        standin = any("stand-in" in w for w in s.warnings)
        s.play()
        return standin, s.run()


@pytest.mark.skipif(not os.path.exists(SURGE), reason="Surge XT not installed")
def test_builtin_standin_and_surge_patch():
    standin, peaks = isolated(_standin)
    assert standin and peaks["a"] > 0.01 and peaks["b"] > 0.01


def _bad_keyboard():
    try:
        with LiveSession(song(), audible=False, keyboard="nope"):
            return "no error"
    except LiveError as e:
        return str(e)


def test_bad_keyboard_channel():
    assert "nope" in isolated(_bad_keyboard)
