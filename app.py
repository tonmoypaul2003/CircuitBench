"""CircuitBench desktop entry point."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from PyQt6.QtCore import QPointF, QProcess, Qt, QUrl
from PyQt6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QFileDialog,
    QFormLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QToolBar,
    QHBoxLayout, QVBoxLayout, QWidget,
)

from canvas import Canvas
from circuit import (GENERIC_MODELS, Profile, Project, SOURCE_DEFAULTS, SYMBOLS, WAVE_FIELDS, connectivity,
                     diode_model, example, marker_node, netlist, number, on_wire, source_tokens,
                     source_value, translate_selection)
from plotting import PlotReportPage, Waveform
from qucs_library import import_diodes
from results import analysis_deck, load_plot
from spice_library import MODEL_PORTS, read_library


RUNTIME = Path(__file__).resolve().parent / "runtime"
BUNDLED_SOLVER = RUNTIME / "ngspice" / "bin" / "ngspice_con.exe"


class Editor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project = Project()
        self.path = None
        self.saved = self.project.dump()
        self.undo_stack, self.redo_stack = [], []
        self.mode = "Select"
        self.selected = None
        self.selected_parts, self.selected_wires, self.selected_junctions = set(), set(), set()
        self.process = None
        self.cancelled = False
        self.run_dir = None
        self.run_revision = None
        self.solver = os.environ.get("NGSPICE") or (
            str(BUNDLED_SOLVER) if BUNDLED_SOLVER.is_file()
            else shutil.which("ngspice_con") or shutil.which("ngspice"))
        self.canvas = Canvas(self)
        self.setCentralWidget(self.canvas)
        self.resize(1260, 800)
        self.setStyleSheet("""
            QMainWindow { background: #e8eeeb; }
            QToolBar { background: #edf3f0; spacing: 5px; padding: 5px; border: 0; }
            QToolButton { border: 1px solid transparent; border-radius: 4px; padding: 5px 8px; }
            QToolButton:hover { background: #d9e7e2; }
            QToolButton:checked { color: #144e43; background: #d1e9df; border-color: #8bb5a7; }
            QDockWidget::title { background: #e0ebe6; padding: 6px; }
            QListWidget::item { padding: 5px 4px; }
            QStatusBar { background: #e0ebe6; color: #294a42; }
        """)
        self.setDockNestingEnabled(True)
        self.catalog = []
        self.placement_model = None
        self.placement_value = None
        self.marker_start = None
        self.markers_visible = True
        self.mode_actions = {}
        self.mode_group = QActionGroup(self)
        self.mode_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        tools = QToolBar("Project and simulation")
        tools.setMovable(False)
        self.addToolBar(tools)
        drawing_tools = QToolBar("Drawing tools")
        drawing_tools.setMovable(False)
        drawing_tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, drawing_tools)
        file_menu = self.menuBar().addMenu("&File")
        tools.addAction(self.action(file_menu, "New", "Ctrl+N", lambda: self.replace(Project())))
        tools.addAction(self.action(file_menu, "Open…", "Ctrl+O", self.open_project))
        tools.addAction(self.action(file_menu, "Save", "Ctrl+S", self.save))
        tools.addSeparator()
        self.action(file_menu, "Save as…", "Ctrl+Shift+S", lambda: self.save(True))
        self.action(file_menu, "Load voltage divider example", "", lambda: self.replace(example()))
        self.action(file_menu, "Load RC filter AC example", "", self.load_ac_example)
        edit = self.menuBar().addMenu("&Edit")
        self.action(edit, "Undo", "Ctrl+Z", lambda: self.restore(self.undo_stack, self.redo_stack))
        self.action(edit, "Redo", "Ctrl+Y", lambda: self.restore(self.redo_stack, self.undo_stack))
        self.action(edit, "Rotate part", "R", self.rotate)
        self.action(edit, "Delete selection", "Del", self.delete)
        self.action(edit, "Create or edit diode model…", "", self.edit_model)
        draw = self.menuBar().addMenu("&Draw")
        for mode, shortcut in (("Select", "Esc"), ("Wire", "W"), ("Junction", "J")):
            action = self.action(drawing_tools, mode, shortcut, lambda mode=mode: self.set_mode(mode))
            action.setCheckable(True)
            self.mode_group.addAction(action)
            self.mode_actions[mode] = action
            draw.addAction(action)
        drawing_tools.addAction(self.action(draw, "Part…", "P", self.focus_library))
        self.action(drawing_tools, "Ground", "", lambda: self.set_mode("G"))
        self.action(drawing_tools, "Label", "", lambda: self.set_mode("N"))
        self.action(draw, "Import model libraries…", "", self.open_library)
        self.action(draw, "Import local ngspice starter pack", "", self.import_starter_pack)
        self.action(draw, "Import bundled Qucs-S diodes", "", self.import_bundled_diodes)
        self.action(draw, "Browse ngspice starter pack", "", lambda: QDesktopServices.openUrl(
            QUrl("https://ngspice.sourceforge.io/modelparams.html")))
        self.action(draw, "Browse Qucs-S libraries", "", lambda: QDesktopServices.openUrl(
            QUrl("https://github.com/ra3xdh/qucs_s/tree/current/library")))
        self.action(tools, "Rotate", "", self.rotate)
        view = self.menuBar().addMenu("&View")
        view.addAction(self.action(tools, "Fit schematic", "F", self.fit_view))
        self.action(view, "Reset zoom", "", self.reset_view)
        tools.addSeparator()
        self.action(tools, "Analysis setup…", "", self.edit_profile)
        self.action(tools, "Netlist", "", self.preview)
        self.run_action = self.action(tools, "Run", "F5", self.run)
        self.cancel_action = self.action(tools, "Cancel run", "", self.cancel)
        self.cancel_action.setEnabled(False)
        simulation = self.menuBar().addMenu("&Simulation")
        self.action(simulation, "New profile…", "", self.new_profile)
        self.action(simulation, "Duplicate active profile…", "", lambda: self.new_profile(True))
        self.action(simulation, "Remove active profile", "", self.remove_profile)
        simulation.addSeparator()
        self.action(simulation, "Choose ngspice executable…", "", self.choose_solver)
        self.action(simulation, "Run history…", "", self.open_run_history)
        self.action(simulation, "Plot window…", "", self.open_plots)
        self.action(simulation, "Open saved waveform…", "", self.open_waveform)
        self.action(simulation, "Export waveform CSV…", "", self.export_waveform)
        markers = self.menuBar().addMenu("&Markers")
        for mode, title, key in (("Mark V", "Voltage", "Ctrl+M"),
                                 ("Mark VD", "Differential voltage", ""),
                                 ("Mark I", "Current into pin", "")):
            self.action(markers, title, key, lambda mode=mode: self.set_mode(mode))
        markers.addSeparator()
        show = self.action(markers, "Show markers", "", self.toggle_markers)
        show.setCheckable(True)
        show.setChecked(True)
        self.action(markers, "Remove last marker", "", self.remove_last_marker)
        self.action(markers, "Clear all markers", "", self.clear_markers)

        library = QWidget()
        library_layout = QVBoxLayout(library)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a part or model")
        self.library = QListWidget()
        self.category = QComboBox()
        self.category.addItems(["All components", "Basic symbols", "Qucs-S models", "SPICE subcircuits",
                                "SPICE device models"])
        import_library = QPushButton("Import libraries…")
        import_library.clicked.connect(self.open_library)
        library_layout.addWidget(self.search)
        library_layout.addWidget(self.category)
        library_layout.addWidget(self.library)
        library_layout.addWidget(import_library)
        self.search.textChanged.connect(self.filter_library)
        self.category.currentIndexChanged.connect(lambda: self.filter_library(self.search.text()))
        self.library.itemClicked.connect(self.choose_part)
        self.library.itemActivated.connect(self.choose_part)
        self.search.returnPressed.connect(self.choose_first_part)
        self.filter_library("")
        self.library_dock = self.dock("Component library", library, Qt.DockWidgetArea.LeftDockWidgetArea)

        properties = QWidget()
        form = QFormLayout(properties)
        self.ref = QLineEdit()
        self.value = QLineEdit()
        self.value_caption = QLabel("Value")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.apply = QPushButton("Apply")
        self.source_editor = QPushButton("Source parameters…")
        self.source_editor.clicked.connect(self.edit_source)
        self.apply.clicked.connect(self.apply_part)
        self.value.returnPressed.connect(self.apply_part)
        form.addRow("Reference", self.ref)
        form.addRow(self.value_caption, self.value)
        form.addRow(self.hint)
        form.addRow(self.source_editor)
        form.addRow(self.apply)
        self.profiles = QComboBox()
        self.profiles.currentTextChanged.connect(self.change_profile)
        tools.addSeparator()
        analysis_label = QLabel(" Analysis ")
        analysis_label.setBuddy(self.profiles)
        tools.addWidget(analysis_label)
        tools.addWidget(self.profiles)
        self.properties_dock = self.dock("Properties", properties, Qt.DockWidgetArea.RightDockWidgetArea)
        self.action(edit, "Edit selected properties", "Alt+Return", self.edit_selection)

        self.tabs = QTabWidget()
        self.log = QPlainTextEdit(readOnly=True)
        self.deck = QPlainTextEdit(readOnly=True)
        self.tabs.addTab(self.log, "Diagnostics")
        self.tabs.addTab(self.deck, "Netlist")
        self.plot_window = QDialog(self)
        self.plot_window.setWindowTitle("Simulation plots")
        self.plot_window.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.plot_window.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.plot_window.resize(950, 580)
        plots_layout = QVBoxLayout(self.plot_window)
        button = QPushButton("Add plot page")
        button.clicked.connect(self.duplicate_plot_page)
        controls = QHBoxLayout()
        controls.addWidget(button)
        close_page = QPushButton("Remove plot page")
        close_page.clicked.connect(lambda: self.close_plot_page(self.plot_pages.currentIndex()))
        controls.addWidget(close_page)
        controls.addStretch()
        plots_layout.addLayout(controls)
        self.plot_pages = QTabWidget()
        self.plot_pages.setTabsClosable(True)
        self.plot_pages.tabCloseRequested.connect(self.close_plot_page)
        self.plot_pages.tabBarDoubleClicked.connect(self.rename_plot_page)
        plots_layout.addWidget(self.plot_pages)
        self.wave = Waveform()
        self.output_dock = self.dock("Simulation output", self.tabs, Qt.DockWidgetArea.BottomDockWidgetArea)
        view.addSeparator()
        for panel in (self.library_dock, self.properties_dock, self.output_dock):
            view.addAction(panel.toggleViewAction())
            panel.hide()
        self.coordinates = QLabel("X 0   Y 0")
        self.coordinates.setMinimumWidth(100)
        self.statusBar().addPermanentWidget(self.coordinates)
        self.select(None)
        self.refresh()
        self.set_mode("Select")

    def action(self, menu, title, key, callback):
        action = QAction(title, self)
        if key:
            action.setShortcut(key)
        action.triggered.connect(lambda checked=False: callback())
        menu.addAction(action)
        return action

    def dock(self, name, content, area):
        dock = QDockWidget(name, self)
        dock.setWidget(content)
        self.addDockWidget(area, dock)
        return dock

    def filter_library(self, query):
        self.library.clear()
        query = query.casefold()
        if self.category.currentIndex() in (0, 1):
            for kind, (name, _) in SYMBOLS.items():
                if kind not in "XQMJS" and query in name.casefold():
                    item = QListWidgetItem(name)
                    item.setData(Qt.ItemDataRole.UserRole, (kind, None, None))
                    self.library.addItem(item)
            source_parts = [("V", "AC voltage source (Vphase)", "AC"),
                            ("I", "AC current source (Iphase)", "AC")]
            for kind in ("V", "I"):
                source_parts.extend((kind, f"{kind}{name.lower()} · {label}", name)
                                    for name, label in (("SIN", "Sine"), ("PULSE", "Pulse"),
                                                        ("PWL", "Piecewise linear"), ("EXP", "Exponential")))
            for kind, title, mode in source_parts:
                if query in title.casefold():
                    item = QListWidgetItem(title)
                    item.setData(Qt.ItemDataRole.UserRole, (kind, None, SOURCE_DEFAULTS[mode]))
                    self.library.addItem(item)
        if self.category.currentIndex() in (0, 2):
            for entry in self.catalog:
                if query in f"{entry.name} {entry.description}".casefold():
                    item = QListWidgetItem(f"{entry.name}  ·  Qucs-S diode")
                    item.setToolTip(entry.description)
                    item.setData(Qt.ItemDataRole.UserRole, ("D", entry, None))
                    self.library.addItem(item)
        if self.category.currentIndex() in (0, 3):
            for name, model in self.project.subcircuits.items():
                if query in name.casefold():
                    item = QListWidgetItem(f"{name}  ·  {len(model['ports'])} ports")
                    item.setToolTip(", ".join(model["ports"]))
                    item.setData(Qt.ItemDataRole.UserRole, ("X", None, name))
                    self.library.addItem(item)
        if self.category.currentIndex() in (0, 1, 4):
            for name, model in sorted(self.project.device_models.items()):
                if self.category.currentIndex() == 1 and name not in GENERIC_MODELS:
                    continue
                kind, ports = MODEL_PORTS[model["type"]]
                family = SYMBOLS[kind][0]
                title = f"{name.removeprefix('GENERIC_')}  ·  {family} ({model['type']})" if name in GENERIC_MODELS else (
                    f"{name}  ·  {family} ({model['type']})")
                if query in title.casefold():
                    item = QListWidgetItem(title)
                    item.setToolTip(", ".join(ports))
                    item.setData(Qt.ItemDataRole.UserRole, (kind, None, name))
                    self.library.addItem(item)
        self.library.setCurrentRow(0)

    def focus_library(self):
        self.library_dock.show()
        self.search.setFocus()
        self.search.selectAll()

    def choose_first_part(self):
        item = self.library.currentItem()
        if item is not None:
            self.choose_part(item)

    def choose_part(self, item):
        kind, model, value = item.data(Qt.ItemDataRole.UserRole)
        self.set_mode(kind)
        self.placement_model = model
        self.placement_value = value
        if model is not None:
            self.statusBar().showMessage(f"Click the canvas to place {model.name} · Esc to stop")
        elif value is not None:
            self.statusBar().showMessage(f"Click the canvas to place an {item.text()} · Esc to stop")
        self.canvas.setFocus()

    def open_library(self):
        names, _ = QFileDialog.getOpenFileNames(
            self, "Import model libraries",
            filter="Model libraries (*.lib *.mod *.cir *.sp *.spi *.sub *.txt);;All files (*)")
        if not names:
            return
        self.show_library_import([Path(name) for name in names])

    def import_starter_pack(self):
        root = RUNTIME / "models" / "basic_models"
        paths = [root / path for path in (
            "bipolar/BC546.lib", "bipolar/BC556.lib", "JFet/2SJ74.mod", "JFet/2SK170.mod",
            "MOS power/IRFP240_NVDMOS.mod", "MOS power/IRFP9240_PVDMOS.mod",
            "OpAmps/generic_opamp.lib", "OpAmps/Op07.mod",
        )]
        if not all(path.is_file() for path in paths):
            self.error("The local starter pack is missing. See fetch-ngspice-models.ps1 in the app folder, "
                       "or use Draw → Browse ngspice starter pack and import selected files.")
            return
        self.show_library_import(paths)

    def import_bundled_diodes(self):
        self.show_library_import([RUNTIME / "models" / "qucs-diodes.lib"])

    def show_library_import(self, paths):
        count, issues = self.import_library_files(paths)
        self.category.setCurrentIndex(0)
        self.search.clear()
        self.refresh()
        self.focus_library()
        if issues:
            self.error("\n".join([f"Imported {count} new definitions.", *issues]))
        else:
            self.statusBar().showMessage(f"Imported {count} new definitions from {len(paths)} files", 6000)

    def import_library_files(self, paths):
        models, subcircuits, issues = {}, {}, []
        count = 0
        for path in paths:
            try:
                with path.open(encoding="utf-8-sig", errors="replace") as file:
                    qucs = file.read(256).lstrip().startswith("<Qucs Library")
                if qucs:
                    for entry in import_diodes(path, number):
                        if entry not in self.catalog:
                            self.catalog.append(entry)
                            count += 1
                    continue
                imported_models, imported_subcircuits, skipped = read_library(path, number)
                issues.extend(f"{path.name}: {message}" for message in skipped)
                for source, existing, pending in ((imported_models, self.project.device_models, models),
                                                   (imported_subcircuits, self.project.subcircuits, subcircuits)):
                    for name, entry in source.items():
                        before = pending.get(name, existing.get(name))
                        if (source is imported_models
                                and (name in self.project.diode_models or name in self.project.qucs_models)):
                            issues.append(f"{path.name}: model name {name} is already in use")
                        elif before is None:
                            pending[name] = entry
                        elif before != entry:
                            issues.append(f"{path.name}: a different definition of {name} is already loaded")
            except (OSError, ValueError) as error:
                issues.append(f"{path.name}: {error}")
        if models or subcircuits:
            self.checkpoint()
            self.project.device_models.update(models)
            self.project.subcircuits.update(subcircuits)
        self.filter_library(self.search.text())
        return count+len(models)+len(subcircuits), issues

    def place_part(self, point):
        self.checkpoint()
        self.project.add(self.mode, point)
        part = self.project.parts[-1]
        if self.placement_model is not None:
            part.value = self.project.register_qucs_diode(self.placement_model)
        elif self.placement_value is not None:
            part.value = self.placement_value
            if part.kind == "X":
                part.port_names = self.project.subcircuits[part.value]["ports"].copy()
            elif part.kind in "QMJS":
                model = self.project.device_models[part.value]
                part.port_names = list(MODEL_PORTS[model["type"]][1])
        self.select(part)
        self.refresh()

    def refresh(self):
        self.filter_library(self.search.text())
        if ((self.mode == "X" and self.placement_value not in self.project.subcircuits)
                or (self.mode in ("Q", "M", "J", "S") and self.placement_value not in self.project.device_models)):
            self.set_mode("Select")
        self.profiles.blockSignals(True)
        self.profiles.clear()
        self.profiles.addItems(self.project.profiles)
        self.profiles.setCurrentText(self.project.active)
        self.profiles.blockSignals(False)
        revision = self.project.dump()
        title = self.path.stem if self.path else "Untitled"
        self.setWindowTitle(f"{title}{' *' if revision != self.saved else ''} — CircuitBench")
        for index in range(self.plot_pages.count()):
            page = self.plot_pages.widget(index)
            suffix = "" if page.revision == revision else " · other revision"
            self.plot_pages.setTabText(index, page.title + suffix)
        self.canvas.update()

    def set_mode(self, mode):
        self.mode = mode
        self.placement_model = None
        self.placement_value = None
        self.marker_start = None
        for key, action in self.mode_actions.items():
            action.setChecked(key == mode)
        self.canvas.begin = None
        if mode in SYMBOLS:
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.canvas.unsetCursor()
        messages = {"Select": "Drag empty space to box-select · Shift-drag extends · drag a selection to move",
                    "Mark V": "Click a wire or pin to mark its voltage relative to ground",
                    "Mark VD": "Click the positive wire or pin, then the negative wire or pin",
                    "Mark I": "Click a component pin to measure current into it",
                    "Wire": "Click two endpoints to route a wire · Esc to stop",
                    "Junction": "Click an intersection to connect the wires"}
        self.statusBar().showMessage(messages[mode] if mode in messages
                                     else f"Click the canvas to place {SYMBOLS[mode][0].lower()}")
        self.canvas.update()

    def reset_view(self):
        self.canvas.offset = QPointF()
        self.canvas.cell = 18.0
        self.canvas.update()

    def fit_view(self):
        points = [pin for part in self.project.parts for pin in part.pins()]
        points.extend(point for wire in self.project.wires for point in wire)
        if not points:
            self.reset_view()
            return
        xs, ys = zip(*points)
        view = self.canvas
        view.cell = min(55., max(2., min(view.width() / (max(xs)-min(xs)+5),
                                          view.height() / (max(ys)-min(ys)+5))))
        view.offset = -QPointF((min(xs)+max(xs))/2, (min(ys)+max(ys))/2) * view.cell
        view.update()

    def select(self, part, wire=None):
        parts = {i for i, item in enumerate(self.project.parts) if item is part}
        self.set_selection(parts, {wire} if wire is not None else set(), set())

    def set_selection(self, parts, wires, junctions):
        self.selected_parts, self.selected_wires, self.selected_junctions = parts, wires, junctions
        count = len(parts)+len(wires)+len(junctions)
        part = self.project.parts[next(iter(parts))] if count == 1 and parts else None
        self.selected = part
        self.ref.setText(part.ref if part else "")
        self.value.setText(part.value if part else "")
        captions = {"R": "Resistance (Ω)", "C": "Capacitance (F)", "L": "Inductance (H)",
                    "V": "Source expression", "I": "Source expression", "D": "Diode model",
                    "N": "Net name", "X": "Subcircuit", **dict.fromkeys("QMJS", "Device model")}
        self.value_caption.setText(captions.get(part.kind, "Value") if part else "Value")
        self.value.setVisible(bool(part and part.kind != "G"))
        self.value_caption.setVisible(bool(part and part.kind != "G"))
        is_source = bool(part and part.kind in "VI")
        self.source_editor.setVisible(is_source)
        self.hint.setText("Use Source parameters to edit the waveform and AC settings." if is_source else
                          "Model: " + part.value if part and part.kind == "X" else
                          f"{count} items selected" if count > 1 else "")
        self.ref.setEnabled(part is not None)
        self.value.setEnabled(part is not None and part.kind not in "GXQMJS")
        self.apply.setEnabled(part is not None)
        self.canvas.update()

    def select_box(self, start, end, extend=False):
        def inside(point):
            return all(min(a, b) <= p <= max(a, b) for p, a, b in zip(point, start, end))

        parts = {i for i, part in enumerate(self.project.parts) if inside((part.x, part.y))}
        wires = {i for i, wire in enumerate(self.project.wires) if all(inside(p) for p in wire)}
        junctions = {i for i, point in enumerate(self.project.junctions) if inside(point)}
        if extend:
            parts |= self.selected_parts
            wires |= self.selected_wires
            junctions |= self.selected_junctions
        self.set_selection(parts, wires, junctions)

    def move_group(self, dx, dy):
        if not (dx or dy):
            return
        snapshot = self.project.dump()
        try:
            translate_selection(self.project, self.selected_parts, self.selected_wires,
                                self.selected_junctions, dx, dy)
        except ValueError as error:
            self.project = Project.load(snapshot)
            self.set_selection(self.selected_parts, self.selected_wires, self.selected_junctions)
            self.error(str(error))
            return
        self.undo_stack.append(snapshot)
        self.redo_stack.clear()
        self.refresh()

    def checkpoint(self):
        self.undo_stack.append(self.project.dump())
        self.redo_stack.clear()

    def restore(self, source, target):
        if source:
            target.append(self.project.dump())
            self.project = Project.load(source.pop())
            self.select(None)
            self.refresh()

    def apply_part(self):
        if self.selected:
            ref, value = self.ref.text().strip(), self.value.text().strip()
            if not ref or any(p is not self.selected and p.ref.upper() == ref.upper() for p in self.project.parts):
                self.error("Component references must be nonempty and unique")
                return
            try:
                if self.selected.kind in "VI":
                    source_value(value)
                elif self.selected.kind in "RCL" and number(value) <= 0:
                    raise ValueError("Component value must be positive")
            except ValueError as error:
                self.error(str(error))
                return
            self.checkpoint()
            for marker in self.project.markers:
                if marker["kind"] == "I" and marker["ref"].upper() == self.selected.ref.upper():
                    marker["ref"] = ref
            self.selected.ref, self.selected.value = ref, value
            self.refresh()

    def edit_source(self):
        part = self.selected
        if not part or part.kind not in "VI":
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{part.ref} source parameters")
        form = QFormLayout(dialog)
        tokens = source_tokens(part.value)
        wave_index = next((index for index, token in enumerate(tokens)
                           if token.upper() in WAVE_FIELDS), len(tokens))
        existing_mode = tokens[wave_index].upper() if wave_index < len(tokens) else "None"
        clauses = [token.upper() for token in tokens[:wave_index]]
        dc = QLineEdit()
        ac = QLineEdit()
        phase = QLineEdit("0")
        if "DC" in clauses and clauses.index("DC")+1 < len(clauses):
            dc.setText(tokens[clauses.index("DC")+1])
        elif len(clauses) == 1:
            dc.setText(tokens[0])
        if "AC" in clauses:
            index = clauses.index("AC")
            if index+1 < len(clauses):
                ac.setText(tokens[index+1])
            if index+2 < len(clauses):
                phase.setText(tokens[index+2])
        units = "V" if part.kind == "V" else "A"
        form.addRow(f"DC bias ({units})", dc)
        form.addRow(f"AC magnitude ({units})", ac)
        form.addRow("AC phase (°)", phase)
        ac.setPlaceholderText("Blank disables small-signal excitation")
        modes = QComboBox()
        modes.addItems(["None", *WAVE_FIELDS])
        modes.setCurrentText(existing_mode)
        form.addRow("Transient waveform", modes)
        group = QWidget()
        fields = QFormLayout(group)
        form.addRow(group)
        inputs = []
        edited = {}

        def populate(mode):
            old_mode = edited.get("mode")
            if old_mode:
                edited[old_mode] = [field.text() for field in inputs]
            while fields.rowCount():
                fields.removeRow(0)
            inputs.clear()
            edited["mode"] = mode
            if mode == "None":
                return
            values = edited.get(mode, source_tokens(SOURCE_DEFAULTS[mode])[1:])
            if mode == existing_mode and mode not in edited:
                values = tokens[wave_index+1:]
            if mode == "PWL" and mode not in edited:
                values = [" ".join(values)]
            for index, name in enumerate(WAVE_FIELDS[mode]):
                widget = QLineEdit(values[index] if index < len(values) else "")
                inputs.append(widget)
                fields.addRow(name, widget)

        populate(existing_mode)
        modes.currentTextChanged.connect(populate)
        message = QLabel()
        message.setWordWrap(True)
        form.addRow(message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def save_source():
            parts = []
            if dc.text().strip():
                parts.extend(("DC", dc.text().strip()))
            if ac.text().strip():
                parts.extend(("AC", ac.text().strip(), phase.text().strip() or "0"))
            if inputs:
                parts.extend((modes.currentText(), *(field.text().strip() for field in inputs)))
            value = " ".join(parts) if parts else "DC 0"
            try:
                source_value(value)
                self.value.setText(value)
                self.apply_part()
            except ValueError as error:
                message.setText(str(error))
                return
            dialog.accept()

        buttons.accepted.connect(save_source)
        dialog.exec()

    def add_marker(self, point):
        kind = self.mode.removeprefix("Mark ")
        if kind == "I":
            matches = [(part, index) for part in self.project.parts if part.kind not in "GN"
                       for index, pin in enumerate(part.pins()) if pin == point]
            if not matches:
                self.statusBar().showMessage("Choose a component pin")
                return
            if len(matches) == 1:
                part, pin = matches[0]
            else:
                names = [f"{part.ref} · pin {pin+1}" for part, pin in matches]
                name, accepted = QInputDialog.getItem(self, "Choose current marker pin", "Pin",
                                                       names, editable=False)
                if not accepted:
                    return
                part, pin = matches[names.index(name)]
            marker = {"kind": kind, "ref": part.ref, "pin": pin}
        else:
            try:
                marker_node(point, self.project, connectivity(self.project))
            except ValueError as error:
                self.statusBar().showMessage(str(error))
                return
            if kind == "VD" and self.marker_start is None:
                self.marker_start = point
                self.statusBar().showMessage("Click the negative wire or pin")
                self.canvas.update()
                return
            points = [self.marker_start, point] if kind == "VD" else [point]
            marker = {"kind": kind, "points": [list(p) for p in points]}
        self.checkpoint()
        self.project.markers.append(marker)
        self.set_mode("Select")
        self.refresh()

    def toggle_markers(self):
        self.markers_visible = not self.markers_visible
        self.canvas.update()

    def remove_last_marker(self):
        if self.project.markers:
            self.checkpoint()
            self.project.markers.pop()
            self.refresh()

    def clear_markers(self):
        if self.project.markers:
            self.checkpoint()
            self.project.markers.clear()
            self.refresh()

    def transform_selected(self, x, y, rotation):
        part = self.selected
        if not part:
            return
        snapshot = self.project.dump()
        old_nodes = connectivity(self.project)
        terminals = [p for p in self.project.parts if p.kind != "N"]
        before = [old_nodes[pin] for p in terminals for pin in p.pins()]
        previous = part.pins()
        part.x, part.y, part.rotation = x, y, rotation
        for old, new in zip(previous, part.pins()):
            if old == new or not any(on_wire(old, wire) for wire in self.project.wires):
                continue
            bend = (new[0], old[1])
            self.project.wires.extend((a, b) for a, b in ((old, bend), (bend, new)) if a != b)
        try:
            new_nodes = connectivity(self.project)
            after = [new_nodes[pin] for p in terminals for pin in p.pins()]
            pairs = set(zip(before, after))
            if (len(pairs) != len(set(before)) or len(pairs) != len(set(after))
                    or any((a == "0") != (b == "0") for a, b in pairs)):
                raise ValueError("This move would change electrical connectivity. Edit the wires first.")
        except ValueError as error:
            index = self.project.parts.index(part)
            self.project = Project.load(snapshot)
            self.select(self.project.parts[index])
            self.error(str(error))
            return
        self.undo_stack.append(snapshot)
        self.redo_stack.clear()
        self.refresh()

    def rotate(self):
        if self.selected:
            part = self.selected
            if any(on_wire(pin, wire) for pin in part.pins() for wire in self.project.wires):
                self.error("Remove the connected wires before rotating this part")
                return
            try:
                self.transform_selected(part.x, part.y, (part.rotation + 1) % 4)
            except ValueError as error:
                self.error(str(error))

    def delete(self):
        if self.selected_parts or self.selected_wires or self.selected_junctions:
            self.checkpoint()
            refs = {self.project.parts[index].ref.upper() for index in self.selected_parts}
            self.project.markers = [marker for marker in self.project.markers
                                    if marker["kind"] != "I" or marker["ref"].upper() not in refs]
            for name, selected in (("parts", self.selected_parts), ("wires", self.selected_wires),
                                   ("junctions", self.selected_junctions)):
                setattr(self.project, name, [item for index, item in enumerate(getattr(self.project, name))
                                             if index not in selected])
            self.select(None)
            self.refresh()

    def confirm_discard(self):
        if self.project.dump() == self.saved:
            return True
        choice = QMessageBox.question(self, "Unsaved schematic", "Save your changes?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                      | QMessageBox.StandardButton.Cancel)
        return choice == QMessageBox.StandardButton.Discard or (
            choice == QMessageBox.StandardButton.Save and self.save())

    def replace(self, project, path=None):
        if not self.confirm_discard():
            return
        self.project, self.path = project, path
        self.saved = project.dump() if path or not project.parts else Project().dump()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.select(None)
        self.set_mode("Select")
        self.reset_view()
        self.refresh()

    def open_project(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open schematic", filter="Workbench schematic (*.cwb)")
        if name:
            try:
                self.replace(Project.load(Path(name).read_text(encoding="utf-8")), Path(name))
            except (OSError, ValueError, TypeError, KeyError) as error:
                self.error(str(error))

    def load_ac_example(self):
        project = example()
        load = project.parts[2]
        load.kind, load.ref, load.value = "C", "C1", "1u"
        project.parts[0].value = "DC 0 AC 1 0"
        project.active = "AC sweep"
        self.replace(project)

    def save(self, save_as=False):
        path = self.path
        if not path or save_as:
            name, _ = QFileDialog.getSaveFileName(self, "Save schematic", filter="Workbench schematic (*.cwb)")
            if not name:
                return False
            path = Path(name)
            if not path.suffix:
                path = path.with_suffix(".cwb")
        try:
            self.project.save(path)
        except OSError as error:
            self.error(str(error))
            return False
        self.path, self.saved = path, self.project.dump()
        self.refresh()
        return True

    def change_profile(self, name):
        if name in self.project.profiles and name != self.project.active:
            self.checkpoint()
            self.project.active = name
            self.refresh()

    def new_profile(self, copy_active=False):
        name, accepted = QInputDialog.getText(self, "Simulation profile", "Profile name")
        if not accepted:
            return
        choices = {"Operating point": "OP", "DC sweep": "DC", "AC sweep": "AC", "Transient": "TRAN"}
        mode = self.project.profiles[self.project.active].mode
        if not copy_active:
            label, accepted = QInputDialog.getItem(self, "Simulation profile", "Analysis",
                                                   list(choices), editable=False)
            if not accepted:
                return
            mode = choices[label]
        snapshot = self.project.dump()
        try:
            self.project.add_profile(name, mode, copy_active)
        except ValueError as error:
            self.error(str(error))
            return
        self.undo_stack.append(snapshot)
        self.redo_stack.clear()
        self.refresh()
        self.edit_profile()

    def remove_profile(self):
        if len(self.project.profiles) == 1:
            self.error("Keep at least one simulation profile")
            return
        self.checkpoint()
        self.project.remove_active_profile()
        self.refresh()

    def edit_profile(self):
        current = self.project.profiles[self.project.active]
        dialog = QDialog(self)
        dialog.setWindowTitle("Analysis setup")
        form = QFormLayout(dialog)
        form.addRow("Active analysis", QLabel(self.project.active))
        fields = {}
        labels = {"step": "Maximum step", "stop": "Stop time",
                  "start_hz": "Start frequency (Hz)", "stop_hz": "Stop frequency (Hz)",
                  "sweep_source": "Source reference", "sweep_start": "Start value",
                  "sweep_stop": "Stop value", "sweep_step": "Step"}
        keys = {"OP": (), "TRAN": ("step", "stop"), "AC": ("start_hz", "stop_hz"),
                "DC": ("sweep_source", "sweep_start", "sweep_stop", "sweep_step")}
        for key in keys[current.mode]:
            fields[key] = QLineEdit(getattr(current, key))
            form.addRow(labels[key], fields[key])
        points = QSpinBox()
        if current.mode == "AC":
            points.setRange(1, 10000)
            points.setValue(current.points)
            form.addRow("Points per decade", points)
        if current.mode == "OP":
            form.addRow(QLabel("Calculate steady-state node voltages and branch currents."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            values = vars(current).copy()
            values.update({key: widget.text().strip() for key, widget in fields.items()})
            if current.mode == "AC":
                values["points"] = points.value()
            profile = Profile(**values)
            try:
                profile.directive()
            except ValueError as error:
                self.error(str(error))
                return
            self.checkpoint()
            self.project.profiles[self.project.active] = profile
            self.refresh()

    def edit_model(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Diode model")
        form = QFormLayout(dialog)
        name = QComboBox()
        name.setEditable(True)
        name.addItems(self.project.diode_models)
        current, emission = QLineEdit(), QLineEdit()

        def fill(key):
            values = self.project.diode_models.get(key, ("1e-14", "1"))
            current.setText(values[0])
            emission.setText(values[1])

        name.currentTextChanged.connect(fill)
        fill(name.currentText())
        form.addRow("Model name", name)
        form.addRow("Saturation current (A)", current)
        form.addRow("Emission coefficient", emission)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            key = name.currentText().strip().upper()
            values = (current.text().strip(), emission.text().strip())
            try:
                diode_model(key, *values)
            except ValueError as error:
                self.error(str(error))
                return
            self.checkpoint()
            self.project.diode_models[key] = values
            self.refresh()

    def edit_selection(self):
        if self.selected is not None:
            self.properties_dock.show()
            self.value.setFocus()
            self.value.selectAll()

    def error(self, message):
        self.output_dock.show()
        self.log.appendPlainText(message)
        self.tabs.setCurrentWidget(self.log)
        self.statusBar().showMessage(message, 6500)

    def preview(self):
        try:
            deck = netlist(self.project)
        except ValueError as error:
            self.error(str(error))
            return None
        self.deck.setPlainText(deck)
        self.output_dock.show()
        self.tabs.setCurrentWidget(self.deck)
        return deck

    def choose_solver(self):
        name, _ = QFileDialog.getOpenFileName(self, "Choose ngspice executable")
        if name:
            self.solver = name
            self.statusBar().showMessage(f"Simulator: {name}", 6000)

    def runs_root(self):
        return (self.path.parent / f"{self.path.stem}.runs" if self.path
                else Path.home() / "CircuitBench Runs")

    def open_run_history(self):
        try:
            folders = sorted(self.runs_root().glob("run-*"), key=lambda folder: folder.stat().st_mtime,
                             reverse=True)
        except OSError as error:
            self.error(str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Simulation run history")
        dialog.resize(540, 310)
        layout = QVBoxLayout(dialog)
        runs = QListWidget()
        for folder in folders:
            try:
                status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
                snapshot = Project.load((folder / "project.cwb").read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, KeyError):
                continue
            item = QListWidgetItem(f"{snapshot.active}   ·   {status.get('status', 'unknown')}   ·   {folder.name}")
            item.setData(Qt.ItemDataRole.UserRole, folder)
            runs.addItem(item)
        if not runs.count():
            layout.addWidget(QLabel("No recorded runs for this project location."))
        layout.addWidget(runs)
        button = QPushButton("Open selected run")
        button.setEnabled(bool(runs.count()))
        button.clicked.connect(dialog.accept)
        runs.itemDoubleClicked.connect(lambda _: dialog.accept())
        runs.setCurrentRow(0)
        layout.addWidget(button)
        if dialog.exec() and runs.currentItem():
            try:
                self.load_saved_run(runs.currentItem().data(Qt.ItemDataRole.UserRole))
            except (OSError, ValueError) as error:
                self.error(str(error))

    def load_saved_run(self, folder):
        logs = []
        for name in ("stdout.log", "stderr.log"):
            path = folder / name
            if path.is_file():
                with path.open(encoding="utf-8", errors="replace") as file:
                    logs.append(file.read(60000))
        self.log.setPlainText("\n".join([str(folder), *logs]))
        self.tabs.setCurrentWidget(self.log)
        if (folder / "result.raw").is_file():
            snapshot = (folder / "project.cwb").read_text(encoding="utf-8")
            self.show_plot(load_plot(folder / "result.raw"), snapshot)

    def run(self):
        if self.process is not None:
            return
        deck = self.preview()
        if deck is None:
            return
        if not self.solver:
            self.error("ngspice unavailable. Choose its executable from the Simulation menu.")
            return
        try:
            parent = self.runs_root()
            parent.mkdir(exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
            (folder / "schematic.cir").write_text(deck, encoding="ascii")
            (folder / "circuit.cir").write_text(analysis_deck(deck), encoding="ascii")
            revision = self.project.dump()
            (folder / "project.cwb").write_text(revision, encoding="utf-8")
        except (OSError, ValueError) as error:
            self.error(str(error))
            return
        self.run_dir = folder
        self.pending_revision = revision
        self.cancelled = False
        self.log.setPlainText(str(folder))
        self.tabs.setCurrentWidget(self.log)
        process = QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(folder))
        process.setProgram(self.solver)
        self.run_executable = self.solver
        process.setArguments(["-n", "-b", "circuit.cir"])
        process.setStandardOutputFile(str(folder / "stdout.log"))
        process.setStandardErrorFile(str(folder / "stderr.log"))
        process.errorOccurred.connect(self.process_error)
        process.finished.connect(self.finished)
        self.run_action.setEnabled(False)
        self.cancel_action.setEnabled(True)
        process.start()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.process is not None:
            self.error(self.process.errorString())
            self.finished(-1)

    def cancel(self):
        if self.process is not None:
            self.cancelled = True
            self.process.kill()

    def finished(self, code):
        process, self.process = self.process, None
        if process is None:
            return
        process.deleteLater()
        self.run_action.setEnabled(True)
        self.cancel_action.setEnabled(False)
        status = "cancelled" if self.cancelled else "failed"
        try:
            for name in ("stdout.log", "stderr.log"):
                path = self.run_dir / name
                if path.exists():
                    with path.open(encoding="utf-8", errors="replace") as log:
                        self.log.appendPlainText(log.read(60000))
            if code == 0 and not self.cancelled:
                self.show_plot(load_plot(self.run_dir / "result.raw"), self.pending_revision)
                status = "completed"
            else:
                self.error(f"Simulation {status} (exit {code})")
        except (OSError, ValueError) as error:
            self.error(str(error))
        try:
            (self.run_dir / "status.json").write_text(json.dumps({
                "status": status, "exit_code": code, "executable": self.run_executable,
            }), encoding="utf-8")
        except OSError as error:
            self.error(str(error))

    def show_plot(self, plot, revision):
        measurements = []
        if revision is not None:
            try:
                snapshot = Project.load(revision)
                revision = snapshot.dump()
                netlist(snapshot, measurements)
            except (KeyError, TypeError, ValueError):
                measurements.clear()
        page = self.add_plot_page(plot, revision, plot.marker_columns(measurements) or None)
        self.wave, self.run_revision = page.wave, revision
        self.refresh()
        self.open_plots()

    def open_plots(self):
        self.plot_window.show()
        self.plot_window.raise_()

    def add_plot_page(self, plot, revision, selected=None):
        page = PlotReportPage(plot, selected)
        page.revision = revision
        page.title = plot.title
        index = self.plot_pages.addTab(page, page.title)
        self.plot_pages.setCurrentIndex(index)
        return page

    def duplicate_plot_page(self):
        page = self.plot_pages.currentWidget()
        if page is not None:
            copy = self.add_plot_page(page.wave.plot, page.revision, page.wave.columns)
            copy.title = f"{page.title} (copy)"
            self.refresh()

    def rename_plot_page(self, index):
        page = self.plot_pages.widget(index)
        if page is not None:
            name, accepted = QInputDialog.getText(self.plot_window, "Name plot page", "Title", text=page.title)
            if accepted and name.strip():
                page.title = name.strip()
                self.refresh()

    def close_plot_page(self, index):
        page = self.plot_pages.widget(index)
        if page is None:
            return
        self.plot_pages.removeTab(index)
        if page.plot is self.wave.plot:
            current = self.plot_pages.currentWidget()
            self.wave = current.wave if current is not None else Waveform()
            self.run_revision = current.revision if current is not None else None
        page.deleteLater()
        self.refresh()

    def open_waveform(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open waveform", filter="ngspice raw file (*.raw)")
        if name:
            try:
                path = Path(name)
                snapshot = path.with_name("project.cwb")
                self.show_plot(load_plot(path), snapshot.read_text(encoding="utf-8") if snapshot.exists() else None)
            except (OSError, ValueError) as error:
                self.error(str(error))

    def export_waveform(self):
        page = self.plot_pages.currentWidget()
        plot = page.wave.plot if page is not None else self.wave.plot
        if plot is None:
            self.error("Open or generate a waveform first")
            return
        name, _ = QFileDialog.getSaveFileName(self, "Export waveform", filter="CSV (*.csv)")
        if name:
            try:
                plot.export(name)
            except OSError as error:
                self.error(str(error))

    def closeEvent(self, event):
        if self.process is not None:
            self.error("Cancel the active simulation before closing")
            event.ignore()
        elif not self.confirm_discard():
            event.ignore()
        else:
            event.accept()


if __name__ == "__main__":
    smoke_test = len(sys.argv) == 3 and sys.argv[1] == "--smoke-test"
    application = QApplication(sys.argv[:1] if smoke_test else sys.argv)
    application.setApplicationName("CircuitBench")
    application.setStyle("Fusion")
    application.setWindowIcon(QIcon(str(RUNTIME / "CircuitBench.ico")))
    window = Editor()
    window.show()
    if smoke_test:
        from smoke_test import verify

        sys.exit(verify(application, window, Path(sys.argv[2])))
    sys.exit(application.exec())
