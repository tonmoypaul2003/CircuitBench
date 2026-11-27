"""Desktop simulation studies and schematic inspection tools."""
from bisect import bisect_right
import cmath
import html
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QTextDocument
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QCheckBox, QFileDialog, QInputDialog, QLabel,
                             QMessageBox, QPushButton, QProgressDialog,
                             QSlider, QVBoxLayout)

from circuit import Project, connectivity, netlist, number, reusable_subcircuit
from results import Plot, analysis_deck, load_plot
from subcircuit_designer import SourcePreview


def choose(parent, title, items):
    if not items:
        raise ValueError("No matching items are available.")
    value, ok = QInputDialog.getItem(parent, title, "Select", items, editable=False)
    return value if ok else None


def prompt(parent, title, label, default):
    value, ok = QInputDialog.getText(parent, title, label, text=default)
    return value if ok else None


def current_page(editor):
    page = editor.plot_pages.currentWidget()
    if page is None:
        raise ValueError("Open a simulation result first.")
    return page


def sample(xs, ys, x):
    i = bisect_right(xs, x)-1
    if i < 0 or x > xs[-1]:
        return complex(math.nan)
    if xs[i] == x or i+1 == len(xs):
        return ys[i]
    return ys[i]+(ys[i+1]-ys[i])*(x-xs[i])/(xs[i+1]-xs[i])


def overlay(editor, runs, title):
    first = runs[0][1]
    if len(first.rows)*(1+len(runs)) > 2_000_000:
        raise ValueError("Comparison exceeds the plot size limit.")
    common = [s for s in first.names if all(s in p.names for _, p in runs)]
    op = "operating point" in first.title.lower()
    if not op:
        common = [s for s in common if s != first.names[0]]
    signal = choose(editor, title, common)
    if not signal:
        return
    xs = [r[0].real for r in first.rows]
    rows = [[x] for x in xs]
    for label, plot in runs:
        if plot.title != first.title or plot.names[0] != first.names[0]:
            raise ValueError("Choose runs of the same analysis.")
        ys = [r[plot.names.index(signal)] for r in plot.rows]
        axis = [r[0].real for r in plot.rows]
        if not op and axis != xs and any(a >= b for a,b in zip(axis, axis[1:])):
            raise ValueError("Resampling requires an increasing sweep.")
        for i,row in enumerate(rows):
            row.append(ys[i] if op or xs == axis else sample(axis,ys,xs[i]))
    names = [first.names[0]]+[f"{signal} [{label}]" for label,_ in runs]
    plot = Plot(title + (" operating point" if op else ""), names,
                [first.units[0]]+[first.units[first.names.index(signal)]]*len(runs), rows)
    page = editor.add_plot_page(plot,None,list(range(1,len(names))))
    editor.open_plots()
    return page


