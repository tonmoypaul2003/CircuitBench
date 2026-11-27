"""Session history, sweep inspection and desktop presentation controls."""
from pathlib import Path
import hashlib
import tempfile

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QTreeWidget,QTreeWidgetItem,
                             QPushButton,QInputDialog,QColorDialog)
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


def populate(editor,tree):
    tree.clear()
    if not editor.path and not getattr(editor,"untitled_history",None):
        return
    root = history_root(editor)
    for session in sorted(root.glob("*_Session*"),key=lambda p:p.stat().st_mtime,reverse=True):
        parent = QTreeWidgetItem(tree,[session.name])
        for status in sorted(session.glob("*/status.json")):
            child = QTreeWidgetItem(parent,[status.parent.name])
            child.setData(0,Qt.ItemDataRole.UserRole,str(status.parent))
        parent.setExpanded(True)


def refresh_history(editor):
    populate(editor,editor.session_tree)


def show_history(editor):
    dialog = QDialog(editor)
    dialog.setWindowTitle("Session History")
    box = QVBoxLayout(dialog)
    tree = QTreeWidget()
    tree.setHeaderLabel("Session Tree")
    populate(editor,tree)
    tree.itemDoubleClicked.connect(lambda item,col:load_selected(editor,item))
    box.addWidget(tree)
    dialog.showMaximized()
    dialog.exec()


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
    signal = next((n for n in first.names[1:] if n=="v(out)"),first.names[-1])
    values = [plot.rows[-1][plot.names.index(signal)] for plot in plots]
    plan = editor.sweep_plan
    from circuit import number
    axis = [number(v) for v in plan["values"]][:len(runs)]
    page = editor.add_plot_page(Plot("Parametric sweep",[plan["ref"],signal],["",first.units[first.names.index(signal)]],
                                    [[x,y] for x,y in zip(axis,values)]),None,[1])
    page.message.setText("Final sample by component value. Use Sweep results to open an individual waveform.")
    editor.open_plots()


def pick_sweep(editor):
    runs = getattr(editor,"sweep_runs",[])
    if not runs:
        editor.error("Run a parametric sweep first.")
        return
    label,ok = QInputDialog.getItem(editor.plot_window,"Sweep results","Run",[p.name for p in runs],editable=False)
    if ok:
        editor.load_saved_run(next(p for p in runs if p.name==label))
        editor.open_plots()


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
    tree.itemDoubleClicked.connect(lambda item,col:load_selected(editor,item))
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
    button = QPushButton("Sweep results…")
    button.setAutoDefault(False)
    button.clicked.connect(lambda:pick_sweep(editor))
    controls.addWidget(button)
