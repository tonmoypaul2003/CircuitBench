"""Session history, sweep inspection and desktop presentation controls."""
from pathlib import Path
import hashlib
import tempfile
import shutil
import json
import re
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QTreeWidget,QTreeWidgetItem,
                             QPushButton,QInputDialog,QColorDialog,QMessageBox,QMenu,QAbstractItemView,QDialogButtonBox)
from PyQt6.QtGui import QShortcut,QKeySequence
from results import Plot,load_plot


def history_root(editor):
    if editor.path:
        key = hashlib.sha256(str(editor.path.resolve()).casefold().encode()).hexdigest()[:12]
        return editor.runs_root()/key
    if getattr(editor,"untitled_project",None) is not editor.project:
        editor.untitled_project = editor.project
        editor.untitled_history = None
    if editor.untitled_history is None:
        editor.runs_root().mkdir(parents=True,exist_ok=True)
        editor.untitled_history = Path(tempfile.mkdtemp(prefix="Untitled-",dir=editor.runs_root()))
    return editor.untitled_history


def session_folder(editor):
    root = history_root(editor)
    root.mkdir(parents=True,exist_ok=True)
    if getattr(editor,"session_path",None) and editor.session_path.parent == root:
        return editor.session_path
    name = editor.path.stem if editor.path else "Untitled"
    prefix = name+"_Session"
    n = max((int(p.name[len(prefix):]) for p in root.iterdir()
             if p.name.startswith(prefix) and p.name[len(prefix):].isdigit()),default=0)+1
    while True:
        path = root/f"{prefix}{n}"
        try:
            path.mkdir()
            editor.session_path = path
            return path
        except FileExistsError:
            n+=1


def load_selected(editor,item):
    path = item.data(0,Qt.ItemDataRole.UserRole)
    if path:
        try:
            editor.load_saved_run(Path(path))
            editor.open_plots()
        except (OSError,ValueError) as error:
            editor.error(str(error))


def run_title(path, profile=None):
    try:
        if profile is None:
            profile = json.loads((path/"status.json").read_text(encoding="utf8")).get("profile")
    except (OSError,ValueError):
        pass
    match = re.fullmatch(r"(.+)_(\d+)_(\d{8}-\d{6})",path.name)
    if not match:
        return path.name.replace("_"," ")
    old_label,sequence,timestamp = match.groups()
    label = profile or old_label.replace("_"," ")
    parametric = re.fullmatch(r"Parametric_([^_]+)_(.+)",label)
    if parametric:
        label = f"Sweep · {parametric[1]} = {parametric[2]}"
    try:
        when = datetime.strptime(timestamp,"%Y%m%d-%H%M%S")
    except ValueError:
        return label
    return f"{label} · Run {int(sequence)} · {when:%d %b %Y, %H:%M:%S}"


def populate(editor,tree):
    tree.clear()
    if not editor.path and not getattr(editor,"untitled_history",None):
        return
    root = history_root(editor)
    for session in sorted(root.glob("*_Session*"),key=lambda p:p.stat().st_mtime,reverse=True):
        if not session.is_dir():
            continue
        number = session.name.rsplit("_Session",1)[-1]
        parent = QTreeWidgetItem(tree,[f"Session {number} · {session.name.rsplit('_Session',1)[0]}"])
        parent.setData(0,Qt.ItemDataRole.UserRole,str(session))
        for status in sorted(session.glob("*/status.json"),key=lambda p:p.stat().st_mtime,reverse=True):
            child = QTreeWidgetItem(parent,[run_title(status.parent)])
            child.setData(0,Qt.ItemDataRole.UserRole,str(status.parent))
        parent.setExpanded(True)


def refresh_history(editor):
    for tree in editor.findChildren(QTreeWidget):
        if tree.property("sessionHistory"):
            populate(editor,tree)


def wire_history(editor,tree):
    tree.setProperty("sessionHistory",True)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.setToolTip("Ctrl-click or Shift-click to select multiple runs or sessions, then press Delete.")
    tree.itemDoubleClicked.connect(lambda item,col:load_selected(editor,item) if item.parent() else None)

    def remove():
        if editor.process is not None:
            editor.statusBar().showMessage("Wait for the simulation batch to finish before deleting data.",5000)
            return
        root = history_root(editor).resolve()
        targets = set()
        for item in tree.selectedItems():
            path = Path(item.data(0,Qt.ItemDataRole.UserRole))
            resolved = path.resolve()
            if path.is_symlink() or path.parent.is_symlink():
                continue
            if resolved.parent == root or (item.parent() and resolved.parent.parent == root):
                targets.add(resolved)
        targets = {path for path in targets if path.parent not in targets}
        if not targets:
            return
        if QMessageBox.question(tree,"Delete simulation data",
                                f"Permanently delete {len(targets)} selected run or session folders and their saved data?") != QMessageBox.StandardButton.Yes:
            return
        for path in sorted(targets):
            try:
                shutil.rmtree(path)
            except OSError as error:
                editor.error(str(error))
        editor.sweep_runs = [p for p in getattr(editor,"sweep_runs",[]) if p.is_dir()]
        if getattr(editor,"session_path",None) and not editor.session_path.is_dir():
            editor.session_path = None
        refresh_history(editor)

    shortcut = QShortcut(QKeySequence("Delete"),tree)
    shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    shortcut.activated.connect(remove)
    tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    def context(point):
        item = tree.itemAt(point)
        if item:
            if not item.isSelected():
                tree.clearSelection()
                tree.setCurrentItem(item)
            menu = QMenu(tree)
            menu.addAction("Delete selected…",remove)
            menu.exec(tree.viewport().mapToGlobal(point))
    tree.customContextMenuRequested.connect(context)


