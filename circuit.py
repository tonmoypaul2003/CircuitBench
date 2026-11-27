"""Versioned schematic, connectivity compiler and SPICE export."""
from collections import Counter
from hashlib import sha256
from dataclasses import asdict, dataclass, field
from dataclasses import replace
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from qucs_library import PARAMETERS
from spice_library import MODEL_PORTS, model_statement, validate_subcircuit


SYMBOLS = {
    "R": ("Resistor", "1k"), "C": ("Capacitor", "1u"), "L": ("Inductor", "1m"),
    "V": ("Voltage source", "DC 5"), "I": ("Current source", "DC 1m"),
    "D": ("Diode", "DDEFAULT"), "G": ("Ground", ""), "N": ("Net label", "out"),
    "X": ("SPICE subcircuit", ""),
    "Q": ("Bipolar transistor", ""), "M": ("MOSFET", ""), "J": ("JFET", ""),
    "S": ("Voltage-controlled switch", ""),
}
GENERIC_MODELS = {
    "GENERIC_NPN": ("NPN", {"IS": "1e-14", "BF": "100"}),
    "GENERIC_PNP": ("PNP", {"IS": "1e-14", "BF": "100"}),
    "GENERIC_NMOS": ("NMOS", {"LEVEL": "1", "VTO": "1", "KP": "1m"}),
    "GENERIC_PMOS": ("PMOS", {"LEVEL": "1", "VTO": "-1", "KP": "1m"}),
    "GENERIC_NJFET": ("NJF", {"VTO": "-2", "BETA": "1m"}),
    "GENERIC_PJFET": ("PJF", {"VTO": "2", "BETA": "1m"}),
    "GENERIC_SWITCH": ("SW", {"VT": "1", "VH": "0.1", "RON": "1", "ROFF": "1g"}),
}
MODELED_SYMBOLS = frozenset("QMJS")
TRANSISTOR_PINS = ((1, -2), (-2, 0), (1, 2))
SWITCH_PINS = ((-2, -1), (2, -1), (-1, 2), (1, 2))
SUFFIXES = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
            "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15, "": 1}
WAVE_FIELDS = {
    "SIN": ("Offset", "Amplitude", "Frequency (Hz)", "Delay (s)", "Damping (1/s)", "Phase (°)"),
    "PULSE": ("Low", "High", "Delay (s)", "Rise (s)", "Fall (s)", "Width (s)", "Period (s)"),
    "EXP": ("Initial", "Pulsed", "Rise delay (s)", "Rise time constant (s)",
            "Fall delay (s)", "Fall time constant (s)"),
    "PWL": ("Time/value pairs",),
}
SOURCE_DEFAULTS = {"DC": "DC 5", "AC": "DC 0 AC 1 0", "SIN": "SIN 0 1 1k 0 0 0",
                   "PULSE": "PULSE 0 1 0 1u 1u 500u 1m",
                   "EXP": "EXP 0 1 0 100u 1m 100u", "PWL": "PWL 0 0 1m 1 2m 0"}


def validate_plot_layouts(layouts):
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)

    def pair(value):
        return isinstance(value, (tuple, list)) and len(value) == 2 and all(map(finite, value))

    def strings(value):
        return isinstance(value, list) and all(isinstance(v, str) for v in value)

    def color(value):
        return isinstance(value, str) and re.fullmatch(r"#[a-fA-F0-9]{6}", value)

    if not isinstance(layouts, dict):
        raise ValueError("Invalid saved plot layouts")
    for layout in layouts.values():
        if (not isinstance(layout, dict) or layout.get("tiles") not in (0, 1)
                or not strings(layout.get("expressions", [])) or not isinstance(layout.get("states"), list)
                or not 1 <= len(layout["states"]) <= 12):
            raise ValueError("Invalid saved plot page")
        for w in layout["states"]:
            if (not isinstance(w, dict) or not strings(w.get("columns"))
                    or not all(isinstance(w.get(k), str) for k in ("title","x","x_label","y_label"))
                    or w.get("x_scale") not in ("Automatic","Logarithmic","Linear")
                    or not all(type(w.get(k)) is bool for k in ("cursor","grid"))
                    or not all(type(w.get(k)) is int for k in ("presentation","tick_count","tick_precision","zoom"))
                    or not 2 <= w["tick_count"] <= 25 or not 0 <= w["tick_precision"] <= 16
                    or w["presentation"] not in (0,1,2) or not 25 <= w["zoom"] <= 800
                    or not pair(w.get("origin"))
                    or any(k not in w or (w[k] is not None and not color(w[k])) for k in ("grid_color","background"))
                    or any(k not in w or (w[k] is not None and not (pair(w[k]) and w[k][0]<w[k][1]))
                           for k in ("x_limits","y_limits"))
                    or not isinstance(w.get("colors"), dict) or not all(map(color,w["colors"].values()))
                    or not isinstance(w.get("styles"), dict)
                    or any(not isinstance(v,(tuple,list)) or len(v)!=2 or v[0] not in ("Solid","Dashed","Dotted","Dash-dot")
                           or not finite(v[1]) or not 0<v[1]<=30 for v in w["styles"].values())):
                raise ValueError("Invalid saved subplot settings")


