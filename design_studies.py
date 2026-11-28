"""Interactive design parameters, tuning, and bounded simulation studies."""
import json
import math
from pathlib import Path
import random
import subprocess

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QInputDialog, QLabel, QLineEdit,
                             QMessageBox, QProgressDialog, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QPushButton, QHeaderView)

from analysis_setup import set_target, targets
from circuit import Project, connectivity, number, simulation_cards
from plot_math import design_scalar, resolve_design
from plotting import Waveform
from results import Plot, RUN_METRICS
from studies import simulate


def fourier_data(path):
    summary = "Fourier harmonics are unavailable for this run."
    rows = []
    active = False
    if path.is_file():
        for line in path.read_text(encoding="utf8",errors="replace").splitlines():
            if "Fourier analysis for" in line:
                active = True
                summary = line.strip().rstrip(":")
            if not active:
                continue
            if "THD:" in line:
                summary += " · " + line.split("THD:",1)[1].split(",",1)[0].strip() + " THD"
            cells = line.split()
            if len(cells)==6 and cells[0].isdigit():
                rows.append(cells)
            elif rows:
                break
    return summary,rows


def fourier_table(rows, parent):
    table = QTableWidget(len(rows),6,parent)
    table.setHorizontalHeaderLabels(("Harmonic","Frequency (Hz)","Magnitude","Phase (°)",
                                      "Relative magnitude","Relative phase (°)"))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().hide()
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    for row,cells in enumerate(rows):
        for column,value in enumerate(cells):
            item = QTableWidgetItem(value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row,column,item)
    return table


def edit_rows(editor, title, columns, rows, validate, fixed=False, choices=(), row_fields=None, new_row=None):
    dialog = QDialog(editor)
    dialog.setWindowTitle(title)
    dialog.resize(760 if row_fields else 530,360)
    form = QFormLayout(dialog)
    table = QTableWidget(0,len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    form.addRow(table)

    def append(values=()):
        row = table.rowCount()
        table.insertRow(row)
        for col in range(len(columns)):
            if col==0 and choices:
                combo = QComboBox()
                combo.addItems(choices)
                if values:
                    combo.setCurrentText(values[0])
                table.setCellWidget(row,col,combo)
                continue
            if row_fields and row_fields(table,row,col,values[col] if col<len(values) else ""):
                continue
            cell = QTableWidgetItem(values[col] if col<len(values) else "")
            if fixed and col != 1:
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row,col,cell)

    for values in rows:
        append(values)
    next_row = table.rowCount()

    def add_row():
        nonlocal next_row
        append(new_row(next_row) if new_row else ())
        next_row += 1

    if not fixed:
        add = QPushButton("Add row")
        remove = QPushButton("Remove selected row")
        add.clicked.connect(add_row)
        remove.clicked.connect(lambda: table.removeRow(table.currentRow()))
        form.addRow(add,remove)
    error_label = QLabel()
    error_label.setWordWrap(True)
    form.addRow(error_label)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    form.addRow(buttons)
    result = []

    def text_at(row,col):
        widget = table.cellWidget(row,col)
        if isinstance(widget,QComboBox):
            return widget.currentText().strip()
        if widget:
            return widget.text().strip()
        return table.item(row,col).text().strip()

    def submit():
        entries = [tuple(text_at(row,col) for col in range(len(columns))) for row in range(table.rowCount())]
        try:
            result.append(validate([row for row in entries if any(row)]))
        except ValueError as error:
            error_label.setText(str(error))
            return
        dialog.accept()

    buttons.accepted.connect(submit)
    buttons.rejected.connect(dialog.reject)
    return result[0] if dialog.exec() else None


def edit_variables(editor):
    def validate(rows):
        values = dict(rows)
        if len(values)!=len(rows):
            raise ValueError("Each variable needs a unique name.")
        resolve_design(values,number)
        return values

    value = edit_rows(editor,"Design variables",("Name","Value or expression"),
                      editor.project.design_variables.items(),validate)
    if value is not None:
        editor.checkpoint()
        editor.project.design_variables = value
        editor.refresh()


