"""muzdaw main window.

Keys (FL style): Space play/stop, F5 Playlist, F6 Channel Rack, F7 Piano Roll,
F9 Mixer, Ctrl+Z undo, Ctrl+Y / Ctrl+Shift+Z redo, Ctrl+R export WAV, Ctrl+O open, Ctrl+N new, Ctrl+S save.
Every edit is saved at once (with undo history), so there is no Save button.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QDockWidget, QFileDialog, QLabel, QMainWindow,
                               QMessageBox, QToolBar, QToolButton)

from ..model import Project, ProjectError, save
from . import session
from .channel_rack import ChannelRack
from .document import Document
from .knobbox import KnobBox, gain_box
from .mixer import Mixer
from .piano_roll import PianoRoll
from .preview_client import headless_env
from .playlist import Playlist


def dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in ((QPalette.ColorRole.Window, "#2b2f35"), (QPalette.ColorRole.WindowText, "#e6e6e6"),
                      (QPalette.ColorRole.Base, "#1f2226"), (QPalette.ColorRole.AlternateBase, "#2b2f35"),
                      (QPalette.ColorRole.Text, "#e6e6e6"), (QPalette.ColorRole.Button, "#3a3f47"),
                      (QPalette.ColorRole.ButtonText, "#e6e6e6"), (QPalette.ColorRole.Highlight, "#f0a030"),
                      (QPalette.ColorRole.HighlightedText, "#111"), (QPalette.ColorRole.ToolTipBase, "#2b2f35"),
                      (QPalette.ColorRole.ToolTipText, "#e6e6e6")):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)


class MainWindow(QMainWindow):
    def __init__(self, path: str | Path):
        super().__init__()
        self.doc = Document(path)
        self.renderer: QProcess | None = None
        self.converter: QProcess | None = None
        session.remember(self.doc.path)
        self._set_title()
        self.resize(1400, 900)

        self.playlist = Playlist(self.doc)
        self.setCentralWidget(self.playlist)
        self.rack = ChannelRack(self.doc)
        self.piano = PianoRoll(self.doc)
        self.mixer = Mixer(self.doc)
        self.docks = {}
        for key, title, widget, area in (("rack", "Channel Rack (F6)", self.rack, Qt.DockWidgetArea.BottomDockWidgetArea),
                                         ("piano", "Piano Roll (F7)", self.piano, Qt.DockWidgetArea.BottomDockWidgetArea),
                                         ("mixer", "Mixer (F9)", self.mixer, Qt.DockWidgetArea.BottomDockWidgetArea)):
            d = QDockWidget(title, self)
            d.setObjectName(key)
            d.setWidget(widget)
            self.addDockWidget(area, d)
            self.docks[key] = d
        self.tabifyDockWidget(self.docks["rack"], self.docks["piano"])
        self.tabifyDockWidget(self.docks["piano"], self.docks["mixer"])
        self.docks["rack"].raise_()
        self.resizeDocks([self.docks["rack"]], [470], Qt.Orientation.Vertical)
        self.rack.openPianoRoll.connect(lambda ch: (self.doc.select(channel=ch), self.show_panel("piano")))
        self.playlist.grid.openPattern.connect(lambda pat: (self.doc.select(pattern=pat), self.show_panel("piano")))

        self._toolbar()
        self._menus()
        self.status = self.statusBar()
        self.doc.message.connect(lambda m: self.status.showMessage(m, 5000))
        self.doc.changed.connect(self._sync_transport)
        from PySide6.QtCore import QTimer
        from .engine_client import EngineClient
        self._clock = None
        self.engine = EngineClient(self)
        self.engine.started.connect(self._engine_started)
        self.engine.stopped.connect(self._transport_idle)
        self.engine.failed.connect(self._engine_failed)
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(150)
        self._update_timer.timeout.connect(self.engine.update)
        self.doc.changed.connect(self._doc_changed_while_playing)
        self.doc.selection.connect(self._selection_while_playing)
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(30)
        self._play_timer.timeout.connect(self._tick)
        self._play_timer.start()
        self._restore_window()

    # ---- window memory -------------------------------------------------------------
    def _restore_window(self) -> None:
        """Size, position, panel layout and panel sizes from the last run."""
        import base64
        from PySide6.QtCore import QByteArray
        from . import prefs
        for key, fn in (("geometry", self.restoreGeometry), ("state", self.restoreState)):
            val = prefs.get(f"window_{key}")
            if isinstance(val, str):
                try:
                    fn(QByteArray(base64.b64decode(val)))
                except (ValueError, TypeError):
                    pass

    def _save_window(self) -> None:
        import base64
        from . import prefs
        prefs.set_("window_geometry", base64.b64encode(bytes(self.saveGeometry())).decode())
        prefs.set_("window_state", base64.b64encode(bytes(self.saveState())).decode())

    # ---- UI parts ------------------------------------------------------------------
    def _toolbar(self) -> None:
        tb = QToolBar("Transport")
        tb.setObjectName("transport")
        self.addToolBar(tb)
        self.play_btn = QToolButton()
        self.play_btn.setText("▶ Play")
        self.play_btn.setCheckable(True)
        self.play_btn.clicked.connect(lambda _=False: self.toggle_play())
        tb.addWidget(self.play_btn)
        from . import prefs as _prefs
        self.song_btn = QToolButton()
        self.song_btn.setCheckable(True)
        self.song_btn.setToolTip("PAT = Space plays the selected pattern on loop; SONG = the whole playlist (Ctrl+L)")
        self.song_btn.setChecked(_prefs.get("mode") == "song")
        self.song_btn.toggled.connect(self._mode_changed)
        self.song_btn.setStyleSheet("QToolButton{min-width:44px; font-weight:bold; color:#f0a030;}"
                                    "QToolButton:checked{color:#5ad65a;}")
        self.song_btn.setText("SONG" if self.song_btn.isChecked() else "PAT")
        tb.addWidget(self.song_btn)
        self.loop_box = QCheckBox("Loop song")
        self.loop_box.setChecked(True)
        tb.addWidget(self.loop_box)
        self.metro_box = QCheckBox("Metronome")
        tb.addWidget(self.metro_box)
        tb.addSeparator()
        self.tempo = KnobBox("Tempo", 20, 300, self.doc.project.tempo, default=120, unit=" BPM", decimals=1)
        self.tempo.committed.connect(lambda v: self.doc.edit(f"tempo {v:.1f}", lambda p: setattr(p, "tempo", round(v, 3))))
        tb.addWidget(self.tempo)
        self.master = gain_box("Master", self.doc.project.master)
        self.master.committed.connect(lambda v: self.doc.edit("master volume", lambda p: setattr(p, "master", v)))
        tb.addWidget(self.master)
        tb.addSeparator()
        render = QToolButton()
        render.setText("Export WAV")
        render.clicked.connect(lambda: self.render())
        tb.addWidget(render)
        self.pos_label = QLabel("  stopped")
        self.pos_label.setMinimumWidth(160)
        tb.addWidget(self.pos_label)
        tb.addSeparator()
        from .typing_keys import TypingKeyboard
        self.typing = TypingKeyboard(self)
        self.typing.noteOn.connect(self.piano.typed_on)
        self.typing.noteOff.connect(self.piano.typed_off)
        self.keys_btn = QToolButton()
        self.keys_btn.setCheckable(True)
        self.keys_btn.setToolTip("Computer keys play notes on the selected channel (PageUp/PageDown = octave)")
        self.keys_btn.clicked.connect(lambda on: self.typing.set_enabled(on))
        tb.addWidget(self.keys_btn)
        set_keys = QToolButton()
        set_keys.setText("⚙")
        set_keys.setToolTip("Typing keyboard settings")
        set_keys.clicked.connect(self.keyboard_settings)
        tb.addWidget(set_keys)
        self.typing.changed.connect(self._sync_keys)
        self._sync_keys()
        tb.addSeparator()
        from . import prefs
        from .recorder import TakeRecorder
        self.recorder = TakeRecorder(self)
        self.typing.noteOn.connect(self.recorder.note_on)
        self.typing.noteOff.connect(self.recorder.note_off)
        self.recorder.click.connect(self.piano.click)
        self.recorder.state.connect(self._rec_state)
        self.rec_btn = QToolButton()
        self.rec_btn.setText("● Rec")
        self.rec_btn.setCheckable(True)
        self.rec_btn.setToolTip("Record the typing keyboard into the selected channel + pattern (Ctrl+Shift+R)")
        self.rec_btn.setStyleSheet("QToolButton:checked{background:#c03030; color:white;}")
        self.rec_btn.clicked.connect(lambda _=False: self.toggle_record())
        tb.addWidget(self.rec_btn)
        self.rec_quant = QCheckBox("Quantize rec")
        self.rec_quant.setToolTip("Snap recorded notes to the Piano Roll snap grid")
        self.rec_quant.setChecked(bool(prefs.get("rec_quantize", True)))
        self.rec_quant.toggled.connect(lambda on: prefs.set_("rec_quantize", on))
        tb.addWidget(self.rec_quant)

    def _mode_changed(self, song: bool) -> None:
        from . import prefs
        prefs.set_("mode", "song" if song else "pattern")
        self.song_btn.setText("SONG" if song else "PAT")
        if self.engine.active and not self.recorder.active:
            self.play()  # switch what is playing right away

    # ---- record the typing keyboard ---------------------------------------------------
    def toggle_record(self) -> None:
        if self.recorder.active:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self) -> None:
        pat = self.doc.current_pattern()
        if not self.doc.channel or pat is None:
            self.status.showMessage("add a channel and a pattern first (Channel Rack, F6)", 6000)
            self.rec_btn.setChecked(False)
            return
        self.stop()
        if not self.typing.layout.enabled:
            self.typing.set_enabled(True)
        self.rec_btn.setChecked(True)
        self.show_panel("piano")
        self._rec_target = (pat.name, self.doc.channel)
        # the engine plays the pattern (with a 4-beat count-in) while you record; the recorder
        # takes beat 0 from the engine's clock when it reports "started"
        self.recorder.wait_for_start()
        if self.song_btn.isChecked():
            self.song_btn.blockSignals(True)
            self.song_btn.setChecked(False)
            self.song_btn.setText("PAT")
            self.song_btn.blockSignals(False)
        self.play(count_in=4)

    def stop_record(self) -> None:
        grid = self.piano.grid.snap if self.rec_quant.isChecked() else None
        notes = self.recorder.stop(grid)
        self.rec_btn.setChecked(False)
        self.stop()
        self.piano.grid.rec_beat = None
        self.piano.grid.update()
        pat_name, ch = getattr(self, "_rec_target", (None, None))
        if not notes or pat_name is None:
            self.status.showMessage("nothing recorded", 5000)
            return

        def fn(p):
            ns = p.pattern(pat_name).notes.setdefault(ch, [])
            ns.extend(notes)
            ns.sort(key=lambda n: (n.start, n.pitch))
        if self.doc.edit(f"record {len(notes)} note{'s' if len(notes) > 1 else ''} into {pat_name}/{ch}", fn):
            if (self.doc.pattern, self.doc.channel) == (pat_name, ch):
                from .piano_roll import match_indices
                self.piano.grid.set_selection(match_indices(self.piano.grid.notes(), notes))

    def _rec_state(self, text: str) -> None:
        """Recorder messages; the playhead + labels come from the engine clock (_tick)."""
        if text == "idle" and not self.engine.active:
            self.pos_label.setText("  stopped")

    def _sync_keys(self) -> None:
        lay = self.typing.layout
        self.keys_btn.setChecked(lay.enabled)
        oct_txt = f" {lay.octave:+d} oct" if lay.octave else ""
        self.keys_btn.setText(f"⌨ Keys {'on' if lay.enabled else 'off'}{oct_txt}")

    def keyboard_settings(self) -> None:
        from .typing_keys import KeyboardDialog
        dlg = KeyboardDialog(self, self.typing.layout)
        if dlg.exec():
            self.typing.set_layout(dlg.layout_)
            self.status.showMessage("typing keyboard: " + "  |  ".join(self.typing.layout.describe()), 10000)

    def _menus(self) -> None:
        def act(menu, text, key, fn):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
                a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            a.triggered.connect(fn)
            menu.addAction(a)
            return a
        m = self.menuBar().addMenu("&File")
        act(m, "New", "Ctrl+N", self.new_project)
        act(m, "Open...", "Ctrl+O", self.open_project)
        act(m, "Save", "Ctrl+S", self.save_project)
        act(m, "Save as...", "Ctrl+Shift+S", lambda: self.save_as())
        m.addSeparator()
        act(m, "Export audio (WAV)...", "Ctrl+R", lambda: self.render())
        act(m, "Export MIDI...", None, self.export_midi)
        act(m, "Import notes from audio...", "Ctrl+Shift+A", lambda: self.audio_to_notes())
        act(m, "Add audio clip to Playlist...", None, self.add_audio_clip)
        act(m, "Add sample instrument...", None, lambda: self.rack.add_sample())
        m.addSeparator()
        act(m, "Quit", "Ctrl+Q", self.close)
        m = self.menuBar().addMenu("&Edit")
        act(m, "Undo", "Ctrl+Z", self.doc.undo)
        act(m, "Redo", "Ctrl+Y", self.doc.redo)
        act(m, "Redo ", "Ctrl+Shift+Z", self.doc.redo)
        act(m, "Quantize (piano roll)", "Alt+Q", lambda: self.piano.quantize())
        m = self.menuBar().addMenu("&View")
        act(m, "Playlist", "F5", lambda: self.show_panel("playlist"))
        act(m, "Channel Rack", "F6", lambda: self.show_panel("rack"))
        act(m, "Piano Roll", "F7", lambda: self.show_panel("piano"))
        act(m, "Mixer", "F9", lambda: self.show_panel("mixer"))
        m = self.menuBar().addMenu("&Transport")
        act(m, "Play / Stop", "Space", self._space)
        act(m, "Record typing keyboard", "Ctrl+Shift+R", self.toggle_record)
        act(m, "Pattern / Song mode", "Ctrl+L", lambda: self.song_btn.toggle())
        m = self.menuBar().addMenu("&Options")
        act(m, "Typing keyboard...", None, self.keyboard_settings)
        m = self.menuBar().addMenu("&Help")
        act(m, "How to use muzdaw", "F1", self.show_help)

    def show_panel(self, key: str) -> None:
        if key == "playlist":
            self.playlist.setFocus()
            return
        d = self.docks[key]
        d.show()
        d.raise_()

    def _sync_transport(self) -> None:
        self.tempo.set_value(self.doc.project.tempo)
        self.master.set_value(self.doc.project.master)

    def show_help(self) -> None:
        """F1: the easy guide (muzdaw/ui/help.md) in its own window."""
        from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout
        if getattr(self, "_help", None) is None:
            self._help = QDialog(self)
            self._help.setWindowTitle("How to use muzdaw")
            self._help.resize(820, 860)
            view = QTextBrowser(self._help)
            view.setOpenExternalLinks(False)
            text = (Path(__file__).with_name("help.md")).read_text(encoding="utf-8")
            view.setMarkdown(text)
            QVBoxLayout(self._help).addWidget(view)
            self._help_view = view
        self._help.show()
        self._help.raise_()

    # ---- files ---------------------------------------------------------------------
    def _load(self, path: Path) -> None:
        self.stop()
        w = MainWindow(path)
        w.show()
        self._replacement = w
        self.close()

    def _set_title(self) -> None:
        name = "untitled (not saved yet: File > Save as)" if session.is_untitled(self.doc.path) else self.doc.path.name
        self.setWindowTitle(f"muzdaw - {name}")

    def _start_dir(self) -> str:
        return str(Path.home() if session.is_untitled(self.doc.path) else self.doc.path.parent)

    def _export_name(self, ext: str) -> str:
        return str(Path(self._start_dir()) / (self.doc.path.name.split(".")[0] + ext))

    def new_project(self) -> None:
        self._load(session.new_untitled())

    def save_project(self) -> None:
        """Edits already save themselves; for an untitled song this asks where to keep it."""
        if session.is_untitled(self.doc.path):
            self.save_as()
        else:
            self.status.showMessage(f"saved (every change saves itself to {self.doc.path.name})", 5000)

    def save_as(self, f: str | None = None) -> None:
        if f is None:
            f, _ = QFileDialog.getSaveFileName(self, "Save song as", str(Path(self._start_dir()) / "song.muz.json"),
                                               "muzdaw project (*.muz.json)")
            if not f:
                return
        if not f.endswith(".json"):
            f += ".muz.json"
        p = self.doc.project
        p.name = Path(f).name.split(".")[0]
        try:
            save(p, f)
        except (ProjectError, OSError) as e:
            self.status.showMessage(f"save failed: {e}", 8000)
            return
        self._load(Path(f))

    def open_project(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Open project", self._start_dir(),
                                           "muzdaw project (*.muz.json *.json)")
        if f:
            self._load(Path(f))

    def add_audio_clip(self, path: str | None = None) -> None:
        """File menu: an audio file onto the Playlist (bar 1, first track); then drag it where you want."""
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Add audio clip", self._start_dir(),
                                                  "Audio (*.wav *.flac *.mp3 *.ogg *.aiff *.aif)")
            if not path:
                return
        self.playlist.grid.add_audio(path, 0.0, 0)

    def export_midi(self) -> None:
        from ..midi_io import export_midi
        f, _ = QFileDialog.getSaveFileName(self, "Export MIDI", self._export_name(".mid"),
                                           "MIDI (*.mid)")
        if f:
            try:
                n = export_midi(self.doc.project, f)
                self.status.showMessage(f"wrote {f} ({n} notes)", 5000)
            except (ProjectError, OSError) as e:
                self.status.showMessage(f"export failed: {e}", 8000)

    # ---- render / play (separate processes, so the UI never blocks) -------------
    def _cmd(self, *args: str) -> tuple[str, list[str]]:
        return sys.executable, ["-m", "muzdaw", *args]

    def render(self, out: str | None = None) -> None:
        if self.renderer and self.renderer.state() != QProcess.ProcessState.NotRunning:
            return
        if out is None:
            out, _ = QFileDialog.getSaveFileName(self, "Export audio (WAV)", self._export_name(".wav"),
                                                 "WAV (*.wav)")
            if not out:
                return
        self.renderer = QProcess(self)
        self.renderer.setProcessEnvironment(headless_env())  # plugins can't open windows
        self.renderer.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.renderer.finished.connect(self._render_done)
        self.status.showMessage("rendering...")
        exe, args = self._cmd("render", str(self.doc.path), out)
        self.renderer.start(exe, args)

    def audio_to_notes(self, wav: str | None = None) -> None:
        """rec2midi in a separate process: WAV -> notes for the current channel/pattern."""
        if self.converter and self.converter.state() != QProcess.ProcessState.NotRunning:
            return
        ch, pat = self.doc.channel, self.doc.pattern or "REC"
        if not ch:
            self.status.showMessage("add a channel first", 5000)
            return
        if wav is None:
            wav, _ = QFileDialog.getOpenFileName(self, "Notes from audio", str(self.doc.path.parent),
                                                 "Audio (*.wav *.flac *.mp3 *.ogg)")
            if not wav:
                return
        self.converter = QProcess(self)
        self.converter.setProcessEnvironment(headless_env())  # plugins can't open windows
        self.converter.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.converter.finished.connect(self._convert_done)
        self.status.showMessage(f"finding notes in {Path(wav).name}...")
        exe, args = self._cmd("rec2midi", str(self.doc.path), wav, ch, pat, "--quantize", "1/16")
        self.converter.start(exe, args)

    def _convert_done(self, code: int, _status) -> None:
        text = bytes(self.converter.readAll()).decode(errors="replace")
        lines = [ln for ln in text.splitlines() if ln.startswith(("ok:", "error"))]
        msg = lines[-1] if lines else f"note finding ended (code {code})"
        if code == 0:
            self.doc.reload(msg)
        self.status.showMessage(msg, 10000)

    def _render_done(self, code: int, _status) -> None:
        text = bytes(self.renderer.readAll()).decode(errors="replace")
        lines = [ln for ln in text.splitlines() if ln.startswith(("wrote", "error"))]
        self.status.showMessage(lines[-1] if lines else f"render ended (code {code})", 10000)

    def _space(self) -> None:
        """Space: stops a recording if one runs, else play / stop."""
        if self.recorder.active:
            self.stop_record()
            return
        self.toggle_play()

    def toggle_play(self) -> None:
        if self.engine.active:
            self.stop()
        else:
            self.play()

    def mode(self) -> str:
        return "song" if self.song_btn.isChecked() else "pattern"

    def play(self, count_in: int = 0) -> None:
        """Play with the engine: PAT = selected pattern on loop, SONG = the playlist."""
        if self.mode() == "pattern" and not self.doc.pattern:
            self.status.showMessage("add a pattern first (Channel Rack, F6)", 6000)
            self.play_btn.setChecked(False)
            return
        self._play_pattern = self.doc.pattern
        self.engine.play(str(self.doc.path), self.mode(), self.doc.pattern, 0.0,
                         loop=self.loop_box.isChecked() or self.mode() == "pattern",
                         metronome=self.metro_box.isChecked(), count_in=count_in)
        self.play_btn.setChecked(True)
        self.play_btn.setText("■ Stop")
        self.pos_label.setText("  preparing…")

    def stop(self) -> None:
        self.engine.stop()
        self._clock = None
        self._transport_idle()

    def _transport_idle(self) -> None:
        self._clock = None
        self.play_btn.setChecked(False)
        self.play_btn.setText("▶ Play")
        if not self.recorder.active:
            self.pos_label.setText("  stopped")
        self.playlist.set_play_position(None)
        self.piano.grid.play_beat = None
        self.piano.grid.update()
        self.rack.set_play_step(None)

    def _engine_started(self, t0: float, beat0: float, bps: float, loop_beats: float, count_in: int) -> None:
        self._clock = (t0, beat0, bps, loop_beats, self.mode())
        if self.recorder.active and self.recorder.waiting:
            pat = self.doc.project.pattern(self._rec_target[0])
            self.recorder.start_at(t0, self.doc.project.tempo, pat.length)

    def _engine_failed(self, text: str) -> None:
        self.status.showMessage(f"play: {text}", 8000)
        self._transport_idle()
        if self.recorder.active:
            self.recorder.stop(None)
            self.rec_btn.setChecked(False)

    def _doc_changed_while_playing(self) -> None:
        if self.engine.state == "playing":
            self._update_timer.start()  # debounce: many quick edits = one update

    def _selection_while_playing(self) -> None:
        """PAT mode follows the pattern you pick (like FL)."""
        if (self.engine.active and self.mode() == "pattern" and not self.recorder.active
                and self.doc.pattern != getattr(self, "_play_pattern", None)):
            self.play()

    def _tick(self) -> None:
        """30 ms: move the playheads from the engine clock (no messages needed)."""
        import time
        if not self._clock:
            return
        t0, beat0, bps, loop_beats, mode = self._clock
        beat = beat0 + (time.monotonic() - t0) * bps
        if beat < beat0:
            self.pos_label.setText(f"  count-in {int((beat0 - beat) + 0.999)}")
            return
        if loop_beats:
            beat = beat0 + (beat - beat0) % loop_beats if mode == "pattern" else beat % loop_beats
        b = int(beat)
        prefix = "REC  " if self.recorder.active else ""
        self.pos_label.setText(f"  {prefix}bar {b // 4 + 1}  beat {b % 4 + 1}")
        pat_beat = None
        if mode == "pattern":
            pat_beat = beat
            self.playlist.set_play_position(None)
        else:
            self.playlist.set_play_position(beat)
            if self.doc.pattern:
                lengths = {p.name: p.length for p in self.doc.project.patterns}
                for c in self.doc.project.playlist:
                    if c.pattern == self.doc.pattern and c.start <= beat < c.start + lengths[c.pattern]:
                        pat_beat = beat - c.start
                        break
        self.piano.grid.play_beat = pat_beat
        self.piano.grid.rec_beat = pat_beat if self.recorder.active else None
        self.piano.grid.update()
        self.rack.set_play_step(None if pat_beat is None else int(pat_beat / 0.25))

    def closeEvent(self, e):
        if self.recorder.active:
            self.stop_record()  # keep the take
        self._save_window()
        self.stop()
        self.engine.close()
        self.typing.release_all()
        self.piano.previewer.close()
        super().closeEvent(e)


def app_icon():
    """muzdaw icon (muzdaw/ui/icon.svg) drawn at the usual sizes, for windows and the taskbar."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(str(Path(__file__).with_name("icon.svg")))
    icon = QIcon()
    if not r.isValid():
        return icon
    for size in (16, 24, 32, 48, 64, 128, 256):
        img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(p, QRectF(0, 0, size, size))
        p.end()
        icon.addPixmap(QPixmap.fromImage(img))
    return icon


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("muzdaw")
    app.setDesktopFileName("muzdaw")  # matches a muzdaw.desktop menu entry (Wayland taskbar icon)
    app.setWindowIcon(app_icon())
    dark_theme(app)
    if len(argv) > 1:
        path = Path(argv[1])
        if not path.exists():
            save(Project(name=path.name.split(".")[0]), path)
    else:
        path = session.last_song() or session.new_untitled()  # open straight away, no dialog
    try:
        w = MainWindow(path)
    except ProjectError as e:
        QMessageBox.critical(None, "muzdaw", str(e))
        return 1
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