def number(token):
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(meg|[tgkmunpf])?",
                         token, re.I | re.ASCII)
    if not match:
        raise ValueError(f"Invalid SPICE number {token!r}")
    value = float(match[1]) * SUFFIXES[(match[2] or "").lower()]
    if not math.isfinite(value):
        raise ValueError(f"Nonfinite SPICE number {token!r}")
    return value


PORT_SIDES = ("Left", "Right", "Top", "Bottom")


def symbol_geometry(sides):
    counts = Counter(sides)
    width = max(2, counts["Top"], counts["Bottom"])
    height = max(2, counts["Left"], counts["Right"])
    used = Counter()
    offsets = []
    for side in sides:
        offset = 2*used[side]-counts[side]+1
        used[side] += 1
        offsets.append({"Left": (-width-1, offset), "Right": (width+1, offset),
                        "Top": (offset, -height-1), "Bottom": (offset, height+1)}[side])
    return width, height, offsets



def valid_label_style(style):
    if not isinstance(style,dict):
        return False
    offsets = [style[k] for k in ("offset","name_offset","value_offset") if k in style]
    return (type(style.get("size",12)) is int and 6<=style.get("size",12)<=48
            and all(isinstance(offset,list) and len(offset)==2
                    and all(type(v) in (int,float) and math.isfinite(v) for v in offset)
                    for offset in offsets)
            and all(k not in style or isinstance(style[k],str) and re.fullmatch(r"#[0-9a-fA-F]{6}",style[k])
                    for k in ("color","point_color","symbol_color"))
            and all(k not in style or type(style[k]) is bool for k in ("name","value","fill")))

@dataclass
class Part:
    ref: str
    kind: str
    x: int
    y: int
    value: str
    rotation: int = 0
    port_names: list[str] = field(default_factory=list)
    legacy_pin_layout: bool = False
    opamp_symbol: bool = False
    port_sides: list[str] = field(default_factory=list)
    label_style: dict = field(default_factory=dict)

    def is_op07(self):
        return self.kind == "X" and self.value == "OP07" and self.port_names == ["3", "2", "7", "4", "6"]

    def pins(self):
        if self.port_sides:
            offsets = symbol_geometry(self.port_sides)[2]
        elif self.opamp_symbol:
            offsets = ((-3, -1), (-3, 1), (0, -3), (0, 3), (3, 0))
        elif self.kind == "X" or self.legacy_pin_layout:
            offsets = self.box_offsets()
        elif self.kind in MODELED_SYMBOLS:
            offsets = self.device_offsets()
        else:
            offsets = ((0, 0),) if self.kind in "GN" else ((-2, 0), (2, 0))
        result = []
        for x, y in offsets:
            for _ in range(self.rotation % 4):
                x, y = -y, x
            result.append((self.x + x, self.y + y))
        return result

    def device_offsets(self):
        if self.kind == "S":
            return SWITCH_PINS
        if self.kind == "M" and len(self.port_names) == 4:
            return (*TRANSISTOR_PINS, (3, 0))
        return TRANSISTOR_PINS

    def box_offsets(self):
        split = (len(self.port_names)+1)//2
        return [(-3, 2*i-split+1) for i in range(split)] + [
            (3, 2*i-(len(self.port_names)-split)+1) for i in range(len(self.port_names)-split)]


@dataclass
class Profile:
    mode: str = "OP"
    step: str = "10u"
    stop: str = "5m"
    start: str = "0"
    start_hz: str = "10"
    stop_hz: str = "100k"
    points: int = 50
    sweep_source: str = "V1"
    sweep_start: str = "0"
    sweep_stop: str = "10"
    sweep_step: str = "1"

    def directive(self):
        if self.mode == "OP":
            return ".op"
        if self.mode == "DC":
            source = self.sweep_source.upper()
            if not re.fullmatch(r"[VI][A-Z0-9_]+", source, re.ASCII):
                raise ValueError("DC sweep needs a voltage or current source reference")
            start, stop, step = map(number, (self.sweep_start, self.sweep_stop, self.sweep_step))
            if not step or not 0 < (stop-start)/step <= 100000:
                raise ValueError("DC sweep step must reach the stop value within 100000 steps")
            return f".dc {source} {self.sweep_start} {self.sweep_stop} {self.sweep_step}"
        if self.mode == "TRAN":
            if not 0 < number(self.step) <= number(self.stop):
                raise ValueError("Transient step must be positive and no greater than stop time")
            if not 0 <= number(self.start) < number(self.stop):
                raise ValueError("Transient start must be nonnegative and below stop time")
            return f".tran {self.step} {self.stop} {self.start} {self.step}"
        if self.mode == "AC":
            if not 0 < number(self.start_hz) < number(self.stop_hz) or not 1 <= self.points <= 10000:
                raise ValueError("AC needs increasing positive frequencies and 1–10000 points per decade")
            return f".ac dec {self.points} {self.start_hz} {self.stop_hz}"
        raise ValueError(f"Unknown analysis {self.mode}")