def simulate(editor, project, deck=None):
    if not editor.solver:
        raise ValueError("Choose an ngspice executable first.")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder/"circuit.cir").write_text(analysis_deck(deck or netlist(project)),encoding="ascii")
        subprocess.run([editor.solver,"-n","-b","circuit.cir"], cwd=tmp, capture_output=True,
                       timeout=20, check=True, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if not (folder/"result.raw").exists():
            raise ValueError("ngspice produced no waveform for this component value.")
        return load_plot(folder/"result.raw")


def compare(editor):
    from desktop04 import history_root
    folders = {p.parent.name:p.parent for p in history_root(editor).glob("*/*/result.raw")}
    a = choose(editor,"First run",sorted(folders))
    if not a:
        return
    b = choose(editor,"Second run",sorted(set(folders)-{a}))
    if not b:
        return
    runs = [folders[a],folders[b]]
    projects = [Project.load((p/"project.cwb").read_text(encoding="utf8")) for p in runs]
    values = [{p.ref:p.value for p in pr.parts} for pr in projects]
    changes = [f"{k}: {values[0].get(k)} -> {values[1].get(k)}" for k in sorted(values[0].keys()|values[1].keys()) if values[0].get(k)!=values[1].get(k)]
    if projects[0].profiles[projects[0].active]!=projects[1].profiles[projects[1].active]:
        changes.append("Profiles: "+" -> ".join(str(p.profiles[p.active]) for p in projects))
    page = overlay(editor,[(p.name,load_plot(p/"result.raw")) for p in runs],"Run comparison")
    if page:
        page.message.setText("; ".join(changes) or "Same component values and analysis settings.")


def inspect_project(editor, project, breadcrumb):
    dlg = QDialog(editor)
    dlg.setWindowTitle(breadcrumb)
    dlg.resize(680,480)
    layout = QVBoxLayout(dlg)
    layout.addWidget(SourcePreview(project,connectivity(project)))
    children = {p.ref:p for p in project.parts if "source" in project.subcircuits.get(p.value,{})}
    for ref,part in children.items():
        button = QPushButton("Inspect "+ref+" ("+part.value+")")
        button.clicked.connect(lambda checked=False,p=part: inspect_project(dlg,Project.load(json.dumps(project.subcircuits[p.value]["source"])),breadcrumb+" / "+p.ref))
        layout.addWidget(button)
    dlg.exec()


def inspect_selected(editor):
    if len(editor.selected_parts)!=1:
        return False
    p = editor.project.parts[next(iter(editor.selected_parts))]
    model = editor.project.subcircuits.get(p.value,{})
    if p.kind!="X" or "source" not in model:
        return False
    inspect_project(editor,Project.load(json.dumps(model["source"])),"Main / "+p.ref+" ("+p.value+")")
    return True


def archive_part(editor):
    name,_ = QFileDialog.getOpenFileName(editor,"Add reusable part",filter="CircuitBench (*.cwb)")
    if not name:
        return
    directory = QFileDialog.getExistingDirectory(editor,"Part library folder")
    if not directory:
        return
    source = Path(name).read_text(encoding="utf-8-sig")
    part,_ = reusable_subcircuit(Project.load(source))
    root = Path(directory)
    revision = 1
    while True:
        target = root/f"{part}-r{revision}.cwb"
        try:
            with target.open("x",encoding="utf8") as handle:
                handle.write(source)
            break
        except FileExistsError:
            revision += 1
    QMessageBox.information(editor,"Part library","Saved "+target.name)


def library(editor):
    directory = QFileDialog.getExistingDirectory(editor,"Browse reusable part library")
    if not directory:
        return
    models = {}
    for path in Path(directory).glob("*.cwb"):
        try:
            project = Project.load(path.read_text(encoding="utf-8-sig"))
            name,model = reusable_subcircuit(project)
            models[f"{name} ({', '.join(model['ports'])}) - {path.name}"] = (path,project)
        except (ValueError,OSError,KeyError,TypeError):
            continue
    query = prompt(editor,"Part library","Filter by name, file or port","")
    if query is None:
        return
    key = choose(editor,"Part library",[k for k in models if query.casefold() in k.casefold()])
    if key:
        from subcircuit_designer import SymbolPreview
        path,project = models[key]
        dlg = QDialog(editor)
        dlg.setWindowTitle(key)
        box = QVBoxLayout(dlg)
        dlg.rows = lambda: project.subcircuit_design["ports"]
        dlg.name = QLabel(project.subcircuit_design["name"])
        preview = SymbolPreview(dlg)
        preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        box.addWidget(preview)
        for port in dlg.rows():
            box.addWidget(QLabel(f"{port['name']}   {port['side']}   internal net {port['net']}   {port.get('description', '')}"))
        button = QPushButton("Import this revision")
        button.clicked.connect(dlg.accept)
        box.addWidget(button)
        if dlg.exec():
            editor.show_library_import([path])


def diagnostics(editor):
    project = editor.project
    nodes = connectivity(project)
    issues = {}
    groups = {}
    for i,p in enumerate(project.parts):
        for j,pin in enumerate(p.pins()):
            groups.setdefault(nodes[pin],[]).append((i,j))
    for net, terminals in groups.items():
        if len(terminals)==1:
            i,j = terminals[0]
            issues[f"{project.parts[i].ref} pin {j+1} has no other terminal on {net}"] = {i}
    if "0" not in groups:
        issues["Circuit has no ground reference"] = set()
    try:
        netlist(project)
    except ValueError as exc:
        issues[str(exc)] = {i for i,p in enumerate(project.parts) if p.ref in re.findall(r"[A-Za-z]+[0-9]+",str(exc))}
    if not issues:
        QMessageBox.information(editor,"Diagnostics","No connection issues found.")
        return
    key = choose(editor,"Schematic diagnostics",list(issues))
    if key:
        editor.set_selection(issues[key],set(),set())
        if issues[key]:
            from PyQt6.QtCore import QPointF
            p = project.parts[min(issues[key])]
            editor.canvas.offset = -QPointF(p.x,p.y)*editor.canvas.cell
            editor.canvas.update()

LAYOUT_FIELDS = ("title","x_label","y_label","x_scale","tick_count","tick_precision","x_limits","y_limits")


def save_layout(editor):
    page = current_page(editor)
    states = []
    for sub in page.subplots:
        w = sub.wave
        state = {k:getattr(w,k) for k in LAYOUT_FIELDS}
        state.update(columns=[page.plot.names[c] for c in w.columns], x=page.plot.names[w.x_column],
                     styles={page.plot.names[c]:v for c,v in w.trace_styles.items()},
                     colors={page.plot.names[c]:v.name() for c,v in w.trace_colors.items()},
                     background=w.background.name() if w.background else None,
                     grid_color=w.grid_color.name() if w.grid_color else None,grid=w.grid_visible,
                     cursor=w.cursor_enabled,presentation=w.presentation,zoom=w.zoom,origin=w.zoom_origin)
        states.append(state)
    editor.checkpoint()
    editor.project.plot_layouts[editor.project.active] = dict(states=states,tiles=page.tiles.currentIndex(),
                                                              expressions=page.expressions)
    editor.refresh()


def restore_layout(page,layout):
    page.tiles.setCurrentIndex(layout["tiles"])
    for expression in layout.get("expressions",[]):
        try:
            page.add_expression(expression)
        except ValueError:
            continue
    for i,state in enumerate(layout["states"][:12]):
        sub = page.subplots[0] if i==0 else page.add_subplot()
        w = sub.wave
        for j in range(sub.traces.count()):
            item = sub.traces.item(j)
            item.setCheckState(Qt.CheckState.Checked if item.text() in state["columns"] else Qt.CheckState.Unchecked)
        sub.x_axis.setCurrentText(state["x"])
        sub.presentation.setCurrentIndex(state["presentation"])
        for k in LAYOUT_FIELDS:
            setattr(w,k,state[k])
        w.trace_styles = {page.plot.names.index(n):v for n,v in state["styles"].items() if n in page.plot.names}
        w.trace_colors = {page.plot.names.index(n):QColor(v) for n,v in state["colors"].items() if n in page.plot.names}
        for k in ("background","grid_color"):
            setattr(w,k,QColor(state[k]) if state[k] else None)
        sub.cursor_button.setChecked(state["cursor"])
        sub.grid_button.setChecked(state["grid"])
        sub.zoom_slider.setValue(state["zoom"])
        w.zoom_origin = state["origin"]
        w.update()


def fft(values):
    if len(values)==1:
        return values
    even,odd = fft(values[::2]),fft(values[1::2])
    terms = [cmath.exp(-2j*math.pi*k/len(values))*v for k,v in enumerate(odd)]
    return [a+b for a,b in zip(even,terms)]+[a-b for a,b in zip(even,terms)]


def measurements(editor,spectrum=False):
    page = current_page(editor)
    p = page.plot
    name = choose(editor,"Inspect waveform",p.names[1:])
    if not name:
        return
    c = p.names.index(name)
    axis = p.names[0].lower()
    if spectrum and p.units[c] == "expression" and any(row[c].imag for row in p.rows):
        raise ValueError("Select a real transient waveform for harmonic analysis.")
    bounds = prompt(editor,"Measurement interval","Start, end ("+axis+")",f"{p.rows[0][0].real!r}, {p.rows[-1][0].real!r}")
    if bounds is None:
        return
    lo,hi = sorted(number(v.strip()) for v in bounds.split(","))
    pairs = sorted((row[0].real,abs(row[c]) if axis=="frequency" and p.units[c]!="expression" else row[c].real) for row in p.rows if lo<=row[0].real<=hi and math.isfinite(abs(row[c])))
    if len(pairs)<2 or pairs[0][0]==pairs[-1][0]:
        raise ValueError("Select a range with at least two distinct samples.")
    xs,ys = map(list,zip(*pairs))
    span = xs[-1]-xs[0]
    if spectrum:
        if axis!="time" or len(xs)<8:
            raise ValueError("Select at least eight transient samples.")
        n = 2**min(15,int(math.log2(len(xs))))
        uniform = [sample(xs,ys,xs[0]+i*span/n) for i in range(n)]
        mean = sum(uniform)/n
        bins = fft([(v-mean)*(.5-.5*math.cos(2*math.pi*i/n)) for i,v in enumerate(uniform)])
        amps = [4*abs(v)/n for v in bins[:n//2]]
        peak = max(range(1,len(amps)),key=amps.__getitem__)
        plot = Plot("Hann spectrum",["frequency",name],["frequency",p.units[c]],[[k/span,v] for k,v in enumerate(amps)])
        result = editor.add_plot_page(plot,None,[1])
        result.message.setText(f"Largest non-DC bin: {peak/span:.7g} Hz; amplitude {amps[peak]:.7g}; bin spacing {1/span:.7g} Hz")
        harmonics = range(2,min(11,(len(amps)-2)//peak+1))
        if peak >= 4 and amps[peak] and len(harmonics):
            fundamental = sum(v*v for v in amps[peak-1:peak+2])
            harmonic = sum(sum(v*v for v in amps[k*peak-1:k*peak+2]) for k in harmonics)
            result.message.setText(result.message.text()+f"; estimated harmonic distortion through harmonic {harmonics.stop-1}: {100*math.sqrt(harmonic/fundamental):.4g}% (bin-centered fundamental assumed)")
        editor.open_plots()
        return
    rms = math.sqrt(sum((b-a)*(u*u+u*v+v*v)/3 for a,b,u,v in zip(xs,xs[1:],ys,ys[1:]))/span)
    mean = sum((b-a)*(u+v)/2 for a,b,u,v in zip(xs,xs[1:],ys,ys[1:]))/span
    metrics = {"Minimum":min(ys),"Maximum":max(ys),"Mean":mean,"RMS":rms,
               "Peak-to-peak":max(ys)-min(ys),"Absolute peak":max(map(abs,ys)),
               "Standard deviation":math.sqrt(max(0,rms*rms-mean*mean)),
               "Integral":mean*span,"Sample count":len(xs)}
    def crossing(level,start,rising):
        for k in range(start,len(xs)-1):
            a,b = ys[k:k+2]
            if (a<=level<b if rising else a>=level>b):
                return xs[k]+(xs[k+1]-xs[k])*(level-a)/(b-a)
    if axis=="time":
        low = min(ys)
        swing = max(ys)-low
        a = crossing(low+.1*swing,0,True)
        b = crossing(low+.9*swing,max(0,bisect_right(xs,a)-1),True) if a is not None else None
        if b is not None:
            metrics["10-90% rise (s)"] = b-a
    elif axis=="frequency":
        cutoff = crossing(max(ys)/math.sqrt(2),ys.index(max(ys)),False) if p.units[c]!="expression" else None
        if cutoff is not None:
            metrics["Upper -3 dB crossing (Hz)"] = cutoff
    dialog = QDialog(editor)
    dialog.setWindowTitle("Choose waveform measurements")
    layout = QVBoxLayout(dialog)
    checks = {}
    for key in metrics:
        checks[key] = QCheckBox(key)
        checks[key].setChecked(key in ("Minimum","Maximum","Mean","RMS"))
        layout.addWidget(checks[key])
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if not dialog.exec():
        return
    text = f"{name} [{xs[0]:.6g}, {xs[-1]:.6g}]  " + ", ".join(
        f"{key}: {value:.7g}" for key,value in metrics.items() if checks[key].isChecked())
    page.message.setText(text)
    sub = page.subplots[page.active.currentIndex()]
    points = [(xs[0],"Start"), (xs[-1],"End"), (xs[ys.index(min(ys))],"Minimum"), (xs[ys.index(max(ys))],"Maximum")]
    if axis=="time" and b is not None:
        points.extend([(a,"10%"),(b,"90%")])
    if axis=="frequency" and cutoff is not None:
        points.append((cutoff,"-3 dB"))
    if QMessageBox.question(editor,"Measurement",text+"\nMark contributing samples on the plot?")==QMessageBox.StandardButton.Yes:
        sub.x_axis.setCurrentIndex(0)
        for j in range(sub.traces.count()):
            item = sub.traces.item(j)
            if item.data(Qt.ItemDataRole.UserRole)==c:
                item.setCheckState(Qt.CheckState.Checked)
        for x,label in points:
            sub.pin_at(c,x,label)
    editor.open_plots()


def report_selection(editor):
    dialog = QDialog(editor)
    dialog.setWindowTitle("Simulation report contents")
    box = QVBoxLayout(dialog)
    choices = {}
    for label in ("Schematic and settings","Data points","Operating point","Select all plots"):
        choices[label] = QCheckBox(label)
        box.addWidget(choices[label])
    choices["Schematic and settings"].setChecked(True)
    choices["Operating point"].setEnabled(bool(getattr(editor,"report_bias",None)))
    pages = {}
    for i in range(editor.plot_pages.count()):
        page = editor.plot_pages.widget(i)
        if page.plot.rows:
            check = QCheckBox(page.title)
            box.addWidget(check)
            check.setChecked(True)
            pages[page] = check
    choices["Select all plots"].setChecked(True)
    choices["Select all plots"].toggled.connect(lambda enabled: [c.setChecked(enabled) for c in pages.values()])
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    box.addWidget(buttons)
    if dialog.exec():
        return {k for k,c in choices.items() if c.isChecked()},[p for p,c in pages.items() if c.isChecked()]


def report(editor):
    selection = report_selection(editor)
    if selection is None:
        return
    sections,pages = selection
    current = editor.plot_pages.currentWidget()
    project = Project.load(current.revision) if current and current.revision else editor.project
    name,_ = QFileDialog.getSaveFileName(editor,"Simulation report",filter="PDF (*.pdf)")
    if not name:
        return
    from PyQt6.QtGui import QPageSize
    from PyQt6.QtPrintSupport import QPrinter
    document = QTextDocument()
    content = ["<h1>CircuitBench simulation report</h1>"]
    if "Schematic and settings" in sections:
        preview = SourcePreview(project,connectivity(project))
        preview.resize(780,450)
        document.addResource(QTextDocument.ResourceType.ImageResource,QUrl("schematic.png"),preview.grab().toImage())
        content.extend(["<h2>Schematic and settings</h2>",'<p><img src="schematic.png" width="640"></p>',
                        "<p>"+html.escape(project.profiles[project.active].directive())+"</p>"])
    if "Operating point" in sections:
        content.append("<h2>Operating point</h2>")
        content.extend("<p>"+html.escape(line)+"</p>" for line in editor.report_bias)
    with tempfile.TemporaryDirectory() as tmp:
        from PyQt6.QtGui import QImage
        for i,page in enumerate(pages):
            content.append("<h2>"+html.escape(page.title)+"</h2>")
            target = Path(tmp)/f"plot{i}.png"
            page.export(target,True)
            url = target.name
            document.addResource(QTextDocument.ResourceType.ImageResource,QUrl(url),QImage(str(target)))
            content.append(f'<p><img src="{url}" width="640"></p>')
            if "Data points" in sections:
                content.append("<p>"+html.escape(page.message.text())+"</p>")
                for sub in page.subplots:
                    for col,sample,label in sub.wave.pins:
                        j = int(sample)
                        frac = sample-j
                        row = page.plot.rows[j]
                        x = row[sub.wave.x_column].real
                        y = row[col].real
                        if frac:
                            next_row = page.plot.rows[j+1]
                            x += frac*(next_row[sub.wave.x_column].real-x)
                            y += frac*(next_row[col].real-y)
                        content.append(f"<p>{html.escape(label or page.plot.names[col])} ({x:.8g}, {y:.8g})</p>")
    document.setHtml("".join(content))
    printer = QPrinter()
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(Path(name).with_suffix(".pdf")))
    document.print(printer)


def install_studies(editor, simulation, draw, imports):
    tools = simulation.addMenu("Simulation studies")
    editor.studies_menu = tools
    def add(menu,title,callback):
        def invoke():
            try:
                callback(editor)
            except (ValueError,KeyError,OSError,subprocess.SubprocessError) as exc:
                editor.error(str(exc))
                QMessageBox.warning(editor,title,str(exc))
        editor.action(menu,title,"",invoke)
    for title,callback in (("Save layout for active profile",save_layout),("Measure waveform",measurements),
                           ("Transient component power",transient_power),("Frequency spectrum",lambda e: measurements(e,True)),
                           ("Compare saved runs",compare),("Export simulation report",report)):
        add(tools,title,callback)
    power = tools.addAction("Show operating-point power")
    power.setCheckable(True)
    def toggle(checked):
        editor.power_visible = checked
        editor.canvas.update()
    power.toggled.connect(toggle)
    add(draw,"Add part revision to library",archive_part)
    add(imports,"Browse reusable part library",library)
    add(draw,"Schematic diagnostics",diagnostics)

def transient_power(editor):
    from results import bias_deck
    project = editor.project
    if project.profiles[project.active].mode != "TRAN":
        raise ValueError("Choose a transient profile first.")
    parts = {p.ref:i for i,p in enumerate(project.parts) if p.kind not in ("G","N")}
    ref = choose(editor,"Transient power",list(parts))
    if not ref:
        return
    index = parts[ref]
    deck,meta = bias_deck(project)
    plot = simulate(editor,project,deck)
    columns = {n.lower():i for i,n in enumerate(plot.names)}
    nets = connectivity(project)
    pins = project.parts[index].pins()
    terminals = [(entry["pin"],columns[entry["vector"]]) for entry in meta["currents"] if entry["part"]==index]
    if not terminals or (len(pins)>2 and len(terminals)!=len(pins)):
        raise ValueError("Power requires independently measurable terminal currents.")
    voltages = [None if nets[p]=="0" else columns[f"v({nets[p]})"] for p in pins]
    rows = []
    for row in plot.rows:
        v = [row[c].real if c is not None else 0 for c in voltages]
        watts = sum(row[c].real*(v[i]-v[-1]) for i,c in terminals)
        rows.append([row[0],watts])
    page = editor.add_plot_page(Plot("Transient power",["time",ref+" power"],["time","W"],rows),project.dump(),[1])
    editor.open_plots()
    bounds = prompt(editor,"Power interval","Start, end (seconds)",f"{rows[0][0].real}, {rows[-1][0].real}")
    if bounds is None:
        return
    lo,hi = sorted(number(v.strip()) for v in bounds.split(","))
    points = [(r[0].real,r[1]) for r in rows if lo<=r[0].real<=hi]
    if len(points)<2 or points[-1][0]<=points[0][0]:
        raise ValueError("Select at least two transient samples.")
    mean = sum((b-a)*(u+v)/2 for (a,u),(b,v) in zip(points,points[1:]))/(points[-1][0]-points[0][0])
    page.message.setText(f"{ref}: average {mean:.7g} W, peak {max(v for _,v in points):.7g} W")