def edit_options(editor):
    labels = {"temperature":"Temperature (°C)", "reltol":"Relative tolerance", "abstol":"Absolute tolerance"}
    options = editor.project.simulation_options
    rows = [(label,"","",str(options.get(key,""))) for key,label in labels.items()]
    for key,label in (("ic","Initial voltage"),("nodeset","Node voltage estimate")):
        rows.extend((label,node,"",str(value)) for node,value in options.get(key,{}).items())
    rows.extend(("Inductor coupling",a,b,str(value)) for a,b,value in options.get("couplings",[]))

    def validate(entries):
        reverse = {label:key for key,label in labels.items()}
        values = {}
        for label,first,second,value in entries:
            if not value and label in reverse and not first and not second:
                continue
            if label in reverse and not first and not second and reverse[label] not in values:
                values[reverse[label]] = value
            elif label in ("Initial voltage","Node voltage estimate") and first and not second:
                group = values.setdefault("ic" if label=="Initial voltage" else "nodeset",{})
                if first in group:
                    raise ValueError(f"Duplicate voltage entry for {first}.")
                group[first] = value
            elif label=="Inductor coupling" and first and second:
                values.setdefault("couplings",[]).append([first,second,value])
            else:
                raise ValueError("Choose an option and enter the associated nodes or inductors.")
        snapshot = Project.load(editor.project.dump())
        snapshot.simulation_options = values
        simulation_cards(snapshot)
        return values

    nodes = sorted(set(connectivity(editor.project).values()) - {"0"})
    inductors = [part.ref for part in editor.project.parts if part.kind=="L"]

    def fields(table,row,col,value):
        option = table.cellWidget(row,0)
        if col==3:
            widget = QLineEdit(value)
            defaults = {"Temperature (°C)":"27","Relative tolerance":"0.001","Absolute tolerance":"1p",
                        "Initial voltage":"0","Node voltage estimate":"0","Inductor coupling":"0.99"}
            def update():
                widget.setPlaceholderText(defaults[option.currentText()])
            option.currentTextChanged.connect(update)
            update()
        else:
            widget = QComboBox()
            def update():
                kind = option.currentText()
                items = (inductors if kind=="Inductor coupling" else nodes
                         if col==1 and kind in ("Initial voltage","Node voltage estimate") else [])
                current = widget.currentText()
                widget.clear()
                widget.addItems(items)
                widget.setEnabled(bool(items))
                if current in items:
                    widget.setCurrentText(current)
                elif col==2 and widget.count()>1:
                    widget.setCurrentIndex(1)
            option.currentTextChanged.connect(update)
            update()
            if value:
                if widget.findText(value)<0:
                    widget.addItem(value)
                widget.setCurrentText(value)
        table.setCellWidget(row,col,widget)
        return True

    value = edit_rows(editor,"Simulation options",("Option","Node or inductor","Second inductor","Value"),
                      rows,validate,choices=("Initial voltage","Node voltage estimate","Inductor coupling",*labels.values()),
                      row_fields=fields)
    if value is not None:
        editor.checkpoint()
        editor.project.simulation_options = value
        editor.refresh()


def edit_instance(editor):
    part = editor.selected
    if part is None or part.kind!="X":
        raise ValueError("Select a subcircuit instance on the schematic.")
    model = editor.project.subcircuits[part.value]
    supported = model.get("source",{}).get("design_variables",model.get("parameters",{}))
    if not supported:
        raise ValueError("This subcircuit declares no parameters.")
    current = editor.project.instance_variables.get(part.ref,{})
    rows = [(name,current.get(name,""),str(default)) for name,default in supported.items()]
    def validate(entries):
        values = {name:value for name,value,_ in entries if value}
        variables = resolve_design(editor.project.design_variables,number)
        for expression in values.values():
            design_scalar(expression,variables,number)
        return values

    value = edit_rows(editor,f"{part.ref} instance parameters",("Parameter","Override","Default"),
                      rows,validate,fixed=True)
    if value is not None:
        editor.checkpoint()
        if value:
            editor.project.instance_variables[part.ref] = value
        else:
            editor.project.instance_variables.pop(part.ref,None)
        editor.refresh()


def trial_values(settings,mode):
    count = settings.get("count",21)
    seed = settings.get("seed",0)
    low,high = (number(str(settings[key])) for key in ("min","max"))
    if type(count) is not int or not 2<=count<=64 or not low<high:
        raise ValueError("Choose 2–64 trials and increasing bounds.")
    if mode=="Monte Carlo":
        rng = random.Random(seed)
        distribution = settings.get("distribution","uniform")
        if distribution=="normal":
            return [rng.gauss((low+high)/2,(high-low)/6) for _ in range(count)]
        if distribution!="uniform":
            raise ValueError("Choose uniform or normal distribution.")
        return [rng.uniform(low,high) for _ in range(count)]
    return [low+(high-low)*i/(count-1) for i in range(count)]