def show_history(editor):
    dialog = QDialog(editor)
    dialog.setWindowTitle("Session History")
    box = QVBoxLayout(dialog)
    tree = QTreeWidget()
    tree.setHeaderLabel("Session Tree")
    populate(editor,tree)
    wire_history(editor,tree)
    box.addWidget(tree)
    dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
    dialog.resize(680,440)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.show()


def fresh_page(editor):
    current = editor.plot_pages.currentWidget()
    if current and current.plot.rows:
        plot = Plot("New plot",current.plot.names,current.plot.units,current.plot.rows)
        editor.add_plot_page(plot,current.revision,[])
    else:
        page = editor.add_plot_page(Plot("New plot",["x"],[""],[]),None,[])
        load = QPushButton("Open waveform…")
        load.setAutoDefault(False)
        load.clicked.connect(editor.open_waveform)
        page.layout().insertWidget(1,load)


def show_sweep(editor):
    runs = editor.sweep_runs
    plots = [load_plot(run/"result.raw") for run in runs]
    first = plots[0]
    if "operating point" not in first.title.lower():
        from studies import overlay
        plan = editor.sweep_plan
        common = [n for n in first.names[1:] if all(n in plot.names for plot in plots)]
        signal = "v(out)" if "v(out)" in common else next(iter(common),None)
        page = overlay(editor,[(f"{plan['ref']}={v}",p) for v,p in zip(plan["values"],plots)],
                       "Parametric sweep",signal)
        if page:
            page.message.setText("All sweep values overlaid. Use Sweep runs for individual runs.")
        return
    signal = next((n for n in first.names[1:] if n=="v(out)"),first.names[-1])
    values = [plot.rows[-1][plot.names.index(signal)] for plot in plots]
    plan = editor.sweep_plan
    from circuit import number
    axis = [number(v) for v in plan["values"]][:len(runs)]
    page = editor.add_plot_page(Plot("Parametric sweep",[plan["ref"],signal],["",first.units[first.names.index(signal)]],
                                    [[x,y] for x,y in zip(axis,values)]),None,[1])
    page.message.setText("Final sample by component value. Use Sweep runs to open an individual waveform.")
    editor.open_plots()


def pick_trace_run(editor):
    runs = sorted(history_root(editor).glob("*/*/result.raw"),key=lambda p:p.stat().st_mtime,reverse=True)
    if not runs:
        editor.error("No saved waveform for this schematic.")
        return
    labels = [f"{p.parent.parent.name.replace('_Session',' · Session ')} / {run_title(p.parent)}" for p in runs]
    label,ok = QInputDialog.getItem(editor.plot_window,"Saved waveforms","Session and run",labels,0,False)
    if ok:
        editor.load_saved_run(runs[labels.index(label)].parent)
        editor.open_plots()


def pick_sweep(editor):
    runs = getattr(editor,"sweep_runs",[])
    if not runs:
        editor.error("Run a parametric sweep first.")
        return
    dialog = QDialog(editor.plot_window)
    dialog.setWindowTitle("Sweep runs")
    dialog.resize(680,360)
    layout = QVBoxLayout(dialog)
    table = QTreeWidget()
    table.setRootIsDecorated(False)
    table.setHeaderLabels(["Component value","Base analysis","Session"])
    layout.addWidget(table)
    plan = editor.sweep_plan
    for path in runs:
        item = QTreeWidgetItem(table,[f"{plan['ref']} = {editor.sweep_values[path]}",
                                     plan["profile"],path.parent.name.replace("_Session"," · Session ")])
        item.setData(0,Qt.ItemDataRole.UserRole,path)
    for column in range(3):
        table.resizeColumnToContents(column)
    table.setCurrentItem(table.topLevelItem(0))
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open|QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    table.itemDoubleClicked.connect(lambda item,col:dialog.accept())
    layout.addWidget(buttons)
    if dialog.exec() and table.currentItem():
        load_selected(editor,table.currentItem())


def appearance(editor):
    index = getattr(editor.canvas,"annotation_index",None)
    if index is not None and index < len(editor.project.annotations):
        key = f"text:{index}"
    elif editor.selected:
        key = editor.selected.ref
    else:
        return
    color = QColorDialog.getColor(parent=editor)
    if color.isValid():
        editor.checkpoint()
        editor.project.element_colors[key] = color.name()
        editor.refresh()


def install(editor,view,edit,simulation,controls):
    tree = QTreeWidget()
    tree.setHeaderLabel("Sessions and runs")
    wire_history(editor,tree)
    editor.session_tree = tree
    dock = editor.dock("Session Tree",tree,Qt.DockWidgetArea.LeftDockWidgetArea)
    toggle = dock.toggleViewAction()
    toggle.setText("Session History")
    view.addAction(toggle)
    dock.hide()
    def new_session():
        editor.session_path = None
        session_folder(editor)
        refresh_history(editor)
    editor.action(simulation,"New session","",new_session)
    editor.action(edit,"Selected item color…","",lambda:appearance(editor))
    button = QPushButton("Sweep runs…")
    button.setAutoDefault(False)
    button.clicked.connect(lambda:pick_sweep(editor))
    controls.addWidget(button)
    trace_run = QPushButton("Saved waveforms…")
    trace_run.setAutoDefault(False)
    trace_run.clicked.connect(lambda:pick_trace_run(editor))
    controls.addWidget(trace_run)
