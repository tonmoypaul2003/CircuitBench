"""Schematic canvas using integer model coordinates."""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPolygonF, QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from circuit import MODELED_SYMBOLS, Part, COMPONENT_SYMBOLS as SYMBOLS, connectivity, on_wire, part_parameters, symbol_geometry
from desktop_theme import desktop_colors
from device_symbols import draw_device
from spice_library import MODEL_PORTS


SCHEMATIC_COLORS = {
    "#edf2f0": "panel", "#fcfdfc": "sheet", "#cbd9d1": "grid", "#86a49b": "boundary",
    "#0f766e": "net", "#157c72": "net", "#173d37": "ink", "#162e29": "ink",
    "#d46127": "selection", "#80469c": "marker", "#1d56b3": "highlight", "#b34b1d": "current",
}


SOURCE_LABELS = {
    "dc": "DC", "ac": "AC", "ac_phase": "AC φ",
    "SIN:0": "Offset", "SIN:1": "Amp", "SIN:2": "f", "SIN:3": "Delay",
    "SIN:4": "Damp", "SIN:5": "φ",
    "PULSE:3": "Rise", "PULSE:4": "Fall", "PULSE:5": "Width", "PULSE:6": "Period",
    "EXP:0": "Init", "EXP:1": "Pulse", "EXP:2": "Rise delay", "EXP:3": "Rise τ",
    "EXP:4": "Fall delay", "EXP:5": "Fall τ",
}


def parameter_label(key,name,value):
    if key=="waveform":
        return value
    if key=="PWL:0":
        return f"{len(value.split())//2} PWL pairs"
    caption,_,unit = name.partition(" (")
    name = SOURCE_LABELS.get(key,caption)
    if key in ("ac_phase","SIN:5"):
        return f"{name} {value}°"
    return f"{name} {value} {unit[:-1]}" if unit else f"{name} {value}"


