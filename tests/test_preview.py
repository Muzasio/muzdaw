"""Piano key preview sampler. Silent: play=False never opens the sound output."""
import json
from dataclasses import asdict

import numpy as np

from muzdaw.model import Channel
from muzdaw.preview import FADE_SEC, Server, Voice, render_note, vel_level
from muzdaw.presets import PRESETS


def on(ch, pitch=60, vel=100, vid=1):
    return json.dumps({"cmd": "on", "id": vid, "channel": asdict(ch), "pitch": pitch, "velocity": vel,
                       "sr": 44100, "master": 0.8})


def test_render_note_follows_volume_and_velocity():
    loud = render_note(Channel("a", "sine", volume=1.0), 69)
    quiet = render_note(Channel("a", "sine", volume=0.25), 69)
    assert loud.shape[0] == 2 and abs(loud).max() > 0.05
    assert abs(quiet).max() < abs(loud).max() * 0.3
    piano = Channel("p", **PRESETS["piano"])
    soft, hard = render_note(piano, 60, velocity=30), render_note(piano, 60, velocity=127)
    assert abs(hard).max() > abs(soft).max() * 1.5  # hard hit is clearly louder


def test_vel_level():
    assert vel_level(1) == 8 and vel_level(127) == 127 and vel_level(100) == 96


def test_voice_release_fades_and_ends():
    sr = 1000
    v = Voice(np.ones((2, 5000), np.float32), sr)
    assert np.all(v.take(100) == 1.0)  # held: full level
    v.release()
    tail = v.take(1000)
    assert tail[0, 0] == 1.0 and tail[-1, 0] == 0.0  # fades to silence
    assert v.done()  # ends after the fade, not after 5000 samples
    assert len(tail[tail[:, 0] > 0]) == int(FADE_SEC * sr)


def test_held_note_lasts_short_click_ends():
    """The rendered note keeps sounding for seconds (hold); release cuts it within the fade."""
    a = render_note(Channel("a", "sine"), 69, sr=8000)
    late = a[:, int(3.0 * 8000):int(3.5 * 8000)]
    assert abs(late).max() > 0.05  # still sounding 3 s in while held


def test_server_on_off_cache_errors():
    srv = Server(play=False)
    ch = Channel("keys", "sine")
    first, second = srv.handle(on(ch, vid=1)), srv.handle(on(ch, vid=2))
    assert first.startswith("ok on 60 96") and first.endswith("new")
    assert second.endswith("cached")
    assert srv.handle(on(ch, vel=30, vid=3)).endswith("new")  # other velocity = other sample
    assert srv.handle('{"cmd": "off", "id": 1}') == "ok off 1"
    assert srv.mixer.voices[1].fade_pos == 0
    assert "already ended" in srv.handle('{"cmd": "off", "id": 99}')
    assert srv.handle("not json").startswith("error")
    assert srv.handle('{"cmd": "jump"}').startswith("error")
    assert srv.handle(on(ch, pitch=200)).startswith("error")
    assert srv.handle(on(Channel("x", "sine", volume=9))).startswith("error")


def test_gm_channel_preview():
    srv = Server(play=False)
    out = srv.handle(on(Channel("piano", **PRESETS["piano"])))
    assert out.startswith("ok on 60"), out
    assert float(out.split()[5]) > 0.01


def test_plugin_instrument_plays_live_instantly():
    """Surge goes through Carla: warm once, then note-on is instant and makes sound (silent: no speakers)."""
    import os
    import time
    import pytest
    from muzdaw.live import CARLA_LIB, SURGE
    if not (CARLA_LIB.exists() and SURGE.exists()):
        pytest.skip("Carla or Surge not installed")
    srv = Server(play=False)
    try:
        ch = Channel("lead", **PRESETS["surge-lead"])
        warm = srv.handle(json.dumps({"cmd": "warm", "channel": asdict(ch), "master": 0.8}))
        assert warm.startswith("ok warm live"), warm
        assert not srv.live.sess.speakers_connected()  # silent
        out = srv.handle(on(ch, 60, 110, vid=5))
        assert out.startswith("ok on 60 110") and out.endswith("live"), out
        assert float(out.split()[4]) < 50  # ms: no render wait
        peak = 0.0
        t = time.monotonic()
        while time.monotonic() - t < 1.5 and peak < 0.01:
            srv.live.idle()
            time.sleep(0.02)
            peak = max(peak, srv.live.peak())
        assert peak > 0.01, "no sound from Surge"
        assert srv.handle('{"cmd": "off", "id": 5}') == "ok off 5"
        # sampler instruments still use the sampler
        assert srv.handle(on(Channel("s", "sine"), 69, vid=6)).endswith(("new", "cached"))
    finally:
        srv.live.close()
        srv.mixer.close()
