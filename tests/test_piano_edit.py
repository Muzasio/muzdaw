"""Piano Roll editing: select, box select, copy/paste/duplicate, arrows, delete, velocity lane,
Alt+wheel, hear-while-drawing. Real mouse/key events, offscreen, silent (conftest)."""
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from muzdaw import history  # noqa: E402
from muzdaw.model import Channel, Clip, Note, Pattern, Project, load, save  # noqa: E402

CTRL, SHIFT, ALT, NONE = (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier,
                          Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.NoModifier)
LEFT = Qt.MouseButton.LeftButton


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pr(app, tmp_path):
    from muzdaw.ui.app import MainWindow
    p = Project()
    p.channels = [Channel("keys", "sine")]
    p.patterns = [Pattern("A", 4, {"keys": [Note(60, 0, 1), Note(64, 1, 1), Note(67, 2, 1)]})]
    p.playlist = [Clip("A", 0)]
    f = tmp_path / "s.muz.json"
    save(p, f)
    w = MainWindow(f)
    w.resize(1400, 950)
    w.show()
    w.show_panel("piano")
    w.doc.select(pattern="A", channel="keys")
    app.processEvents()
    yield w, w.piano, w.piano.grid, f
    w.piano.previewer.close()
    w.close()
    w.deleteLater()
    app.processEvents()


def pos(g, beat, pitch):
    return QPoint(int(g.x_of(beat) + 4), int(g.y_of(pitch) + 6))


def pitches(f):
    return [(n.pitch, n.start) for n in load(f).pattern("A").notes["keys"]]


def test_select_all_copy_paste_continue_and_undo(pr):
    w, _, g, f = pr
    g.setFocus()
    QTest.keyClick(g, Qt.Key.Key_A, CTRL)
    assert g.selected == {0, 1, 2}
    QTest.keyClick(g, Qt.Key.Key_C, CTRL)
    QTest.keyClick(g, Qt.Key.Key_V, CTRL)
    assert pitches(f)[3:] == [(60, 3.0), (64, 4.0), (67, 5.0)]  # right after the copy (span 3 beats)
    assert load(f).pattern("A").length == 8  # pattern grew to whole bars
    assert g.selected == {3, 4, 5}  # pasted notes are selected
    QTest.keyClick(g, Qt.Key.Key_V, CTRL)  # again = continue
    assert pitches(f)[6:] == [(60, 6.0), (64, 7.0), (67, 8.0)] and load(f).pattern("A").length == 12
    history.undo(f)
    w.doc.reload()
    assert len(load(f).pattern("A").notes["keys"]) == 6  # one paste = one undo step


def test_arrows_delete_duplicate(pr):
    _, _, g, f = pr
    QTest.mouseClick(g, LEFT, NONE, pos(g, 1.2, 64))  # click E4 = select it
    assert g.selected == {1}
    QTest.keyClick(g, Qt.Key.Key_Up)
    assert (65, 1.0) in pitches(f) and g.selected == {1}
    QTest.keyClick(g, Qt.Key.Key_Down, SHIFT)
    assert (53, 1.0) in pitches(f)
    QTest.keyClick(g, Qt.Key.Key_Right)
    assert (53, 1.25) in pitches(f)
    QTest.keyClick(g, Qt.Key.Key_D, CTRL)
    assert (53, 2.25) in pitches(f)  # duplicate lands one beat later
    QTest.keyClick(g, Qt.Key.Key_Delete)
    assert (53, 2.25) not in pitches(f) and (53, 1.25) in pitches(f)
    QTest.keyClick(g, Qt.Key.Key_Escape)
    assert g.selected == set()


def test_shift_click_box_select_and_group_drag(pr):
    _, _, g, f = pr
    QTest.mouseClick(g, LEFT, NONE, pos(g, 0.2, 60))
    QTest.mouseClick(g, LEFT, SHIFT, pos(g, 2.2, 67))
    assert g.selected == {0, 2}
    QTest.mouseClick(g, LEFT, SHIFT, pos(g, 2.2, 67))
    assert g.selected == {0}
    # Ctrl+drag a box over E4 and G4
    a, b = QPoint(int(g.x_of(0.9)), int(g.y_of(70))), QPoint(int(g.x_of(3.5)), int(g.y_of(62)))
    QTest.mousePress(g, LEFT, CTRL, a)
    QTest.mouseMove(g, b)
    QTest.mouseRelease(g, LEFT, CTRL, b)
    assert g.selected == {1, 2}
    # drag E4: both selected notes move right 1 beat and up 2
    start = pos(g, 1.2, 64)
    end = QPoint(start.x() + int(g.px_per_beat), start.y() - 2 * 16)
    QTest.mousePress(g, LEFT, NONE, start)
    QTest.mouseMove(g, end)
    QTest.mouseRelease(g, LEFT, NONE, end)
    assert pitches(f) == [(60, 0.0), (66, 2.0), (69, 3.0)]
    assert g.selected == {1, 2}


