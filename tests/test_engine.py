"""Playback engine: same audio as export, pattern loops, stem cache, live swap. Silent (no stream)."""
import time

import numpy as np
import pytest

from muzdaw.engine import Builder, Engine, Player
from muzdaw.model import Channel, Clip, Note, Pattern, Project, save
from muzdaw.render import render_audio


def song():
    p = Project(tempo=120)
    p.channels = [Channel("lead", "saw", volume=0.7), Channel("kick", "kick", volume=1.0)]
    p.patterns = [Pattern("A", 4, {"lead": [Note(60, 0, 1), Note(64, 1, 1), Note(67, 2, 2)],
                                   "kick": [Note(36, b, 0.25, 120) for b in range(4)]}),
                  Pattern("B", 8, {"lead": [Note(72, 0, 4)]})]
    p.playlist = [Clip("A", 0), Clip("B", 4)]
    return p


def test_song_mode_is_exactly_the_export():
    p = song()
    take = Builder().build(p, "song", None, 0.0, False, False, 0)
    ref = render_audio(p)
    assert take.buf.shape == ref.shape
    assert np.array_equal(take.buf, ref)  # what you hear = what you export


def test_pattern_mode_loops_pattern_length_with_folded_tail():
    p = song()
    take = Builder().build(p, "pattern", "A", 0.0, False, False, 0)
    assert take.loop and take.loop_beats == 4
    assert take.buf.shape[1] == round(2.0 * p.sample_rate)  # 4 beats at 120 BPM = 2 s
    take_b = Builder().build(p, "pattern", "B", 0.0, False, False, 0)
    assert take_b.buf.shape[1] == round(4.0 * p.sample_rate)


def test_stem_cache_rerenders_only_changed_channel():
    p = song()
    b = Builder()
    b.build(p, "pattern", "A", 0, True, False, 0)
    assert b.renders == 2
    b.build(p, "pattern", "A", 0, True, False, 0)
    assert b.renders == 2  # nothing changed: all from cache
    p.patterns[0].notes["lead"][0].pitch = 62
    b.build(p, "pattern", "A", 0, True, False, 0)
    assert b.renders == 3  # only "lead" again
    p.channels[0].volume = 0.2
    p.channels[0].fx = [{"type": "reverb", "params": {}, "bypass": False}]
    b.build(p, "pattern", "A", 0, True, False, 0)
    assert b.renders == 3  # mixer changes = re-mix only


def test_metronome_and_count_in():
    p = song()
    plain = Builder().build(p, "pattern", "A", 0, True, False, 0)
    clicks = Builder().build(p, "pattern", "A", 0, True, True, 4)
    assert clicks.pre.shape[1] == round(2.0 * p.sample_rate) and abs(clicks.pre).max() > 0.05
    assert not np.array_equal(plain.buf, clicks.buf)


def test_player_swap_keeps_position_and_wraps():
    p = song()
    b = Builder()
    take = b.build(p, "pattern", "A", 0, True, False, 0)
    pl = Player(play=False)
    pl.start(take)
    out = np.zeros((44100, 2), np.float32)  # pull 1 s = beat 2
    pl._callback(out, 44100, None, None)
    assert pl.pos == 44100
    p.patterns[0].notes["lead"].append(Note(72, 3, 1))
    t0, beat = pl.swap(b.build(p, "pattern", "A", 0, True, False, 0))
    assert beat == pytest.approx(2.0) and pl.pos == 44100
    out2 = np.zeros((88200, 2), np.float32)  # 2 s more: wraps around the 2 s loop
    pl._callback(out2, 88200, None, None)
    assert pl.playing and pl.pos == 44100


def test_song_mode_ends_and_reports_stopped():
    p = song()
    pl = Player(play=False)
    take = Builder().build(p, "song", None, 0, False, False, 0)
    pl.start(take, from_beat=11.0)  # 1 beat + tail before the end
    big = np.zeros((take.buf.shape[1], 2), np.float32)
    pl._callback(big, big.shape[0], None, None)
    assert not pl.playing and pl.ended


def test_engine_protocol(tmp_path):
    """The real engine process (as the window starts it): play, update, stop, errors. Silent."""
    import os
    import subprocess
    import sys
    f = tmp_path / "s.muz.json"
    save(song(), f)
    env = dict(os.environ, MUZDAW_SILENT="1")
    proc = subprocess.Popen([sys.executable, "-m", "muzdaw.engine"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
    lines = []

    def send(obj):
        import json
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def wait(prefix):
        while True:
            line = proc.stdout.readline()
            assert line, f"engine ended early: {lines}"
            line = line.strip()
            lines.append(line)
            if line.startswith(prefix):
                return line

    try:
        send({"cmd": "play", "path": str(f), "mode": "pattern", "pattern": "A"})
        assert wait("preparing") == "preparing"
        started = wait("started").split()
        assert float(started[3]) == pytest.approx(2.0) and float(started[4]) == 4.0  # 2 beats/s, 4-beat loop
        assert abs(float(started[1]) - time.monotonic()) < 5  # T0 is on this machine's monotonic clock
        p = song()
        p.patterns[0].notes["lead"].append(Note(71, 3, 0.5))
        save(p, f)
        send({"cmd": "update"})
        upd = wait("updated").split()
        assert int(upd[2]) == 1  # only the edited channel was rendered again
        send({"cmd": "stop"})
        assert wait("stopped") == "stopped"
        send({"cmd": "play", "path": str(tmp_path / "nope.muz.json"), "mode": "song"})
        assert "not found" in wait("error")
        save(Project(channels=[Channel("x")], patterns=[Pattern("A")]), tmp_path / "empty.muz.json")
        send({"cmd": "play", "path": str(tmp_path / "empty.muz.json"), "mode": "song"})
        assert "playlist is empty" in wait("error")
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