@dataclass
class Project:
    parts: list[Part] = field(default_factory=list)
    wires: list[tuple] = field(default_factory=list)
    junctions: list[tuple] = field(default_factory=list)
    profiles: dict[str, Profile] = field(default_factory=lambda: {
        "Operating point": Profile(), "AC sweep": Profile("AC"), "Transient": Profile("TRAN")})
    active: str = "Operating point"
    diode_models: dict[str, tuple[str, str]] = field(
        default_factory=lambda: {"DDEFAULT": ("1e-14", "1")})
    qucs_models: dict[str, dict[str, str]] = field(default_factory=dict)
    subcircuits: dict[str, dict] = field(default_factory=dict)
    device_models: dict[str, dict] = field(default_factory=lambda: {
        name: {"type": family, "parameters": parameters.copy(), "flags": []}
        for name, (family, parameters) in GENERIC_MODELS.items()})
    markers: list[dict] = field(default_factory=list)
    voltage_labels: dict[str, dict] = field(default_factory=dict)
    element_colors: dict = field(default_factory=dict)
    annotations: list = field(default_factory=list)
    analysis_plan: dict = field(default_factory=dict)
    plot_layouts: dict = field(default_factory=dict)
    subcircuit_design: dict | None = None

    def register_qucs_diode(self, entry):
        stem = "QUCS_" + re.sub(r"[^A-Z0-9_]", "_", entry.name.upper())[:80]
        name = stem
        index = 2
        while name in self.diode_models or name in self.device_models or (name in self.qucs_models
                                            and self.qucs_models[name] != entry.parameters):
            name = f"{stem}_{index}"
            index += 1
        qucs_diode_model(name, entry.parameters)
        self.qucs_models[name] = entry.parameters.copy()
        return name

    def add(self, kind, point):
        used = {part.ref for part in self.parts}
        index = 1
        while f"{kind}{index}" in used:
            index += 1
        self.parts.append(Part(f"{kind}{index}", kind, *point, SYMBOLS[kind][1]))

    def add_profile(self, name, mode, copy_active=False):
        name = name.strip()
        if not name or any(name.casefold() == key.casefold() for key in self.profiles):
            raise ValueError("Enter a unique profile name")
        profile = replace(self.profiles[self.active]) if copy_active else Profile(mode)
        profile.directive()
        self.profiles[name] = profile
        self.active = name

    def remove_active_profile(self):
        if len(self.profiles) == 1:
            raise ValueError("Keep at least one simulation profile")
        self.plot_layouts.pop(self.active, None)
        del self.profiles[self.active]
        self.active = next(iter(self.profiles))

    def dump(self, *, include_label_styles=True):
        data = asdict(self)
        for part in data["parts"]:
            for option in ("opamp_symbol", "port_sides"):
                if not part[option]:
                    del part[option]
        for part in data["parts"]:
            if not include_label_styles or not part["label_style"]:
                part.pop("label_style")
        if not include_label_styles:
            data["annotations"] = [note[:3] for note in data["annotations"]]
            for marker in data["markers"]:
                marker.pop("label_style",None)
        if not include_label_styles or not self.plot_layouts:
            del data["plot_layouts"]
        for key in ("annotations","analysis_plan","element_colors"):
            if not data[key]:
                del data[key]
        if self.subcircuit_design is None:
            del data["subcircuit_design"]
        if not include_label_styles or not self.voltage_labels:
            del data["voltage_labels"]
        return json.dumps({"version": 2, **data}, indent=2)

    @classmethod
    def load(cls, source, *, _depth=0):
        if _depth > 8:
            raise ValueError("Subcircuits can be nested up to eight levels.")
        data = json.loads(source)
        version = data.pop("version", None)
        if version not in (1, 2):
            raise ValueError("Unsupported project version")
        data["parts"] = [Part(**entry) for entry in data["parts"]]
        data["wires"] = [tuple(map(tuple, wire)) for wire in data["wires"]]
        data["junctions"] = list(map(tuple, data["junctions"]))
        data["profiles"] = {key: Profile(**value) for key, value in data["profiles"].items()}
        project = cls(**data)
        if (project.active not in project.profiles
                or any(p.kind not in SYMBOLS or type(p.x) is not int or type(p.y) is not int
                       or type(p.rotation) is not int or not isinstance(p.value, str)
                       or type(p.legacy_pin_layout) is not bool
                       or type(p.opamp_symbol) is not bool or (p.opamp_symbol and not p.is_op07())
                       or not valid_label_style(p.label_style)
                       or not isinstance(p.port_sides, list)
                       or (p.port_sides and (p.kind != "X" or p.opamp_symbol
                           or len(p.port_sides) != len(p.port_names)
                           or any(side not in PORT_SIDES for side in p.port_sides)))
                       or (p.legacy_pin_layout and p.kind not in MODELED_SYMBOLS)
                       or not isinstance(p.ref, str) for p in project.parts)
                or any(len(wire) != 2 or wire[0] == wire[1] for wire in project.wires)
                or any(len(point) != 2 or any(type(axis) is not int for axis in point)
                       for point in [*project.junctions, *(p for w in project.wires for p in w)])):
            raise ValueError("Invalid project geometry, active profile, or symbol")
        if not isinstance(project.annotations,list) or any(
                not isinstance(note,list) or len(note) not in (3,4) or type(note[0]) is not int or type(note[1]) is not int
                or not isinstance(note[2],str) or (len(note)==4 and not valid_label_style(note[3])) for note in project.annotations):
            raise ValueError("Invalid schematic text annotation")
        plan = project.analysis_plan
        if not isinstance(plan,dict) or (plan and (
                not isinstance(plan.get("enabled"),list) or not all(isinstance(n,str) for n in plan["enabled"])
                or (plan.get("parametric") and (not isinstance(plan.get("values"),list)
                    or not 1<=len(plan["values"])<=64 or not all(isinstance(v,str) for v in plan["values"])
                    or not all(isinstance(plan.get(k),str) for k in ("ref","profile")))))):
            raise ValueError("Invalid analysis setup")
        if not isinstance(project.element_colors,dict) or any(
                not isinstance(v,str) or not re.fullmatch(r"#[0-9a-fA-F]{6}",v)
                for v in project.element_colors.values()):
            raise ValueError("Invalid schematic item color")
        validate_plot_layouts(project.plot_layouts)
        for profile in project.profiles.values():
            profile.directive()
        for name, (current, emission) in project.diode_models.items():
            diode_model(name, current, emission)
        for name, parameters in project.qucs_models.items():
            qucs_diode_model(name, parameters)
        for name, model in project.subcircuits.items():
            if "source" in model:
                if set(model) != {"source", "ports", "sides"}:
                    raise ValueError(f"{name}: invalid schematic subcircuit")
                source_project = cls.load(json.dumps(model["source"]), _depth=_depth+1)
                design = validate_design(source_project)
                if (name, model["ports"], model["sides"]) != (
                        design["name"], [p["name"] for p in design["ports"]],
                        [p["side"] for p in design["ports"]]):
                    raise ValueError(f"{name}: subcircuit ports do not match its source")
            else:
                validate_subcircuit(name, model["ports"], model["body"], number, model.get("parameters"))
        if project.subcircuit_design is not None:
            validate_design(project, check_nets=False)
        for name, model in project.device_models.items():
            if name in project.diode_models or name in project.qucs_models:
                raise ValueError(f"Duplicate model name: {name}")
            model_statement(name, model, number)
        for part in project.parts:
            if part.kind == "X" and (part.port_names != project.subcircuits[part.value]["ports"]
                                     or part.port_sides != project.subcircuits[part.value].get("sides", [])):
                raise ValueError(f"{part.ref}: subcircuit port order changed")
            if part.kind in "QMJS":
                model = project.device_models.get(part.value.upper())
                if not model or (part.kind, tuple(part.port_names)) != MODEL_PORTS[model["type"]]:
                    raise ValueError(f"{part.ref}: device model or port order changed")
        for marker in project.markers:
            if not valid_label_style(marker.get("label_style",{})):
                raise ValueError("Invalid marker label style")
            if marker.get("kind") == "I":
                if not isinstance(marker.get("ref"), str) or type(marker.get("pin")) is not int:
                    raise ValueError("Invalid current marker")
                continue
            points = marker.get("points", [])
            if (marker.get("kind") not in ("V", "VD")
                    or len(points) != (2 if marker["kind"] == "VD" else 1)
                    or any(len(p) != 2 or any(type(v) is not int for v in p) for p in points)):
                raise ValueError("Invalid voltage marker")
        if not isinstance(project.voltage_labels, dict) or any(
                not isinstance(style, dict)
                or style.keys() != {"color", "size", "x", "y"}
                or (style["color"] is not None and (not isinstance(style["color"], str)
                    or not re.fullmatch(r"#[0-9a-fA-F]{6}", style["color"])))
                or type(style["size"]) is not int or not 8 <= style["size"] <= 48
                or any(type(style[axis]) is not int or not -500 <= style[axis] <= 500 for axis in ("x", "y"))
                for style in project.voltage_labels.values()):
            raise ValueError("Invalid voltage label appearance")
        if version == 1:
            project.preserve_connected_legacy_pins()
        return project

    def preserve_connected_legacy_pins(self):
        devices = [part for part in self.parts if part.kind in MODELED_SYMBOLS]
        if not devices:
            return
        for part in devices:
            part.legacy_pin_layout = True
        occupied = Counter(pin for part in self.parts for pin in part.pins())
        anchors = set(self.junctions)
        anchors.update(tuple(point) for marker in self.markers if marker["kind"] != "I"
                       for point in marker["points"])
        for part in devices:
            old_pins = part.pins()
            occupied.subtract(old_pins)
            part.legacy_pin_layout = False
            if any(occupied[point] or point in anchors or any(on_wire(point, wire) for wire in self.wires)
                   for point in (*old_pins, *part.pins())):
                part.legacy_pin_layout = True
            occupied.update(part.pins())

    def save(self, path):
        path = Path(path)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temp = Path(file.name)
            try:
                file.write(self.dump())
                file.close()
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)


