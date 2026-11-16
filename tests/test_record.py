"""Step 6: quantize, MIDI record, audio record, rec2midi. All silent: no speakers, no real devices."""
import threading
import time

import mido
import numpy as np
import pytest
from scipy.io import wavfile

from muzdaw import history
from muzdaw.cli import main
from muzdaw.model import Channel, Clip, Note, Pattern, Project, ProjectError, load, save
from muzdaw.quantize import parse_grid, pattern_length_for, quantize
from muzdaw.rec2midi import put_notes, to_notes
from muzdaw.record import MidiRecorder, pick_input, record_audio

# ---- quantize ----------------------------------------------------------------


@pytest.mark.parametrize("text,beats", [("1/16", 0.25), ("1/8", 0.5), ("1/4", 1.0), ("1/1", 4.0),
                                        ("1/8t", 1 / 3), ("1/4.", 1.5), ("beat", 1.0), ("bar", 4.0),
                                        ("0.25", 0.25)])
def test_parse_grid(text, beats):
    assert parse_grid(text) == pytest.approx(beats)


@pytest.mark.parametrize("bad", ["1/3", "x", "0", "1/128", "99"])
def test_parse_grid_bad(bad):
    with pytest.raises(ProjectError):
        parse_grid(bad)


def test_quantize_full_half_swing_ends():
    notes = [Note(60, 0.27, 0.2), Note(62, 0.9, 0.3), Note(64, 0.05, 0.1)]
    full = quantize(notes, 0.25)
    assert [(n.pitch, n.start) for n in full] == [(64, 0.0), (60, 0.25), (62, 1.0)]
    assert [n.length for n in full] == [0.1, 0.2, 0.3]  # lengths kept without --ends
    half = quantize([Note(60, 0.3, 0.2)], 0.25, strength=0.5)
    assert half[0].start == pytest.approx(0.275)
    sw = quantize([Note(60, 0.0, 0.1), Note(61, 0.26, 0.1), Note(62, 0.5, 0.1)], 0.25, swing=1.0)
    assert [n.start for n in sw] == [0.0, 0.375, 0.5]  # 2nd step pushed half a step
    ends = quantize([Note(60, 0.27, 0.1), Note(61, 1.0, 0.6)], 0.25, ends=True)
    assert [(n.start, n.length) for n in ends] == [(0.25, 0.25), (1.0, 0.5)]  # min one step
    with pytest.raises(ProjectError):
        quantize(notes, 0.25, strength=2)


def test_pattern_length_for():
    assert pattern_length_for([]) == 4.0
    assert pattern_length_for([Note(60, 3.5, 1)]) == 8.0
    assert pattern_length_for([Note(60, 0, 4)]) == 4.0


def proj(tmp_path):
    p = Project(name="t", tempo=120)
    p.channels = [Channel("lead", "sine"), Channel("bass", "bass")]
    p.patterns = [Pattern("A", 4, {"lead": [Note(60, 0.07, 0.4), Note(62, 0.55, 0.4)],
                                   "bass": [Note(36, 1.1, 0.5)]})]
    p.playlist = [Clip("A", 0)]
    f = tmp_path / "s.muz.json"
    save(p, f)
    return f


def test_cli_quantize_and_undo(tmp_path, capsys):
    f = proj(tmp_path)
    assert main(["quantize", str(f), "A", "--channel", "lead", "--grid", "1/8"]) == 0
    p = load(f)
    assert [n.start for n in p.patterns[0].notes["lead"]] == [0.0, 0.5]
    assert p.patterns[0].notes["bass"][0].start == 1.1  # other channel untouched
    assert main(["quantize", str(f), "A", "--grid", "beat"]) == 0
    assert load(f).patterns[0].notes["bass"][0].start == 1.0
    history.undo(f)
    history.undo(f)
    assert load(f).patterns[0].notes["lead"][0].start == 0.07
    assert main(["quantize", str(f), "A", "--grid", "1/3"]) == 1
    assert main(["quantize", str(f), "A", "--channel", "nope"]) == 1


# ---- rec2midi helpers (no model) ---------------------------------------------

def test_to_notes_and_put_notes(tmp_path):
    ev = [(0.5, 1.0, 60, 1.0), (0.1, 0.3, 50, 0.0), (1.25, 1.5, 64, 0.5)]
    notes = to_notes(ev, tempo=120, offset_s=0.5)  # 0.5 s count-in
    assert [(n.pitch, n.start, n.length) for n in notes] == [(60, 0.0, 1.0), (64, 1.5, 0.5)]  # 50 dropped
    assert notes[0].velocity == 127 and 40 < notes[1].velocity < 127
    p = load(proj(tmp_path))
    put_notes(p, "B", "lead", [Note(60, 6.0, 1.0)])
    assert p.pattern("B").length == 8.0
    put_notes(p, "A", "lead", [Note(70, 0, 1)], replace_old=True)
    assert [n.pitch for n in p.pattern("A").notes["lead"]] == [70]
    with pytest.raises(ProjectError):
        put_notes(p, "A", "nope", [])


def test_rec2midi_errors(tmp_path, monkeypatch):
    f = proj(tmp_path)
    assert main(["rec2midi", str(f), str(tmp_path / "missing.wav"), "lead", "A"]) == 1
    wav = tmp_path / "x.wav"
    wavfile.write(str(wav), 22050, np.zeros(2205, np.int16))
    assert main(["rec2midi", str(f), str(wav), "lead", "A", "--onset", "2"]) == 1
    monkeypatch.setenv("MUZDAW_PITCH_PYTHON", str(tmp_path / "no-python"))
    assert main(["rec2midi", str(f), str(wav), "lead", "A"]) == 1


