"""CircuitBench desktop entry point."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from PyQt6.QtCore import QEvent, QItemSelectionModel, QPointF, QProcess, QSettings, Qt, QUrl
from PyQt6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QFileDialog,
    QFormLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QAbstractItemView,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QToolBar,
    QHBoxLayout, QVBoxLayout, QWidget,
)

from desktop_theme import apply_desktop_theme
from canvas import Canvas, engineering
from circuit import (GENERIC_MODELS, Profile, Project, SOURCE_DEFAULTS, SYMBOLS, WAVE_FIELDS, connectivity,
                     diode_model, example, marker_node, netlist, number, on_wire, source_tokens,
                     source_value, translate_selection, reusable_subcircuit)
from plotting import PlotReportPage, Waveform
from qucs_library import import_diodes
from results import analysis_deck, bias_deck, bias_readings, load_plot, trim_bias_vectors
from spice_library import MODEL_PORTS, read_library
from subcircuit_designer import SubcircuitDesigner


VERSION = "0.4.6-beta"

RUNTIME = Path(__file__).resolve().parent / "runtime"
BUNDLED_SOLVER = RUNTIME / "ngspice" / "bin" / "ngspice_con.exe"


class MarkerList(QListWidget):
    def selectionCommand(self, index, event=None):
        return QItemSelectionModel.SelectionFlag.NoUpdate


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
        self.selected_markers = set()
        self.readings = self.active_readings = None
        self.readings_revision = None
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
        self.setDockNestingEnabled(True)
        self.catalog = []
        try:
            _, opamps, _ = read_library(RUNTIME / "models/basic_models/OpAmps/Op07.mod", number)
            self.bundled_opamp = opamps.get("OP07")
        except (OSError, ValueError):
            self.bundled_opamp = None
        self.placement_model = None
        self.placement_value = None
        self.marker_start = None
        self.markers_visible = True
        self.mode_actions = {}
        self.mode_group = QActionGroup(self)
        self.mode_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        tools = QToolBar("Project and simulation")
        tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tools.setMovable(False)
        self.addToolBar(tools)
        drawing_tools = QToolBar("Drawing tools")
        drawing_tools.setMovable(False)
        drawing_tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, drawing_tools)
        file_menu = self.menuBar().addMenu("&File")
        tools.addAction(self.action(file_menu, "New", "Ctrl+N", lambda: self.replace(Project())))
        tools.addAction(self.action(file_menu, "Open…", "Ctrl+O", self.open_project))
        tools.addAction(self.action(file_menu, "Save", "Ctrl+S", self.save))
        tools.addSeparator()
        self.action(file_menu, "Save as…", "Ctrl+Shift+S", lambda: self.save(True))
        examples = file_menu.addMenu("Examples")
        exports = file_menu.addMenu("Export")
        self.action(examples, "Load voltage divider example", "", lambda: self.replace(example()))
        self.action(examples, "Load RC filter AC example", "", self.load_ac_example)
        self.action(examples, "Load RC transient example", "", lambda: self.load_ac_example(True))
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
        imports = self.menuBar().addMenu("&Import")
        self.action(imports, "Import model libraries…", "", self.open_library)
        self.action(draw, "Build reusable subcircuit…", "", self.build_reusable_subcircuit)
        self.action(imports, "Import CircuitBench subcircuit…", "", self.import_reusable_subcircuit)
        self.action(imports, "Import local ngspice starter pack", "", self.import_starter_pack)
        self.action(imports, "Import bundled Qucs-S diodes", "", self.import_bundled_diodes)
        self.action(imports, "Browse ngspice starter pack", "", lambda: QDesktopServices.openUrl(
            QUrl("https://ngspice.sourceforge.io/modelparams.html")))
        self.action(imports, "Browse Qucs-S libraries", "", lambda: QDesktopServices.openUrl(
            QUrl("https://github.com/ra3xdh/qucs_s/tree/current/library")))
        self.action(tools, "Rotate", "", self.rotate)
        from analysis_setup import setup, export_canvas, annotation
        analysis = self.menuBar().addMenu("&Analysis")
        self.action(analysis,"Analysis setup…","",lambda: setup(self))
        self.action(exports,"Schematic diagram…","",lambda: export_canvas(self))
        self.action(draw,"Text annotation…","",lambda: annotation(self))
        view = self.menuBar().addMenu("&View")
        dots = view.addAction("Canvas dots")
        dots.setCheckable(True)
        dots.setChecked(True)
        def toggle_dots(enabled):
            self.show_dots = enabled
            self.canvas.update()
        dots.toggled.connect(toggle_dots)
        view.addAction(self.action(tools, "Fit schematic", "F", self.fit_view))
        self.action(view, "Reset zoom", "", self.reset_view)
        theme_menu = view.addMenu("Theme")
        self.theme_settings = QSettings("CircuitBench", "CircuitBench")
        self.theme_options = QActionGroup(self)
        for theme in ("System", "Light", "Dark"):
            action = self.action(theme_menu, theme, "", lambda theme=theme: self.set_theme(theme))
            action.setCheckable(True)
            self.theme_options.addAction(action)
        self.set_theme(self.theme_settings.value("theme", "System"))
        tools.addSeparator()
        self.action(tools, "Analysis setup…", "", lambda: setup(self))
        self.action(tools, "Netlist", "", self.preview)
        self.run_action = self.action(tools, "Run", "F5", self.run)
        tools.widgetForAction(self.run_action).setObjectName("runButton")
        self.cancel_action = self.action(tools, "Cancel run", "", self.cancel)
        self.cancel_action.setEnabled(False)
        simulation = self.menuBar().addMenu("&Simulation")
        self.action(simulation, "New profile…", "", self.new_profile)
        self.action(simulation, "Duplicate active profile…", "", lambda: self.new_profile(True))
        self.action(simulation, "Remove active profile", "", self.remove_profile)
        simulation.addSeparator()
        self.action(simulation, "Choose ngspice executable…", "", self.choose_solver)
        self.action(simulation, "Session History…", "", self.open_run_history)
        self.action(simulation, "Show plot", "F6", self.open_plots)
        self.action(imports, "Open saved waveform…", "", self.open_waveform)
        self.action(exports, "CSV data…", "", self.export_waveform)
        self.action(exports,"Active plot…","",lambda: self.export_plot_page(False))
        self.action(exports,"Plot page…","",self.export_plot_page)
        from studies import install_studies
        install_studies(self, simulation, draw, imports)
        markers = self.menuBar().addMenu("&Markers")
        for mode, title, key in (("Mark V", "Voltage", "Ctrl+M"),
                                 ("Mark VD", "Differential voltage", "Ctrl+Shift+M"),
                                 ("Mark I", "Current into pin", "Ctrl+Alt+I"), ("Mark P","Power into component","Ctrl+Alt+P")):
            self.action(markers, title, key, lambda mode=mode: self.set_mode(mode))
        markers.addSeparator()
        show = self.action(markers, "Show markers", "", self.toggle_markers)
        show.setCheckable(True)
        show.setChecked(True)
        self.action(markers, "Remove last marker", "", self.remove_last_marker)
        self.action(markers, "Clear all markers", "", self.clear_markers)
        help_menu = self.menuBar().addMenu("&Help")
        self.action(help_menu, "About CircuitBench…", "", self.show_about)

        library = QWidget()
        library_layout = QVBoxLayout(library)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a part or model")
        self.library = QListWidget()
        self.library.setDragEnabled(True)
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
        self.profiles = QLabel()
        tools.addSeparator()
        self.profiles.setFrameShape(self.profiles.Shape.StyledPanel)
        self.profiles.setObjectName("analysisMode")
        tools.addWidget(self.profiles)
        labels = QPushButton("Label appearance…")
        labels.clicked.connect(self.edit_label_appearance)
        form.addRow(labels)
        self.properties_dock = self.dock("Properties", properties, Qt.DockWidgetArea.RightDockWidgetArea)
        self.action(edit, "Edit selected properties", "Alt+Return", self.edit_selection)

        self.tabs = QTabWidget()
        self.log = QPlainTextEdit(readOnly=True)
        self.deck = QPlainTextEdit(readOnly=True)
        self.tabs.addTab(self.log, "Diagnostics")
        self.tabs.addTab(self.deck, "Netlist")
        self.plot_window = QDialog(self)
        self.plot_window.setWindowTitle("CircuitBench Simulation")
        self.plot_window.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.plot_window.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.plot_window.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.plot_window.resize(1060, 670)
        schematic_shortcut = QAction("Return to schematic",self.plot_window)
        schematic_shortcut.setShortcuts(["F6","Shift+F6"])
        schematic_shortcut.triggered.connect(self.return_to_schematic)
        self.plot_window.addAction(schematic_shortcut)
        plots_layout = QVBoxLayout(self.plot_window)
        button = QPushButton("Add plot page")
        button.clicked.connect(self.duplicate_plot_page)
        controls = QHBoxLayout()
        controls.addWidget(button)
        close_page = QPushButton("Remove plot page")
        close_page.clicked.connect(lambda: self.close_plot_page(self.plot_pages.currentIndex()))
        controls.addWidget(close_page)
        studies_button = QPushButton("Simulation studies")
        studies_button.setMenu(self.studies_menu)
        controls.addWidget(studies_button)
        for control in (button,close_page,studies_button):
            control.setAutoDefault(False)
        controls.addStretch()
        plots_layout.addLayout(controls)
        self.plot_pages = QTabWidget()
        self.plot_pages.setTabsClosable(True)
        self.plot_pages.tabCloseRequested.connect(self.close_plot_page)
        self.plot_pages.tabBarDoubleClicked.connect(self.rename_plot_page)
        plots_layout.addWidget(self.plot_pages)
        self.wave = Waveform()
        self.marker_list = MarkerList()
        self.marker_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.marker_list.itemClicked.connect(self.select_marker_item)
        self.marker_list.installEventFilter(self)
        self.marker_dock = self.dock("Measurement markers", self.marker_list, Qt.DockWidgetArea.RightDockWidgetArea)
        self.bias_list = QListWidget()
        self.bias_list.itemSelectionChanged.connect(self.canvas.update)
        self.reading_nets = {}
        bias_panel = QWidget()
        bias_layout = QVBoxLayout(bias_panel)
        bias_layout.addWidget(self.bias_list)
        self.voltage_style_button = QPushButton("Voltage label appearance…")
        self.voltage_style_button.clicked.connect(self.edit_voltage_labels)
        bias_layout.addWidget(self.voltage_style_button)
        self.bias_dock = self.dock("Readings", bias_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.bias_action = self.action(simulation, "Show schematic readings", "", self.canvas.update)
        self.bias_action.setCheckable(True)
        self.bias_action.setChecked(True)
        tools.addAction(self.bias_action)
        self.action(edit,"Voltage label appearance…","",self.edit_voltage_labels)
        from desktop04 import install
        install(self,view,edit,simulation,controls)
        self.output_dock = self.dock("Simulation output", self.tabs, Qt.DockWidgetArea.BottomDockWidgetArea)
        view.addSeparator()
        for panel in (self.library_dock, self.properties_dock, self.marker_dock, self.bias_dock, self.output_dock):
            view.addAction(panel.toggleViewAction())
            panel.hide()
        from PyQt6.QtWidgets import QStyle
        for button, icon in (
            (import_library, QStyle.StandardPixmap.SP_DialogOpenButton),
            (self.apply, QStyle.StandardPixmap.SP_DialogApplyButton),
            (self.source_editor, QStyle.StandardPixmap.SP_FileDialogDetailedView),
            (labels, QStyle.StandardPixmap.SP_DialogResetButton),
            (self.voltage_style_button, QStyle.StandardPixmap.SP_FileDialogContentsView),
        ):
            button.setIcon(self.style().standardIcon(icon))
        self.coordinates = QLabel("X 0   Y 0")
        self.coordinates.setMinimumWidth(100)
        self.statusBar().addPermanentWidget(self.coordinates)
        self.select(None)
        self.refresh()
        self.set_mode("Select")
        self.apply_theme()
        QApplication.styleHints().colorSchemeChanged.connect(self.apply_theme)

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle("About CircuitBench")
        box.setText(f"CircuitBench {VERSION}")
        box.setInformativeText(
            "CircuitBench is a desktop schematic editor and circuit simulator. "
            "Draw circuits, run DC, AC, and transient analyses with ngspice, "
            "and inspect or export the results in CircuitBench Simulation.\n\n"
            "Developer: Tonmoy Paul\n"
            "Licensed under GPL-3.0-only.")
        project = box.addButton("GitHub project", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is project:
            QDesktopServices.openUrl(QUrl("https://github.com/tonmoypaul2003/CircuitBench"))

    def set_theme(self, preference):
        options = {"System": Qt.ColorScheme.Unknown, "Light": Qt.ColorScheme.Light, "Dark": Qt.ColorScheme.Dark}
        if preference not in options:
            preference = "System"
        self.theme_preference = preference
        self.theme_settings.setValue("theme", preference)
        for action in self.theme_options.actions():
            action.setChecked(action.text() == preference)
        QApplication.styleHints().setColorScheme(options[preference])
        self.apply_theme()

    def apply_theme(self):
        self.dark_theme = (self.theme_preference == "Dark" or
                           (self.theme_preference == "System"
                            and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark))
        QApplication.instance().setProperty("circuitbenchDark", self.dark_theme)
        apply_desktop_theme(self,self.dark_theme)
        self.canvas.update()

    def eventFilter(self, watched, event):
        if watched is self.marker_list and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Delete:
            self.delete()
            return True
        return super().eventFilter(watched, event)

    def select_marker_item(self, item):
        self.set_mode("Select")
        self.select_marker(self.marker_list.row(item),
                           bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier))

    def select_marker(self, index, extend):
        selected = self.selected_markers ^ {index} if extend else {index}
        groups = (self.selected_parts, self.selected_wires, self.selected_junctions) if extend else (set(), set(), set())
        self.set_selection(*groups, markers=selected)

    def refresh_markers(self):
        self.marker_list.clear()
        for index, marker in enumerate(self.project.markers):
            label = (f"p({marker['ref']})" if marker.get("power") else f"I({marker['ref']}:{marker['pin']+1})" if marker["kind"] == "I" else
                     marker["kind"] + " " + " / ".join(f"{x},{y}" for x, y in marker["points"]))
            unit = "W" if marker.get("power") else "A" if marker["kind"]=="I" else "V"
            self.marker_list.addItem(f"{index+1}. {label} ({unit})")
        self.highlight_markers()

    def highlight_markers(self):
        for index in range(self.marker_list.count()):
            self.marker_list.item(index).setSelected(index in self.selected_markers)

    def refresh_readings(self, revision):
        self.active_readings = self.readings if revision == self.readings_revision else None

        self.voltage_style_button.setEnabled(bool(self.active_readings))
        selected = self.bias_list.selectedItems()
        previous = selected[0].data(Qt.ItemDataRole.UserRole) if selected else None
        self.bias_list.clear()
        self.reading_nets = connectivity(self.project)

        def add_reading(text, net, pin=None):
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, (net, pin))
            self.bias_list.addItem(item)
            if (net, pin) == previous:
                self.bias_list.setCurrentItem(item)

        if not self.active_readings:
            for net in sorted(set(self.reading_nets.values())):
                add_reading("Ground" if net=="0" else net,net)
            return

        for node in self.active_readings["nodes"]:
            name = "Ground" if node["net"] == "0" else node["net"]
            add_reading(f"{name}    {engineering(node['value'], 'V')}", node["net"])
        self.part_powers = {}
        volts = {n["net"]: n["value"] for n in self.active_readings["nodes"]}
        currents = self.active_readings["currents"]
        from collections import Counter
        measured = Counter(current["part"] for current in currents)
        for current in currents:
            part = self.project.parts[current["part"]]
            pin, value = current["pin"], current["value"]
            pins = part.pins()
            index = current["part"]
            if all(self.reading_nets[p] in volts for p in pins):
                potential = volts[self.reading_nets[pins[pin]]]
                if len(pins) == 2 and pin == 0:
                    self.part_powers[index] = value * (potential - volts[self.reading_nets[pins[1]]])
                elif measured[index] == len(pins):
                    self.part_powers[index] = self.part_powers.get(index, 0) + value * potential
            terminal = part.port_names[pin] if pin < len(part.port_names) else f"pin {pin+1}"
            direction = " into" if value > 0 else " out of" if value < 0 else ""
            point = part.pins()[pin]
            add_reading(f"{part.ref} · {terminal}    {engineering(abs(value), 'A')}{direction}",
                        self.reading_nets[point], point)

    def edit_voltage_labels(self):
        if not self.active_readings:
            return
        settings = self.project.voltage_labels
        dialog = QDialog(self)
        dialog.setWindowTitle("Voltage label appearance")
        form = QFormLayout(dialog)
        target = QComboBox()
        target.addItem("Default for all nodes", "*")
        for node in self.active_readings["nodes"]:
            target.addItem("Ground" if node["net"] == "0" else node["net"], node["net"])
        form.addRow("Apply to", target)
        inherit = QCheckBox("Use default for this node")
        form.addRow(inherit)
        automatic = QCheckBox("Use theme color")
        color_button = QPushButton()
        form.addRow(automatic, color_button)
        size, horizontal, vertical = QSpinBox(), QSpinBox(), QSpinBox()
        for spin, title, low, high in ((size, "Text size", 8, 48),
                                       (horizontal, "Horizontal offset", -500, 500),
                                       (vertical, "Vertical offset", -500, 500)):
            spin.setRange(low, high)
            spin.setSuffix(" px")
            form.addRow(title, spin)
        help_text = QLabel("Positive offsets move the label right and down.")
        form.addRow(help_text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        color = self.canvas.ink("#0f766e")

        def update_controls():
            custom = not inherit.isChecked()
            for widget in (automatic, size, horizontal, vertical):
                widget.setEnabled(custom)
            color_button.setEnabled(custom and not automatic.isChecked())
            color_button.setText(color.name())

        def choose_color():
            nonlocal color
            chosen = QColorDialog.getColor(color, dialog, "Voltage label color")
            if chosen.isValid():
                color = chosen
                automatic.setChecked(False)
                update_controls()

        def load_style():
            nonlocal color
            key = target.currentData()
            style = settings.get(key, settings.get("*", {"color": None, "size": 12, "x": 0, "y": 0}))
            color = QColor(style["color"]) if style["color"] else self.canvas.ink("#0f766e")
            inherit.setVisible(key != "*")
            inherit.setChecked(key != "*" and key not in settings)
            automatic.setChecked(style["color"] is None)
            size.setValue(style["size"])
            horizontal.setValue(style["x"])
            vertical.setValue(style["y"])
            update_controls()

        target.currentIndexChanged.connect(load_style)
        inherit.toggled.connect(update_controls)
        automatic.toggled.connect(update_controls)
        color_button.clicked.connect(choose_color)
        load_style()
        if dialog.exec():
            key = target.currentData()
            style = (None if inherit.isChecked() else
                     {"color": None if automatic.isChecked() else color.name(), "size": size.value(),
                      "x": horizontal.value(), "y": vertical.value()})
            if style != settings.get(key):
                self.checkpoint()
                if style is None:
                    settings.pop(key, None)
                else:
                    settings[key] = style
                self.refresh()

    def action(self, menu, title, key, callback):
        action = QAction(title, self)
        from PyQt6.QtWidgets import QStyle
        icons = {"Run":"SP_MediaPlay","Cancel run":"SP_MediaStop","Open…":"SP_DialogOpenButton",
                 "Save":"SP_DialogSaveButton","New":"SP_FileIcon","Analysis setup…":"SP_FileDialogDetailedView",
                 "Select":"SP_ArrowUp","Wire":"SP_ArrowRight","Junction":"SP_DialogApplyButton",
                 "Part…":"SP_FileDialogNewFolder","Ground":"SP_ArrowDown","Label":"SP_FileDialogInfoView",
                 "Rotate":"SP_BrowserReload","Fit schematic":"SP_TitleBarMaxButton","Netlist":"SP_FileDialogListView",
                 "Show schematic readings":"SP_FileDialogContentsView","Undo":"SP_ArrowBack","Redo":"SP_ArrowForward",
                 "Save as…":"SP_DialogSaveButton","Delete selection":"SP_TrashIcon",
                 "Session History…":"SP_DirIcon","Show plot":"SP_ComputerIcon"}
        if title in icons:
            action.setIcon(self.style().standardIcon(getattr(QStyle.StandardPixmap,icons[title])))
            action.setIconVisibleInMenu(True)
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
            subcircuits = self.project.subcircuits.copy()
            if self.bundled_opamp:
                subcircuits.setdefault("OP07", self.bundled_opamp)
            for name, model in subcircuits.items():
                opamp = name == "OP07" and model == self.bundled_opamp
                title = "OpAmp · OP07" if opamp else f"{name}  ·  {len(model['ports'])} ports"
                keywords = title + (" op amp operational amplifier" if opamp else "")
                if query in keywords.casefold():
                    item = QListWidgetItem(title)
                    item.setToolTip("IN+, IN−, V+, V−, OUT" if opamp else ", ".join(model["ports"]))
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

    def build_reusable_subcircuit(self):
        name, _ = QFileDialog.getOpenFileName(self, "Choose the source schematic",
                                             str(self.path or ""), "CircuitBench schematic (*.cwb)")
        if name:
            saved = SubcircuitDesigner.open_file(self, Path(name))
            if saved is not None:
                self.statusBar().showMessage(f"Saved reusable subcircuit to {saved.name}", 6500)

    def import_reusable_subcircuit(self):
        names, _ = QFileDialog.getOpenFileNames(self, "Import CircuitBench subcircuits",
                                               filter="CircuitBench schematic (*.cwb)")
        if names:
            self.show_library_import([Path(name) for name in names])

    def open_library(self):
        names, _ = QFileDialog.getOpenFileNames(
            self, "Import model libraries",
            filter="Model libraries (*.cwb *.lib *.mod *.cir *.sp *.spi *.sub *.txt);;All files (*)")
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
                if path.suffix.lower() == ".cwb":
                    name, entry = reusable_subcircuit(Project.load(path.read_text(encoding="utf-8-sig")))
                    before = subcircuits.get(name, self.project.subcircuits.get(name))
                    if before is None:
                        subcircuits[name] = entry
                    elif before != entry:
                        issues.append(f"{path.name}: a different definition of {name} is already loaded")
                    continue
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
            except (OSError, ValueError, KeyError, TypeError) as error:
                issues.append(f"{path.name}: {error}")
        if models or subcircuits:
            self.checkpoint()
            self.project.device_models.update(models)
            self.project.subcircuits.update(subcircuits)
        self.filter_library(self.search.text())
        return count+len(models)+len(subcircuits), issues

    def drop_part(self, point):
        project = self.project
        touching = [(index, wire) for index, wire in enumerate(project.wires) if on_wire(point, wire)]
        splice = None
        if touching and self.mode in ("R", "C", "L", "V", "I", "D"):
            index, wire = touching[0]
            vertical = wire[0][0] == wire[1][0]
            dx, dy = (0, 2) if vertical else (2, 0)
            pins = (point[0]-dx, point[1]-dy), (point[0]+dx, point[1]+dy)
            anchors = {pin for part in project.parts for pin in part.pins()}
            anchors.update(project.junctions)
            anchors.update(end for i, segment in enumerate(project.wires) if i != index for end in segment)
            anchors.update(tuple(p) for marker in project.markers if marker["kind"] != "I" for p in marker["points"])
            if (len(touching) != 1 or any(not on_wire(pin, wire) for pin in pins)
                    or any(on_wire(anchor, pins) and anchor not in pins for anchor in anchors)):
                self.statusBar().showMessage("Drop onto a clear wire segment with room for both pins.", 5000)
                return False
            endpoints = sorted(wire)
            segments = [(a, b) for a, b in zip(endpoints, pins) if a != b]
            splice = index, segments, int(vertical)
        self.place_part(point, splice)
        return True

    def placement_subcircuit(self):
        if self.placement_value == "OP07":
            return self.project.subcircuits.get("OP07", self.bundled_opamp)
        return self.project.subcircuits.get(self.placement_value)

    def place_part(self, point, splice=None):
        self.checkpoint()
        self.project.add(self.mode, point)
        part = self.project.parts[-1]
        if self.placement_model is not None:
            part.value = self.project.register_qucs_diode(self.placement_model)
        elif self.placement_value is not None:
            part.value = self.placement_value
            if part.kind == "X":
                model = self.placement_subcircuit()
                self.project.subcircuits.setdefault(part.value, model)
                part.port_names = model["ports"].copy()
                part.port_sides = model.get("sides", []).copy()
                part.opamp_symbol = part.is_op07() and not part.port_sides
            elif part.kind in "QMJS":
                model = self.project.device_models[part.value]
                part.port_names = list(MODEL_PORTS[model["type"]][1])
        if splice:
            index, segments, part.rotation = splice
            self.project.wires[index:index+1] = segments
        self.select(part)
        self.refresh()

    def refresh(self):
        self.filter_library(self.search.text())
        if ((self.mode == "X" and self.placement_subcircuit() is None)
                or (self.mode in ("Q", "M", "J", "S") and self.placement_value not in self.project.device_models)):
            self.set_mode("Select")
        enabled = self.project.analysis_plan.get("enabled",[self.project.active]).copy()
        if self.project.analysis_plan.get("parametric"):
            enabled.append("Parametric Sweep")
        self.profiles.setText("Analysis: " + ", ".join(enabled))
        revision = self.project.dump()
        self.refresh_markers()
        simulation_revision = self.project.dump(include_label_styles=False)
        self.refresh_readings(simulation_revision)
        title = self.path.stem if self.path else "Untitled"
        self.setWindowTitle(f"{title}{' *' if revision != self.saved else ''} — CircuitBench")
        for index in range(self.plot_pages.count()):
            page = self.plot_pages.widget(index)
            suffix = "" if page.revision == simulation_revision else " · other revision"
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
                    "Mark P": "Click a two-terminal component to measure power",
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
        points.extend(tuple(note[:2]) for note in self.project.annotations)
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

    def set_selection(self, parts, wires, junctions, *, markers=None):
        self.selected_markers = markers if markers is not None else set()
        self.highlight_markers()
        self.selected_parts, self.selected_wires, self.selected_junctions = parts, wires, junctions
        count = len(parts)+len(wires)+len(junctions)+len(self.selected_markers)
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
        self.set_selection(parts, wires, junctions, markers=self.selected_markers if extend else None)

    def routed_move(self, dx, dy):
        snapshot = self.project.dump()
        for reroute, vertical in ((True, False), (True, True), (False, False), (False, True)):
            project = Project.load(snapshot)
            try:
                translate_selection(project, self.selected_parts, self.selected_wires,
                                    self.selected_junctions, dx, dy,
                                    reroute_terminals=reroute, vertical_first=vertical)
            except ValueError as error:
                reason = str(error)
            else:
                return project, ""
        return None, reason

    def move_group(self, dx, dy):
        if not (dx or dy) or not (self.selected_parts or self.selected_wires or self.selected_junctions):
            return
        project, error = self.routed_move(dx, dy)
        if project is None:
            self.statusBar().showMessage(error, 5000)
            return
        self.checkpoint()
        self.project = project
        self.set_selection(self.selected_parts, self.selected_wires, self.selected_junctions,
                           markers=self.selected_markers)
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
        if kind == "P":
            candidates = [p for p in self.project.parts if len(p.pins())==2
                          and (point in p.pins() or point==(p.x,p.y))]
            if candidates:
                self.checkpoint()
                self.project.markers.append({"kind":"I","ref":candidates[0].ref,"pin":0,"power":True})
                self.refresh()
            return
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
            self.selected_markers.discard(len(self.project.markers))
            self.refresh()

    def clear_markers(self):
        if self.project.markers:
            self.checkpoint()
            self.project.markers.clear()
            self.selected_markers.clear()
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

    def edit_label_appearance(self):
        part = self.selected
        marker = self.project.markers[next(iter(self.selected_markers))] if len(self.selected_markers)==1 else None
        index = getattr(self.canvas,"annotation_index",None)
        note = self.project.annotations[index] if index is not None and index<len(self.project.annotations) else None
        if part is None and marker is None and note is None:
            self.statusBar().showMessage("Select a part, marker or annotation first.",5000)
            return
        style = (part.label_style if part else marker.get("label_style",{}) if marker else note[3] if len(note)>3 else {}).copy()
        dialog = QDialog(self)
        dialog.setWindowTitle("Label appearance")
        form = QFormLayout(dialog)
        size = QSpinBox()
        size.setRange(6,48)
        size.setValue(style.get("size",12))
        form.addRow("Text size (px)",size)
        colors = [("color","Text color")]
        if marker:
            colors.append(("point_color","Point color"))
        elif part:
            colors.append(("symbol_color","Symbol color"))
        for key,caption in colors:
            button = QPushButton(style.get(key) or "Choose color…")
            def choose_color(checked=False,k=key,b=button):
                color = QColorDialog.getColor(QColor(style.get(k) or "#173d37"),dialog)
                if color.isValid():
                    style[k] = color.name()
                    b.setText(color.name())
            button.clicked.connect(choose_color)
            form.addRow(caption,button)
        flags = {}
        for key,caption in (("name","Show reference"),("value","Show value")) if part else (
                ("name","Show marker ID"),("value","Show measurement and unit"),("fill","Filled point")) if marker else ():
            check = QCheckBox(caption)
            check.setChecked(style.get(key,True))
            form.addRow(check)
            flags[key] = check
        reset = QPushButton("Reset label position")
        reset.clicked.connect(lambda:style.pop("offset",None))
        if note is None:
            form.addRow(reset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            self.checkpoint()
            style.update(size=size.value(),**{k:v.isChecked() for k,v in flags.items()})
            if part:
                part.label_style = style
            elif marker:
                marker["label_style"] = style
            else:
                note[3:] = [style]
            self.refresh()

    def delete(self):
        if getattr(self.canvas,"annotation_index",None) is not None:
            index = self.canvas.annotation_index
            self.checkpoint()
            if index >= len(self.project.annotations):
                self.canvas.annotation_index = None
                return
            self.project.annotations.pop(index)
            colors = {k:v for k,v in self.project.element_colors.items() if not k.startswith("text:")}
            for k,v in self.project.element_colors.items():
                if k.startswith("text:"):
                    i = int(k.split(":")[1])
                    if i!=index:
                        colors[f"text:{i-(i>index)}"] = v
            self.project.element_colors = colors
            self.canvas.annotation_index = None
            self.refresh()
            return
        if self.selected_parts or self.selected_wires or self.selected_junctions or self.selected_markers:
            self.checkpoint()
            refs = {self.project.parts[index].ref.upper() for index in self.selected_parts}
            self.project.markers = [marker for index, marker in enumerate(self.project.markers)
                                    if index not in self.selected_markers
                                    and (marker["kind"] != "I" or marker["ref"].upper() not in refs)]
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
        for part in project.parts:
            if not part.is_op07() or part.opamp_symbol:
                continue
            old_pins = part.pins()
            part.opamp_symbol = True
            occupied = {pin for other in project.parts if other is not part for pin in other.pins()}
            occupied.update(project.junctions)
            occupied.update(tuple(point) for marker in project.markers if marker["kind"] != "I"
                            for point in marker["points"])
            if any(pin in occupied or any(on_wire(pin, wire) for wire in project.wires)
                   for pin in set(old_pins) ^ set(part.pins())):
                part.opamp_symbol = False
        self.project, self.path = project, path
        self.session_path = None
        self.report_bias = None
        self.untitled_history = None
        self.canvas.annotation_index = None
        from desktop04 import refresh_history
        refresh_history(self)
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

    def load_ac_example(self, transient=False):
        project = example()
        load = project.parts[2]
        load.kind, load.ref, load.value = "C", "C1", "1u"
        project.parts[0].value = "PULSE 0 1 0 1n 1n 10m 20m" if transient else "DC 0 AC 1 0"
        project.active = "Transient" if transient else "AC sweep"
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
        from desktop04 import refresh_history
        refresh_history(self)
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
        return (self.path.parent if self.path else Path.home()) / "Simulation Runs"

    def open_run_history(self):
        from desktop04 import show_history
        show_history(self)

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
            self.show_run_result(folder, snapshot)
            from desktop04 import run_title
            page = self.plot_pages.currentWidget()
            page.title = run_title(folder)
            self.plot_pages.setTabText(self.plot_pages.currentIndex(),page.title)

    def run(self):
        if self.process is not None:
            return
        from analysis_setup import jobs
        try:
            self.analysis_jobs = jobs(self.project)
        except ValueError as error:
            self.error(str(error))
            return
        self.sweep_runs = []
        self.sweep_values = {}
        self.sweep_plan = self.project.analysis_plan.copy()
        self.run_next()

    def run_next(self):
        project, label = self.analysis_jobs.pop(0)
        self.sweep_job = label.startswith("Parametric_")
        self.run_label = label
        try:
            deck = netlist(project)
        except ValueError as error:
            self.error(str(error))
            self.analysis_jobs.clear()
            return
        if deck is None:
            return
        if not self.solver:
            self.error("ngspice unavailable. Choose its executable from the Simulation menu.")
            return
        try:
            parent = self.runs_root()
            parent.mkdir(exist_ok=True)
            from analysis_setup import allocate_run
            from desktop04 import session_folder
            folder = allocate_run(session_folder(self), self.path, label)
            (folder / "schematic.cir").write_text(deck, encoding="ascii")
            solver_deck = deck
            if project.profiles[project.active].mode == "OP":
                solver_deck, metadata = bias_deck(project)
                (folder / "operating-point.json").write_text(json.dumps(metadata), encoding="utf-8")
            (folder / "circuit.cir").write_text(analysis_deck(solver_deck), encoding="ascii")
            revision = project.dump()
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
                if self.sweep_job:
                    self.sweep_values[self.run_dir] = self.sweep_plan["values"][len(self.sweep_runs)]
                    self.sweep_runs.append(self.run_dir)
                else:
                    self.show_run_result(self.run_dir, self.pending_revision)
                    page = self.plot_pages.currentWidget()
                    from desktop04 import run_title
                    page.title = run_title(self.run_dir,self.run_label)
                    self.plot_pages.setTabText(self.plot_pages.currentIndex(),page.title)
                status = "completed"
            else:
                self.error(f"Simulation {status} (exit {code})")
        except (OSError, ValueError) as error:
            self.error(str(error))
        if status != "completed":
            self.readings = None
            self.refresh()
        try:
            (self.run_dir / "status.json").write_text(json.dumps({
                "status": status, "exit_code": code, "executable": self.run_executable,
                "profile": self.run_label,
            }), encoding="utf-8")
        except OSError as error:
            self.error(str(error))

        from desktop04 import refresh_history, show_sweep
        refresh_history(self)
        if status=='completed' and self.analysis_jobs:
            self.run_next()
        elif status=='completed' and self.sweep_runs:
            try:
                show_sweep(self)
            except (OSError,ValueError) as error:
                self.error(str(error))

    def show_run_result(self, folder, revision):
        metadata = folder / "operating-point.json"
        operating_point = json.loads(metadata.read_text(encoding="utf-8")) if metadata.is_file() else None
        snapshot = Project.load(revision)
        self.show_plot(load_plot(folder / "result.raw"), revision, operating_point=operating_point,
                       open_window=snapshot.profiles[snapshot.active].mode not in ("OP", "DC"))
        if self.active_readings:
            self.report_bias = [self.bias_list.item(i).text() for i in range(self.bias_list.count())]
            self.bias_dock.show()

    def show_plot(self, plot, revision, *, operating_point=None, open_window=True):
        measurements = []
        snapshot = None
        if revision is not None:
            try:
                snapshot = Project.load(revision)
                revision = snapshot.dump(include_label_styles=False)
                netlist(snapshot, measurements)
            except (KeyError, TypeError, ValueError):
                measurements.clear()
        self.marker_values = {}
        if operating_point and len(plot.rows)==1:
            selected = []
            for index,measurement in enumerate(measurements):
                columns = plot.marker_columns([measurement])
                selected.extend(columns)
                if columns:
                    self.marker_values[index] = plot.rows[0][columns[0]].real
        else:
            selected = plot.marker_columns(measurements)
        self.readings_revision = revision
        self.readings = None
        if operating_point and len(plot.rows) == 1:
            self.readings = bias_readings(plot, operating_point)
            selected = trim_bias_vectors(plot, operating_point, selected)
        page = self.add_plot_page(plot, revision, selected or None)
        if revision and snapshot is not None and snapshot.active in snapshot.plot_layouts:
            from studies import restore_layout
            restore_layout(page, snapshot.plot_layouts[snapshot.active])
            if operating_point and selected:
                for column in selected:
                    if not any(column in sub.wave.columns for sub in page.subplots):
                        traces = page.subplots[0].traces
                        traces.item(column).setCheckState(Qt.CheckState.Checked)
        self.wave, self.run_revision = page.wave, revision
        self.refresh()
        if open_window:
            self.open_plots()
        else:
            self.plot_window.hide()

    def return_to_schematic(self):
        self.plot_window.hide()
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        self.canvas.setFocus()

    def open_plots(self):
        self.plot_window.show()
        self.plot_window.raise_()
        self.plot_window.activateWindow()

    def add_plot_page(self, plot, revision, selected=None):
        page = PlotReportPage(plot, selected)
        page.revision = revision
        page.title = plot.title
        index = self.plot_pages.addTab(page, page.title)
        self.plot_pages.setCurrentIndex(index)
        return page

    def duplicate_plot_page(self):
        from desktop04 import fresh_page
        fresh_page(self)

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
                if path.name == "result.raw" and snapshot.exists() and path.with_name("operating-point.json").exists():
                    self.show_run_result(path.parent, snapshot.read_text(encoding="utf-8"))
                    self.open_plots()
                else:
                    self.show_plot(load_plot(path), snapshot.read_text(encoding="utf-8") if snapshot.exists() else None)
            except (OSError, ValueError) as error:
                self.error(str(error))

    def export_plot_page(self,whole_page=True):
        page = self.plot_pages.currentWidget()
        if page:
            page.choose_export(whole_page)
        else:
            self.error("Open a simulation plot first.")

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
    application.setApplicationVersion(VERSION)
    application.setStyle("Fusion")
    font = application.font()
    font.setFamily("Segoe UI")
    application.setFont(font)
    application.setWindowIcon(QIcon(str(RUNTIME / "CircuitBench.ico")))
    window = Editor()
    window.show()
    if smoke_test:
        from smoke_test import verify

        sys.exit(verify(application, window, Path(sys.argv[2])))
    sys.exit(application.exec())