def engineering(value, unit):
    for scale, prefix in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""),
                          (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")):
        if abs(value) >= scale or value == 0:
            return f"{value/scale:.4g} {prefix if value else ''}{unit}"
    return f"{value:.3g} {unit}"


class Canvas(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.cell = 18.0
        self.offset = QPointF()
        self.begin = None
        self.drag = None
        self.move_preview = None
        self.group_offset = (0, 0)
        self.library_drag = False
        self.setAcceptDrops(True)
        self.pointer = (0, 0)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def origin(self):
        return QPointF(self.width()/2, self.height()/2) + self.offset

    def grid(self, pos):
        p = (pos - self.origin()) / self.cell
        return round(p.x()), round(p.y())

    def ink(self, light):
        if getattr(self,"glow_ink",None) is not None:
            return self.glow_ink
        if light=="#173d37" and getattr(self,"symbol_color",None):
            return QColor(self.symbol_color)
        colors = desktop_colors(self.editor.dark_theme)
        return QColor(colors.get(SCHEMATIC_COLORS.get(light), light))

    def paintEvent(self, event):
        self.label_hits = []
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.ink("#edf2f0"))
        origin = self.origin()
        sheet = QRectF(origin.x()-26*self.cell, origin.y()-17*self.cell,
                       52*self.cell, 34*self.cell)
        painter.fillRect(sheet, self.ink("#fcfdfc"))
        painter.setPen(self.ink("#cbd9d1"))
        x = origin.x() % self.cell
        while getattr(self.editor,'show_dots',True) and x < self.width():
            y = origin.y() % self.cell
            while y < self.height():
                painter.drawPoint(QPointF(x, y))
                y += self.cell
            x += self.cell
        painter.setPen(self.ink("#86a49b"))
        painter.drawRect(sheet)
        for column in range(4):
            center = sheet.left() + sheet.width()*(column+.5)/4
            painter.drawText(QPointF(center-4, sheet.top()-6), str(column+1))
        for row in range(3):
            center = sheet.top() + sheet.height()*(row+.5)/3
            painter.drawText(QPointF(sheet.left()-16, center+4), "ABC"[row])
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(origin)
        painter.scale(self.cell, self.cell)
        project = self.editor.project
        for i,note in enumerate(project.annotations):
            x,y,text = note[:3]
            style = note[3] if len(note)>3 else {}
            painter.save()
            painter.translate(x,y)
            painter.scale(1/self.cell,1/self.cell)
            font = painter.font()
            font.setPixelSize(style.get("size",12))
            font.setBold(style.get("bold",False))
            font.setItalic(style.get("italic",False))
            painter.setFont(font)
            color = style.get("color",project.element_colors.get(f"text:{i}"))
            painter.setPen(QColor(color) if color else self.ink("#173d37"))
            painter.drawText(QPointF(0,0),text)
            if i==getattr(self,"annotation_index",None):
                painter.drawLine(0,3,painter.fontMetrics().horizontalAdvance(text),3)
            painter.restore()
        reading = self.editor.bias_list.selectedItems()
        net, terminal = reading[0].data(Qt.ItemDataRole.UserRole) if reading else (None, None)
        highlighted = {point for point, name in self.editor.reading_nets.items() if name == net}
        highlight_ink = self.ink("#1d56b3")
        for index, (a, b) in enumerate(project.wires):
            wire_ink = self.ink("#d46127" if index in self.editor.selected_wires else "#0f766e")
            painter.setPen(QPen(highlight_ink if a in highlighted else wire_ink,
                               .20 if a in highlighted else .09))
            painter.drawLine(QPointF(*a), QPointF(*b))
        painter.setPen(QPen(self.ink("#0f766e"), .09))
        if self.begin is not None:
            bend = (self.pointer[0], self.begin[1])
            painter.drawLine(QPointF(*self.begin), QPointF(*bend))
            painter.drawLine(QPointF(*bend), QPointF(*self.pointer))
        endpoints = {point for wire in project.wires for point in wire}
        selected_junctions = {project.junctions[i] for i in self.editor.selected_junctions}
        for p in sorted(endpoints | set(project.junctions)):
            count = sum(1 if p in w else 2 for w in project.wires if on_wire(p, w))
            if count >= 3 or p in project.junctions:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(highlight_ink if p in highlighted else
                                 self.ink("#d46127" if p in selected_junctions else "#0f766e"))
                painter.drawEllipse(QPointF(*p), .13, .13)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index, part in enumerate(project.parts):
            selected = index in self.editor.selected_parts
            painter.save()
            painter.translate(part.x, part.y)
            painter.rotate(part.rotation * 90)
            self.symbol_color = part.label_style.get("symbol_color",project.element_colors.get(part.ref))

            def draw_component():
                if part.kind == "X":
                    self.draw_subcircuit(painter, part, selected)
                elif part.kind in MODELED_SYMBOLS:
                    self.draw_modeled_symbol(painter, part, selected)
                else:
                    self.draw_symbol(painter, part.kind, selected)

            if part is getattr(self.editor,"component_highlight",None):
                self.glow_ink = QColor(highlight_ink)
                self.glow_ink.setAlpha(65)
                for dx,dy in ((-.10,0),(.10,0),(0,-.10),(0,.10)):
                    painter.save()
                    painter.translate(dx,dy)
                    draw_component()
                    painter.restore()
                self.glow_ink = highlight_ink
            draw_component()
            self.glow_ink = None
            self.symbol_color = None
            painter.restore()
            if selected and part.kind in MODELED_SYMBOLS:
                self.draw_pin_targets(painter, part)
            painter.save()
            painter.translate(part.x, part.y)
            painter.scale(1/self.cell, 1/self.cell)
            color = project.element_colors.get(part.ref)
            painter.setPen(QColor(color) if color else self.ink("#162e29"))
            powers = getattr(self.editor, "part_powers", {})
            if getattr(self.editor, "power_visible", False) and self.editor.active_readings and index in powers:
                painter.drawText(QPointF(8, 38), engineering(powers[index], "W"))
            if part.kind != "G":
                style = part.label_style
                left = -max(self.cell*.9+5,(part.x-min(x for x,_ in part.pins()))*self.cell+5)
                fields = []
                if part.kind!="N" and style.get("name",True):
                    fields.append(("name",part.ref))
                if style.get("value",True):
                    if "hidden_parameters" in style:
                        hidden = set(style["hidden_parameters"])
                        fields.extend((f"param:{key}", parameter_label(key,name,value))
                                      for key,name,value in part_parameters(project,part) if key not in hidden)
                    else:
                        fields.append(("value",part.value))
                for line,(field,text) in enumerate(fields):
                    self.draw_label(painter,("part",index,field),(part.x,part.y),text,style,
                                    default_x=left,line=line)
            painter.restore()
        if highlighted:
            painter.setPen(QPen(highlight_ink, .09))
            for point in {pin for part in project.parts for pin in part.pins()} & highlighted:
                radius = .30 if point == terminal else .18
                painter.drawEllipse(QPointF(*point), radius, radius)
        bias = getattr(self.editor,"bias_action",None)
        if bias and bias.isChecked():
            if self.editor.active_readings:
                self.draw_bias(painter)
            else:
                painter.save()
                painter.scale(1/self.cell,1/self.cell)
                painter.setPen(self.ink("#0f766e"))
                seen = set()
                for (x,y),net in connectivity(project).items():
                    if net not in seen:
                        painter.drawText(QPointF(x*self.cell+4,y*self.cell-5),net)
                        seen.add(net)
                painter.restore()
        if self.editor.markers_visible:
            self.draw_markers(painter)
        if (self.underMouse() or self.library_drag) and self.editor.mode in SYMBOLS:
            self.draw_placement(painter)
        if self.drag and self.drag[0] in ("box", "group"):
            self.draw_selection_preview(painter)

    def draw_selection_preview(self, painter):
        mode, start, _ = self.drag
        edge = self.ink("#157c72")
        painter.setPen(QPen(edge, .07, Qt.PenStyle.DashLine))
        if mode == "box":
            fill = QColor(edge)
            fill.setAlpha(32)
            painter.setBrush(fill)
            painter.drawRect(QRectF(QPointF(*start), QPointF(*self.pointer)).normalized())
            return
        dx, dy = self.group_offset
        if not (dx or dy):
            return
        editor = self.editor
        delta = dx, dy
        if self.move_preview is None or self.move_preview[0] != delta:
            self.move_preview = delta, editor.routed_move(*delta)[0]
        ghost = self.move_preview[1]
        if ghost is None:
            painter.setPen(QPen(self.ink("#d46127"), .1, Qt.PenStyle.DashLine))
        else:
            original = set(editor.project.wires)
            for a, b in ghost.wires:
                if (a, b) not in original:
                    painter.drawLine(QPointF(*a), QPointF(*b))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in editor.selected_parts:
            part = editor.project.parts[i]
            center = QPointF(part.x+dx, part.y+dy)
            painter.drawRect(QRectF(center.x()-1.1, center.y()-1.1, 2.2, 2.2))
            if ghost is None:
                continue
            painter.save()
            painter.translate(center)
            painter.rotate(part.rotation*90)
            if part.kind == "X":
                self.draw_subcircuit(painter, part, True)
            elif part.kind in MODELED_SYMBOLS:
                self.draw_modeled_symbol(painter, part, True)
            else:
                self.draw_symbol(painter, part.kind, True)
            painter.restore()
            for j, target in enumerate(editor.project.parts):
                if j in editor.selected_parts:
                    continue
                if target.x == center.x() and abs(target.y-center.y()) <= 24:
                    painter.drawLine(center, QPointF(target.x, target.y))
                if target.y == center.y() and abs(target.x-center.x()) <= 24:
                    painter.drawLine(center, QPointF(target.x, target.y))

    def draw_placement(self, painter):
        painter.save()
        painter.translate(*self.pointer)
        if self.library_drag and self.editor.mode in ("R", "C", "L", "V", "I", "D"):
            wire = next((wire for wire in self.editor.project.wires if on_wire(self.pointer, wire)), None)
            if wire and wire[0][0] == wire[1][0]:
                painter.rotate(90)
        painter.setOpacity(.6)
        kind = self.editor.mode
        if kind == "X" or kind in MODELED_SYMBOLS:
            models = self.editor.project.subcircuits if kind == "X" else self.editor.project.device_models
            model = (self.editor.placement_subcircuit() if kind == "X" else
                     models.get(self.editor.placement_value))
            if model:
                ports = model["ports"] if kind == "X" else list(MODEL_PORTS[model["type"]][1])
                preview = Part("", kind, 0, 0, self.editor.placement_value, port_names=ports)
                if kind == "X":
                    preview.port_sides = model.get("sides", [])
                    preview.opamp_symbol = preview.is_op07() and not preview.port_sides
                    self.draw_subcircuit(painter, preview, True)
                else:
                    self.draw_modeled_symbol(painter, preview, True)
        else:
            self.draw_symbol(painter, kind, True)
        painter.setOpacity(1)
        painter.setPen(QPen(self.ink("#d46127"), .06))
        painter.drawEllipse(QPointF(0, 0), .22, .22)
        painter.drawLine(QPointF(-.36, 0), QPointF(.36, 0))
        painter.drawLine(QPointF(0, -.36), QPointF(0, .36))
        painter.restore()

    def enterEvent(self, event):
        self.pointer = self.grid(event.position())
        self.editor.coordinates.setText(f"X {self.pointer[0]}   Y {self.pointer[1]}")
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def marker_positions(self):
        by_ref = {part.ref.upper(): part for part in self.editor.project.parts}
        for index, marker in enumerate(self.editor.project.markers):
            unit = "W" if marker.get("power") else "A" if marker["kind"]=="I" else "V"
            code = "p" if marker.get("power") else marker["kind"]
            name = marker.get("name") or f"{code}{index+1}"
            values = getattr(self.editor,"marker_values",{})
            reading = values.get(index) if self.editor.active_readings else None
            detail = engineering(reading,unit) if reading is not None else unit
            if marker["kind"] == "I":
                part = by_ref.get(marker["ref"].upper())
                if part and marker["pin"] < len(part.pins()):
                    yield index, part.pins()[marker["pin"]], f"{name} · {detail}"
            else:
                for point, suffix in zip(marker["points"], ("+", "−") if marker["kind"] == "VD" else ("",)):
                    yield index, point, f"{name}{suffix} · {detail}"

    def draw_markers(self, painter):
        markers = list(self.marker_positions())
        if self.editor.marker_start is not None:
            markers.append((-1, self.editor.marker_start, "+"))
        for index, point, text in markers:
            active = index in self.editor.selected_markers
            color = self.ink("#d46127" if active else "#80469c")
            style = self.editor.project.markers[index].get("label_style",{}) if index>=0 else {}
            point_color = QColor(style["point_color"]) if style.get("point_color") else color
            painter.setPen(QPen(point_color, .08))
            painter.setBrush(point_color if style.get("fill",True) else Qt.BrushStyle.NoBrush)
            radius = (6 if active else 4) / self.cell
            painter.save()
            painter.translate(*point)
            painter.rotate(90*style.get("rotation",0))
            painter.scale(-1 if style.get("mirror") else 1,1)
            painter.drawPolygon(QPolygonF([QPointF(0,0),QPointF(radius*1.4,-radius*.65),
                                          QPointF(radius*1.0,-radius*1.65)]))
            painter.restore()
            painter.save()
            painter.translate(*point)
            painter.scale(1/self.cell, 1/self.cell)
            name,separator,value = text.partition(" · ")
            visible = [name if style.get("name",True) else "",value if style.get("value",True) else ""]
            painter.setPen(color)
            self.draw_label(painter,("marker",index),point," · ".join(filter(None,visible)),style)
            painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def draw_label(self,painter,key,anchor,text,style,default_x=-9,line=0):
        if not text:
            return
        painter.save()
        font = painter.font()
        font.setPixelSize(style.get("size",12))
        painter.setFont(font)
        if style.get("color"):
            painter.setPen(QColor(style["color"]))
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text)
        baseline = QPointF(default_x-width,-7+line*metrics.height())
        baseline += QPointF(*self.label_offset(style,key))*self.cell
        if getattr(self,"label_drag",None) and self.label_drag[0]==key:
            baseline += self.label_drag[2]
        painter.drawText(baseline,text)
        top = self.origin()+QPointF(*anchor)*self.cell+baseline-QPointF(2,metrics.ascent())
        self.label_hits.append((key,QRectF(top.x(),top.y(),width+4,metrics.height())))
        painter.restore()

    @staticmethod
    def label_offset(style,key):
        field = key[2] if len(key)>2 else ""
        legacy = style.get("offset",(0,0))
        if field.startswith("param:"):
            return style.get("parameter_offsets",{}).get(field,style.get("value_offset",legacy))
        return style.get(f"{field}_offset",legacy) if field else legacy

    def marker_hit(self, position):
        if self.editor.markers_visible:
            hits = []
            for index, point, _ in self.marker_positions():
                distance = (position - self.origin() - QPointF(*point)*self.cell).manhattanLength()
                if distance <= 9:
                    hits.append((distance, -index))
            if hits:
                return -min(hits)[1]
        return None

    def draw_bias(self, painter):
        readings = self.editor.active_readings
        voltage_ink = self.ink("#0f766e")
        current_ink = self.ink("#b34b1d")
        painter.save()
        painter.scale(1/self.cell, 1/self.cell)
        font = painter.font()
        voltage_font = painter.font()
        voltage_font.setBold(True)
        styles = self.editor.project.voltage_labels
        default = styles.get("*", {"color": None, "size": 12, "x": 0, "y": 0})
        for node in readings["nodes"]:
            point = QPointF(*node["point"])*self.cell
            style = styles.get(node["net"], default)
            ink = QColor(style["color"]) if style["color"] else voltage_ink
            voltage_font.setPixelSize(style["size"])
            painter.setFont(voltage_font)
            target = point + QPointF(7 + style["x"], -10 + style["y"])
            painter.setPen(QPen(ink, 1))
            if style["x"] or style["y"]:
                painter.drawLine(point, target)
            text = engineering(node["value"], "V")
            bounds = painter.fontMetrics().boundingRect(text).translated(round(target.x()), round(target.y()))
            painter.fillRect(bounds.adjusted(-2, -1, 2, 1), self.ink("#fcfdfc"))
            painter.drawText(target, text)
        painter.setFont(font)
        for current in readings["currents"]:
            part = self.editor.project.parts[current["part"]]
            pin = QPointF(*part.pins()[current["pin"]])*self.cell
            center = QPointF(part.x, part.y)*self.cell
            inward = center - pin
            length = math.hypot(inward.x(), inward.y())
            if not length:
                continue
            inward /= length
            normal = QPointF(-inward.y(), inward.x())
            value = current["value"]
            painter.setPen(QPen(current_ink, 1.5))
            start, end = pin + normal*6 - inward*5, pin + normal*6 + inward*7
            if value < 0:
                start, end = end, start
            if value:
                painter.drawLine(start, end)
                for wing in (normal, -normal):
                    painter.drawLine(end, end - (end-start)/3 + wing*3)
            painter.drawText(pin + QPointF(7, 16), engineering(abs(value), "A"))
        painter.restore()

    def draw_opamp(self, painter, selected):
        color = self.ink("#d46127" if selected else "#173d37")
        painter.setPen(QPen(color, .09))
        left, tip, height = -1.75, 1.75, 1.85
        painter.drawLine(QPointF(left, -height), QPointF(tip, 0))
        painter.drawLine(QPointF(tip, 0), QPointF(left, height))
        painter.drawLine(QPointF(left, height), QPointF(left, -height))
        for y in (-1, 1):
            painter.drawLine(QPointF(-3, y), QPointF(left, y))
        for sign in (-1, 1):
            painter.drawLine(QPointF(0, sign*height/2), QPointF(0, sign*3))
        painter.drawLine(QPointF(tip, 0), QPointF(3, 0))
        painter.setPen(QPen(color, .075, cap=Qt.PenCapStyle.RoundCap))
        for y in (-1, 1):
            painter.drawLine(QPointF(-1.43, y), QPointF(-1.06, y))
        painter.drawLine(QPointF(-1.245, -1.19), QPointF(-1.245, -.81))
        for y, label in ((-2.2, "V+"), (2.5, "V−")):
            painter.save()
            painter.translate(.16, y)
            painter.scale(1/self.cell, 1/self.cell)
            painter.drawText(QPointF(1, 0), label)
            painter.restore()

    def draw_subcircuit(self, painter, part, selected=False):
        if part.opamp_symbol:
            self.draw_opamp(painter, selected)
            return
        if part.port_sides:
            width, height, ports = symbol_geometry(part.port_sides)
            color = self.ink("#d46127" if selected else "#173d37")
            painter.setPen(QPen(color, .09))
            painter.drawRect(QRectF(-width, -height, 2*width, 2*height))
            for (x, y), name, side in zip(ports, part.port_names, part.port_sides):
                edge = max(-width, min(width, x)), max(-height, min(height, y))
                painter.drawLine(QPointF(x, y), QPointF(*edge))
                painter.save()
                painter.translate(*edge)
                painter.scale(1/self.cell, 1/self.cell)
                text_width = painter.fontMetrics().horizontalAdvance(name)
                shifts = {"Left": (5, -5), "Right": (-text_width-5, -5),
                          "Top": (-text_width/2, 13), "Bottom": (-text_width/2, -4)}
                painter.drawText(QPointF(*shifts[side]), name)
                painter.restore()
            return
        ports = part.box_offsets()
        height = max(abs(y) for _, y in ports) + 1
        color = self.ink("#d46127" if selected else "#173d37")
        painter.setPen(QPen(color, .09))
        painter.drawRect(QRectF(-2, -height, 4, 2*height))
        labels = part.port_names
        if part.value == "OP07" and labels == ["3", "2", "7", "4", "6"]:
            labels = ("IN+", "IN−", "V+", "V−", "OUT")
        for (x, y), name in zip(ports, labels):
            edge = -2 if x < 0 else 2
            painter.drawLine(QPointF(x, y), QPointF(edge, y))
            painter.save()
            painter.translate(edge, y)
            painter.scale(1/self.cell, 1/self.cell)
            width = painter.fontMetrics().horizontalAdvance(name)
            painter.setPen(color)
            painter.drawText(QPointF(3 if x < 0 else -width-3, -3), name)
            painter.restore()

    def draw_modeled_symbol(self, painter, part, selected):
        model = self.editor.project.device_models[part.value.upper()]
        family = model["type"]
        if family == "VDMOS" and "PCHAN" in model["flags"]:
            family = "PVDMOS"
        draw_device(painter, part, family, selected,self.ink("#173d37").name(),self.ink("#d46127").name())

    def draw_pin_targets(self, painter, part):
        painter.save()
        color = self.ink("#b34b1d")
        painter.setPen(QPen(color, .067))
        for (x, y), name in zip(part.pins(), part.port_names):
            painter.drawEllipse(QPointF(x, y), .11, .11)
            painter.save()
            painter.translate(x, y)
            painter.scale(1/self.cell, 1/self.cell)
            painter.setPen(color)
            left = x < part.x
            width = painter.fontMetrics().horizontalAdvance(name)
            painter.drawText(QPointF(-width-4 if left else 4, -4), name)
            painter.restore()
        painter.restore()

    def draw_symbol(self, painter, kind, selected):
        painter.setPen(QPen(self.ink("#d46127" if selected else "#173d37"), .09))

        def path(points):
            for a, b in zip(points, points[1:]):
                painter.drawLine(QPointF(*a), QPointF(*b))

        if kind == "G":
            path([(0, 0), (0, .4)])
            for y, half in ((.4, .55), (.57, .35), (.74, .17)):
                path([(-half, y), (half, y)])
        elif kind=="Y":
            path([(-2,-1),(-.75,-1),(-.75,1),(-2,1)])
            path([(-.75,-1),(.65,-1),(1.1,0),(.65,1),(-.75,1)])
            path([(1.1,0),(2,0)])
        elif kind=="Z":
            path([(-2,0),(-.8,0),(-.8,-.7),(.52,0),(-.8,.7),(-.8,0)])
            painter.drawEllipse(QPointF(.7,0),.18,.18)
            path([(.88,0),(2,0)])
        elif kind in "EUFHB":
            path([(-.8,0),(0,-.65),(.8,0),(0,.65),(-.8,0)])
            if kind not in "EU":
                path([(-2,0),(-.8,0)])
                path([(.8,0),(2,0)])
            if kind in "EU":
                for x,y in ((-2,-1),(2,-1),(-1,2),(1,2)):
                    p = QPointF(x,y)
                    painter.drawEllipse(p,.13,.13)
                path([(-2,-1),(-.8,-1),(-.8,0)])
                path([(2,-1),(.8,-1),(.8,0)])
                path([(-1,2),(-1,1.25),(-.45,1.25)])
                path([(1,2),(1,1.25),(.45,1.25)])
        elif kind == "N":
            painter.drawEllipse(QPointF(0, 0), .16, .16)
        else:
            path([(-2, 0), (-.8, 0)])
            path([(.8, 0), (2, 0)])
            if kind == "R":
                path([(-.8, 0), (-.6, -.3), (-.35, .3), (-.1, -.3),
                      (.15, .3), (.4, -.3), (.65, .3), (.8, 0)])
            elif kind == "C":
                path([(-.8, 0), (-.2, 0)])
                path([(.2, 0), (.8, 0)])
                for x in (-.2, .2):
                    path([(x, -.55), (x, .55)])
            elif kind == "L":
                for index in range(4):
                    painter.drawArc(QRectF(-.8 + index*.4, -.34, .4, .68), 0, 180*16)
            elif kind == "D":
                path([(-.8, 0), (-.5, 0)])
                path([(-.5, -.45), (.44, 0), (-.5, .45), (-.5, -.45)])
                path([(.44, -.47), (.44, .47)])
                path([(.44, 0), (.8, 0)])
            else:
                painter.drawEllipse(QPointF(0, 0), .8, .8)
                path([(-.36, 0), (.35, 0)] if kind == "I" else [(-.52, 0), (-.22, 0)])
                if kind == "I":
                    path([(.12, -.22), (.35, 0), (.12, .22)])
                else:
                    path([(-.37, -.15), (-.37, .15)])
                    path([(.22, 0), (.52, 0)])

    def dragEnterEvent(self, event):
        library = self.editor.library
        if event.source() is library and library.currentItem() is not None:
            self.editor.choose_part(library.currentItem())
            self.library_drag = True
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self.library_drag:
            self.pointer = self.grid(event.position())
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.library_drag = False
        self.editor.set_mode("Select")
        self.update()
        event.accept()

    def dropEvent(self, event):
        if self.library_drag:
            self.library_drag = False
            placed = self.editor.drop_part(self.grid(event.position()))
            self.editor.set_mode("Select")
            if placed:
                event.acceptProposedAction()

    def mousePressEvent(self, event):
        self.editor.bias_list.clearSelection()
        self.editor.component_highlight = None
        self.update()
        self.setFocus()
        pos = event.position()
        point = self.grid(pos)
        if getattr(self.editor,"pending_text",None) and event.button()==Qt.MouseButton.LeftButton:
            self.editor.checkpoint()
            self.editor.project.annotations.append([*point,self.editor.pending_text])
            self.editor.pending_text = None
            self.editor.refresh()
            return
        self.pointer = point
        modifiers = event.modifiers()
        if (event.button() == Qt.MouseButton.MiddleButton
                or (self.editor.mode == "Select" and event.button() == Qt.MouseButton.RightButton)
                or (event.button() == Qt.MouseButton.LeftButton and modifiers & Qt.KeyboardModifier.AltModifier)):
            self.drag = ("pan", pos, self.offset)
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.editor.set_mode("Select")
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        editor = self.editor
        self.annotation_index = None
        if editor.mode=="Select":
            for key, hit in reversed(getattr(self,"label_hits",[])):
                if key[1]>=0 and hit.contains(pos):
                    kind,index = key[:2]
                    if kind=="part":
                        editor.set_selection({index},set(),set())
                    else:
                        editor.select_marker(index,False)
                    self.label_drag = key,pos,QPointF()
                    self.update()
                    return
            for i,note in reversed(list(enumerate(editor.project.annotations))):
                x,y,text = note[:3]
                anchor = self.origin()+QPointF(x,y)*self.cell
                from PyQt6.QtGui import QFontMetrics
                font = self.font()
                font.setPixelSize(note[3].get("size",12) if len(note)>3 else 12)
                metrics = QFontMetrics(font)
                if QRectF(anchor.x(),anchor.y()-metrics.ascent(),metrics.horizontalAdvance(text)+5,
                          metrics.height()+3).contains(pos):
                    editor.select(None)
                    self.annotation_index = i
                    editor.properties_dock.show()
                    self.drag = ("annotation",point,(x,y))
                    self.update()
                    return
        if editor.mode.startswith("Mark "):
            editor.add_marker(point)
        elif editor.mode == "Wire":
            if self.begin is not None and self.begin != point:
                bend = (point[0], self.begin[1])
                editor.checkpoint()
                editor.project.wires.extend((a, b) for a, b in
                                            ((self.begin, bend), (bend, point)) if a != b)
                editor.refresh()
                self.begin = None
            else:
                self.begin = point
        elif editor.mode == "Junction":
            if point not in editor.project.junctions:
                editor.checkpoint()
                editor.project.junctions.append(point)
                editor.refresh()
        elif editor.mode != "Select":
            editor.place_part(point)
        else:
            extend = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            marker = self.marker_hit(pos)
            if marker is not None:
                editor.select_marker(marker, extend)
                return
            hit = self.selection_hit(point)
            if extend or not any(hit):
                self.drag = ("box", point, extend)
            else:
                groups = (editor.selected_parts, editor.selected_wires, editor.selected_junctions)
                if not any(existing & incoming for existing, incoming in zip(groups, hit)):
                    editor.set_selection(*hit)
                self.move_preview = None
                self.group_offset = (0, 0)
                self.drag = ("group", point, event.globalPosition())
        self.update()

    def selection_hit(self, point):
        project = self.editor.project
        for index in reversed(range(len(project.parts))):
            part = project.parts[index]
            if abs(point[0]-part.x) <= 1 and abs(point[1]-part.y) <= 1:
                return {index}, set(), set()
        for index, junction in enumerate(project.junctions):
            if point == junction:
                return set(), set(), {index}
        for index, wire in enumerate(project.wires):
            if on_wire(point, wire):
                return set(), {index}, set()
        return set(), set(), set()

    def group_delta(self, event):
        movement = event.globalPosition() - self.drag[2]
        if movement.manhattanLength() < QApplication.startDragDistance():
            return 0, 0
        return round(movement.x()/self.cell), round(movement.y()/self.cell)

    def mouseMoveEvent(self, event):
        if getattr(self,"label_drag",None):
            key,origin,_ = self.label_drag
            self.label_drag = key,origin,event.position()-origin
            self.update()
            return
        self.pointer = self.grid(event.position())
        if self.drag and self.drag[0] == "pan":
            self.offset = self.drag[2] + event.position() - self.drag[1]
        elif self.drag and self.drag[0] == "group":
            self.group_offset = self.group_delta(event)
        self.editor.coordinates.setText(f"X {self.pointer[0]}   Y {self.pointer[1]}")
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.editor.mode == "Select":
            self.drag = None
            if self.marker_hit(event.position()) is not None:
                return
            self.editor.set_selection(*self.selection_hit(self.grid(event.position())))
            from studies import inspect_selected
            if not inspect_selected(self.editor):
                self.editor.edit_selection()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self,"label_drag",None) and event.button()==Qt.MouseButton.LeftButton:
            key,origin,_ = self.label_drag
            kind,index = key[:2]
            self.label_drag = None
            delta = (event.position()-origin)/self.cell
            if (event.position()-origin).manhattanLength() >= QApplication.startDragDistance():
                self.editor.checkpoint()
                project = self.editor.project
                style = project.parts[index].label_style if kind=="part" else project.markers[index].setdefault("label_style",{})
                x,y = self.label_offset(style,key)
                offset = [x+delta.x(),y+delta.y()]
                if kind=="part" and key[2].startswith("param:"):
                    style.setdefault("parameter_offsets",{})[key[2]] = offset
                else:
                    style[f"{key[2]}_offset" if kind=="part" else "offset"] = offset
                self.editor.refresh()
            self.update()
            return
        if self.drag and event.button() == Qt.MouseButton.LeftButton:
            mode, start, extend = self.drag
            end = self.grid(event.position())
            if mode=="annotation":
                if end!=start:
                    self.editor.checkpoint()
                    item = self.editor.project.annotations[self.annotation_index]
                    item[:2] = [extend[0]+end[0]-start[0],extend[1]+end[1]-start[1]]
                    self.editor.refresh()
            elif mode == "box":
                if extend and start == end:
                    groups = (self.editor.selected_parts, self.editor.selected_wires, self.editor.selected_junctions)
                    self.editor.set_selection(*(existing ^ incoming for existing, incoming
                                                in zip(groups, self.selection_hit(start))), markers=self.editor.selected_markers)
                else:
                    self.editor.select_box(start, end, extend)
            elif mode == "group":
                self.editor.move_group(*self.group_delta(event))
        self.drag = None
        self.update()

    def keyPressEvent(self, event):
        directions = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                      Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        editor = self.editor
        if event.key()==Qt.Key.Key_Escape and getattr(self,"label_drag",None):
            self.label_drag = None
            self.update()
            return
        if event.key() == Qt.Key.Key_Escape and self.drag is not None:
            self.drag = None
            self.move_preview = None
            self.update()
            return
        delta = directions.get(event.key())
        if (delta is not None and editor.mode == "Select"
                and (editor.selected_parts or editor.selected_wires or editor.selected_junctions)):
            editor.move_group(*delta)
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        pos = event.position()
        before = (pos - self.origin()) / self.cell
        self.cell = max(9., min(78., self.cell * (1.15 if event.angleDelta().y() > 0 else 1/1.15)))
        self.offset = pos - QPointF(self.width()/2, self.height()/2) - before*self.cell
        self.update()
