"""Connect the nets of a saved schematic to a reusable block symbol."""
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTabWidget,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from canvas import Canvas
from circuit import PORT_SIDES, Project, connectivity, reusable_subcircuit, symbol_geometry


class SymbolPreview(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setMinimumHeight(220)
        self.handles = []
        self.dragged = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        rows = self.editor.rows()
        sides = [row["side"] for row in rows]
        width, height, offsets = symbol_geometry(sides)
        scale = min(29, self.width()/(2*width+6), self.height()/(2*height+5))
        origin = QPointF(self.width()/2, self.height()/2)
        block = QRectF(origin.x()-width*scale, origin.y()-height*scale,
                       2*width*scale, 2*height*scale)
        painter.setPen(palette.text().color())
        painter.drawRect(block)
        painter.drawText(block, Qt.AlignmentFlag.AlignCenter, self.editor.name.text() or "Subcircuit")
        self.handles = []
        for row, (x, y) in zip(rows, offsets):
            terminal = origin+QPointF(x*scale, y*scale)
            self.handles.append(terminal)
            edge = origin+QPointF(max(-width, min(width, x))*scale, max(-height, min(height, y))*scale)
            painter.setPen(palette.text().color())
            painter.drawLine(edge, terminal)
            painter.setPen(palette.highlight().color())
            painter.drawEllipse(terminal, 4, 4)
            label = row["name"] or "Port"
            label_width = painter.fontMetrics().horizontalAdvance(label)
            shifts = {"Left": (5, -5), "Right": (-label_width-5, -5),
                      "Top": (-label_width/2, 13), "Bottom": (-label_width/2, -5)}
            painter.drawText(edge+QPointF(*shifts[row["side"]]), label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for index, point in enumerate(self.handles):
                if (event.position()-point).manhattanLength() <= 13:
                    self.dragged = index
                    self.editor.table.setCurrentCell(index, 0)
                    break

    def mouseReleaseEvent(self, event):
        if self.dragged is None or event.button() != Qt.MouseButton.LeftButton:
            return
        x = (event.position().x()-self.width()/2)/self.width()
        y = (event.position().y()-self.height()/2)/self.height()
        side = ("Left" if x < 0 else "Right") if abs(x) > abs(y) else ("Top" if y < 0 else "Bottom")
        self.editor.table.cellWidget(self.dragged, 2).setCurrentText(side)
        self.dragged = None


class SourcePreview(Canvas):
    def __init__(self, project, nodes):
        selection = QListWidget()
        state = SimpleNamespace(project=project, selected_parts=set(), selected_wires=set(),
                                selected_junctions=set(), active_readings=None, markers_visible=False,
                                mode="Select", bias_list=selection, reading_nets=nodes,
                                dark_theme=bool(QApplication.instance().property("circuitbenchDark")))
        super().__init__(state)
        selection.setParent(self)
        selection.hide()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(220)

    def highlight(self, net):
        self.editor.bias_list.clear()
        if net:
            item = QListWidgetItem(net, self.editor.bias_list)
            item.setData(Qt.ItemDataRole.UserRole, (net, None))
            item.setSelected(True)
        self.update()

    def resizeEvent(self, event):
        points = list(self.editor.reading_nets)
        if points:
            xs, ys = zip(*points)
            self.cell = min(27, self.width()/(max(xs)-min(xs)+8), self.height()/(max(ys)-min(ys)+8))
            self.offset = -QPointF((min(xs)+max(xs))/2, (min(ys)+max(ys))/2)*self.cell
        super().resizeEvent(event)


class SubcircuitDesigner(QDialog):
    def __init__(self, parent, project, source_path):
        super().__init__(parent)
        self.project, self.source_path = project, source_path
        self.original_is_design = project.subcircuit_design is not None
        self.setWindowTitle("Build reusable subcircuit")
        self.resize(650, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Source schematic  {source_path.name}"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Subcircuit name, e.g. ACTIVE_FILTER")
        layout.addWidget(self.name)
        instructions = QLabel("Map each port to a net. Ports mapped to the same net are internally connected. "
                              "Drag a pin in the preview to change its side.")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Port name", "Schematic net", "Symbol side", "Description"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        for label, action in (("Add port", self.add_default_port), ("Remove port", self.remove_port),
                              ("Move up", lambda: self.move_port(-1)), ("Move down", lambda: self.move_port(1))):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(action)
            controls.addWidget(button)
        controls.addStretch()
        self.preview = SymbolPreview(self)
        nodes = connectivity(project)
        self.source_preview = SourcePreview(project, nodes)
        tabs = QTabWidget()
        tabs.addTab(self.preview, "Block symbol")
        tabs.addTab(self.source_preview, "Source schematic")
        layout.addWidget(tabs)
        self.table.currentCellChanged.connect(self.highlight_net)
        self.name.textChanged.connect(self.preview.update)
        self.table.cellChanged.connect(self.preview.update)
        self.connection_summary = QLabel()
        self.connection_summary.setWordWrap(True)
        layout.addWidget(self.connection_summary)
        self.table.cellChanged.connect(self.update_summary)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_design)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.nets = {}
        for part in project.parts:
            if part.kind not in "GN":
                for index, point in enumerate(part.pins(), 1):
                    self.nets.setdefault(nodes[point], []).append(f"{part.ref} pin {index}")
        self.aliases = {}
        named_ports = []
        seen = set()
        for part in project.parts:
            if part.kind != "N":
                continue
            net = nodes[part.pins()[0]]
            if net not in self.nets:
                continue
            title = part.value.replace("−", "-")
            if title.casefold() not in seen:
                seen.add(title.casefold())
                self.aliases.setdefault(net, []).append(title)
                if title[0].isascii() and title[0].isalpha():
                    named_ports.append({"name": title, "net": net,
                                        "side": "Right" if title.casefold().startswith("out") else "Left"})
        design = project.subcircuit_design
        if design:
            self.name.setText(design["name"])
            for port in design["ports"]:
                self.add_port(port)
        elif 1 <= len(named_ports) <= 16:
            for port in named_ports:
                self.add_port(port)
        else:
            for name, aliases, side in (("IN", ("in", "input"), "Left"), ("OUT", ("out", "output"), "Right")):
                net = next((net for net in aliases if net in self.nets), "")
                self.add_port({"name": name, "net": net, "side": side})

    def update_summary(self):
        groups = {}
        for port in self.rows():
            if port["net"]:
                groups.setdefault(port["net"], []).append(port["name"])
        shared = [" and ".join(names) + " share " + net for net, names in groups.items() if len(names) > 1]
        self.connection_summary.setText(f"{self.table.rowCount()} external pins, {len(groups)} electrical nets. " + ". ".join(shared))

    def highlight_net(self):
        self.update_summary()
        row = self.table.currentRow()
        picker = self.table.cellWidget(row, 1) if row >= 0 else None
        self.source_preview.highlight(picker.currentData() if picker else "")

    def rows(self):
        return [{"name": self.table.item(i, 0).text().strip().replace("−", "-"),
                 "net": self.table.cellWidget(i, 1).currentData(),
                 "side": self.table.cellWidget(i, 2).currentText(),
                 **({"description": self.table.item(i, 3).text().strip()}
                    if self.table.item(i, 3) and self.table.item(i, 3).text().strip() else {})}
                for i in range(self.table.rowCount())
                if all(self.table.cellWidget(i, col) for col in (1, 2))]

    def add_default_port(self):
        if self.table.rowCount() < 16:
            self.add_port({"name": "", "net": "", "side": "Left"})

    def add_port(self, port):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(port["name"]))
        self.table.setItem(row, 3, QTableWidgetItem(port.get("description", "")))
        nets = QComboBox()
        nets.addItem("Choose net", "")
        for net, pins in sorted(self.nets.items()):
            label = "Ground (0)" if net == "0" else " / ".join(self.aliases.get(net, [net]))
            hint = ", ".join(pins[:3])
            nets.addItem(f"{label}  ·  {hint}", net)
        nets.setCurrentIndex(max(0, nets.findData(port["net"])))
        nets.currentIndexChanged.connect(self.highlight_net)
        side = QComboBox()
        side.addItems(PORT_SIDES)
        side.setCurrentText(port["side"])
        side.currentTextChanged.connect(self.preview.update)
        self.table.setCellWidget(row, 1, nets)
        self.table.setCellWidget(row, 2, side)
        self.update_summary()
        self.preview.update()

    def remove_port(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.update_summary()
            self.preview.update()

    def move_port(self, delta):
        source = self.table.currentRow()
        target = source+delta
        if source < 0 or not 0 <= target < self.table.rowCount():
            return
        rows = self.rows()
        rows[source], rows[target] = rows[target], rows[source]
        self.table.setRowCount(0)
        for row in rows:
            self.add_port(row)
        self.table.setCurrentCell(target, 0)

    def save_design(self):
        self.project.subcircuit_design = {"name": self.name.text().strip().upper(), "ports": self.rows()}
        try:
            reusable_subcircuit(self.project)
        except ValueError as error:
            self.message.setText(str(error))
            return
        default = self.source_path if self.original_is_design else self.source_path.with_name(
            self.source_path.stem + "-subcircuit.cwb")
        name, _ = QFileDialog.getSaveFileName(self, "Save reusable subcircuit", str(default),
                                             "CircuitBench schematic (*.cwb)")
        if not name:
            return
        target = Path(name)
        if target.suffix.lower() != ".cwb":
            target = target.with_suffix(".cwb")
        try:
            self.project.save(target)
        except OSError as error:
            self.message.setText(str(error))
            return
        self.saved_path = target
        self.accept()

    @classmethod
    def open_file(cls, parent, path):
        try:
            project = Project.load(path.read_text(encoding="utf-8-sig"))
            dialog = cls(parent, project, path)
        except (OSError, ValueError, TypeError, KeyError) as error:
            QMessageBox.warning(parent, "Build reusable subcircuit", str(error))
            return None
        return dialog.saved_path if dialog.exec() else None
