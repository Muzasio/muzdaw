import hashlib
import json

import numpy as np
import pytest

from muzdaw import history
from muzdaw.cli import main, parse_pitch
from muzdaw.events import song_events
from muzdaw.midi_io import export_midi, import_midi
from muzdaw.model import Channel, Clip, Note, Pattern, Project, ProjectError, load, save
from muzdaw.render import render, render_audio


def song() -> Project:
    p = Project(name="t", tempo=120)
    p.channels = [Channel("lead", "saw"), Channel("kick", "kick", volume=1.0)]
    p.patterns = [Pattern("A", 4, {"lead": [Note(60, 0, 1), Note(64, 1, 1), Note(67, 2, 2)],
                                   "kick": [Note(36, b, 0.25, 120) for b in range(4)]})]
    p.playlist = [Clip("A", 0), Clip("A", 4)]
    return p


def notes_of(p: Project):
    return {ch: [(e.start, e.pitch, e.length, e.velocity) for e in evs] for ch, evs in song_events(p).items()}


# ---- model -----------------------------------------------------------------

def test_save_load_roundtrip(tmp_path):
    f = tmp_path / "s.muz.json"
    save(song(), f)
    assert load(f) == song()


@pytest.mark.parametrize("bad", [
    lambda p: setattr(p, "tempo", 5),
    lambda p: p.patterns[0].notes["lead"].append(Note(200, 0, 1)),
    lambda p: p.patterns[0].notes["lead"].append(Note(60, 0, 0)),
    lambda p: p.playlist.append(Clip("nope", 0)),
    lambda p: p.channels.append(Channel("lead")),
    lambda p: p.channels.append(Channel("x", "nosuchsynth")),
    lambda p: setattr(p.channels[0], "pan", 3),
])
def test_validation_rejects(bad):
    p = song()
    bad(p)
    with pytest.raises(ProjectError):
        p.validate()


def test_broken_file(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("{not json")
    with pytest.raises(ProjectError):
        load(f)
    f.write_text(json.dumps({"channels": [{"bogus": 1}]}))
    with pytest.raises(ProjectError):
        load(f)


def test_song_length_and_events():
    p = song()
    assert p.song_length() == 8
    ev = notes_of(p)
    assert len(ev["lead"]) == 6 and len(ev["kick"]) == 8
    assert ev["lead"][3][0] == 4  # second clip shifted by 4 beats


def test_notes_past_pattern_end_are_cut():
    p = song()
    p.patterns[0].notes["lead"].append(Note(72, 5, 1))
    assert all(e[1] != 72 for e in notes_of(p)["lead"])


@pytest.mark.parametrize("txt,val", [("60", 60), ("C4", 60), ("A4", 69), ("F#3", 54), ("Bb2", 46), ("C-1", 0)])
def test_parse_pitch(txt, val):
    assert parse_pitch(txt) == val


@pytest.mark.parametrize("txt", ["H4", "128", "-1", "C#", "G9x"])
def test_parse_pitch_bad(txt):
    with pytest.raises(ProjectError):
        parse_pitch(txt)


# ---- undo / redo -----------------------------------------------------------

def test_undo_redo(tmp_path):
    f = tmp_path / "s.muz.json"
    save(song(), f)
    a = load(f)
    b = load(f)
    b.tempo = 90
    history.commit(f, a, b, "tempo 90")
    assert load(f).tempo == 90
    history.undo(f)
    assert load(f).tempo == 120
    history.redo(f)
    assert load(f).tempo == 90
    with pytest.raises(ProjectError):
        history.redo(f)


def test_new_edit_clears_redo(tmp_path):
    f = tmp_path / "s.muz.json"
    assert main(["new", str(f)]) == 0
    main(["tempo", str(f), "100"])
    main(["undo", str(f)])
    main(["tempo", str(f), "140"])
    assert main(["redo", str(f)]) == 1


# ---- CLI -------------------------------------------------------------------

def test_cli_build_song(tmp_path):
    f = str(tmp_path / "s.muz.json")
    assert main(["new", f]) == 0
    assert main(["new", f]) == 1  # no silent overwrite
    assert main(["add-channel", f, "lead", "--inst", "pluck", "--param", "release=0.5"]) == 0
    assert main(["add-pattern", f, "A"]) == 0
    assert main(["add-note", f, "A", "lead", "C4", "0", "1"]) == 0
    assert main(["add-note", f, "A", "ghost", "C4", "0", "1"]) == 1  # unknown channel
    assert main(["place", f, "A", "0"]) == 0
    assert main(["del-note", f, "A", "lead", "60", "0"]) == 0
    assert load(f).patterns[0].notes["lead"] == []
    assert main(["undo", f]) == 0
    assert len(load(f).patterns[0].notes["lead"]) == 1


def test_failed_edit_does_not_touch_file(tmp_path):
    f = tmp_path / "s.muz.json"
    main(["new", str(f)])
    before = f.read_bytes()
    assert main(["add-channel", str(f), "x", "--param", "cutoff=1e9"]) == 1
    assert f.read_bytes() == before


# ---- MIDI --------------------------------------------------------------------

def test_midi_roundtrip(tmp_path):
    p = song()
    mid = tmp_path / "s.mid"
    assert export_midi(p, mid) == 14
    back = import_midi(mid)
    assert back.tempo == 120
    assert notes_of(back) == notes_of(p)


# ---- render ----------------------------------------------------------------

def test_render_not_silent_no_clip_and_repeatable(tmp_path):
    p = song()
    r1 = render(p, tmp_path / "a.wav")
    r2 = render(p, tmp_path / "b.wav")
    assert 0.05 < r1.peak <= 1.0 and r1.rms > 0.005
    h = [hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in ("a.wav", "b.wav")]
    assert h[0] == h[1]
    assert abs(r1.seconds - (4.0 + 2.0)) < 0.05  # 8 beats @120 + tail


def test_render_pan_and_mute():
    p = song()
    p.channels[1].mute = True
    p.channels[0].pan = -1.0
    a = render_audio(p)
    left, right = np.abs(a[0]).max(), np.abs(a[1]).max()
    assert left > 0.05 and right < left * 0.05


def test_render_empty_playlist():
    p = song()
    p.playlist = []
    with pytest.raises(ProjectError):
        render_audio(p)


# ---- real plugins (skipped when not installed) ------------------------------

SURGE = "/usr/lib/vst3/Surge XT.vst3"


@pytest.mark.skipif(not __import__("os").path.exists(SURGE), reason="Surge XT not installed")
def test_render_plugin_channel(tmp_path):
    p = song()
    p.channels[0] = Channel("lead", "plugin", plugin=SURGE)
    r = render(p, tmp_path / "s.wav")
    assert 0.01 < r.peak <= 1.0
    # Surge's default patch has random drift, so renders are close but not byte-identical
    r2 = render(p, tmp_path / "s2.wav")
    assert abs(r.rms - r2.rms) / r.rms < 0.05


def test_plugin_missing_file():
    p = song()
    p.channels[0] = Channel("lead", "plugin", plugin="/nonexistent/x.vst3")
    with pytest.raises(ProjectError):
        render_audio(p)
