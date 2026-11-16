"""Channel Rack: one row per channel, with mute, volume, pan and a step sequencer.

A step = 1/4 beat (a 16th note). Clicking a step adds or removes a note on that
step for the current pattern (pitch = the channel's step pitch).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..model import Channel, Note, Pattern
from ..presets import PRESETS, available
from .knobbox import gain_box, pan_box

STEP = 0.25  # beats


def step_pitch(project, pattern: Pattern | None, ch: Channel) -> int:
    """Pitch used by the step sequencer: the channel's first note, else a sensible default."""
    if pattern and pattern.notes.get(ch.name):
        return pattern.notes[ch.name][0].pitch
    if ch.bank == 128:  # GM drum kit: pick the drum by channel name
        name = ch.name.lower()
        for word, pitch in (("snare", 38), ("clap", 39), ("hat", 42), ("tom", 45), ("crash", 49), ("ride", 51)):
            if word in name:
                return pitch
        return 36
    if ch.instrument == "sample":
        return ch.root
    if ch.instrument == "kick":
        return 36
    if ch.instrument == "hat":
        return 42
    return 60


def toggle_step(project, pattern_name: str, channel: str, index: int) -> None:
    """Add a note on the step, or remove all notes that start on it."""
    pat = project.pattern(pattern_name)
    ch = project.channel(channel)
    notes = pat.notes.setdefault(channel, [])
    t = index * STEP
    hit = [n for n in notes if abs(n.start - t) < 1e-6]
    if hit:
        for n in hit:
            notes.remove(n)
    else:
        notes.append(Note(step_pitch(project, pat, ch), t, STEP, 100))
        notes.sort(key=lambda n: (n.start, n.pitch))