def on_wire(point, wire):
    (x, y), ((ax, ay), (bx, by)) = point, wire
    return ((x-ax)*(by-ay) == (y-ay)*(bx-ax)
            and min(ax, bx) <= x <= max(ax, bx) and min(ay, by) <= y <= max(ay, by))


def port_groups(model):
    """Return the physical pin indices for each distinct electrical port."""
    groups = {}
    for index, port in enumerate(model.get("source", {}).get("subcircuit_design", {}).get("ports", [])):
        groups.setdefault(port["net"], []).append(index)
    return list(groups.values())


def shared_port_pins(project, part):
    if part.kind != "X":
        return set()
    return {pin for group in port_groups(project.subcircuits.get(part.value, {}))
            if len(group) > 1 for pin in group}


def normalized_label(name):
    return name.lower().replace("−", "-")


def connectivity(project):
    parent = {}

    def find(point):
        parent.setdefault(point, point)
        while parent[point] != point:
            parent[point] = parent[parent[point]]
            point = parent[point]
        return point

    def join(a, b):
        parent[find(b)] = find(a)

    points = {point for part in project.parts for point in part.pins()}
    points.update(point for wire in project.wires for point in wire)
    points.update(project.junctions)
    for point in sorted(points):
        find(point)
    for wire in project.wires:
        for point in sorted(points):
            if on_wire(point, wire):
                join(wire[0], point)
    for part in project.parts:
        if part.kind != "X":
            continue
        pins = part.pins()
        for group in port_groups(project.subcircuits.get(part.value, {})):
            for index in group[1:]:
                join(pins[group[0]], pins[index])
    labels = {}
    for part in project.parts:
        if part.kind in "GN":
            name = "0" if part.kind == "G" else normalized_label(part.value)
            if not re.fullmatch(r"[a-z_][a-z0-9_+-]*|0", name, re.ASCII):
                raise ValueError(f"{part.ref}: invalid net label {part.value!r}. Start with a letter or _, "
                                 "then use letters, numbers, _, + or -.")
            point = part.pins()[0]
            join(labels.setdefault(name, point), point)
    names = {}
    for name in sorted(labels):
        names.setdefault(find(labels[name]), name)
    used = set(labels)
    for index, root in enumerate(sorted({find(point) for point in points}), 1):
        name = f"n{index}"
        while name in used:
            name += "_"
        names.setdefault(root, name)
    return {point: names[find(point)] for point in points}


