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

from plot_math import level_crossings, linear_square_integral, sample_rms
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
    if not hasattr(page,'plot'):
        raise ValueError("Select a graph tab first.")
    return page


def sample(xs, ys, x):
    i = bisect_right(xs, x)-1
    if i < 0 or x > xs[-1]:
        return complex(math.nan)
    if xs[i] == x or i+1 == len(xs):
        return ys[i]
    return ys[i]+(ys[i+1]-ys[i])*(x-xs[i])/(xs[i+1]-xs[i])


def overlay(editor, runs, title, signal=None):
    first = runs[0][1]
    if len(first.rows)*(1+len(runs)) > 2_000_000:
        raise ValueError("Comparison exceeds the plot size limit.")
    common = [s for s in first.names if all(s in p.names for _, p in runs)]
    op = "operating point" in first.title.lower()
    if not op:
        common = [s for s in common if s != first.names[0]]
    if isinstance(signal,list):
        signals = [name for name in signal if name in common]
    else:
        signal = signal if signal in common else choose(editor,title,common)
        signals = [signal] if signal else []
    if not signals:
        return
    if len(first.rows)*(1+len(runs)*len(signals))>2_000_000:
        raise ValueError("Comparison exceeds the plot size limit.")
    xs = [r[0].real for r in first.rows]
    rows = [[x] for x in xs]
    for label, plot in runs:
        if plot.title != first.title or plot.names[0] != first.names[0]:
            raise ValueError("Choose runs of the same analysis.")
        axis = [r[0].real for r in plot.rows]
        if not op and axis != xs and any(a >= b for a,b in zip(axis, axis[1:])):
            raise ValueError("Resampling requires an increasing sweep.")
        for signal in signals:
            ys = [r[plot.names.index(signal)] for r in plot.rows]
            for i,row in enumerate(rows):
                row.append(ys[i] if op or xs == axis else sample(axis,ys,xs[i]))
    names = [first.names[0]]+[f"{signal} [{label}]" for label,_ in runs for signal in signals]
    suffix = " operating point" if op else " · AC" if first.names[0].lower()=="frequency" else ""
    units = [first.units[first.names.index(signal)] for _,_plot in runs for signal in signals]
    plot = Plot(title+suffix,names,[first.units[0]]+units,rows)
    page = editor.add_plot_page(plot,None,list(range(1,len(names))))
    editor.open_plots()
    return page


def simulate(editor, project, deck=None):
    if not editor.solver:
        raise ValueError("Choose an ngspice executable first.")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder/"circuit.cir").write_text(analysis_deck(deck or netlist(project),project.profiles[project.active]),encoding="ascii")
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
                     grid_density=w.grid_density, y_grid_density=w.y_grid_density, text_size=w.text_size,
                     text_color=w.text_color.name() if w.text_color else None,cursor_lines=w.cursor_lines,
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
        sub.grid_density.setValue(state.get("grid_density",4))
        sub.y_grid_density.setValue(state.get("y_grid_density",state.get("grid_density",4)))
        sub.guides.setCurrentText(state.get("cursor_lines","Vertical"))
        w.text_size = max(6,min(32,state.get("text_size",12)))
        w.text_color = QColor(state["text_color"]) if state.get("text_color") else None
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


def visible_segments(xs, ys, x_limits, y_limits, logarithmic):
    """Clip each plotted segment independently and retain its fractional sample index."""
    for index, (x1, x2, y1, y2) in enumerate(zip(xs, xs[1:], ys, ys[1:])):
        if not all(math.isfinite(v) for v in (x1, x2, y1, y2)) or x1 == x2:
            continue
        start, end = 0., 1.
        for before, after, (low, high) in ((x1, x2, x_limits), (y1, y2, y_limits)):
            if before == after:
                if not low <= before <= high:
                    break
                continue
            t0, t1 = sorted(((low-before)/(after-before), (high-before)/(after-before)))
            start, end = max(start, t0), min(end, t1)
        else:
            if start < end:
                points = []
                for fraction in (start, end):
                    x = x1+(x2-x1)*fraction
                    y = max(y_limits[0], min(y_limits[1], y1+(y2-y1)*fraction))
                    points.append((10**x if logarithmic else x, y, index+fraction))
                yield tuple(sorted(points))


