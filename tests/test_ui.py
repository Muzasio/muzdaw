"""UI tests: offscreen (no window on the desktop), simulated mouse and keys."""
import os
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from muzdaw.model import Channel, Clip, Note, Pattern, Project, load, save  # noqa: E402
from muzdaw.ui.knobbox import gain_box, pan_box, KnobBox  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def dispose(app, *widgets):
    """Close + delete test widgets while Qt is still alive (else: crash at exit)."""
    for w in widgets:
        w.close()
        w.deleteLater()
    app.processEvents()


@pytest.fixture
def song(tmp_path):
    p = Project(tempo=120)
    p.channels = [Channel("keys", "saw"), Channel("kick", "kick")]
    p.patterns = [Pattern("A", 4, {"keys": [Note(60, 0, 1)]})]
    p.playlist = [Clip("A", 0)]
    f = tmp_path / "s.muz.json"
    save(p, f)
    return f


@pytest.fixture
def win(app, song):
    from muzdaw.ui.app import MainWindow
    w = MainWindow(song)
    w.resize(1400, 900)
    w.show()
    app.processEvents()
    yield w
    dispose(app, w)


# ---- KnobBox ----------------------------------------------------------------------

def test_knobbox_typing(app):
    g = gain_box("v", 0.8)
    got = []
    g.committed.connect(got.append)
    g.box.setText("-6dB")
    g.box.editingFinished.emit()
    assert got[-1] == pytest.approx(0.501187, abs=1e-6)
    g.box.setText("0.5")
    g.box.editingFinished.emit()
    assert got[-1] == 0.5
    n = len(got)
    g.box.setText("loud")
    g.box.editingFinished.emit()
    assert len(got) == n and "d04040" in g.box.styleSheet()
    pn = pan_box("p", 0.0)
    pn.box.setText("30L")
    pn.box.editingFinished.emit()
    assert pn.value == pytest.approx(-0.3)
    assert pn.box.text() == "30L"
    dispose(app, g, pn)


def test_knob_drag_and_reset(app):
    k = KnobBox("f", 20, 20000, 440, log=True)
    got = []
    k.committed.connect(got.append)
    k.show()
    QTest.mousePress(k.knob, Qt.MouseButton.LeftButton, pos=QPoint(10, 20))
    QTest.mouseMove(k.knob, QPoint(10, 0))
    QTest.mouseRelease(k.knob, Qt.MouseButton.LeftButton, pos=QPoint(10, 0))
    assert got and got[-1] > 440
    k.knob.reset.emit()
    assert got[-1] == pytest.approx(440)
    k.box.setText("2kHz")
    k.box.editingFinished.emit()
    assert k.value == 2000
    dispose(app, k)


# ---- panels ---------------------------------------------------------------------------

def test_step_toggle_and_undo(win, song):
    row = win.rack.rows.itemAt(1).widget()  # kick
    from muzdaw.ui.channel_rack import StepButton
    steps = row.findChildren(StepButton)
    QTest.mouseClick(steps[4], Qt.MouseButton.LeftButton)
    notes = load(song).pattern("A").notes["kick"]
    assert [(n.pitch, n.start) for n in notes] == [(36, 1.0)]
    win.doc.undo()
    assert load(song).pattern("A").notes.get("kick", []) == []