def source_tokens(value):
    return value.replace("(", " ").replace(")", " ").replace(",", " ").split()


def source_value(value):
    tokens = source_tokens(value)
    if not tokens:
        raise ValueError("Source value is empty")
    if len(tokens) == 1:
        number(tokens[0])
        return "DC " + tokens[0]
    index, clauses = 0, []
    for clause in ("DC", "AC"):
        if index < len(tokens) and tokens[index].upper() == clause:
            first = index
            index += 1
            value = number(tokens[index] if index < len(tokens) else "")
            index += 1
            if clause == "AC" and value < 0:
                raise ValueError("AC magnitude must be nonnegative")
            if clause == "AC" and index < len(tokens) and tokens[index].upper() not in WAVE_FIELDS:
                number(tokens[index])
                index += 1
            clauses.append(" ".join(tokens[first:index]))
    if index < len(tokens) and tokens[index].upper() in WAVE_FIELDS:
        mode = tokens[index].upper()
        arguments = tokens[index+1:]
        values = list(map(number, arguments))
        if mode == "PWL":
            times = values[::2]
            if len(values) < 4 or len(values) % 2 or times[0] < 0 or any(a >= b for a, b in zip(times, times[1:])):
                raise ValueError("PWL needs at least two time/value pairs with increasing nonnegative times")
        elif len(values) != len(WAVE_FIELDS[mode]):
            raise ValueError(f"{mode} requires {', '.join(WAVE_FIELDS[mode])}")
        elif mode == "SIN" and (values[2] <= 0 or min(values[3:5]) < 0):
            raise ValueError("SIN frequency must be positive, with nonnegative delay and damping")
        elif mode == "PULSE" and (min(values[2:6]) < 0 or values[-1] <= 0):
            raise ValueError("PULSE timing must be nonnegative with a positive period")
        elif mode == "EXP" and (not 0 <= values[2] <= values[4] or min(values[3], values[5]) <= 0):
            raise ValueError("EXP needs ordered nonnegative delays and positive time constants")
        clauses.append(f"{mode}({' '.join(arguments)})")
        index = len(tokens)
    if index != len(tokens):
        raise ValueError("Use DC, AC, SIN, PULSE, EXP, or PWL source parameters")
    return " ".join(clauses)


