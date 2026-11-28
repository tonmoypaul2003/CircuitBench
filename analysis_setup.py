"""Desktop analysis selection, batch preparation and diagram export."""
from datetime import datetime
import math
from itertools import product
import re
from pathlib import Path
from PyQt6.QtWidgets import (QDialog,QFormLayout,QCheckBox,QPushButton,QDialogButtonBox,
                             QComboBox,QLabel,QLineEdit,QInputDialog,QFileDialog,QMessageBox)
from PyQt6.QtGui import QPdfWriter,QPainter
from PyQt6.QtCore import Qt
from circuit import Project,Profile,number,resolve_design


def allocate_run(parent,path,label):
    prefix = re.sub(r'[^\w.-]+','_',((path.stem+'_') if path else '')+label).strip('.')[:120]
    if len(str(parent))+len(prefix)>223:
        prefix = prefix[:max(8,223-len(str(parent)))]
    pattern = re.compile(re.escape(prefix)+r'_(\d+)_')
    n = max((int(m[1]) for p in parent.iterdir() if (m:=pattern.match(p.name))),default=0)+1
    while True:
        dest = parent/f"{prefix}_{n:04}_{datetime.now():%Y%m%d-%H%M%S}"
        try:
            dest.mkdir()
            return dest
        except FileExistsError:
            n+=1


ANALYSIS_NAMES = {"OP":"Operating point","DC":"DC Sweep","AC":"AC sweep","TRAN":"Transient",
                  "NOISE":"Noise","PZ":"Pole-zero","SENS":"DC Sensitivity","TF":"DC Transfer Function","FOURIER":"Fourier","DISTO":"Distortion"}
ANALYSIS_FIELDS = {
    "OP":(), "AC":("start_hz","stop_hz","points"), "TRAN":("step","start","stop"),
    "DC":("sweep_source","sweep_start","sweep_stop","sweep_step"),
    "NOISE":("sweep_source","output_node","start_hz","stop_hz","points"),
    "PZ":("input_node","output_node"), "SENS":("output_node",), "TF":("sweep_source","output_node"),
    "FOURIER":("output_node","fourier_hz","step","start","stop"),
    "DISTO":("start_hz","stop_hz","points"),
}


def targets(project):
    return [p.ref for p in project.parts if p.kind in "RCL"]+[f"${name}" for name in project.design_variables]+["@temperature"]


def set_target(project,target,value):
    numeric = number(value)
    if target.startswith("$") and target[1:] in project.design_variables:
        project.design_variables[target[1:]] = value
        resolve_design(project.design_variables,number)
    elif target=="@temperature" and numeric>-273.15:
        project.simulation_options["temperature"] = value
    else:
        part = next((p for p in project.parts if p.ref==target and p.kind in "RCL"),None)
        if part is None or numeric<=0:
            raise ValueError("Use a valid sweep target and value. Passive values must be positive.")
        part.value = value


def assignments(plan):
    first = [(plan["ref"],value) for value in plan["values"]]
    second = [(plan["second_ref"],value) for value in plan.get("second_values",[])]
    if second and plan["ref"]==plan["second_ref"]:
        raise ValueError("Choose different targets for the two sweep axes.")
    if len(first)*max(1,len(second))>64:
        raise ValueError("Choose at most 64 sweep combinations.")
    return [dict(pair) for pair in product(first,second)] if second else [dict([item]) for item in first]


def jobs(project):
    plan = project.analysis_plan
    names = plan.get("enabled",[project.active])
    result = []
    for name in names:
        if name not in project.profiles:
            raise ValueError("Update Analysis setup: missing profile "+name)
        snapshot = Project.load(project.dump())
        snapshot.active = name
        result.append((snapshot,name))
    if plan.get("parametric"):
        profile = plan["profile"]
        if profile not in project.profiles:
            raise ValueError("Choose an existing parametric base profile.")
        for index,assignment in enumerate(assignments(plan),1):
            snapshot = Project.load(project.dump())
            snapshot.active = profile
            for target,value in assignment.items():
                set_target(snapshot,target,value)
            label = "_".join(f"{name}_{value}" for name,value in assignment.items())
            result.append((snapshot,f"Parametric_{label}_{index:02}"))
    if not result:
        raise ValueError("Enable at least one analysis.")
    return result