def trial_result(plot,signal):
    index = next((i for i,name in enumerate(plot.names) if name.casefold()==signal.casefold()),None)
    if index is None or not plot.rows:
        raise ValueError(f"Result vector {signal} is absent.")
    value = plot.rows[-1][index]
    scalar = abs(value) if value.imag else value.real
    if not math.isfinite(scalar):
        raise ValueError("The trial returned a nonfinite result.")
    return scalar


def trial_setup(editor, mode, defaults, choices):
    monte_carlo = mode == "Monte Carlo"
    dialog = QDialog(editor)
    dialog.setWindowTitle("Monte Carlo and yield" if monte_carlo else "Optimize a parameter")
    dialog.setMinimumWidth(410)
    form = QFormLayout(dialog)
    form.addRow("Analysis profile", QLabel(editor.project.active))
    target = QComboBox()
    for name in choices:
        label = "Temperature (°C)" if name == "@temperature" else name.lstrip("$")
        target.addItem(f"Variable · {label}" if name.startswith("$") else label, name)
    form.addRow("Parameter", target)
    fields = {}

    def input_field(key, label):
        field = QLineEdit(str(defaults[key]))
        form.addRow(label, field)
        fields[key] = field

    input_field("min", "Lower bound")
    input_field("max", "Upper bound")
    count = QSpinBox()
    count.setRange(2, 64)
    count.setValue(defaults["count"])
    form.addRow("Trials", count)
    signal = QComboBox()
    signal.setEditable(True)
    nodes = sorted(set(connectivity(editor.project).values()) - {"0"})
    signal.addItems([f"v({node})" for node in nodes])
    signal.setCurrentText(defaults["signal"] if "out" in nodes or not nodes else f"v({nodes[0]})")
    form.addRow("Measure at final sample", signal)
    hint = QLabel()
    hint.setWordWrap(True)
    hint.setVisible(monte_carlo)
    distribution = QComboBox()
    seed = QSpinBox()
    if monte_carlo:
        distribution.addItem("Uniform", "uniform")
        distribution.addItem("Normal", "normal")
        seed.setRange(0, 2147483647)
        form.addRow("Distribution", distribution)
        form.addRow(hint)
        form.addRow("Random seed", seed)
        input_field("pass_min", "Passing minimum")
        input_field("pass_max", "Passing maximum")
    else:
        input_field("goal", "Target reading")

    def update_hint():
        hint.setText("Bounds are three standard deviations from the mean."
                     if distribution.currentData() == "normal"
                     else "Every value between the bounds is equally likely.")

    def update_bounds():
        key = target.currentData()
        if key == "@temperature":
            values = "20", "80"
        elif key.startswith("$"):
            try:
                value = resolve_design(editor.project.design_variables, number)[key[1:]]
                delta = abs(value)*.1 or 1
                values = f"{value-delta:.8g}", f"{value+delta:.8g}"
            except ValueError:
                values = defaults["min"], defaults["max"]
        else:
            part = next(p for p in editor.project.parts if p.ref == key)
            try:
                value = design_scalar(part.value, resolve_design(editor.project.design_variables, number), number)
                values = f"{value*.9:.8g}", f"{value*1.1:.8g}"
            except ValueError:
                values = defaults["min"], defaults["max"]
        fields["min"].setText(values[0])
        fields["max"].setText(values[1])

    distribution.currentIndexChanged.connect(update_hint)
    target.currentIndexChanged.connect(update_bounds)
    update_hint()
    update_bounds()
    warning = QLabel()
    warning.setWordWrap(True)
    form.addRow(warning)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Run study")
    form.addRow(buttons)
    settings = {}

    def submit():
        candidate = {key: field.text().strip() for key, field in fields.items()}
        candidate.update(target=target.currentData(), count=count.value(), signal=signal.currentText().strip())
        if monte_carlo:
            candidate.update(seed=seed.value(), distribution=distribution.currentData())
        try:
            if not candidate["signal"]:
                raise ValueError("Select or enter a result signal.")
            values = trial_values(candidate, mode)
            if monte_carlo and number(candidate["pass_min"]) > number(candidate["pass_max"]):
                raise ValueError("The passing minimum must be less than or equal to the maximum.")
            if not monte_carlo:
                number(candidate["goal"])
            snapshot = Project.load(editor.project.dump())
            for value in values:
                set_target(snapshot, candidate["target"], f"{value:.15g}")
        except ValueError as error:
            warning.setText(str(error))
            return
        settings.update(candidate)
        dialog.accept()

    buttons.accepted.connect(submit)
    buttons.rejected.connect(dialog.reject)
    return settings if dialog.exec() else None