def test_piano_roll_add_move_delete(win, song, app):
    win.show_panel("piano")
    g = win.piano.grid
    win.doc.select(channel="keys")
    x, y = int(g.x_of(2.0) + 3), int(g.y_of(64) + 5)
    QTest.mouseClick(g, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    notes = load(song).pattern("A").notes["keys"]
    assert (64, 2.0) in [(n.pitch, n.start) for n in notes]
    # drag that note up 2 semitones and right 1 beat
    QTest.mousePress(g, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    QTest.mouseMove(g, QPoint(x + int(g.px_per_beat), y - 2 * 12))
    QTest.mouseRelease(g, Qt.MouseButton.LeftButton, pos=QPoint(x + int(g.px_per_beat), y - 2 * 12))
    notes = load(song).pattern("A").notes["keys"]
    assert (66, 3.0) in [(n.pitch, n.start) for n in notes]
    # resize the C4 note (0..1) to 2 beats via its right edge
    ex, ey = int(g.x_of(1.0) - 2), int(g.y_of(60) + 5)
    QTest.mousePress(g, Qt.MouseButton.LeftButton, pos=QPoint(ex, ey))
    QTest.mouseMove(g, QPoint(int(g.x_of(2.0)), ey))
    QTest.mouseRelease(g, Qt.MouseButton.LeftButton, pos=QPoint(int(g.x_of(2.0)), ey))
    c4 = [n for n in load(song).pattern("A").notes["keys"] if n.pitch == 60][0]
    assert c4.length == 2.0
    QTest.mouseClick(g, Qt.MouseButton.RightButton, pos=QPoint(int(g.x_of(0.5)), ey))
    assert 60 not in [n.pitch for n in load(song).pattern("A").notes["keys"]]


def test_playlist_place_move_remove(win, song):
    g = win.playlist.grid
    from muzdaw.ui.playlist import RULER_H, TRACK_H
    y2 = RULER_H + 2 * TRACK_H + 10
    QTest.mouseClick(g, Qt.MouseButton.LeftButton, pos=QPoint(int(g.x_of(9.5)), y2))
    clips = load(song).playlist
    assert [(c.start, c.track) for c in clips] == [(0.0, 0), (8.0, 2)]
    x = int(g.x_of(9))
    QTest.mousePress(g, Qt.MouseButton.LeftButton, pos=QPoint(x, y2))
    QTest.mouseMove(g, QPoint(x + int(4 * g.px_per_beat), y2 + TRACK_H))
    QTest.mouseRelease(g, Qt.MouseButton.LeftButton, pos=QPoint(x + int(4 * g.px_per_beat), y2 + TRACK_H))
    assert (12.0, 3) in [(c.start, c.track) for c in load(song).playlist]
    QTest.mouseClick(g, Qt.MouseButton.RightButton, pos=QPoint(int(g.x_of(13)), y2 + TRACK_H))
    assert len(load(song).playlist) == 1


def test_mixer_fx_and_send(win, song, app):
    win.show_panel("mixer")
    app.processEvents()
    win.mixer.add_bus("verb")
    app.processEvents()
    strips = [win.mixer.strips.itemAt(i).widget() for i in range(win.mixer.strips.count())]
    keys = [s for s in strips if s.name == "keys"][0]
    keys.add_box.setCurrentText("compressor")
    keys.add_fx()
    app.processEvents()
    assert load(song).channel("keys").fx[0]["type"] == "compressor"
    strips = [win.mixer.strips.itemAt(i).widget() for i in range(win.mixer.strips.count())]
    keys = [s for s in strips if s.name == "keys"][0]
    keys.fx_list.setCurrentRow(0)
    from muzdaw.ui.knobbox import KnobBox as KB
    boxes = {keys.params.itemAt(i, keys.params.ItemRole.LabelRole).widget().text(): keys.params.itemAt(i, keys.params.ItemRole.FieldRole).widget()
             for i in range(keys.params.rowCount())}
    kb = boxes["threshold db"]
    assert isinstance(kb, KB)
    kb.box.setText("-18dB")
    kb.box.editingFinished.emit()
    assert load(song).channel("keys").fx[0]["params"]["threshold_db"] == -18
    strips = [win.mixer.strips.itemAt(i).widget() for i in range(win.mixer.strips.count())]
    keys = [s for s in strips if s.name == "keys"][0]
    keys.send_level.box.setText("-6dB")
    keys.send_level.box.editingFinished.emit()
    assert load(song).channel("keys").sends["verb"] == pytest.approx(0.501, abs=1e-3)


def test_tempo_and_undo_redo_keys(win, song, app):
    win.tempo.box.setText("140")
    win.tempo.box.editingFinished.emit()
    assert load(song).tempo == 140
    win.activateWindow()
    QTest.keyClick(win, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert load(song).tempo == 120 and win.tempo.value == 120
    QTest.keyClick(win, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert load(song).tempo == 140


def test_bad_edit_keeps_file(win, song):
    before = song.read_bytes()
    assert not win.doc.edit("bad", lambda p: setattr(p, "tempo", 5))
    assert song.read_bytes() == before


def _wait(app, proc, secs):
    from PySide6.QtCore import QProcess
    t = time.monotonic()
    while proc.state() != QProcess.ProcessState.NotRunning and time.monotonic() - t < secs:
        app.processEvents()
        time.sleep(0.05)


def test_render_button(win, song, app, tmp_path):
    out = tmp_path / "o.wav"
    win.render(str(out))
    _wait(app, win.renderer, 60)
    app.processEvents()
    assert out.exists() and out.stat().st_size > 1000


def _until(app, cond, secs=60):
    t = time.monotonic()
    while not cond() and time.monotonic() - t < secs:
        app.processEvents()
        time.sleep(0.02)
    assert cond()


def test_play_pattern_mode_playheads_live_update_and_stop(win, song, app):
    """Space = engine (real process, silent): PAT plays pattern A on loop, playheads move,
    an edit while playing reaches the engine, Space stops."""
    win.doc.select(pattern="A", channel="keys")
    win.show_panel("piano")
    if win.song_btn.isChecked():
        win.song_btn.setChecked(False)
    assert win.song_btn.text() == "PAT"
    updates = []
    win.engine.updated.connect(updates.append)
    QTest.keyClick(win, Qt.Key.Key_Space)
    assert win.play_btn.isChecked() and "preparing" in win.pos_label.text()
    _until(app, lambda: win._clock is not None)
    t0, beat0, bps, loop_beats, mode = win._clock
    assert (bps, loop_beats, mode) == (2.0, 4.0, "pattern")
    _until(app, lambda: "bar" in win.pos_label.text())
    _until(app, lambda: win.piano.grid.play_beat is not None)
    assert 0 <= win.piano.grid.play_beat < 4
    assert win.rack._play_step is not None and 0 <= win.rack._play_step < 16
    win.doc.edit("add", lambda p: p.pattern("A").notes["keys"].append(Note(67, 2, 1)))
    _until(app, lambda: bool(updates))  # the engine re-rendered while playing
    assert updates[0].split()[2] == "1"  # only the edited channel
    QTest.keyClick(win, Qt.Key.Key_Space)
    assert not win.play_btn.isChecked() and win.pos_label.text().strip() == "stopped"
    assert win.piano.grid.play_beat is None and win.rack._play_step is None


def test_song_mode_and_empty_playlist_message(win, song, app):
    win.song_btn.setChecked(True)
    assert win.song_btn.text() == "SONG"
    win.play()
    _until(app, lambda: win._clock is not None)
    assert win._clock[4] == "song"
    _until(app, lambda: win.playlist.grid.play_beat is not None)
    win.stop()
    win.doc.edit("clear", lambda p: p.playlist.clear())
    win.play()
    _until(app, lambda: not win.engine.active)
    assert "playlist is empty" in win.status.currentMessage()
    win.song_btn.setChecked(False)


def test_piano_quantize_button_and_undo(win, song, app):
    win.doc.edit("off-grid", lambda p: p.pattern("A").notes["keys"].append(Note(62, 1.1, 0.5)))
    win.doc.select(pattern="A", channel="keys")
    win.piano.snap_box.setCurrentIndex(4)  # 1 beat
    QTest.mouseClick(win.piano.quant_btn, Qt.MouseButton.LeftButton)
    assert [n.start for n in load(song).pattern("A").notes["keys"]] == [0.0, 1.0]
    QTest.keyClick(win, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert [n.start for n in load(song).pattern("A").notes["keys"]] == [0.0, 1.1]


def test_notes_from_audio(win, song, app, tmp_path):
    """Render a WAV offline, then File > Notes from audio puts its notes into a pattern."""
    from muzdaw.render import render
    src = Project(tempo=120)
    src.channels = [Channel("x", "sine")]
    src.patterns = [Pattern("P", 4, {"x": [Note(67, 0, 0.75), Note(72, 2, 0.75)]})]
    src.playlist = [Clip("P", 0)]
    wav = tmp_path / "take.wav"
    render(src, wav)
    win.doc.select(pattern="A", channel="kick")
    win.audio_to_notes(str(wav))
    _wait(app, win.converter, 120)
    app.processEvents()
    assert [(n.pitch, n.start) for n in win.doc.project.pattern("A").notes["kick"]] == [(67, 0.0), (72, 2.0)]
    assert "2 notes" in win.status.currentMessage()


def test_start_without_file_and_save_as(app, tmp_path):
    """No file given: window opens on an untitled song; Save as moves it; next start reopens it."""
    from muzdaw.ui import app as appmod
    from muzdaw.ui import session
    recent = session.config_dir() / "recent.json"
    recent.unlink(missing_ok=True)
    path = session.last_song() or session.new_untitled()
    assert session.is_untitled(path)
    w = appmod.MainWindow(path)
    assert "untitled" in w.windowTitle()
    assert oct(session.config_dir().stat().st_mode & 0o777) == "0o700"
    assert oct(recent.stat().st_mode & 0o777) == "0o600"
    w.doc.edit("tempo", lambda p: setattr(p, "tempo", 140))
    w.save_as(str(tmp_path / "mysong"))
    new = w._replacement
    assert new.doc.path == tmp_path / "mysong.muz.json"
    assert load(new.doc.path).tempo == 140
    assert "mysong.muz.json" in new.windowTitle()
    assert session.last_song() == (tmp_path / "mysong.muz.json").resolve()
    n2 = session.new_untitled()  # never overwrites an older untitled song
    assert n2 != path and path.exists()
    dispose(app, w, new)


def test_new_song_by_clicking_real_buttons(app, tmp_path):
    """User path from an empty song: + pattern, pick sound, + channel (real clicks), click a step."""
    from PySide6.QtWidgets import QPushButton
    from muzdaw.ui.app import MainWindow
    from muzdaw.ui.channel_rack import StepButton
    f = tmp_path / "empty.muz.json"
    save(Project(), f)
    w = MainWindow(f)
    w.show()
    app.processEvents()
    btn = {b.text(): b for b in w.rack.findChildren(QPushButton)}
    QTest.mouseClick(btn["+ pattern"], Qt.MouseButton.LeftButton)
    w.rack.preset_box.setCurrentText("kick")
    QTest.mouseClick(btn["+ channel"], Qt.MouseButton.LeftButton)
    QTest.mouseClick(btn["+ channel"], Qt.MouseButton.LeftButton)
    app.processEvents()
    p = load(f)
    assert [c.name for c in p.channels] == ["kick", "kick-2"]
    assert p.channels[0].instrument == "kick"
    steps = [s for s in w.rack.findChildren(StepButton) if s.isVisible()]
    assert steps, "no step buttons shown after adding a channel"
    QTest.mouseClick(steps[0], Qt.MouseButton.LeftButton)
    assert sum(len(n) for n in load(f).patterns[0].notes.values()) == 1
    dispose(app, w)


def test_piano_key_hold_velocity_glide(win, app, monkeypatch):
    """Press = note on (velocity from x), drag = glide, release = note off. Silent (MUZDAW_SILENT)."""
    from muzdaw.ui.piano_roll import KEYS_W, ROW_H
    monkeypatch.setenv("MUZDAW_SILENT", "1")
    win.doc.select(channel="keys")
    got = []
    win.piano.previewer.result.connect(got.append)
    grid = win.piano.grid
    assert KEYS_W >= 100 and ROW_H >= 16  # bigger keyboard
    assert grid.key_velocity(2) < 35 and grid.key_velocity(KEYS_W - 1) > 120
    y60 = int((127 - 60) * ROW_H + ROW_H / 2)
    y62 = int((127 - 62) * ROW_H + ROW_H / 2)
    x = KEYS_W - 5  # front of the key = hard
    QTest.mousePress(grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y60))
    assert grid.pressed_key == 60
    QTest.mouseMove(grid, QPoint(x, y62))
    assert grid.pressed_key == 62  # glided
    QTest.mouseRelease(grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y62))
    assert grid.pressed_key is None
    t = time.monotonic()
    while len(got) < 4 and time.monotonic() - t < 30:
        app.processEvents()
        time.sleep(0.05)
    ons = [g for g in got if g.startswith("ok on")]
    offs = [g for g in got if g.startswith("ok off")]
    assert [o.split()[2] for o in ons] == ["60", "62"], got
    assert all(int(o.split()[3]) >= 120 for o in ons), got  # hard hit
    assert len(offs) == 2, got
    assert load(win.doc.path).patterns[0].notes["keys"] == [Note(60, 0, 1)]  # keys add no notes
    win.piano.previewer.close()


def test_new_song_has_starter_channels_and_steps_render(app, tmp_path):
    """New song: kick/clap/hat/bass/piano + Pattern 1; drum steps land on the right GM drum; it renders."""
    from muzdaw.render import render_audio
    from muzdaw.ui import session
    from muzdaw.ui.channel_rack import toggle_step
    path = session.new_untitled()
    p = load(path)
    assert [c.name for c in p.channels] == ["kick", "clap", "hat", "bass", "piano"]
    assert [x.name for x in p.patterns] == ["Pattern 1"]
    toggle_step(p, "Pattern 1", "kick", 0)
    toggle_step(p, "Pattern 1", "clap", 4)
    toggle_step(p, "Pattern 1", "hat", 2)
    pat = p.pattern("Pattern 1")
    assert [(c, pat.notes[c][0].pitch) for c in ("kick", "clap", "hat")] == [("kick", 36), ("clap", 39), ("hat", 42)]
    p.playlist = [Clip("Pattern 1", 0)]
    assert abs(render_audio(p)).max() > 0.05


def test_piano_roll_opens_around_middle_c(win, app):
    """The visible keys include C4..C5 (not C8-C9) when the channel has no notes."""
    from muzdaw.ui.piano_roll import ROW_H
    win.doc.select(channel="kick")  # no notes
    win.show_panel("piano")
    app.processEvents()
    pr = win.piano
    pr.center_view()
    top = pr.scroll.verticalScrollBar().value()
    h = pr.scroll.viewport().height()
    lo, hi = 127 - (top + h) // ROW_H, 127 - top // ROW_H  # lowest / highest visible pitch
    assert h > 3 * ROW_H
    assert lo <= 66 <= hi, (lo, hi)
    win.doc.select(channel="keys")  # has C4 (60) -> centered on it
    app.processEvents()
    top = pr.scroll.verticalScrollBar().value()
    assert 127 - (top + h) // ROW_H <= 60 <= 127 - top // ROW_H


def test_help_guide_opens(win, app):
    """F1 shows the easy guide from help.md; every shortcut it lists exists in the menus."""
    QTest.keyClick(win, Qt.Key.Key_F1)
    app.processEvents()
    assert win._help.isVisible()
    text = win._help_view.toPlainText()
    assert "How to use muzdaw" in text and "Typing keyboard" in text and "Quantize" in text
    menu_keys = {a.shortcut().toString() for m in win.menuBar().actions() for a in m.menu().actions()}
    for key in ("Ctrl+N", "Ctrl+O", "Ctrl+S", "Ctrl+Shift+S", "Ctrl+R", "Ctrl+Shift+A", "Ctrl+Z", "Ctrl+Y",
                "Alt+Q", "F1", "F5", "F6", "F7", "F9", "Space", "Ctrl+Q", "Ctrl+Shift+R"):
        assert key in menu_keys, key
    from muzdaw.effects import BUILTINS
    for fx in ("reverb", "delay", "eq-peak", "compressor"):
        assert fx in BUILTINS


def test_channel_delete_and_rename(win, song, app):
    win.rack.rename_channel("keys", "lead")
    p = load(song)
    assert [c.name for c in p.channels] == ["lead", "kick"] and p.pattern("A").notes["lead"] == [Note(60, 0, 1)]
    assert win.doc.channel == "lead"
    win.rack.rename_channel("lead", "kick")  # name taken: refused, nothing changes
    assert [c.name for c in load(song).channels] == ["lead", "kick"]
    win.rack.delete_channel("lead")
    p = load(song)
    assert [c.name for c in p.channels] == ["kick"] and "lead" not in p.pattern("A").notes
    app.processEvents()
    assert [r.name for r in win.rack._row_widgets] == ["kick"]
    QTest.keyClick(win, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert [c.name for c in load(song).channels] == ["lead", "kick"]
