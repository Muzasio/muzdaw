"""KnobBox: a knob and a number box side by side, always in sync.

Drag the knob up/down (Shift = fine), use the wheel, double-click to reset,
or type an exact value in the box: 0.75, -6dB, 440Hz, 2kHz, 50%.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from ..effects import parse_value

ARC_START, ARC_SPAN = 225.0, -270.0  # degrees, Qt counts counter-clockwise


def _pen(color: str, width: float) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


class Knob(QWidget):
    moved = Signal(float)      # normalized 0..1 while dragging
    released = Signal()
    reset = Signal()

    def __init__(self, size: int = 34, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.norm = 0.0
        self._drag_y: float | None = None
        self._drag_norm = 0.0
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    def set_norm(self, n: float) -> None:
        self.norm = min(1.0, max(0.0, n))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(4, 4, self.width() - 8, self.height() - 8)
        p.setPen(_pen("#3a3f47", 4))
        p.drawArc(r, int(ARC_START * 16), int(ARC_SPAN * 16))
        p.setPen(_pen("#f0a030", 4))
        p.drawArc(r, int(ARC_START * 16), int(ARC_SPAN * self.norm * 16))
        ang = math.radians(ARC_START + ARC_SPAN * self.norm)
        c = r.center()
        rad = r.width() / 2 - 3
        p.setPen(QPen(QColor("#e8e8e8"), 2))
        p.drawLine(c, QPointF(c.x() + rad * math.cos(ang), c.y() - rad * math.sin(ang)))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_y, self._drag_norm = e.position().y(), self.norm

    def mouseMoveEvent(self, e):
        if self._drag_y is None:
            return
        scale = 2000.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 200.0
        self.set_norm(self._drag_norm + (self._drag_y - e.position().y()) / scale)
        self.moved.emit(self.norm)

    def mouseReleaseEvent(self, e):
        if self._drag_y is not None:
            self._drag_y = None
            self.released.emit()

    def mouseDoubleClickEvent(self, e):
        self.reset.emit()

    def wheelEvent(self, e):
        step = 0.002 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.02
        self.set_norm(self.norm + step * (1 if e.angleDelta().y() > 0 else -1))
        self.moved.emit(self.norm)
        self.released.emit()


class KnobBox(QWidget):
    valueChanged = Signal(float)   # while dragging (preview)
    committed = Signal(float)      # final value (drag released, wheel, Enter, reset)

    def __init__(self, label: str = "", minimum: float = 0.0, maximum: float = 1.0, value: float = 0.0,
                 default: float | None = None, unit: str = "", decimals: int = 2, log: bool = False,
                 to_text=None, from_text=None, compact: bool = False, parent=None):
        super().__init__(parent)
        self.minimum, self.maximum = float(minimum), float(maximum)
        self.default = float(value if default is None else default)
        self.unit, self.decimals, self.log = unit, decimals, log and minimum > 0
        self._to_text, self._from_text = to_text, from_text
        self.value = float(value)
        self.knob = Knob(26 if compact else 34)
        self.box = QLineEdit()
        self.box.setFixedWidth(58 if compact else 72)
        self.box.setAlignment(Qt.AlignmentFlag.AlignRight)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        row.addWidget(self.knob)
        row.addWidget(self.box)
        if label and not compact:
            col = QVBoxLayout(self)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(1)
            lab = QLabel(label)
            lab.setStyleSheet("color:#9aa3ad; font-size:10px;")
            col.addWidget(lab)
            col.addLayout(row)
        else:
            self.setLayout(row)
            if label:
                self.setToolTip(label)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.knob.moved.connect(self._knob_moved)
        self.knob.released.connect(lambda: self.committed.emit(self.value))
        self.knob.reset.connect(lambda: self.set_value(self.default, commit=True))
        self.box.editingFinished.connect(self._typed)
        self.set_value(self.value)

    # value <-> knob position
    def to_norm(self, v: float) -> float:
        if self.maximum == self.minimum:
            return 0.0
        if self.log:
            return math.log(v / self.minimum) / math.log(self.maximum / self.minimum)
        return (v - self.minimum) / (self.maximum - self.minimum)

    def from_norm(self, n: float) -> float:
        if self.log:
            return self.minimum * (self.maximum / self.minimum) ** n
        return self.minimum + n * (self.maximum - self.minimum)

    def text(self, v: float) -> str:
        if self._to_text:
            return self._to_text(v)
        return f"{v:.{self.decimals}f}{self.unit}"

    def set_value(self, v: float, commit: bool = False) -> None:
        self.value = min(self.maximum, max(self.minimum, float(v)))
        self.knob.set_norm(self.to_norm(self.value))
        self.box.setText(self.text(self.value))
        self.box.setStyleSheet("")
        if commit:
            self.committed.emit(self.value)

    def _knob_moved(self, n: float) -> None:
        v = self.from_norm(n)
        if not self.log and self.decimals == 0:
            v = round(v)
        self.value = v
        self.box.setText(self.text(v))
        self.valueChanged.emit(v)

    def _typed(self) -> None:
        raw = self.box.text()
        try:
            v = self._from_text(raw) if self._from_text else parse_value(raw)
            if isinstance(v, (bool, str)):
                raise ValueError
        except (ValueError, ZeroDivisionError, OverflowError):
            self.box.setStyleSheet("border: 1px solid #d04040;")
            self.box.setToolTip(f"not a number: {raw!r}")
            return
        if abs(float(v) - self.value) < 1e-12 and self.box.text() == self.text(self.value):
            return
        self.set_value(float(v), commit=True)


# ---- ready-made boxes ----------------------------------------------------------

def gain_to_text(v: float) -> str:
    return "-inf dB" if v <= 1e-6 else f"{20 * math.log10(v):+.1f} dB"


def gain_from_text(t: str) -> float:
    s = t.strip().lower()
    if s in ("-inf", "-inf db", "off"):
        return 0.0
    v = parse_value(t)
    if isinstance(v, (bool, str)):
        raise ValueError(t)
    return 10 ** (v / 20) if s.endswith("db") else float(v)


def gain_box(label: str, value: float, maximum: float = 2.0, compact: bool = False) -> KnobBox:
    """Linear gain 0..max, shown in dB; accepts '0.8' or '-6dB'."""
    return KnobBox(label, 0.0, maximum, value, default=0.8 if maximum >= 2 else 1.0,
                   to_text=gain_to_text, from_text=gain_from_text, compact=compact)


def pan_to_text(v: float) -> str:
    if abs(v) < 0.005:
        return "C"
    return f"{abs(v) * 100:.0f}{'L' if v < 0 else 'R'}"


def pan_from_text(t: str) -> float:
    s = t.strip().upper()
    if s in ("C", "0"):
        return 0.0
    if s[-1:] in ("L", "R") and s[:-1].replace(".", "", 1).isdigit():
        v = float(s[:-1]) / 100
        return -v if s.endswith("L") else v
    v = parse_value(t)
    if isinstance(v, (bool, str)):
        raise ValueError(t)
    return float(v)


def pan_box(label: str, value: float, compact: bool = False) -> KnobBox:
    """Pan -1..1, shown as 30L / C / 30R; accepts -0.3, 30L, C."""
    return KnobBox(label, -1.0, 1.0, value, default=0.0, to_text=pan_to_text, from_text=pan_from_text,
                   compact=compact)
