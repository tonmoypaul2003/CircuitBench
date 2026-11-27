"""Schematic glyphs for discrete ngspice devices."""
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPen, QPolygonF


def draw_device(painter, part, family, selected, custom_color=None):
    color = QColor("#d46127" if selected else (custom_color or "#173d37"))
    painter.setPen(QPen(color, .09))

    def path(*points):
        for start, end in zip(points, points[1:]):
            painter.drawLine(QPointF(*start), QPointF(*end))

    def arrow(tail, tip):
        tip = QPointF(*tip)
        delta = tip - QPointF(*tail)
        length = (delta.x()**2 + delta.y()**2)**.5
        direction = delta/length
        normal = QPointF(-direction.y(), direction.x())
        base = tip-direction*.37
        painter.save()
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([tip, base+normal*.14, base-normal*.14]))
        painter.restore()

    pins = part.device_offsets()
    if part.kind == "Q":
        collector, base, emitter = pins
        path(base, (-.35, 0))
        path((-.35, -.8), (-.35, .8))
        path((-.35, -.4), (1, -1.12), collector)
        path((-.35, .4), (1, 1.12), emitter)
        if family == "PNP":
            arrow((.7, .96), (-.08, .54))
        else:
            arrow((.02, .6), (.8, 1.01))
    elif part.kind == "J":
        drain, gate, source = pins
        path(drain, (1, -.95), (.24, -.95), (.24, .95), (1, .95), source)
        path(gate, (.24, 0))
        if family == "PJF":
            arrow((.24, 0), (-.38, 0))
        else:
            arrow((-.4, 0), (.2, 0))
    elif part.kind == "M":
        drain, gate, source = pins[:3]
        path(gate, (-.43, 0))
        path((-.43, -.93), (-.43, .93))
        for y in (-.74, 0, .74):
            path((-.08, y-.22), (-.08, y+.22))
        path(drain, (1, -.74), (-.08, -.74))
        path(source, (1, .74), (-.08, .74))
        body = pins[3] if len(pins) == 4 else (1, 0)
        path((-.08, 0), body)
        if len(pins) == 3:
            path(body, (1, .74))
        if family in ("PMOS", "PVDMOS"):
            arrow((.05, 0), (.62, 0))
        else:
            arrow((.62, 0), (.02, 0))
    else:
        left, right, control_positive, control_negative = pins
        path(left, (-.68, -1))
        path((.68, -1), right)
        path((-.68, -1), (.57, -1.55))
        for x in (-.68, .68):
            painter.drawEllipse(QPointF(x, -1), .105, .105)
        path(control_positive, (-1, .53), (-.34, .53))
        path(control_negative, (1, .53), (.34, .53))
        path((-.56, .72), (-.56, .34))
        painter.save()
        painter.setPen(QPen(color, .07, Qt.PenStyle.DashLine))
        path((0, .53), (0, -.79))
        painter.restore()
    if part.legacy_pin_layout:
        painter.setPen(QPen(color, .075, Qt.PenStyle.DotLine))
        for (x, y), old in zip(pins, part.box_offsets()):
            if old == (x, y):
                continue
            path((x, y), (old[0], y), old)