def diode_model(name, current, emission):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name, re.ASCII):
        raise ValueError("Use an uppercase SPICE model name")
    if min(number(current), number(emission)) <= 0:
        raise ValueError("Diode model saturation current and emission coefficient must be positive")
    return f".model {name} D(IS={current} N={emission})"


def qucs_diode_model(name, parameters):
    if (not re.fullmatch(r"[A-Z][A-Z0-9_]*", name, re.ASCII)
            or parameters.keys() - PARAMETERS or not {"IS", "N"} <= parameters.keys()):
        raise ValueError("Invalid Qucs-S diode model")
    for value in parameters.values():
        number(value)
    if min(number(parameters["IS"]), number(parameters["N"])) <= 0:
        raise ValueError("Diode model saturation current and emission coefficient must be positive")
    fields = " ".join(f"{key}={parameters[key]}" for key in sorted(parameters))
    return f".model {name} D({fields})"


def translate_selection(project, selected_parts, selected_wires, selected_junctions, dx, dy,
                        *, reroute_terminals=False, vertical_first=False):
    def shifted(point):
        return point[0]+dx, point[1]+dy

    before = connectivity(project)
    old_nets = [before[pin] for part in project.parts for pin in part.pins()]
    wires = list(project.wires)
    moving_wires = [w for i, w in enumerate(wires) if i in selected_wires]
    fixed_wires = [w for i, w in enumerate(wires) if i not in selected_wires]
    moving = {pin for i in selected_parts for pin in project.parts[i].pins()}
    moving.update(pin for wire in moving_wires for pin in wire)
    moving.update(project.junctions[i] for i in selected_junctions)
    fixed = {pin for i, part in enumerate(project.parts) if i not in selected_parts for pin in part.pins()}
    fixed.update(pin for wire in fixed_wires for pin in wire)
    fixed.update(p for i, p in enumerate(project.junctions) if i not in selected_junctions)
    contacts = {p for p in moving if p in fixed or any(on_wire(p, wire) for wire in fixed_wires)}
    contacts.update(p for p in fixed if any(on_wire(p, wire) for wire in moving_wires))
    replacements = {}
    if reroute_terminals:
        stationary_pins = {pin for i, part in enumerate(project.parts) if i not in selected_parts
                           for pin in part.pins()}
        anchors = stationary_pins | set(project.junctions) | {
            tuple(point) for marker in project.markers if marker["kind"] != "I"
            for point in marker["points"]}
        for point in sorted(contacts):
            incident = [(i, wire) for i, wire in enumerate(wires) if on_wire(point, wire)]
            if len(incident) != 1 or point in anchors:
                continue
            index, wire = incident[0]
            if index in selected_wires or point not in wire:
                continue
            other = wire[1] if point == wire[0] else wire[0]
            if other in moving or any(p != other and on_wire(p, wire) for p in (fixed | anchors | moving) - {point}):
                continue
            replacements[point] = index, other

    for i in selected_parts:
        part = project.parts[i]
        part.x, part.y = shifted((part.x, part.y))
    for i in selected_wires:
        project.wires[i] = tuple(shifted(pin) for pin in wires[i])
    for i in selected_junctions:
        project.junctions[i] = shifted(project.junctions[i])
    existing = {frozenset(wire) for wire in project.wires}
    for contact in sorted(contacts):
        end = shifted(contact)
        replacement = replacements.get(contact)
        start = replacement[1] if replacement else contact
        bend = (start[0], end[1]) if vertical_first else (end[0], start[1])
        segments = [(a, b) for a, b in ((start, bend), (bend, end)) if a != b]
        if replacement and segments:
            original = replacement[0]
            existing.discard(frozenset(project.wires[original]))
            project.wires[original] = segments.pop(0)
            existing.add(frozenset(project.wires[original]))
        for a, b in segments:
            key = frozenset((a, b))
            if a != b and key not in existing:
                project.wires.append((a, b))
                existing.add(key)
    after = connectivity(project)
    new_nets = [after[pin] for part in project.parts for pin in part.pins()]
    pairs = set(zip(old_nets, new_nets))
    if (len(pairs) != len(set(old_nets)) or len(pairs) != len(set(new_nets))
            or any((a == "0") != (b == "0") for a, b in pairs)):
        raise ValueError("This move would change electrical connectivity. Edit the nearby wires first.")
    for marker in project.markers:
        if marker["kind"] != "I":
            marker["points"] = [
                list(shifted(p)) if tuple(p) in moving or any(on_wire(p, w) for w in moving_wires) else p
                for p in marker["points"]]