def measurements(editor,spectrum=False):
    page = current_page(editor)
    p = page.plot
    sub = page.subplots[page.active.currentIndex()]
    if spectrum:
        trace_indices = {name:i for i,name in enumerate(p.names) if i}
    else:
        sub.wave.grab()
        if not sub.wave.screen_axes:
            raise ValueError("Open a waveform with visible traces first.")
        _,display_x,display_y,columns,xmin,xmax,ymin,ymax = sub.wave.screen_axes
        trace_indices = {p.names[i]:i for i in columns}
    name = choose(editor,"Inspect waveform",list(trace_indices))
    if not name:
        return
    c = trace_indices[name]
    axis = p.names[0 if spectrum else sub.wave.x_column].lower()
    logarithmic = not spectrum and sub.wave.logarithmic()
    if spectrum:
        if axis!="time" or p.units[c]=="expression" and any(row[c].imag for row in p.rows):
            raise ValueError("Select a real transient waveform for harmonic analysis.")
        candidates = [(row[0].real,row[c].real) for row in p.rows if math.isfinite(abs(row[c]))]
        if len(candidates)<8:
            raise ValueError("Select at least eight transient samples.")
        start,end = candidates[0][0],candidates[-1][0]
    else:
        values = display_y[columns.index(c)]
        start,end = (10**xmin,10**xmax) if logarithmic else (xmin,xmax)
    bounds = prompt(editor,"Measurement interval","Start, end ("+axis+")",f"{start!r}, {end!r}")
    if bounds is None:
        return
    lo,hi = sorted(number(v.strip()) for v in bounds.split(","))
    if spectrum:
        pairs = sorted((x,y) for x,y in candidates if lo<=x<=hi)
        if len(pairs)<8 or pairs[0][0]==pairs[-1][0]:
            raise ValueError("Select at least eight transient samples with distinct times.")
        xs,ys = map(list,zip(*pairs))
        span = xs[-1]-xs[0]
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
    lo,hi = max(lo,start),min(hi,end)
    if lo>=hi:
        raise ValueError("Choose a measurement interval overlapping the visible X axis.")
    x_limits = (math.log10(lo),math.log10(hi)) if logarithmic else (lo,hi)
    segments = list(visible_segments(display_x,values,x_limits,(ymin,ymax),logarithmic))
    if not segments:
        raise ValueError("No visible line segments in the selected interval.")
    points = [point for segment in segments for point in segment]
    minimum = min(points,key=lambda point:point[1])
    maximum = max(points,key=lambda point:point[1])
    start_point = min(points)
    end_point = max(points)
    span = sum(b[0]-a[0] for a,b in segments)
    energy = sum(linear_square_integral(b-a,u,v) for (a,u,_),(b,v,_) in segments)
    integral = sum((b-a)*(u+v)/2 for (a,u,_),(b,v,_) in segments)
    mean = integral/span
    rms = math.sqrt(energy/span)
    trace_rms = sample_rms(row[c] for row in p.rows)
    metrics = {"Minimum":minimum[1],"Maximum":maximum[1],"Mean":mean,"RMS":trace_rms,
               "Peak-to-peak":maximum[1]-minimum[1],"Absolute peak":max(abs(minimum[1]),abs(maximum[1])),
               "Standard deviation":math.sqrt(max(0,rms*rms-mean*mean)),
               "Integral":integral,"Sample count":sum(x_limits[0]<=x<=x_limits[1] and ymin<=y<=ymax
                                                      for x,y in zip(display_x,values))}
    metrics.update({"Midrange":(minimum[1]+maximum[1])/2,
                    "AC RMS":metrics["Standard deviation"],"Squared integral":energy})
    absolute_area = sum((b-a)*(abs(u)+abs(v))/2 if u*v>=0
                        else (b-a)*(u*u+v*v)/(2*(abs(u)+abs(v)))
                        for (a,u,_),(b,v,_) in segments)
    slopes = [(v-u)/(b-a) for (a,u,_),(b,v,_) in segments if b>a]
    metrics.update({"Absolute integral":absolute_area,"Mean slope":sum(v-u for (_,u,_),(_,v,_) in segments)/span})
    if slopes:
        metrics["Maximum slope"] = max(slopes)
        metrics["Minimum slope"] = min(slopes)
    metrics["Mean absolute value"] = absolute_area/span
    if absolute_area:
        metrics["Form factor"] = rms/metrics["Mean absolute value"]
    if rms:
        metrics["Crest factor"] = metrics["Absolute peak"]/rms

    def crossings(level, rising):
        for (x,u,i),(other_x,v,j) in segments:
            if u<=level<v if rising else u>=level>v:
                ratio = (level-u)/(v-u)
                location = (10**(math.log10(x)+ratio*(math.log10(other_x)-math.log10(x)))
                            if logarithmic else x+ratio*(other_x-x))
                yield location,i+ratio*(j-i)
    timing = {}
    if axis=="time":
        metrics["Rising zero crossings"] = sum(1 for _ in crossings(0,True))
        metrics["Falling zero crossings"] = sum(1 for _ in crossings(0,False))
        swing = maximum[1]-minimum[1]
        low = next(crossings(minimum[1]+.1*swing,True),None)
        high = next((point for point in crossings(minimum[1]+.9*swing,True)
                     if low is not None and point[0]>=low[0]),None)
        if high is not None:
            timing["10-90% rise (s)"] = [(low[1],"10%"),(high[1],"90%")]
            metrics["10-90% rise (s)"] = high[0]-low[0]
        high_fall = next(crossings(minimum[1]+.9*swing,False),None)
        low_fall = next((point for point in crossings(minimum[1]+.1*swing,False)
                         if high_fall is not None and point[0]>=high_fall[0]),None)
        if low_fall is not None:
            timing["90-10% fall (s)"] = [(high_fall[1],"90% falling"),(low_fall[1],"10% falling")]
            metrics["90-10% fall (s)"] = low_fall[0]-high_fall[0]
        edges = iter(crossings(metrics["Midrange"],True))
        first,second = next(edges,None),next(edges,None)
        if first and second and second[0]>first[0]:
            period = second[0]-first[0]
            falling = next((point for point in crossings(metrics["Midrange"],False)
                            if first[0]<point[0]<second[0]),None)
            markers = [(first[1],"Cycle start"),(second[1],"Next cycle")]
            for key,value in (("Period (s)",period),("Frequency (Hz)",1/period)):
                timing[key] = markers
                metrics[key] = value
            if falling:
                timing["Duty cycle (%)"] = [markers[0],(falling[1],"Falling edge"),markers[1]]
                metrics["Duty cycle (%)"] = 100*(falling[0]-first[0])/period
    elif axis=="frequency" and p.units[c]!="expression" and sub.wave.presentation!=1:
        peak = maximum[1]
        threshold = peak-20*math.log10(math.sqrt(2)) if sub.wave.presentation==2 else peak/math.sqrt(2)
        cutoff = next((point for point in crossings(threshold,False) if point[0]>=maximum[0]),None)
        if cutoff is not None:
            timing["Upper -3 dB crossing (Hz)"] = [(cutoff[1],"-3 dB")]
            metrics["Upper -3 dB crossing (Hz)"] = cutoff[0]
        lower = next((point for point in reversed(list(crossings(threshold,True)))
                      if point[0]<=maximum[0]),None)
        if lower is not None:
            timing["Lower -3 dB crossing (Hz)"] = [(lower[1],"Lower −3 dB")]
            metrics["Lower -3 dB crossing (Hz)"] = lower[0]
        if lower is not None and cutoff is not None:
            timing["-3 dB bandwidth (Hz)"] = [(lower[1],"Lower −3 dB"),(cutoff[1],"Upper −3 dB")]
            bandwidth = cutoff[0]-lower[0]
            metrics["-3 dB bandwidth (Hz)"] = bandwidth
            if lower[0]>0 and bandwidth>0:
                center = math.sqrt(lower[0]*cutoff[0])
                metrics["Geometric center (Hz)"] = center
                metrics["Loaded Q (center / bandwidth)"] = center/bandwidth
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
    if not any(check.isChecked() for check in checks.values()):
        return
    readings = [f"{key}: {value:.7g}" for key,value in metrics.items() if checks[key].isChecked()]
    interval = f"{name} [{start_point[0]:.6g}, {end_point[0]:.6g}]"
    text = interval + "  " + ", ".join(readings)
    note = ""
    peak = max((minimum,maximum),key=lambda point:abs(point[1]))
    point_metrics = (("Minimum",minimum),("Maximum",maximum),("Absolute peak",peak))
    pins = [(point[2],key,None) for key,point in point_metrics if checks[key].isChecked()]
    if checks["Peak-to-peak"].isChecked():
        pins.extend((point[2],label,None) for point,label in ((minimum,"Peak-to-peak low"),(maximum,"Peak-to-peak high")))
    for key,matches in timing.items():
        if checks[key].isChecked():
            pins.extend((index,label,None) for index,label in matches)
    anchor = min(points,key=lambda point:abs(point[0]-(lo+hi)/2))
    if checks["RMS"].isChecked():
        for (_,first,i),(_,last,j) in segments:
            crossing = next(level_crossings((i,j),(first,last),trace_rms,min(i,j),max(i,j)),None)
            if crossing is None and first==last==trace_rms:
                crossing = i
            if crossing is not None:
                pins.append((crossing,"RMS",None))
                break
        else:
            note = "RMS has no crossing in the visible interval."
    for key in ("Mean","Midrange","AC RMS"):
        if checks[key].isChecked():
            pins.append((anchor[2],key,(metrics[key],lo,hi,sub.wave.x_column,sub.wave.presentation)))
    page.message.setText(text + (" · "+note if note else ""))
    report = "\n".join([f"Waveform measurements — {page.title}",f"{interval} · X: {axis}",
                        *readings,*([note] if note else [])])
    editor.log.appendPlainText(report)
    editor.results_log.appendPlainText("\n"+report)
    if pins and QMessageBox.question(editor,"Measurement",text+"\nAdd the selected measurements to the plot?")==QMessageBox.StandardButton.Yes:
        limit,ok = QInputDialog.getInt(editor,"Measure waveform","Maximum annotations to add",len(pins),1,10000)
        if ok:
            w = sub.wave
            existing = {(column,sample,entry,w.pin_levels[row]) if row in w.pin_levels else (column,sample)
                        for row,(column,sample,entry) in enumerate(w.pins)}
            added = 0
            for index,label,level in pins:
                identity = (c,index,label,level) if level is not None else (c,index)
                if identity not in existing:
                    existing.add(identity)
                    if level is not None:
                        w.pin_levels[len(w.pins)] = level
                    w.pins.append((c,index,label))
                    w.pin_shapes.append(sub.pin_shape.currentText())
                    added += 1
                    if added>=limit:
                        break
            sub.wave.update()
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
        if hasattr(page,'plot') and page.plot.rows:
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
                    for index,(col,sample,label) in enumerate(sub.wave.pins):
                        if index in sub.wave.pin_levels:
                            value,lo,hi,_,_ = sub.wave.pin_levels[index]
                            content.append(f"<p>{html.escape(label)} = {value:.8g} [{lo:.8g}, {hi:.8g}]</p>")
                            continue
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
