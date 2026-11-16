"""Typing keyboard: computer keys -> notes. Silent (MUZDAW_SILENT) and offscreen."""
import json
import os
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from muzdaw.model import Channel, Pattern, Project, ProjectError, save  # noqa: E402
from muzdaw.ui.typing_keys import KeyLayout, config_path, key_char, load_layout, save_layout  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_default_layout_pitches():
    lay = KeyLayout()
    assert [lay.pitch_of(c) for c in "q]a'z/"] == [72, 83, 60, 70, 48, 57]
    assert lay.pitch_of("1") is None
    lay.octave = -2
    assert lay.pitch_of("q") == 48
    lay.rows[0][1] = 120  # top row runs past 127 -> those keys are silent
    lay.octave = 0
    assert lay.pitch_of("q") == 120 and lay.pitch_of("]") is None
    assert "C5" in KeyLayout().describe()[0] and "B5" in KeyLayout().describe()[0]


def test_layout_checks():
    with pytest.raises(ProjectError):
        KeyLayout(rows=[["qq", 60]]).validate()
    with pytest.raises(ProjectError):
        KeyLayout(velocity=0).validate()


def test_key_char():
    assert key_char(Qt.Key.Key_Q) == "q" and key_char(Qt.Key.Key_BracketLeft) == "["
    assert key_char(Qt.Key.Key_Apostrophe) == "'" and key_char(Qt.Key.Key_Space) is None


def test_save_load_perms_and_broken_file():
    lay = KeyLayout(octave=1, velocity=80)
    lay.rows[2][1] = 36  # bottom row from C2 (mixed octaves)
    save_layout(lay)
    assert oct(os.stat(config_path()).st_mode & 0o777) == "0o600"
    back = load_layout()
    assert (back.octave, back.velocity, back.rows[2][1]) == (1, 80, 36)
    config_path().write_text("{broken")
    assert load_layout() == KeyLayout()
    config_path().unlink()


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    from muzdaw.ui.app import MainWindow
    monkeypatch.setenv("MUZDAW_SILENT", "1")
    p = Project()
    p.channels = [Channel("keys", "sine")]
    p.patterns = [Pattern("A")]
    f = tmp_path / "t.muz.json"
    save(p, f)
    w = MainWindow(f)
    w.show()
    w.activateWindow()
    app.processEvents()
    yield w
    w.piano.previewer.close()
    w.close()
    w.deleteLater()
    app.processEvents()
    config_path().unlink(missing_ok=True)


def events(w):
    got = []
    w.typing.noteOn.connect(lambda p, v: got.append(("on", p, v)))
    w.typing.noteOff.connect(lambda p: got.append(("off", p)))
    return got


def test_keys_play_hold_release_octave(win, app):
    assert QApplication.activeWindow() is win
    got = events(win)
    target = win.piano.grid
    target.setFocus()
    QTest.keyPress(target, Qt.Key.Key_Q)
    assert 72 in win.piano.grid.held
    QTest.keyPress(target, Qt.Key.Key_Q, Qt.KeyboardModifier.NoModifier)  # still held: no 2nd note
    QTest.keyPress(target, Qt.Key.Key_A)  # chord
    QTest.keyRelease(target, Qt.Key.Key_Q)
    QTest.keyRelease(target, Qt.Key.Key_A)
    assert got == [("on", 72, 100), ("on", 60, 100), ("off", 72), ("off", 60)]
    assert not win.piano.grid.held
    QTest.keyClick(target, Qt.Key.Key_PageUp)
    assert win.typing.layout.octave == 1 and "+1 oct" in win.keys_btn.text()
    QTest.keyClick(target, Qt.Key.Key_Z)
    assert got[-2:] == [("on", 60, 100), ("off", 60)]
    assert json.loads(config_path().read_text())["octave"] == 1  # remembered
    # the preview process answered for real (silent)
    lines = []
    win.piano.previewer.result.connect(lines.append)
    QTest.keyPress(target, Qt.Key.Key_W)
    t = time.monotonic()
    while not any(x.startswith("ok on 85") for x in lines) and time.monotonic() - t < 30:
        app.processEvents()
        time.sleep(0.05)
    QTest.keyRelease(target, Qt.Key.Key_W)
    assert any(x.startswith("ok on 85") for x in lines), lines  # w = C#6 with +1 octave


def test_keys_ignored_in_text_boxes_and_when_off(win, app):
    got = events(win)
    from PySide6.QtWidgets import QLineEdit
    box = win.tempo.findChildren(QLineEdit)[0]
    box.setFocus()
    app.processEvents()
    QTest.keyClick(box, Qt.Key.Key_Q)
    assert got == []  # typed into the box instead
    win.piano.grid.setFocus()
    QTest.mouseClick(win.keys_btn, Qt.MouseButton.LeftButton)  # off
    assert not win.typing.layout.enabled and "off" in win.keys_btn.text()
    QTest.keyClick(win.piano.grid, Qt.Key.Key_Q)
    assert got == []
    win.typing.set_enabled(True)


def test_focus_loss_releases_notes(win, app):
    got = events(win)
    QTest.keyPress(win.piano.grid, Qt.Key.Key_E)
    win.typing._app_state(Qt.ApplicationState.ApplicationInactive)
    assert got == [("on", 74, 100), ("off", 74)]  # e = 3rd key of the top row = D5


def test_settings_dialog_parse(win):
    from muzdaw.ui.typing_keys import KeyboardDialog
    dlg = KeyboardDialog(win, win.typing.layout)
    dlg.starts[0].setText("C2")
    dlg.starts[2].setText("F#3")
    lay = dlg.result_layout()
    assert [r[1] for r in lay.rows] == [36, 60, 54]
    dlg.starts[1].setText("X9")
    dlg._ok()
    assert "bad note" in dlg.error.text() and dlg.result() == 0
    dlg._defaults()
    assert [b.text() for b in dlg.starts] == ["C5", "C4", "C3"]
    dlg.deleteLater()