# ---- rec2midi with the real model (offline render -> WAV -> notes) -------------

def test_rec2midi_roundtrip(tmp_path, capsys):
    """Render known notes offline, detect them, compare after quantize."""
    from muzdaw.render import render
    src = Project(name="src", tempo=120)
    src.channels = [Channel("lead", "sine", volume=0.9)]
    want = [(60, 0.0), (64, 1.0), (67, 2.0), (72, 3.0)]
    src.patterns = [Pattern("A", 4, {"lead": [Note(p, s, 0.75, 100) for p, s in want]})]
    src.playlist = [Clip("A", 0)]
    wav = tmp_path / "take.wav"
    render(src, wav)
    f = proj(tmp_path)
    assert main(["rec2midi", str(f), str(wav), "bass", "REC", "--quantize", "1/4"]) == 0
    assert "4 notes" in capsys.readouterr().out
    got = [(n.pitch, n.start) for n in load(f).pattern("REC").notes["bass"]]
    assert got == want


# ---- MIDI record (virtual port, no sound) ------------------------------------

@pytest.fixture
def vport():
    out = mido.open_output("muzdaw-test-kbd", virtual=True)
    time.sleep(0.05)
    yield out
    out.close()


def test_pick_input(vport):
    name = pick_input("muzdaw-test-kbd")
    assert "muzdaw-test-kbd" in name
    with pytest.raises(ProjectError):
        pick_input("no-such-keyboard")


def test_midi_recorder_timing(vport):
    rec = MidiRecorder(pick_input("muzdaw-test-kbd"), tempo=120)  # 1 beat = 0.5 s
    rec.open()
    vport.send(mido.Message("note_on", note=50, velocity=80))  # before start: dropped
    vport.send(mido.Message("note_off", note=50))
    time.sleep(0.05)
    rec.start()
    t0 = time.perf_counter()

    def at(sec, msg):
        time.sleep(max(0.0, t0 + sec - time.perf_counter()))
        vport.send(msg)

    at(0.25, mido.Message("note_on", note=60, velocity=90))   # beat 0.5
    at(0.50, mido.Message("note_off", note=60))                # len 0.5
    at(0.50, mido.Message("note_on", note=64, velocity=70))   # beat 1.0, held to stop
    time.sleep(max(0.0, t0 + 0.75 - time.perf_counter()))
    notes = rec.stop()
    assert [(n.pitch, n.velocity) for n in notes] == [(60, 90), (64, 70)]
    assert notes[0].start == pytest.approx(0.5, abs=0.06)
    assert notes[0].length == pytest.approx(0.5, abs=0.06)
    assert notes[1].start == pytest.approx(1.0, abs=0.06)
    assert notes[1].length == pytest.approx(0.5, abs=0.08)  # closed at stop
    assert rec.stop() == notes  # second stop changes nothing


def test_cli_record_midi(tmp_path, vport, capsys):
    f = proj(tmp_path)
    p = load(f)
    p.tempo = 240  # 1 bar = 1 s
    save(p, f)
    res = {}
    th = threading.Thread(target=lambda: res.setdefault("rc", main(
        ["record-midi", str(f), "lead", "REC", "--port", "muzdaw-test-kbd", "--bars", "1",
         "--count-in", "0", "--quantize", "1/4"])))
    th.start()
    time.sleep(0.3)  # ~beat 1.2 -> quantized to beat 1
    vport.send(mido.Message("note_on", note=67, velocity=100))
    time.sleep(0.2)
    vport.send(mido.Message("note_off", note=67))
    th.join(5)
    assert res["rc"] == 0
    assert "1 notes" in capsys.readouterr().out
    n = load(f).pattern("REC").notes["lead"]
    assert [(x.pitch, x.start) for x in n] == [(67, 1.0)]
    history.undo(f)
    with pytest.raises(ProjectError):
        load(f).pattern("REC")


def test_cli_record_midi_nothing_played(tmp_path, vport, capsys):
    f = proj(tmp_path)
    before = f.read_text()
    p = load(f)
    p.tempo = 960  # 1 bar = 0.25 s
    save(p, f)
    before = f.read_text()
    assert main(["record-midi", str(f), "lead", "REC", "--port", "muzdaw-test-kbd",
                 "--bars", "1", "--count-in", "0"]) == 1
    assert "no notes recorded" in capsys.readouterr().err
    assert f.read_text() == before


# ---- audio record (fake input) -------------------------------------------------

def test_record_audio_fake_input(tmp_path):
    def fake(frames, sr, ch, dev):
        t = np.arange(frames) / sr
        return np.repeat((0.5 * np.sin(2 * np.pi * 440 * t))[:, None], ch, axis=1)

    path, peak = record_audio(tmp_path / "a.wav", 0.5, 22050, 2, None, rec=fake)
    sr, data = wavfile.read(str(path))
    assert sr == 22050 and data.shape == (11025, 2) and data.dtype == np.int16
    assert peak == pytest.approx(0.5, abs=0.01)
    with pytest.raises(ProjectError):
        record_audio(tmp_path / "b.wav", 0, rec=fake)
    with pytest.raises(ProjectError):
        record_audio(tmp_path / "b.wav", 1, channels=3, rec=fake)


def test_cli_record_audio_refuses_overwrite(tmp_path):
    out = tmp_path / "a.wav"
    out.write_bytes(b"x")
    assert main(["record-audio", str(out), "--seconds", "1"]) == 1
    assert out.read_bytes() == b"x"
