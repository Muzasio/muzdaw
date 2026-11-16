"""Mixer: one strip per channel, bus and master. Each strip has volume, pan,
mute, output and an effect chain; the selected effect's params show below.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..effects import BUILTINS, make_effect, parse_value
from ..model import Bus
from .knobbox import KnobBox, gain_box, pan_box


def param_range(name: str, value) -> tuple[float, float, bool]:
    """(min, max, log) guessed from a pedalboard param name."""
    n = name.lower()
    if n.endswith("_db"):
        return -60.0, 24.0, False
    if n.endswith("_hz") or n.startswith("cutoff") or "frequency" in n:
        return 20.0, 20000.0, True
    if n.endswith("_ms"):
        return 0.0, 2000.0, False
    if n.endswith("_seconds"):
        return 0.0, 5.0, False
    if n == "ratio":
        return 1.0, 30.0, False
    if n == "semitones":
        return -24.0, 24.0, False
    if n == "bit_depth":
        return 1.0, 32.0, False
    if n == "q":
        return 0.1, 20.0, True
    if n in ("drive", "resonance"):
        return 0.0, 10.0 if n == "drive" else 1.0, False
    v = abs(float(value)) if isinstance(value, (int, float)) else 1.0
    return 0.0, max(1.0, math.ceil(v * 2)), False


class Strip(QFrame):
    def __init__(self, mixer: Mixer, kind: str, name: str):
        super().__init__()
        self.mixer, self.kind, self.name = mixer, kind, name
        doc = mixer.doc
        p = doc.project
        obj = None if kind == "master" else (p.channel(name) if kind == "channel" else p.bus(name))
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(178)
        color = {"master": "#6b5b85", "bus": "#5b7b85", "channel": "#3f454e"}[kind]
        self.setStyleSheet(f"Strip{{background:#2c3036; border-top:3px solid {color};}}")
        lay = QVBoxLayout(self)
        lay.setSpacing(4)
        title = QLabel(f"<b>{name}</b> <span style='color:#888'>{kind}</span>")
        lay.addWidget(title)
        vol_value = p.master if kind == "master" else obj.volume
        vol = gain_box("volume", vol_value)
        vol.committed.connect(lambda v: doc.edit(f"volume {name}", lambda q: self._set(q, "volume", v)))
        lay.addWidget(vol)
        if kind != "master":
            pan = pan_box("pan", obj.pan)
            pan.committed.connect(lambda v: doc.edit(f"pan {name}", lambda q: self._set(q, "pan", v)))
            lay.addWidget(pan)
            row = QHBoxLayout()
            mute = QPushButton("mute")
            mute.setCheckable(True)
            mute.setChecked(obj.mute)
            mute.clicked.connect(lambda on: doc.edit(f"{'mute' if on else 'unmute'} {name}",
                                                     lambda q: self._set(q, "mute", on)))
            row.addWidget(mute)
            out = QComboBox()
            targets = ["master"] + [b.name for b in p.buses if b.name != name]
            out.addItems(targets)
            out.setCurrentText(obj.output)
            out.setToolTip("output")
            out.activated.connect(lambda i: doc.edit(f"route {name} -> {out.itemText(i)}",
                                                     lambda q: self._set(q, "output", out.itemText(i))))
            row.addWidget(out)
            lay.addLayout(row)
            if kind == "channel" and p.buses:
                srow = QHBoxLayout()
                self.send_bus = QComboBox()
                self.send_bus.addItems([b.name for b in p.buses])
                self.send_bus.setToolTip("send to bus (post-fader)")
                last = mixer.send_choice.get(name)
                if last in [b.name for b in p.buses]:
                    self.send_bus.setCurrentText(last)
                srow.addWidget(self.send_bus)
                self.send_level = gain_box("send", obj.sends.get(self.send_bus.currentText(), 0.0), compact=True)
                self.send_bus.currentTextChanged.connect(self._show_send)
                self.send_level.committed.connect(self._set_send)
                srow.addWidget(self.send_level)
                lay.addLayout(srow)
        lay.addWidget(QLabel("effects"))
        self.fx_list = QListWidget()
        self.fx_list.setFixedHeight(80)
        for i, fx in enumerate(self.chain(p)):
            label = fx.get("name") or (fx["plugin"].rsplit("/", 1)[-1] if fx["type"] == "plugin" else fx["type"])
            item = QListWidgetItem(f"{i}: {label}{'  (off)' if fx.get('bypass') else ''}")
            self.fx_list.addItem(item)
        self.fx_list.currentRowChanged.connect(self.show_params)
        lay.addWidget(self.fx_list)
        btns = QHBoxLayout()
        self.add_box = QComboBox()
        self.add_box.addItems(sorted(BUILTINS))
        self.add_box.setCurrentText("reverb")
        btns.addWidget(self.add_box)
        add = QPushButton("+")
        add.setFixedWidth(26)
        add.setToolTip("add effect")
        add.clicked.connect(self.add_fx)
        btns.addWidget(add)
        lay.addLayout(btns)
        btns2 = QHBoxLayout()
        for text, tip, fn in (("−", "remove", self.del_fx), ("on/off", "bypass", self.bypass_fx),
                              ("↑", "move up", lambda: self.move_fx(-1)), ("↓", "move down", lambda: self.move_fx(1))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            btns2.addWidget(b)
        lay.addLayout(btns2)
        self.params = QFormLayout()
        self.params.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.params.setVerticalSpacing(2)
        holder = QWidget()
        holder.setLayout(self.params)
        pscroll = QScrollArea()
        pscroll.setWidgetResizable(True)
        pscroll.setWidget(holder)
        pscroll.setMinimumHeight(170)
        pscroll.setFrameShape(QFrame.Shape.NoFrame)
        lay.addWidget(pscroll, 1)
        sel = mixer.selected_fx.get((kind, name))
        if sel is not None and sel < self.fx_list.count():
            self.fx_list.setCurrentRow(sel)

    # ---- model helpers ---------------------------------------------------------
    def target(self, p):
        return p.channel(self.name) if self.kind == "channel" else p.bus(self.name)

    def chain(self, p) -> list[dict]:
        return p.master_fx if self.kind == "master" else self.target(p).fx

    def _set(self, p, attr: str, v) -> None:
        if self.kind == "master":
            p.master = v
        else:
            setattr(self.target(p), attr, v)

    def _show_send(self, bus: str) -> None:
        self.mixer.send_choice[self.name] = bus
        self.send_level.set_value(self.target(self.mixer.doc.project).sends.get(bus, 0.0))

    def _set_send(self, v: float) -> None:
        bus = self.send_bus.currentText()
        self.mixer.send_choice[self.name] = bus

        def fn(p):
            sends = self.target(p).sends
            if v <= 1e-6:
                sends.pop(bus, None)
            else:
                sends[bus] = v
        self.mixer.doc.edit(f"send {self.name} -> {bus}", fn)

    def row(self) -> int:
        return self.fx_list.currentRow()

    # ---- effect actions ----------------------------------------------------------
    def add_fx(self) -> None:
        t = self.add_box.currentText()
        n = len(self.chain(self.mixer.doc.project))
        self.mixer.selected_fx[(self.kind, self.name)] = n
        self.mixer.doc.edit(f"add {t} to {self.name}",
                            lambda p: self.chain(p).append({"type": t, "params": {}, "bypass": False}))

    def del_fx(self) -> None:
        i = self.row()
        if i >= 0:
            self.mixer.doc.edit(f"remove fx {i} from {self.name}", lambda p: self.chain(p).pop(i))

    def bypass_fx(self) -> None:
        i = self.row()
        if i < 0:
            return

        def fn(p):
            fx = self.chain(p)[i]
            fx["bypass"] = not fx.get("bypass", False)
        self.mixer.doc.edit(f"toggle fx {i} on {self.name}", fn)

    def move_fx(self, d: int) -> None:
        i = self.row()
        j = i + d
        if i < 0 or not 0 <= j < self.fx_list.count():
            return
        self.mixer.selected_fx[(self.kind, self.name)] = j

        def fn(p):
            c = self.chain(p)
            c[i], c[j] = c[j], c[i]
        self.mixer.doc.edit(f"move fx {i} -> {j} on {self.name}", fn)

    def show_params(self, i: int) -> None:
        while self.params.rowCount():
            self.params.removeRow(0)
        self.mixer.selected_fx[(self.kind, self.name)] = i
        if i < 0:
            return
        fx = self.chain(self.mixer.doc.project)[i]
        doc = self.mixer.doc

        def setter(key):
            def fn(v):
                doc.edit(f"{self.name} fx {i} {key}={v:g}" if isinstance(v, float) else f"{self.name} fx {i} {key}",
                         lambda p: self.chain(p)[i].setdefault("params", {}).__setitem__(key, v))
            return fn
        if fx["type"] == "plugin":
            for k, v in (fx.get("params") or {}).items():
                self.params.addRow(k, QLabel(str(v)))
            edit = QLineEdit()
            edit.setPlaceholderText("name=value")
            edit.returnPressed.connect(lambda: self._plugin_param(i, edit.text()))
            self.params.addRow("set", edit)
            return
        try:
            proc = make_effect(fx)
        except ValueError as e:
            self.params.addRow(QLabel(str(e)))
            return
        from ..effects import _is_param
        for key in sorted(k for k in dir(proc) if not k.startswith("_") and _is_param(proc, k)):
            cur = getattr(proc, key)
            if type(cur).__name__ == "Mode":
                box = QComboBox()
                modes = [m for m in dir(type(cur)) if m.isupper()]
                box.addItems(modes)
                box.setCurrentText(cur.name)
                box.activated.connect(lambda j, key=key, box=box: setter(key)(box.itemText(j)))
                self.params.addRow(key, box)
                continue
            lo, hi, log = param_range(key, cur)
            kb = KnobBox("", lo, hi, float(cur), decimals=2 if hi <= 100 else 0, log=log, compact=True)
            kb.committed.connect(setter(key))
            self.params.addRow(key.replace("_", " "), kb)

    def _plugin_param(self, i: int, text: str) -> None:
        key, sep, val = text.partition("=")
        if not sep:
            self.mixer.doc.message.emit("use name=value")
            return
        v = parse_value(val)

        def fn(p):
            fx = self.chain(p)[i]
            fx.setdefault("params", {})[key.strip()] = v
            make_effect(fx)  # raises ValueError on unknown names
        self.mixer.doc.edit(f"{self.name} fx {i} {key.strip()}", fn)


class Mixer(QWidget):
    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        self.selected_fx: dict[tuple[str, str], int] = {}
        self.send_choice: dict[str, str] = {}
        top = QHBoxLayout()
        add_bus = QPushButton("+ bus")
        add_bus.clicked.connect(lambda: self.add_bus())
        top.addWidget(add_bus)
        top.addStretch(1)
        self.strips = QHBoxLayout()
        self.strips.setAlignment(Qt.AlignmentFlag.AlignLeft)
        inner = QWidget()
        inner.setLayout(self.strips)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        self.scroll = scroll
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(scroll)
        self._sig: str | None = None
        self._dirty = False
        doc.changed.connect(self.rebuild)
        self.rebuild(force=True)

    def _signature(self) -> str:
        """Everything the mixer shows. Note/tempo/playlist edits don't change it."""
        import json
        from dataclasses import asdict
        p = self.doc.project
        return json.dumps([p.master, p.master_fx, [asdict(b) for b in p.buses],
                           [asdict(c) for c in p.channels]], sort_keys=True, default=str)

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.rebuild()

    def rebuild(self, force: bool = False) -> None:
        """Rebuild strips only when mixer settings changed, and only while the mixer is visible."""
        sig = self._signature()
        if sig == self._sig and not force:
            return
        if not self.isVisible() and not force:
            self._dirty = True
            return
        self._sig, self._dirty = sig, False
        hpos = self.scroll.horizontalScrollBar().value()
        while self.strips.count():
            w = self.strips.takeAt(0).widget()
            if w:
                w.deleteLater()
        p = self.doc.project
        self.strips.addWidget(Strip(self, "master", "master"))
        for b in p.buses:
            self.strips.addWidget(Strip(self, "bus", b.name))
        for c in p.channels:
            self.strips.addWidget(Strip(self, "channel", c.name))
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.scroll.horizontalScrollBar().setValue(hpos))  # keep scroll place

    def add_bus(self, name: str | None = None) -> None:
        if name is None:
            name, ok = QInputDialog.getText(self, "New bus", "Name:")
            if not ok or not name.strip():
                return
        name = name.strip()

        def fn(p):
            if any(b.name == name for b in p.buses) or name == "master":
                raise ValueError(f"bus {name} already exists")
            p.buses.append(Bus(name=name))
        self.doc.edit(f"add bus {name}", fn)
