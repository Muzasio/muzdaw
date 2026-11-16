"""Samples in the window: + sample / drop on the Channel Rack, audio clips on the Playlist
(drop, move, right-click remove), and playback = export with samples. Offscreen + silent."""
import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from muzdaw.model import AudioClip, Channel, Clip, Note, Pattern, Project, load, save  # noqa: E402


def tone(path, secs=1.0):
    from pedalboard.io import AudioFile
    sr = 22050
    t = np.arange(int(sr * secs)) / sr
    with AudioFile(str(path), "w", samplerate=sr, num_channels=1) as f:
        f.write((0.5 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)[None, :])
    return path


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path):
    from muzdaw.ui.app import MainWindow
    p = Project(tempo=120)
    p.channels = [Channel("keys", "sine")]
    p.patterns = [Pattern("A", 4, {"keys": [Note(60, 0, 1)]})]
    p.playlist = [Clip("A", 0)]
    f = tmp_path / "s.muz.json"
    save(p, f)
    w = MainWindow(f)
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    yield w, f, tmp_path
    w.piano.previewer.close()
    w.close()
    w.deleteLater()
    app.processEvents()


def drop(widget, pos, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    ev = QDropEvent(QPointF(pos), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier)
    widget.dropEvent(ev)


def test_add_sample_button_path_and_drop(win, app):
    w, f, tmp = win
    kick = tone(tmp / "Kick 01.wav", 0.3)
    assert w.rack.add_sample(str(kick))
    c = load(f).channel("Kick 01")
    assert c.instrument == "sample" and c.root == 60 and w.doc.channel == "Kick 01"
    drop(w.rack, QPoint(20, 20), [kick, tmp / "notes.txt"])  # second one is not audio: refused
    names = [c.name for c in load(f).channels]
    assert names == ["keys", "Kick 01", "Kick 01-2"]
    # a step on a sample row uses its root note
    w.rack.toggle("Kick 01", 0)
    assert load(f).pattern("A").notes["Kick 01"][0].pitch == 60


def test_playlist_drop_move_remove_audio_clip(win, app):
    w, f, tmp = win
    music = tone(tmp / "music.wav", 2.0)  # 2 s = 4 beats at 120 BPM
    g = w.playlist.grid
    drop(g, QPoint(int(g.x_of(8.4)), int(20 + 2 * 34 + 10)), [music])
    a = load(f).audio_clips[0]
    assert (a.start, a.track) == (8.0, 2)
    r = g.audio_rect(a)
    assert r.width() == pytest.approx(4 * g.px_per_beat - 2)
    assert len(g.wave(a.path)) == 200  # 10 ms steps
    start = QPoint(int(r.left() + 5), int(r.center().y()))
    end = QPoint(start.x() + int(4 * g.px_per_beat), start.y() + 34)
    QTest.mousePress(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(g, end)
    QTest.mouseRelease(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    a = load(f).audio_clips[0]
    assert (a.start, a.track) == (12.0, 3)
    assert load(f).song_length() == pytest.approx(16.0)  # the clip ends at beat 16
    r = g.audio_rect(a)
    QTest.mouseClick(g, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(int(r.left() + 5), int(r.center().y())))
    assert load(f).audio_clips == []


def test_playback_with_samples_equals_export(tmp_path):
    from muzdaw.engine import Builder
    from muzdaw.render import render_audio
    hit = str(tone(tmp_path / "hit.wav", 0.3))
    p = Project(tempo=120)
    p.channels = [Channel("hit", "sample", sample=hit, root=60), Channel("lead", "saw")]
    p.patterns = [Pattern("A", 4, {"hit": [Note(60, 0, 0.25), Note(67, 2, 0.25)], "lead": [Note(64, 1, 1)]})]
    p.playlist = [Clip("A", 0)]
    p.audio_clips = [AudioClip(hit, 2.0, 1, 0.8)]
    take = Builder().build(p, "song", None, 0, False, False, 0)
    assert np.array_equal(take.buf, render_audio(p))