def run_trials(editor,mode):
    if editor.process is not None:
        raise ValueError("Finish the current simulation before starting a study.")
    choices = targets(editor.project)
    defaults = {"target":choices[0],"min":"1k","max":"3k","count":21,"signal":"v(out)","goal":"5"}
    if mode=="Monte Carlo":
        defaults.update(seed=0,distribution="uniform",pass_min="4.5",pass_max="5.5")
    settings = trial_setup(editor,mode,defaults,choices)
    if settings is None:
        return
    values = trial_values(settings,mode)
    if mode=="Optimization":
        number(str(settings.get("goal","0")))
    if mode=="Monte Carlo":
        lower,upper = (number(str(settings[k])) for k in ("pass_min","pass_max"))
        if lower>upper:
            raise ValueError("Yield bounds must be increasing.")
    project = Project.load(editor.project.dump())
    target,signal = settings["target"],settings["signal"]
    # Validate all proposed assignments before launching jobs.
    for value in values:
        set_target(project,target,f"{value:.15g}")
    progress = QProgressDialog(mode,"Cancel",0,len(values),editor)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    samples = []
    errors = []
    try:
        for i,value in enumerate(values):
            progress.setValue(i)
            if progress.wasCanceled():
                break
            set_target(project,target,f"{value:.15g}")
            try:
                samples.append((value,trial_result(simulate(editor,project),signal)))
            except (ValueError,OSError,subprocess.SubprocessError) as error:
                errors.append({"value":value,"error":str(error)})
    finally:
        progress.close()
    from analysis_setup import allocate_run
    from desktop04 import session_folder
    folder = allocate_run(session_folder(editor),editor.path,mode)
    (folder/"study.json").write_text(json.dumps({"mode":mode,"profile":project.active,"settings":settings,
                                               "samples":samples,"failures":errors,
                                               "cancelled":len(samples)+len(errors)<len(values)},indent=2),encoding="utf8")
    from desktop04 import refresh_history
    refresh_history(editor)
    open_study(editor,folder/"study.json")


def open_study(editor,path):
    record = json.loads(path.read_text(encoding="utf8"))
    mode,settings,samples,errors = (record[key] for key in ("mode","settings","samples","failures"))
    if not samples:
        raise ValueError(f"No valid trials. See {path}")
    target,signal = settings["target"],settings["signal"]
    data = Plot(mode,[target,signal],["",""],[list(row) for row in sorted(samples)])
    page = editor.add_plot_page(data,None,[1])
    suffix = f" {len(errors)} failures." + (" Cancelled." if record["cancelled"] else "")
    if mode=="Monte Carlo":
        lower,upper = (number(str(settings[k])) for k in ("pass_min","pass_max"))
        passed = sum(lower<=y<=upper for _,y in samples)
        attempted = len(samples)+len(errors)
        page.message.setText(f"Yield {passed}/{attempted} attempted trials ({passed/attempted:.1%})."+suffix)
    else:
        goal = number(str(settings.get("goal","0")))
        best = min(samples,key=lambda pair:abs(pair[1]-goal))
        page.message.setText(f"Best tested value {best[0]:.7g} gave {signal} = {best[1]:.7g}."+suffix)
    editor.open_plots()


def tune(editor):
    if editor.process is not None:
        raise ValueError("Finish the current simulation before tuning.")
    choices = targets(editor.project)
    target,ok = QInputDialog.getItem(editor,"Tune parameter","Target",choices,editable=False)
    if not ok:
        return
    bounds,ok = QInputDialog.getText(editor,"Tune parameter","Minimum, maximum",text="1k, 3k")
    if not ok:
        return
    low,high = (number(token.strip()) for token in bounds.split(","))
    if not low<high:
        raise ValueError("Choose increasing tuning bounds.")
    draft = Project.load(editor.project.dump())
    for value in (low,high):
        set_target(draft,target,f"{value:.15g}")
    dialog = QDialog(editor)
    dialog.setWindowTitle(f"Tune {target}")
    dialog.resize(650,420)
    form = QFormLayout(dialog)
    wave = Waveform()
    form.addRow(wave)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0,100)
    slider.setValue(50)
    form.addRow(target,slider)
    readout = QLabel()
    form.addRow(readout)
    def current_value():
        return f"{low+(high-low)*slider.value()/100:.15g}"
    def preview():
        try:
            value = current_value()
            set_target(draft,target,value)
            result = simulate(editor,draft)
            column = result.names.index("v(out)") if "v(out)" in result.names else len(result.names)-1
            wave.plot = result
            wave.columns = [column]
            wave.update()
            readout.setText(f"{target} = {value} · {result.names[column]} = {trial_result(result,result.names[column]):.7g}")
        except (ValueError,OSError,subprocess.SubprocessError) as error:
            readout.setText(str(error))
    timer = QTimer(dialog)
    timer.setSingleShot(True)
    timer.setInterval(250)
    timer.timeout.connect(preview)
    slider.valueChanged.connect(lambda _:timer.start())
    controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
    controls.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dialog.accept)
    controls.rejected.connect(dialog.reject)
    form.addRow(controls)
    timer.start(0)
    if dialog.exec():
        editor.checkpoint()
        set_target(editor.project,target,current_value())
        editor.refresh()