def marker_node(point, project, nodes):
    point = tuple(point)
    if point in nodes:
        return nodes[point]
    candidates = {nodes[wire[0]] for wire in project.wires if on_wire(point, wire)}
    if len(candidates) == 1:
        return candidates.pop()
    raise ValueError(f"Marker at {point} needs an unambiguous wire or pin")


def validate_design(project, *, check_nets=True):
    design = project.subcircuit_design
    if not isinstance(design, dict) or set(design) != {"name", "ports"}:
        raise ValueError("Use Build reusable subcircuit to assign ports to this schematic.")
    name, ports = design["name"], design["ports"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", name, re.ASCII):
        raise ValueError("Use an ASCII letter followed by up to 39 letters, numbers or underscores for the name.")
    if not isinstance(ports, list) or not 1 <= len(ports) <= 16:
        raise ValueError("Assign between 1 and 16 ports.")
    device_nets = set()
    if check_nets:
        nodes = connectivity(project)
        device_nets = {nodes[pin] for part in project.parts if part.kind not in "GN" for pin in part.pins()}
    used_names = set()
    for port in ports:
        if (not isinstance(port, dict) or set(port)-{"description"} != {"name", "net", "side"}
                or not isinstance(port.get("description", ""), str) or len(port.get("description", "")) > 500):
            raise ValueError("Invalid subcircuit port.")
        title, net, side = port["name"], port["net"], port["side"]
        if (not isinstance(title, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+-]{0,23}", title, re.ASCII)
                or title.casefold() in used_names):
            raise ValueError("Use distinct port names starting with a letter. Letters, numbers, _, + and - are allowed.")
        if (not isinstance(net, str) or (check_nets and net not in device_nets)
                or side not in PORT_SIDES):
            raise ValueError(f"{title}: choose a net connected to a component and a valid side.")
        used_names.add(title.casefold())
    return design


def reusable_subcircuit(project):
    design = validate_design(project)
    name = design["name"]
    ports = design["ports"]
    source = json.loads(project.dump())
    emit_subcircuit(name, {"source": source}, 0)
    return name, {"source": source, "ports": [p["name"] for p in ports],
                  "sides": [p["side"] for p in ports]}


def emit_subcircuit(name, model, depth):
    if depth > 8:
        raise ValueError("Subcircuits can be nested up to eight levels.")
    if "source" in model:
        project = Project.load(json.dumps(model["source"]))
        ports = validate_design(project)["ports"]
        port_map = {}
        for port in ports:
            port_map.setdefault(port["net"], f"cbp_{len(port_map)+1}")
        elements, models, definitions, _ = compile_circuit(project, None, port_map, name, depth)
        return [*definitions, " ".join([".subckt", name, *port_map.values()]), *elements, *models,
                f".ends {name}"]
    parameters = model.get("parameters", {})
    validate_subcircuit(name, model["ports"], model["body"], number, parameters)
    defaults = ["params:", *(f"{key}={value}" for key, value in parameters.items())] if parameters else []
    return [" ".join([".subckt", name, *model["ports"], *defaults]), *model["body"], f".ends {name}"]


def netlist(project, measurements=None):
    lines, models, definitions, refs = compile_circuit(project, measurements)
    profile = project.profiles[project.active]
    directive = profile.directive()
    if profile.mode == "DC" and profile.sweep_source.upper() not in refs:
        raise ValueError(f"DC sweep source {profile.sweep_source} is missing from the schematic")
    return "\n".join(["CircuitBench", *lines, *models, *definitions, directive, ".end", ""])


def compile_circuit(project, measurements, ports=None, scope="", depth=0):
    nodes = connectivity(project)
    devices = sorted((p for p in project.parts if p.kind not in "GN"), key=lambda p: p.ref.upper())
    if not devices or (ports is None and "0" not in nodes.values()):
        raise ValueError("Place electrical components and connect a ground reference")
    neighbors = {}
    refs, models, pin_counts = set(), set(), {}
    subcircuits = set()
    lines = []
    names = {}
    if ports is not None:
        reserved = {value.lower() for value in ports.values()}
        for i, node in enumerate(sorted(set(nodes.values()))):
            local = f"cbn_{i}"
            while local in reserved:
                local += "_"
            names[node] = ports.get(node, "0" if node == "0" else local)
    by_ref = {p.ref.upper(): p for p in devices}
    used_names = set(by_ref)
    used_nodes = set(nodes.values())
    sensors = {}
    for index, marker in enumerate(project.markers if ports is None else (), 1):
        if marker["kind"] == "I":
            ref, pin = marker["ref"].upper(), marker["pin"]
            if ref not in by_ref or not 0 <= pin < len(by_ref[ref].pins()):
                raise ValueError(f"Current marker needs an existing pin on {ref}")
            if pin in shared_port_pins(project, by_ref[ref]):
                raise ValueError(f"{ref}: use a current marker on an external component to measure a shared port")
            if (ref, pin) not in sensors:
                tag = f"cwb{index}"
                while f"V_{tag.upper()}" in used_names or f"n_{tag}" in used_nodes:
                    tag += "_"
                sensor, inner = f"V_{tag}", f"n_{tag}"
                used_names.add(sensor.upper())
                used_nodes.add(inner)
                sensors[ref, pin] = sensor, inner
            sensor, _ = sensors[ref, pin]
            if measurements is not None:
                if marker.get("power"):
                    pins = by_ref[ref].pins()
                    if len(pins)!=2:
                        raise ValueError("Power markers require a two-terminal component")
                    measurements.append((f"p({ref})", f"i({sensor.lower()})",
                                         [f"v({nodes[p]})" for p in pins]))
                else:
                    measurements.append((f"I({ref}:{pin+1})", f"i({sensor.lower()})", None))
        else:
            nets = [marker_node(point, project, nodes) for point in marker["points"]]
            args = [f"v({node})" for node in nets]
            if measurements is not None:
                measurements.append((" − ".join(args), args[0], args[1] if len(args) == 2 else None))
    for part in devices:
        ref = part.ref.upper()
        if ref in refs or not re.fullmatch(part.kind + r"[A-Z0-9_]+", ref, re.ASCII):
            raise ValueError(f"Invalid or duplicate designator {part.ref}")
        refs.add(ref)
        terminals = [nodes[pin] for pin in part.pins()]
        pin_indices = list(range(len(terminals)))
        if part.kind == "X":
            groups = port_groups(project.subcircuits.get(part.value, {}))
            if groups:
                pin_indices = [group[0] for group in groups]
                terminals = [terminals[i] for i in pin_indices]
        if part.kind in "RCLVID" and terminals[0] == terminals[1]:
            raise ValueError(f"{ref}: both pins are on the same net")
        for node in terminals:
            pin_counts.setdefault(node, []).append(ref)
            neighbors.setdefault(node, set()).update(terminals)
        value = part.value.strip()
        if part.kind in "RCL":
            if number(value) <= 0:
                raise ValueError(f"{ref}: value must be positive")
        elif part.kind in "VI":
            value = source_value(value)
        elif part.kind == "X":
            if value not in project.subcircuits or part.port_names != project.subcircuits[value]["ports"]:
                raise ValueError(f"{ref}: unknown subcircuit or mismatched port order")
            subcircuits.add(value)
            if scope:
                value = scoped_name(scope, value)
        elif value.upper() in project.device_models or part.kind in "QMJS":
            value = value.upper()
            model = project.device_models.get(value)
            ports = ("A", "K") if part.kind == "D" else tuple(part.port_names)
            if not model or (part.kind, ports) != MODEL_PORTS[model["type"]]:
                raise ValueError(f"{ref}: incompatible model or pin order")
            models.add(model_statement(value, model, number))
        else:
            value = value.upper()
            if value in project.qucs_models:
                models.add(qucs_diode_model(value, project.qucs_models[value]))
            elif value in project.diode_models:
                models.add(diode_model(value, *project.diode_models[value]))
            else:
                raise ValueError(f"{ref}: unknown diode model {value}")
        for index, (pin, terminal) in enumerate(zip(pin_indices, terminals)):
            if (ref, pin) in sensors:
                sensor, inner = sensors[ref, pin]
                lines.append(f"{sensor} {terminal} {inner} 0")
                terminals[index] = inner
        lines.append(" ".join([ref, *(names.get(pin, pin) for pin in terminals), value]))
    reached = {"0"} | set(ports or ())
    pending = list(reached)
    while pending:
        for node in neighbors.get(pending.pop(), set()) - reached:
            reached.add(node)
            pending.append(node)
    if neighbors.keys() - reached:
        raise ValueError("Circuit contains a group of devices disconnected from ground"
                         if ports is None else "A group of devices is disconnected from all ports and ground")
    for node, endpoints in pin_counts.items():
        if node != "0" and node not in (ports or ()) and len(endpoints) == 1:
            raise ValueError(f"{endpoints[0]}: dangling terminal on {node}")
    definitions = []
    for name in sorted(subcircuits):
        definitions.extend(emit_subcircuit(scoped_name(scope, name) if scope else name,
                                           project.subcircuits[name], depth+1))
    return lines, sorted(models), definitions, refs


def scoped_name(scope, name):
    digest = sha256(f"{scope}/{name}".encode("ascii")).hexdigest()[:24]
    return f"CBW_{digest}"


def example():
    project = Project()
    project.parts = [
        Part("V1", "V", -6, 0, "DC 10 AC 1", 1),
        Part("R1", "R", 0, -2, "1k"), Part("R2", "R", 5, 0, "1k", 1),
        Part("N1", "N", 5, -2, "out"), Part("G1", "G", -6, 2, ""),
    ]
    project.wires = [((-6, -2), (-2, -2)), ((2, -2), (5, -2)), ((-6, 2), (5, 2))]
    return project
