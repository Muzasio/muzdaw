"""Sample channels + audio clips: model, render, CLI. Silent (offline render only)."""
import numpy as np
import pytest

from muzdaw import samples
from muzdaw.cli import main
from muzdaw.model import AudioClip, Channel, Clip, Note, Pattern, Project, ProjectError, load, save
from muzdaw.render import render_audio


def tone(path, sr=22050, secs=0.5, hz=440.0, amp=0.5):
    from pedalboard.io import AudioFile
    t = np.arange(int(sr * secs)) / sr
    with AudioFile(str(path), "w", samplerate=sr, num_channels=1) as f:
        f.write((amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)[None, :])
    return path


def test_load_resample_pitch_and_errors(tmp_path):
    w = tone(tmp_path / "a.wav")
    a = samples.load(w, 44100)
    assert a.shape == (2, 22050) and abs(a).max() == pytest.approx(0.5, abs=0.02)
    assert samples.pitched(a, 12).shape[1] == 11025 and samples.pitched(a, -12).shape[1] == 44100
    assert samples.duration(w) == pytest.approx(0.5, abs=1e-3)
    assert samples.duration(tmp_path / "gone.wav") == 0.0
    with pytest.raises(ProjectError):
        samples.check_path(tmp_path / "gone.wav")
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ProjectError):
        samples.check_path(tmp_path / "x.txt")


def song(tmp_path):
    w = str(tone(tmp_path / "hit.wav"))
    p = Project(tempo=120, sample_rate=44100)
    p.channels = [Channel("hit", "sample", sample=w, root=60, volume=1.0)]
    p.patterns = [Pattern("A", 4, {"hit": [Note(60, 0, 0.25, 127), Note(60, 2, 0.25, 64)]})]
    p.playlist = [Clip("A", 0)]
    return p, w


def test_sample_channel_renders_at_note_times_with_velocity(tmp_path):
    p, _ = song(tmp_path)
    a = render_audio(p)
    sr = 44100
    first = abs(a[:, : sr // 4]).max()
    second = abs(a[:, sr: sr + sr // 4]).max()  # beat 2 = 1.0 s
    silence = abs(a[:, int(0.6 * sr): int(0.9 * sr)]).max()
    assert first > 0.3 and silence < 1e-4
    assert second == pytest.approx(first * 64 / 127, rel=0.05)


def test_audio_clip_position_gain_and_song_length(tmp_path):
    p, w = song(tmp_path)
    p.channels[0].mute = True
    p.audio_clips = [AudioClip(w, 4.0, 2, 0.5)]  # beat 4 = 2.0 s
    assert p.song_length() == pytest.approx(5.0)  # 4 beats + 0.5 s (= 1 beat)
    a = render_audio(p)
    sr = 44100
    assert abs(a[:, : 2 * sr - 10]).max() < 1e-6
    assert abs(a[:, 2 * sr: 2 * sr + 2000]).max() > 0.1


def test_model_roundtrip_and_old_files(tmp_path):
    p, w = song(tmp_path)
    p.audio_clips = [AudioClip(w, 1.0)]
    f = tmp_path / "s.muz.json"
    save(p, f)
    assert load(f) == p and p.clone() == p
    old = Project(channels=[Channel("x")]).to_dict()
    old.pop("audio_clips")
    for c in old["channels"]:
        c.pop("sample"), c.pop("root")
    assert Project.from_dict(old).audio_clips == []  # files from before still load
    with pytest.raises(ProjectError):
        Channel("s", "sample").validate()  # no file
    with pytest.raises(ProjectError):
        AudioClip(str(tmp_path / "a.txt"), 0).validate()


def test_cli_sample_channel_and_audio_clips(tmp_path, capsys):
    w = tone(tmp_path / "kick.wav")
    f = tmp_path / "s.muz.json"
    assert main(["new", str(f)]) == 0
    assert main(["add-channel", str(f), "kick", "--sample", str(w), "--root", "C3"]) == 0
    c = load(f).channel("kick")
    assert c.instrument == "sample" and c.root == 48 and c.sample == str(w.resolve())
    assert main(["add-audio", str(f), str(w), "8", "--track", "3", "--gain", "-6dB"]) == 0
    a = load(f).audio_clips[0]
    assert (a.start, a.track) == (8.0, 3) and a.gain == pytest.approx(0.501, abs=1e-3)
    assert main(["info", str(f)]) == 0
    out = capsys.readouterr().out
    assert "sample kick.wav root 48" in out and "audio clips:" in out
    assert main(["del-audio", str(f), "0"]) == 0 and load(f).audio_clips == []
    assert main(["del-audio", str(f), "5"]) == 1
    assert main(["add-channel", str(f), "bad", "--sample", str(tmp_path / "nope.wav")]) == 1
    assert main(["add-channel", str(f), "bad2", "--root", "C4"]) == 1  # root without sample