def setup(editor):
    project = editor.project
    draft = Project.load(project.dump())
    for mode,name in ANALYSIS_NAMES.items():
        if not any(p.mode==mode for p in draft.profiles.values()):
            draft.profiles.setdefault(name,Profile(mode))
    dialog = QDialog(editor)
    dialog.setWindowTitle("Analysis Setup")
    form = QFormLayout(dialog)
    checks = {}
    def configure(name):
        profile = draft.profiles[name]
        detail = QDialog(dialog)
        detail.setWindowTitle(name)
        fields = QFormLayout(detail)
        keys = ANALYSIS_FIELDS[profile.mode]
        inputs = {}
        for key in keys:
            inputs[key] = QLineEdit(str(getattr(profile,key)))
            fields.addRow(key.replace("_"," ").capitalize(),inputs[key])
        if not keys:
            fields.addRow(QLabel("Calculate steady-state node voltages and currents."))
        buttons(detail,fields)
        if detail.exec():
            try:
                args = vars(profile).copy()
                args.update({k:int(v.text()) if k=="points" else v.text().strip() for k,v in inputs.items()})
                candidate = Profile(**args)
                candidate.directive()
                draft.profiles[name] = candidate
            except ValueError as error:
                QMessageBox.warning(dialog,name,str(error))
    for name in draft.profiles:
        check = QCheckBox()
        check.setChecked(name in project.analysis_plan.get("enabled",[project.active]))
        checks[name] = check
        edit = QPushButton(name+"…")
        edit.clicked.connect(lambda checked=False,n=name:configure(n))
        form.addRow(check,edit)
    sweep = QCheckBox("Parametric Sweep")
    plan = project.analysis_plan
    sweep.setChecked(plan.get("parametric",False))
    form.addRow(sweep)
    base = QComboBox()
    base.addItems(draft.profiles.keys())
    base.setCurrentText(plan.get("profile",project.active))
    part = QComboBox()
    part.addItems(targets(project))
    part.setCurrentText(plan.get("ref",""))
    second = QComboBox()
    second.addItems(["None",*targets(project)])
    second.setCurrentText(plan.get("second_ref","None"))
    second_values = QLineEdit(", ".join(plan.get("second_values",[])))
    values = QLineEdit(", ".join(plan.get("values",[])))
    values.setPlaceholderText("e.g. 1k, 2.2k, 4.7k")
    spacing = QComboBox()
    spacing.addItems(["Value list","Linear range","Logarithmic range"])
    spacing.setCurrentText(plan.get("spacing","Value list"))
    bounds = QLineEdit(plan.get("bounds","1k, 10k, 10"))
    for label,w in (("Base analysis",base),("Primary target",part),("Spacing",spacing),
                    ("Second target",second),("Second target values",second_values),
                    ("Explicit values",values),("Range: start, end, point count",bounds)):
        form.addRow(label,w)
        w.setEnabled(sweep.isChecked())
        sweep.toggled.connect(w.setEnabled)
    buttons(dialog,form)
    while dialog.exec():
        v = [s.strip() for s in values.text().split(",")]
        if sweep.isChecked() and spacing.currentIndex():
            try:
                start,end,count = [t.strip() for t in bounds.text().split(",")]
                start,end,count = number(start),number(end),int(count)
                if min(start,end)<=0 or not 2<=count<=64:
                    raise ValueError("Use positive endpoints and 2 to 64 points.")
                if spacing.currentIndex()==2:
                    v = [format(math.exp(math.log(start)+(math.log(end)-math.log(start))*i/(count-1)),".12g") for i in range(count)]
                else:
                    v = [format(start+(end-start)*i/(count-1),".12g") for i in range(count)]
            except (ValueError,OverflowError) as error:
                QMessageBox.warning(dialog,"Parametric Sweep",str(error))
                continue
        draft.analysis_plan = dict(spacing=spacing.currentText(),bounds=bounds.text(),enabled=[n for n,c in checks.items() if c.isChecked()],parametric=sweep.isChecked(),
                                   profile=base.currentText(),ref=part.currentText(),values=v)
        try:
            if sweep.isChecked() and len(v)>64:
                raise ValueError("Choose up to 64 sweep values.")
            if sweep.isChecked() and second.currentText()!="None":
                draft.analysis_plan.update(second_ref=second.currentText(),
                                           second_values=[item.strip() for item in second_values.text().split(",")])
            jobs(draft)
        except ValueError as error:
            QMessageBox.warning(dialog,"Analysis Setup",str(error))
            continue
        editor.checkpoint()
        project.profiles,project.analysis_plan = draft.profiles,draft.analysis_plan
        editor.refresh()
        break


def buttons(dialog,form):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    form.addRow(box)


def annotation(editor):
    text,ok = QInputDialog.getText(editor,"Text annotation","Text to place")
    if ok and text.strip():
        editor.pending_text = text.strip()
        editor.statusBar().showMessage("Click the schematic to place the annotation")


def export_canvas(editor):
    name,fmt = QFileDialog.getSaveFileName(editor,"Export schematic",filter="PNG image (*.png);;PDF diagram (*.pdf)")
    if not name:
        return
    pdf = fmt.startswith("PDF")
    path = Path(name)
    if not path.suffix:
        path = path.with_suffix(".pdf" if pdf else ".png")
    canvas = editor.canvas
    cell,offset = canvas.cell,canvas.offset
    try:
        editor.fit_view()
        image = canvas.grab()
    finally:
        canvas.cell,canvas.offset = cell,offset
        canvas.update()
    if pdf:
        writer = QPdfWriter(str(path))
        painter = QPainter(writer)
        if painter.isActive():
            size = image.size().scaled(writer.width(),writer.height(),Qt.AspectRatioMode.KeepAspectRatio)
            painter.drawPixmap(0,0,size.width(),size.height(),image)
            painter.end()
        else:
            editor.error("Could not write PDF.")
    elif not image.save(str(path),"PNG"):
        editor.error("Could not write PNG.")