class StepButton(QPushButton):
    def __init__(self, index: int):
        super().__init__()
        self.index = index
        self._playing = False
        self.setCheckable(True)
        self.setFixedSize(18, 26)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        dark = (index // 4) % 2 == 1
        self._off = "#3b4048" if dark else "#4a505a"
        self._style()

    def _style(self) -> None:
        border = "2px solid #5ad65a" if self._playing else "1px solid #23262b"  # green = playing now
        self.setStyleSheet(f"QPushButton{{background:{self._off}; border:{border}; border-radius:3px;}}"
                           "QPushButton:checked{background:#f0a030;}")

    def set_playing(self, on: bool) -> None:
        if on != self._playing:
            self._playing = on
            self._style()


class ChannelRow(QWidget):
    def __init__(self, rack: ChannelRack, ch: Channel, steps: int, on: set[int], selected: bool):
        super().__init__()
        doc = rack.doc
        self.name = ch.name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(4)
        mute = QPushButton()
        mute.setCheckable(True)
        mute.setChecked(not ch.mute)
        mute.setFixedSize(14, 14)
        mute.setToolTip("mute / unmute")
        mute.setStyleSheet("QPushButton{background:#555; border-radius:7px;} QPushButton:checked{background:#5ad65a;}")
        mute.clicked.connect(lambda on_: doc.edit(f"{'unmute' if on_ else 'mute'} {ch.name}",
                                                   lambda p: setattr(p.channel(ch.name), "mute", not on_)))
        lay.addWidget(mute)
        self.mute = mute
        vol = gain_box("volume", ch.volume, compact=True)
        vol.committed.connect(lambda v: doc.edit(f"volume {ch.name}", lambda p: setattr(p.channel(ch.name), "volume", v)))
        pan = pan_box("pan", ch.pan, compact=True)
        pan.committed.connect(lambda v: doc.edit(f"pan {ch.name}", lambda p: setattr(p.channel(ch.name), "pan", v)))
        lay.addWidget(vol)
        lay.addWidget(pan)
        self.vol, self.pan = vol, pan
        btn = QPushButton(ch.name)
        btn.setFixedWidth(110)
        btn.setToolTip(f"{ch.instrument}{' / ' + ch.patch if ch.patch else ''}  (double-click: piano roll)")
        self.btn = btn
        self.set_selected(selected)
        btn.clicked.connect(lambda: doc.select(channel=ch.name))
        btn.mouseDoubleClickEvent = lambda e: rack.openPianoRoll.emit(ch.name)
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(lambda pos: rack.channel_menu(ch.name, btn.mapToGlobal(pos)))
        btn.setToolTip(btn.toolTip() + "\nright-click: rename / delete")
        lay.addWidget(btn)
        self.steps: list[StepButton] = []
        for i in range(steps):
            b = StepButton(i)
            self.steps.append(b)
            b.setChecked(i in on)
            b.clicked.connect(lambda _=False, i=i: rack.toggle(ch.name, i))
            lay.addWidget(b)
            if i % 4 == 3:
                lay.addSpacing(3)
        lay.addStretch(1)


    def set_selected(self, selected: bool) -> None:
        self.btn.setStyleSheet("QPushButton{text-align:left; padding:3px 6px; background:%s; border-radius:3px;}"
                               % ("#5b6b85" if selected else "#353a42"))

    def update_from(self, ch: Channel, on: set[int], selected: bool) -> None:
        """Show new values without rebuilding the widgets (fast, keeps focus and scroll)."""
        self.mute.setChecked(not ch.mute)
        for box, v in ((self.vol, ch.volume), (self.pan, ch.pan)):
            if abs(box.value - v) > 1e-9:  # don't touch a box the user may be typing in
                box.set_value(v)
        for b in self.steps:
            if b.isChecked() != (b.index in on):
                b.setChecked(b.index in on)
        self.set_selected(selected)


class ChannelRack(QWidget):
    openPianoRoll = Signal(str)

    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        top = QHBoxLayout()
        top.addWidget(QLabel("Pattern"))
        self.pattern_box = QComboBox()
        self.pattern_box.setMinimumWidth(120)
        self.pattern_box.activated.connect(lambda i: doc.select(pattern=self.pattern_box.itemText(i)))
        top.addWidget(self.pattern_box)
        add_pat = QPushButton("+ pattern")
        add_pat.clicked.connect(self.add_pattern)
        top.addWidget(add_pat)
        top.addSpacing(20)
        self.preset_box = QComboBox()
        for name in sorted(PRESETS):
            if available(name):
                self.preset_box.addItem(name)
        top.addWidget(self.preset_box)
        add_ch = QPushButton("+ channel")
        add_ch.clicked.connect(lambda: self.add_channel())  # clicked sends a bool; don't pass it on
        top.addWidget(add_ch)
        add_smp = QPushButton("+ sample")
        add_smp.setToolTip("Add an audio file (WAV, FLAC, MP3, OGG) as an instrument; or drag files here")
        add_smp.clicked.connect(lambda: self.add_sample())
        top.addWidget(add_smp)
        self.setAcceptDrops(True)
        top.addStretch(1)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.addLayout(self.rows)
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(scroll)
        doc.changed.connect(self.rebuild)
        doc.selection.connect(self.rebuild)
        self.rebuild()

    def rebuild(self) -> None:
        """Update in place when the channel list is the same; rebuild rows only when it changed."""
        p, pat = self.doc.project, self.doc.current_pattern()
        names = [x.name for x in p.patterns]
        if [self.pattern_box.itemText(i) for i in range(self.pattern_box.count())] != names:
            self.pattern_box.clear()
            self.pattern_box.addItems(names)
        if pat and self.pattern_box.currentText() != pat.name:
            self.pattern_box.setCurrentText(pat.name)
        steps = int(round((pat.length if pat else 4) / STEP))
        shape = (tuple((c.name, c.instrument, c.patch, c.program, c.bank) for c in p.channels), steps)

        def on_steps(ch):
            on = set()
            if pat:
                for n in pat.notes.get(ch.name, []):
                    i = n.start / STEP
                    if abs(i - round(i)) < 1e-6 and round(i) < steps:
                        on.add(round(i))
            return on

        if shape == getattr(self, "_shape", None):
            for row, ch in zip(self._row_widgets, p.channels):
                row.update_from(ch, on_steps(ch), ch.name == self.doc.channel)
            return
        self._shape = shape
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._row_widgets = [ChannelRow(self, ch, steps, on_steps(ch), ch.name == self.doc.channel)
                             for ch in p.channels]
        for row in self._row_widgets:
            self.rows.addWidget(row)

    # ---- samples ---------------------------------------------------------------
    def add_sample(self, path: str | None = None) -> bool:
        """New sample channel from an audio file (one-shot; the note C4 plays it at its own pitch)."""
        from ..model import Channel
        from ..model import ProjectError
        from ..samples import check_path
        if path is None:
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(self, "Add sample", "", "Audio (*.wav *.flac *.mp3 *.ogg *.aiff *.aif)")
            if not path:
                return False
        try:
            p = check_path(path).resolve()
        except ProjectError as e:
            self.doc.message.emit(str(e))
            return False
        names = {c.name for c in self.doc.project.channels}
        name, i = p.stem[:24] or "sample", 2
        base = name
        while name in names:
            name, i = f"{base}-{i}", i + 1
        ch = Channel(name=name, instrument="sample", sample=str(p), root=60, volume=0.8)
        if self.doc.edit(f"add sample {p.name}", lambda q: q.channels.append(ch)):
            self.doc.select(channel=name)
            return True
        return False

    def dragEnterEvent(self, e):
        from ..samples import AUDIO_EXT
        if e.mimeData().hasUrls() and any(Path(u.toLocalFile()).suffix.lower() in AUDIO_EXT
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            self.add_sample(u.toLocalFile())
        e.acceptProposedAction()

    def set_play_step(self, step: int | None) -> None:
        """Light the step column that is playing now (only repaints when it moves)."""
        if step == getattr(self, "_play_step", None):
            return
        old, self._play_step = getattr(self, "_play_step", None), step
        for row in getattr(self, "_row_widgets", []):
            for i in (old, step):
                if i is not None and 0 <= i < len(row.steps):
                    row.steps[i].set_playing(i == step)

    def channel_menu(self, name: str, at) -> None:
        """Right-click on a channel name."""
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("Open in Piano Roll", lambda: self.openPianoRoll.emit(name))
        m.addAction("Rename...", lambda: self.rename_channel(name))
        m.addSeparator()
        m.addAction("Delete instrument", lambda: self.delete_channel(name))
        m.exec(at)

    def delete_channel(self, name: str) -> None:
        """Remove the channel and its notes in every pattern (Ctrl+Z brings it back)."""
        def fn(p):
            p.channels.remove(p.channel(name))
            for pat in p.patterns:
                pat.notes.pop(name, None)
        if self.doc.edit(f"delete channel {name}", fn):
            self.doc.message.emit(f"deleted {name} (Ctrl+Z brings it back)")

    def rename_channel(self, name: str, new: str | None = None) -> None:
        if new is None:
            from PySide6.QtWidgets import QInputDialog
            new, ok = QInputDialog.getText(self, "Rename instrument", "New name:", text=name)
            if not ok:
                return
        new = (new or "").strip()
        if not new or new == name:
            return

        def fn(p):
            if any(c.name == new for c in p.channels):
                raise ValueError(f"channel {new} already exists")
            p.channel(name).name = new
            for pat in p.patterns:
                if name in pat.notes:
                    pat.notes[new] = pat.notes.pop(name)
        if self.doc.edit(f"rename {name} -> {new}", fn) and self.doc.channel == name:
            self.doc.select(channel=new)

    def toggle(self, channel: str, index: int) -> None:
        if not self.doc.pattern:
            self.doc.message.emit("add a pattern first")
            self.rebuild()
            return
        self.doc.edit(f"step {index + 1} {channel}", lambda p: toggle_step(p, self.doc.pattern, channel, index))

    def add_pattern(self) -> None:
        names = {x.name for x in self.doc.project.patterns}
        i = len(names) + 1
        while f"Pattern {i}" in names:
            i += 1
        name = f"Pattern {i}"
        if self.doc.edit(f"add pattern {name}", lambda p: p.patterns.append(Pattern(name=name))):
            self.doc.select(pattern=name)

    def add_channel(self, name: str | None = None) -> None:
        """Add the chosen sound as a new channel. No name given: use the sound's name (kick, kick-2...)."""
        preset = self.preset_box.currentText()
        names = {c.name for c in self.doc.project.channels}
        if not isinstance(name, str) or not name.strip():
            name, i = preset, 2
            while name in names:
                name, i = f"{preset}-{i}", i + 1
        name = name.strip()

        def fn(p):
            if any(c.name == name for c in p.channels):
                raise ValueError(f"channel {name} already exists")
            fields = PRESETS[preset]
            p.channels.append(Channel(name=name, **fields))
        if self.doc.edit(f"add channel {name}", fn):
            self.doc.select(channel=name)