def edit_run_measurements(editor):
    def validate(rows):
        if len(rows)>32 or len({row[1] for row in rows}) != len(rows):
            raise ValueError("Use up to 32 measurements with unique names.")
        for metric,name,signal,start,stop in rows:
            if not name or not signal or any(len(cell)>100 for cell in (name,signal,start,stop)):
                raise ValueError("Enter a name and result vector for each measurement.")
            if metric=="Value at X" and not start:
                raise ValueError("Enter an X coordinate for Value at X.")
            if start:
                number(start)
            if stop and metric!="Value at X" and start and number(stop)<=number(start):
                raise ValueError("End X must be greater than Start X.")
            if stop:
                number(stop)
        return [[name,metric,signal,start,stop] for metric,name,signal,start,stop in rows]

    profile = editor.project.profiles[editor.project.active]
    vectors = [f"v({node})" for node in sorted(set(connectivity(editor.project).values()) - {"0"})]
    vectors.extend(f"i({part.ref.lower()})" for part in editor.project.parts if part.kind=="V")
    loaded = editor.wave.plot
    if loaded and not (profile.mode=="FOURIER" and loaded.names[0]=="frequency") and editor.run_revision == editor.project.dump(include_label_styles=False):
        vectors.extend(loaded.names[1:] if profile.mode not in ("OP","TF","SENS","PZ") else loaded.names)
    vectors = list(dict.fromkeys(vectors))
    if profile.mode in ("TRAN","FOURIER"):
        bounds = profile.start,profile.stop
    elif profile.mode=="DC":
        bounds = tuple(sorted((profile.sweep_start,profile.sweep_stop),key=number))
    elif profile.mode in ("AC","NOISE","DISTO"):
        bounds = profile.start_hz,profile.stop_hz
    else:
        bounds = "",""

    def fields(table,row,col,value):
        if col==2:
            widget = QComboBox()
            widget.addItems(vectors)
            if value and widget.findText(value)<0:
                widget.addItem(value)
            widget.setCurrentText(value or ("v(out)" if "v(out)" in vectors else widget.currentText()))
        else:
            widget = QLineEdit(value)
        table.setCellWidget(row,col,widget)
        return True

    existing = [(metric,name,signal,start,stop)
                for name,metric,signal,start,stop in editor.project.run_measurements]
    used_names = {row[1] for row in existing}
    def default_measurement(index):
        index += 1
        while f"Measurement {index}" in used_names:
            index += 1
        name = f"Measurement {index}"
        used_names.add(name)
        return "Maximum",name,"",*bounds

    values = edit_rows(editor,"Automatic waveform measurements",
                       ("Measurement","Name","Signal","Start X","End X"),
                       existing,validate,choices=RUN_METRICS,row_fields=fields,
                       new_row=default_measurement)
    if values is not None:
        editor.checkpoint()
        editor.project.run_measurements = values
        editor.refresh()


def install_design_tools(editor,edit,simulation):
    menu = simulation.addMenu("Design studies")
    for parent,title,action in ((edit,"Design variables…",edit_variables),(edit,"Instance parameters…",edit_instance),
                                (simulation,"Simulation options…",edit_options),(menu,"Automatic waveform measurements…",edit_run_measurements),(menu,"Live tuning…",tune),
                                (menu,"Monte Carlo and yield…",lambda e:run_trials(e,"Monte Carlo")),
                                (menu,"Optimize a parameter…",lambda e:run_trials(e,"Optimization"))):
        def invoke(action=action,title=title):
            try:
                action(editor)
            except (ValueError,TypeError,KeyError,OSError,subprocess.SubprocessError) as error:
                QMessageBox.warning(editor,title,str(error))
        editor.action(parent,title,"",invoke)
