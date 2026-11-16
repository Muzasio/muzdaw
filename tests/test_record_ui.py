"""Typing-keyboard recording (fake clock), window memory, Playlist double-click. Offscreen + silent."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from muzdaw import history  # noqa: E402
from muzdaw.model import Channel, Clip, Note, Pattern, Project, load, save  # noqa: E402
from muzdaw.ui.recorder import TakeRecorder  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_recorder_count_in_wrap_clamp_quantize(app):
    clk = FakeClock()
    r = TakeRecorder(clock=clk)
    clicks, states = [], []
    r.click.connect(clicks.append)
    r.state.connect(states.append)
    r.start(tempo=120, length_beats=4, count_in=4)  # 1 beat = 0.5 s; beat 0 at t = 102
    assert clicks == [True] and states[-1] == "count-in 4"
    for k in range(1, 4):
        clk.t = 100 + 0.5 * k
        r.tick()
    assert clicks == [True, False, False, False] and states[-1] == "count-in 1"
    clk.t = 101.0
    r.note_on(50, 90)            # during the count-in: ignored
    clk.t = 102.26               # beat 0.52
    r.note_on(60, 100)
    clk.t = 102.5                # beat 1.0
    r.note_off(60)
    clk.t = 104.0 + 0.24         # beat 4.48 -> wraps to 0.48
    r.note_on(64, 80)
    clk.t = 104.5
    r.note_off(64)
    clk.t = 103.75               # (time going back is impossible; just set up next note)
    clk.t = 105.75               # beat 7.5 = 3.5 in the pattern, held past the end
    r.note_on(67, 70)
    clk.t = 107.0                # beat 10: stop with the key still down
    raw = r.stop(None)
    assert [(n.pitch, round(n.start, 2), round(n.length, 2), n.velocity) for n in raw] == [
        (64, 0.48, 0.52, 80), (60, 0.52, 0.48, 100), (67, 3.5, 0.5, 70)]  # 67 clamped to the pattern end
    assert not r.active and states[-1] == "idle"
    r.start(tempo=120, length_beats=4, count_in=0)
    clk.t += 0.26
    r.note_on(60, 100)
    clk.t += 0.2
    r.note_off(60)
    q = r.stop(0.25)
    assert [(n.pitch, n.start) for n in q] == [(60, 0.5)]


@pytest.fixture
def win(app, tmp_path):
    from muzdaw.ui.app import MainWindow
    p = Project(tempo=120)
    p.channels = [Channel("keys", "sine")]
    p.patterns = [Pattern("A", 4, {"keys": [Note(72, 3, 1)]}), Pattern("B", 4)]
    p.playlist = [Clip("A", 0), Clip("B", 4)]
    f = tmp_path / "s.muz.json"
    save(p, f)
    w = MainWindow(f)
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    yield w, f
    w.piano.previewer.close()
    w.close()
    w.deleteLater()
    app.processEvents()


def wait_until(app, cond, secs=60):
    import time
    t = time.monotonic()
    while not cond() and time.monotonic() - t < secs:
        app.processEvents()
        time.sleep(0.01)
    assert cond()


def test_record_take_from_typing_keys(win, app):
    """Rec: the engine (real process, silent) plays the pattern with a count-in; the recorder
    takes beat 0 from the engine clock. Then a fake clock makes the note times exact."""
    import time
    w, f = win
    w.doc.select(pattern="A", channel="keys")
    QTest.mouseClick(w.rec_btn, Qt.MouseButton.LeftButton)
    assert w.recorder.active and w.recorder.waiting and w.rec_btn.isChecked()
    assert not w.song_btn.isChecked()  # recording always plays the pattern (PAT)
    wait_until(app, lambda: not w.recorder.waiting)  # engine said "started"
    t0 = w.recorder.t0
    assert t0 - time.monotonic() > 1.0  # 4-beat count-in at 120 BPM = 2 s before beat 0
    w._tick()
    assert "count-in" in w.pos_label.text()
    clk = FakeClock()
    clk.t = t0 + 0.5             # beat 1.0
    w.recorder.clock = clk
    w.typing.noteOn.emit(60, 100)
    clk.t += 0.5
    w.typing.noteOff.emit(60)
    w.typing.noteOn.emit(64, 100)
    clk.t += 0.26
    w._space()                   # Space stops the recording (key still held -> closed at stop)
    assert not w.recorder.active and not w.rec_btn.isChecked()
    notes = [(n.pitch, n.start) for n in load(f).pattern("A").notes["keys"]]
    assert notes == [(60, 1.0), (64, 2.0), (72, 3.0)]
    assert w.piano.grid.selected == {0, 1}  # the take is selected
    label = history.undo(f)
    assert label.startswith("record 2 notes") and len(load(f).pattern("A").notes["keys"]) == 1


def test_record_needs_channel(win, app):
    w, f = win
    w.doc.project.channels.clear()  # simulate an empty song in memory
    w.doc.channel = None
    w.start_record()
    assert not w.recorder.active and not w.rec_btn.isChecked()


def test_window_memory_and_playlist_double_click(win, app, tmp_path):
    from muzdaw.ui.app import MainWindow
    from muzdaw.ui import prefs
    w, f = win
    g = w.playlist.grid
    clip = w.doc.project.playlist[1]  # pattern B
    r = g.clip_rect(clip)
    QTest.mouseDClick(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      QPoint(int(r.center().x()), int(r.center().y())))
    app.processEvents()
    assert w.doc.pattern == "B" and w.docks["piano"].isVisible()
    w.resize(760, 520)  # fits the offscreen test screen (Qt keeps windows on screen)
    w.docks["mixer"].raise_()
    app.processEvents()
    w.close()
    app.processEvents()
    assert isinstance(prefs.get("window_geometry"), str) and isinstance(prefs.get("window_state"), str)
    w2 = MainWindow(f)
    w2.show()
    app.processEvents()
    assert (w2.width(), w2.height()) == (760, 520)
    w2.piano.previewer.close()
    w2.close()
    w2.deleteLater()
    app.processEvents()
