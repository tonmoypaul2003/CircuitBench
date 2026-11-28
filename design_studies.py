"""Interactive design parameters, tuning, and bounded simulation studies."""
import json
import math
from pathlib import Path
import random
import subprocess

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QInputDialog, QLabel,
                             QMessageBox, QPlainTextEdit, QProgressDialog, QSlider)

from analysis_setup import set_target, targets
from circuit import Project, number, simulation_cards
from plot_math import design_scalar, resolve_design
from plotting import Waveform
from results import Plot
from studies import simulate


def fourier_report(path):
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf8",errors="replace").splitlines()
    start = next((i for i,line in enumerate(lines) if "Fourier analysis" in line),None)
    if start is None:
        return "No Fourier summary was emitted. Inspect the solver log."
    return "\n".join(lines[start:start+22]).strip()


def json_editor(editor, title, value, instructions):
    dialog = QDialog(editor)
    dialog.setWindowTitle(title)
    dialog.resize(520,370)
    layout = QFormLayout(dialog)
    help_text = QLabel(instructions)
    help_text.setWordWrap(True)
    layout.addRow(help_text)
    text = QPlainTextEdit(json.dumps(value,indent=2))
    layout.addRow(text)
    controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    controls.accepted.connect(dialog.accept)
    controls.rejected.connect(dialog.reject)
    layout.addRow(controls)
    return json.loads(text.toPlainText()) if dialog.exec() else None


def edit_variables(editor):
    value = json_editor(editor,"Design variables",editor.project.design_variables,
                        'Map each name to an expression, for example {"BASE":"1k","LOAD":"2 * BASE"}. '
                        "Use a name or expression in a passive value or controlled-source gain.")
    if value is not None:
        resolve_design(value,number)
        editor.checkpoint()
        editor.project.design_variables = value
        editor.refresh()


def edit_options(editor):
    value = json_editor(editor,"Simulation options",editor.project.simulation_options,
                        'Optional entries include "temperature", "ic", "nodeset", "reltol", "abstol" and "couplings". '
                        'For example {"temperature":"40","ic":{"out":"2"},"couplings":[["L1","L2","0.98"]]}')
    if value is not None:
        snapshot = Project.load(editor.project.dump())
        snapshot.simulation_options = value
        simulation_cards(snapshot)
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
    value = json_editor(editor,"Instance parameters",editor.project.instance_variables.get(part.ref,{}),
                        "Override only the selected instance. Available parameters: "+", ".join(supported))
    if value is not None:
        if not isinstance(value,dict) or value.keys()-supported.keys():
            raise ValueError("Enter an object with declared parameter names and expressions.")
        variables = resolve_design(editor.project.design_variables,number)
        for expression in value.values():
            design_scalar(expression,variables,number)
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


def run_trials(editor,mode):
    if editor.process is not None:
        raise ValueError("Finish the current simulation before starting a study.")
    choices = targets(editor.project)
    defaults = {"target":choices[0],"min":"1k","max":"3k","count":21,"signal":"v(out)","goal":"5"}
    if mode=="Monte Carlo":
        defaults.update(seed=0,distribution="uniform",pass_min="4.5",pass_max="5.5")
    settings = json_editor(editor,mode,defaults,
                           "Uses the active profile and the final sample. Sweep targets: "+", ".join(choices))
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


def install_design_tools(editor,edit,simulation):
    menu = simulation.addMenu("Design studies")
    for parent,title,action in ((edit,"Design variables…",edit_variables),(edit,"Instance parameters…",edit_instance),
                                (simulation,"Simulation options…",edit_options),(menu,"Live tuning…",tune),
                                (menu,"Monte Carlo and yield…",lambda e:run_trials(e,"Monte Carlo")),
                                (menu,"Optimize a parameter…",lambda e:run_trials(e,"Optimization"))):
        def invoke(action=action,title=title):
            try:
                action(editor)
            except (ValueError,TypeError,KeyError,OSError,subprocess.SubprocessError) as error:
                QMessageBox.warning(editor,title,str(error))
        editor.action(parent,title,"",invoke)