def test_right_click_deletes_selection(pr):
    _, _, g, f = pr
    g.setFocus()
    QTest.keyClick(g, Qt.Key.Key_A, CTRL)
    QTest.mouseClick(g, Qt.MouseButton.RightButton, NONE, pos(g, 1.2, 64))
    assert pitches(f) == []


def test_velocity_lane_and_alt_wheel(pr, app):
    w, piano, g, f = pr
    lane = piano.lane
    x = int(g.x_of(1.0) + 2)
    QTest.mousePress(lane, LEFT, NONE, QPoint(x, 4))  # top = loudest
    QTest.mouseRelease(lane, LEFT, NONE, QPoint(x, 4))
    assert load(f).pattern("A").notes["keys"][1].velocity == 127
    QTest.mousePress(lane, LEFT, NONE, QPoint(x, 50))
    QTest.mouseRelease(lane, LEFT, NONE, QPoint(x, 50))
    v = load(f).pattern("A").notes["keys"][1].velocity
    assert 20 < v < 50
    p = QPointF(pos(g, 2.2, 67))
    ev = QWheelEvent(p, g.mapToGlobal(p), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton, ALT,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(g, ev)
    assert load(f).pattern("A").notes["keys"][2].velocity == 108


def test_hear_while_drawing(pr, app):
    w, piano, g, f = pr
    heard = []
    g.audition.connect(lambda p, v: heard.append(p))
    lines = []
    piano.previewer.result.connect(lines.append)
    QTest.mouseClick(g, LEFT, NONE, pos(g, 3.05, 72))  # add C5
    assert heard == [72] and (72, 3.0) in pitches(f)
    t = time.monotonic()
    while not any(x.startswith("ok on 72") for x in lines) and time.monotonic() - t < 30:
        app.processEvents()
        time.sleep(0.05)
    assert any(x.startswith("ok on 72") for x in lines), lines  # preview process answered (silent)
    piano.sound_btn.setChecked(False)
    QTest.mouseClick(g, LEFT, NONE, pos(g, 3.05, 74))
    assert (74, 3.0) in pitches(f)  # still added, just no sound
    from muzdaw.ui import prefs
    assert prefs.get("piano_sound") is False
    piano.sound_btn.setChecked(True)


def test_right_drag_sweep_erases(pr):
    """Hold right + sweep across C4, E4, G4 (one fast move) = all three erased, one undo step."""
    _, _, g, f = pr
    RIGHT = Qt.MouseButton.RightButton
    a, b = pos(g, 0.5, 60), pos(g, 2.5, 67)
    QTest.mousePress(g, RIGHT, NONE, a)
    QTest.mouseMove(g, pos(g, 1.5, 64))
    QTest.mouseMove(g, b)
    QTest.mouseRelease(g, RIGHT, NONE, b)
    assert pitches(f) == []
    history.undo(f)
    assert len(pitches(f)) == 3
    # a single right click still deletes just one note
    pr[0].doc.reload()
    QTest.mouseClick(g, RIGHT, NONE, pos(g, 1.5, 64))
    assert pitches(f) == [(60, 0.0), (67, 2.0)]


def test_grid_fills_panel_and_drawing_past_end_grows_pattern(pr, app):
    w, piano, g, f = pr
    app.processEvents()
    vw = piano.scroll.viewport().width()
    assert g.width() >= vw - 2  # grid fills the visible panel, not just the 4-beat pattern
    assert g.shown_beats() >= 8
    QTest.mouseClick(g, LEFT, NONE, pos(g, 6.05, 62))  # beat 6 is past the 4-beat pattern
    p = load(f).pattern("A")
    assert (62, 6.0) in [(n.pitch, n.start) for n in p.notes["keys"]] and p.length == 8
