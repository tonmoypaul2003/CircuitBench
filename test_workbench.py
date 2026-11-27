import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from circuit import Part, Project, connectivity, example, netlist, number, source_value
from results import Plot, analysis_deck, load_plot
from plot_math import evaluate
from qucs_library import import_diodes
from spice_library import read_library, read_subcircuits


class CircuitTests(unittest.TestCase):
    def test_discrete_pin_coordinates_and_rotation(self):
        bjt = Part("Q1", "Q", 10, 20, "GENERIC_NPN", port_names=["C", "B", "E"])
        self.assertEqual(bjt.pins(), [(11, 18), (8, 20), (11, 22)])
        bjt.rotation = 1
        self.assertEqual(bjt.pins(), [(12, 21), (10, 18), (8, 21)])
        mos = Part("M1", "M", 0, 0, "GENERIC_PMOS", port_names=["D", "G", "S", "B"])
        self.assertEqual(mos.pins(), [(1, -2), (-2, 0), (1, 2), (3, 0)])

    def test_legacy_wired_pins_are_preserved(self):
        project = Project(parts=[
            Part("Q1", "Q", 0, 0, "GENERIC_NPN", port_names=["C", "B", "E"],
                 legacy_pin_layout=True),
            Part("V1", "V", 10, 0, "DC 5"), Part("V2", "V", 10, 8, "DC .65"),
            Part("R1", "R", 10, 16, "1k"),
        ])
        label_pins(project, ("out", "base", "0"), ("rail", "0"), ("base", "0"), ("rail", "out"))
        previous_deck = netlist(project)
        old = json.loads(project.dump())
        old["version"] = 1
        for part in old["parts"]:
            part.pop("legacy_pin_layout")
        old["parts"].append({"ref": "J1", "kind": "J", "x": -10, "y": -10,
                             "value": "GENERIC_NJFET", "port_names": ["D", "G", "S"]})
        reopened = Project.load(json.dumps(old))
        unwired = reopened.parts.pop()
        self.assertFalse(unwired.legacy_pin_layout)
        self.assertEqual(unwired.pins(), [(-9, -12), (-12, -10), (-9, -8)])
        self.assertTrue(reopened.parts[0].legacy_pin_layout)
        self.assertEqual(netlist(reopened), previous_deck)
        self.assertEqual(netlist(Project.load(reopened.dump())), previous_deck)

    def test_plot_expressions(self):
        plot = Plot("AC", ["frequency", "v(out)"], ["frequency", "voltage"],
                    [[10, 0j], [100, .1j]])
        values = evaluate('db(v(out)) + multiply(log10(frequency), 2)', plot)
        self.assertTrue(math.isnan(values[0].real))
        self.assertAlmostEqual(values[1].real, -16)
        self.assertEqual(evaluate('loge(exp(2)) + ln(1) + real(trace("v(out)"))', plot), [2, 2])
        with self.assertRaises(ValueError):
            evaluate("__import__('os').getcwd()", plot)
    def test_source_forms(self):
        self.assertEqual(source_value("DC 0 AC 2 90 SIN(0,1,1k,0,0,90)"),
                         "DC 0 AC 2 90 SIN(0 1 1k 0 0 90)")
        self.assertEqual(source_value("PWL 0 0 1m 2"), "PWL(0 0 1m 2)")
        self.assertEqual(source_value("EXP 0 1 0 100u 1m 100u"), "EXP(0 1 0 100u 1m 100u)")
        with self.assertRaisesRegex(ValueError, "increasing"):
            source_value("PWL 1m 0 0 1")
    def test_example_netlist_and_roundtrip(self):
        project = example()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "example.cwb"
            project.save(target)
            copy = Project.load(target.read_text(encoding="utf-8"))
        self.assertEqual(project.dump(), copy.dump())
        self.assertEqual(netlist(copy), "CircuitBench\nR1 n1 out 1k\nR2 out 0 1k\nV1 n1 0 DC 10 AC 1\n.op\n.end\n")

    def test_crossing_and_tap(self):
        project = Project(wires=[((-2, 0), (2, 0)), ((0, -2), (0, 2))])
        self.assertNotEqual(connectivity(project)[(-2, 0)], connectivity(project)[(0, -2)])
        project.junctions.append((0, 0))
        self.assertEqual(len(set(connectivity(project).values())), 1)
        project.junctions.clear()
        project.wires[1] = ((0, -2), (0, 0))
        self.assertEqual(len(set(connectivity(project).values())), 1)

    def test_invalid_and_dangling(self):
        project = example()
        project.parts[0].value = "DC 1\n.end"
        with self.assertRaises(ValueError):
            netlist(project)
        project.parts[0].value = "DC 1"
        project.wires.pop(1)
        with self.assertRaisesRegex(ValueError, "dangling"):
            netlist(project)
        self.assertAlmostEqual(number("1M"), .001)
        self.assertEqual(number("1MEG"), 1e6)

    def test_model_emission(self):
        project = example()
        project.parts[2] = Part("D2", "D", 5, 0, "custom", 1)
        project.diode_models["CUSTOM"] = ("2p", "1.5")
        deck = netlist(project)
        self.assertIn("D2 out 0 CUSTOM", deck)
        self.assertIn(".model CUSTOM D(IS=2p N=1.5)", deck)

    def test_saved_named_profiles(self):
        project = example()
        project.add_profile("Voltage sweep", "DC")
        project.profiles[project.active].sweep_stop = "5"
        project.add_profile("Fine sweep", "OP", copy_active=True)
        project.profiles[project.active].sweep_step = ".25"
        project = Project.load(project.dump())
        self.assertIn(".dc V1 0 5 .25", netlist(project))
        self.assertEqual(project.profiles["Voltage sweep"].sweep_step, "1")
        with self.assertRaisesRegex(ValueError, "unique"):
            project.add_profile("voltage sweep", "DC")
        project.profiles[project.active].sweep_source = "V99"
        with self.assertRaisesRegex(ValueError, "missing"):
            netlist(project)
        project.remove_active_profile()
        self.assertNotIn("Fine sweep", project.profiles)

    def test_complex_plot_and_truncation(self):
        raw = ("Title: Example\nPlotname: AC Analysis\nFlags: complex\n"
               "No. Variables: 2\nNo. Points: 1\nVariables:\n"
               "\t0\tfrequency\tfrequency\n\t1\tv(out)\tvoltage\n"
               "Values:\n0\t100,0\n\t0.5,-0.5\n")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.raw"
            path.write_text(raw, encoding="ascii")
            self.assertEqual(load_plot(path).rows, [[100+0j, .5-.5j]])
            path.write_text(raw.rsplit("\t", 1)[0], encoding="ascii")
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                load_plot(path)


RUNTIME = Path(__file__).resolve().parent / "runtime"
BUNDLED_SOLVER = RUNTIME / "ngspice" / "bin" / "ngspice_con.exe"
SOLVER = os.environ.get("NGSPICE") or (str(BUNDLED_SOLVER) if BUNDLED_SOLVER.is_file() else shutil.which("ngspice"))
QUCS_REFERENCE = RUNTIME / "models" / "qucs-diodes.lib"
STARTER_ROOT = RUNTIME / "models" / "basic_models"


def label_pins(project, *groups):
    for part, names in zip(project.parts.copy(), groups):
        for (x, y), name in zip(part.pins(), names):
            project.parts.append(Part(f"N{len(project.parts)}", "N", x, y, name))


def imported_divider():
    with tempfile.TemporaryDirectory() as directory:
        library = Path(directory) / "divider.lib"
        library.write_text(".subckt DIVIDER IN REF OUT\nR1 IN OUT 1k\nR2 OUT REF 1k\n.ends DIVIDER\n")
        models = read_subcircuits(library, number)
    project = Project(subcircuits=models)
    project.parts = [
        Part("V1", "V", -6, -3, "DC 10", 1),
        Part("X1", "X", 0, 0, "DIVIDER", port_names=models["DIVIDER"]["ports"]),
        Part("R1", "R", 6, 2, "1k", 1),
        Part("G1", "G", -6, -1, ""), Part("N1", "N", 6, 0, "out"),
    ]
    project.wires = [
        ((-6, -5), (-7, -5)), ((-7, -5), (-7, -2)), ((-7, -2), (-3, -2)),
        ((-3, -2), (-3, -1)), ((-6, -1), (-6, 1)), ((-6, 1), (-3, 1)),
        ((3, 0), (6, 0)), ((6, 4), (-6, 4)), ((-6, 4), (-6, 1)),
    ]
    return Project.load(project.dump())


@unittest.skipUnless(SOLVER, "Set NGSPICE to run electrical integration tests")
class ElectricalTests(unittest.TestCase):
    def run_circuit(self, project):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "circuit.cir").write_text(analysis_deck(netlist(project)), encoding="ascii")
            run = subprocess.run([SOLVER, "-n", "-b", "circuit.cir"], cwd=path, timeout=25,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertTrue((path / "result.raw").exists(), run.stdout + run.stderr)
            return load_plot(path / "result.raw")

    def test_divider_operating_point(self):
        plot = self.run_circuit(example())
        self.assertAlmostEqual(plot.rows[0][plot.names.index("v(out)")].real, 5, delta=1e-6)
        current = next(value for key, value in zip(plot.names, plot.rows[0])
                       if key.lower() in ("i(v1)", "v1#branch"))
        self.assertAlmostEqual(current.real, -.005, delta=1e-8)

    def test_saved_voltage_and_current_markers(self):
        project = example()
        project.markers = [{"kind": "VD", "points": [[-2, -2], [5, -2]]},
                           {"kind": "I", "ref": "R2", "pin": 0},
                           {"kind": "V", "points": [[-6, 2]]}]
        project = Project.load(project.dump())
        measurements = []
        netlist(project, measurements)
        plot = self.run_circuit(project)
        diff, current, ground = plot.marker_columns(measurements)
        self.assertAlmostEqual(plot.rows[0][diff].real, 5)
        self.assertAlmostEqual(plot.rows[0][current].real, .005)
        self.assertEqual(plot.rows[0][ground], 0)

    def test_phased_ac_and_sine_source(self):
        project = example()
        project.parts[0].value = "DC 0 AC 2 90 SIN 0 2 1k 0 0 0"
        project.active = "AC sweep"
        plot = self.run_circuit(project)
        index = plot.names.index("v(out)")
        self.assertAlmostEqual(plot.rows[0][index].imag, 1, delta=1e-8)
        self.assertAlmostEqual(plot.rows[0][index].real, 0, delta=1e-8)
        project.active = "Transient"
        plot = self.run_circuit(project)
        index = plot.names.index("v(out)")
        row = min(plot.rows, key=lambda row: abs(row[0].real-.00025))
        self.assertAlmostEqual(row[index].real, math.sin(2*math.pi*1000*row[0].real), delta=.001)

    def test_divider_dc_sweep(self):
        project = example()
        project.add_profile("Voltage sweep", "DC")
        plot = self.run_circuit(project)
        self.assertEqual(len(plot.rows), 11)
        column = plot.names.index("v(out)")
        for voltage, row in enumerate(plot.rows):
            self.assertAlmostEqual(row[0].real, voltage, delta=1e-6)
            self.assertAlmostEqual(row[column].real, voltage / 2, delta=1e-6)

    @unittest.skipUnless(STARTER_ROOT.is_dir(), "Optional upstream model pack is unavailable")
    def test_upstream_power_mosfet_and_opamp(self):
        models, _, issues = read_library(STARTER_ROOT / "MOS power/IRFP240_NVDMOS.mod", number)
        self.assertFalse(issues)
        project = Project()
        project.device_models.update(models)
        project.parts = [Part("M1", "M", 0, 0, "IRFP240", port_names=["D", "G", "S"]),
                         Part("R1", "R", 0, 8, "1k"), Part("V1", "V", 10, 0, "DC 10"),
                         Part("V2", "V", 10, 8, "DC 8")]
        label_pins(project, ("out", "gate", "0"), ("supply", "out"), ("supply", "0"), ("gate", "0"))
        plot = self.run_circuit(Project.load(project.dump()))
        self.assertLess(plot.rows[0][plot.names.index("v(out)")].real, 1)
        for filename, model, expected in (("generic_opamp.lib", "GENOPA1", .04),
                                          ("Op07.mod", "OP07", .05)):
            _, subcircuits, issues = read_library(STARTER_ROOT / "OpAmps" / filename, number)
            self.assertFalse(issues)
            project = Project(subcircuits=subcircuits)
            project.parts = [Part("X1", "X", 0, 0, model, port_names=subcircuits[model]["ports"],
                                  opamp_symbol=model == "OP07"),
                             Part("V1", "V", 10, 0, "DC 0.05"), Part("V2", "V", 10, 6, "DC 12"),
                             Part("V3", "V", 10, 10, "DC -12")]
            label_pins(project, ("input", "out", "vcc", "vee", "out"), ("input", "0"), ("vcc", "0"), ("vee", "0"))
            plot = self.run_circuit(Project.load(project.dump()))
            self.assertAlmostEqual(plot.rows[0][plot.names.index("v(out)")].real, expected, delta=.001)

    def test_rc_subcircuit_with_four_named_pins_and_common_return(self):
        from circuit import reusable_subcircuit
        from results import bias_deck

        rc = Project()
        rc.parts = [Part("R1", "R", 0, 0, "1k"), Part("C1", "C", 0, 6, "1u")]
        label_pins(rc, ("In+", "Out+"), ("Out+", "In−"))
        rc.parts.append(Part("N5", "N", 3, 6, "Out-"))
        rc.wires.append(((2, 6), (3, 6)))
        nodes = connectivity(rc)
        self.assertEqual(nodes[(2, 6)], nodes[(3, 6)])
        rc.subcircuit_design = {"name": "RC_FILTER", "ports": [
            {"name": name, "net": nodes[point], "side": side}
            for name, point, side in (("In+", (-2, 0), "Left"), ("In-", (2, 6), "Left"),
                                      ("Out+", (2, 0), "Right"), ("Out-", (3, 6), "Right"))
        ]}
        name, model = reusable_subcircuit(rc)
        host = Project(subcircuits={name: model})
        host.parts = [Part("X1", "X", 0, 0, name, port_names=model["ports"], port_sides=model["sides"]),
                      Part("V1", "V", 12, 0, "DC 10"), Part("R1", "R", 12, 6, "1k")]
        label_pins(host, ("source+", "0", "output+", "load-"), ("source+", "0"), ("output+", "load-"))
        host = Project.load(host.dump())
        nets = connectivity(host)
        self.assertEqual(nets[host.parts[0].pins()[3]], "0")
        _, readings = bias_deck(host)
        self.assertIn("X1 source+ 0 output+ RC_FILTER", netlist(host))
        self.assertEqual([entry["pin"] for entry in readings["currents"] if entry["part"] == 0], [0, 2])
        plot = self.run_circuit(host)
        self.assertAlmostEqual(plot.rows[0][plot.names.index("v(output+)")].real, 5)

    def test_nested_reusable_schematic_with_explicit_return(self):
        from circuit import reusable_subcircuit

        divider = Project()
        divider.parts = [Part("R1", "R", 0, 0, "1k"), Part("R2", "R", 0, 6, "1k")]
        label_pins(divider, ("input", "out"), ("out", "0"))
        ports = [{"name": "IN", "net": "input", "side": "Left"},
                 {"name": "OUT", "net": "out", "side": "Right"},
                 {"name": "RETURN", "net": "0", "side": "Bottom"}]
        divider.subcircuit_design = {"name": "DIVIDER", "ports": ports}
        name, definition = reusable_subcircuit(divider)
        wrapper = Project(subcircuits={name: definition})
        wrapper.parts = [Part("X1", "X", 0, 0, name, port_names=definition["ports"],
                              port_sides=definition["sides"])]
        label_pins(wrapper, ("input", "out", "0"))
        wrapper.subcircuit_design = {"name": "FILTER_BLOCK", "ports": ports}
        name, definition = reusable_subcircuit(wrapper)
        host = Project(subcircuits={name: definition})
        host.parts = [Part("X1", "X", 0, 0, name, port_names=definition["ports"],
                           port_sides=definition["sides"]),
                      Part("V1", "V", 12, 0, "DC 10"), Part("V2", "V", 12, 6, "DC 2"),
                      Part("R1", "R", 12, 12, "1meg")]
        label_pins(host, ("input", "out", "return"), ("input", "0"), ("return", "0"),
                   ("out", "return"))
        plot = self.run_circuit(Project.load(host.dump()))
        self.assertAlmostEqual(plot.rows[0][plot.names.index("v(out)")].real, 5.998, places=3)

    def test_imported_subcircuit_ports(self):
        project = imported_divider()
        self.assertRegex(netlist(project), r"X1 n\d+ 0 out DIVIDER")
        plot = self.run_circuit(project)
        self.assertAlmostEqual(plot.rows[0][plot.names.index("v(out)")].real, 10/3, delta=1e-6)

    def test_rc_ac(self):
        project = example()
        project.parts[2] = Part("C2", "C", 5, 0, "1u", 1)
        project.active = "AC sweep"
        profile = project.profiles[project.active]
        profile.start_hz = str(1/(2*math.pi*.001))
        profile.stop_hz = "2k"
        plot = self.run_circuit(project)
        transfer = plot.rows[0][plot.names.index("v(out)")]
        self.assertAlmostEqual(20*math.log10(abs(transfer)), -3.0103, delta=.1)
        self.assertAlmostEqual(math.degrees(math.atan2(transfer.imag, transfer.real)), -45, delta=.5)

    def test_rc_transient(self):
        project = example()
        project.parts[2] = Part("C2", "C", 5, 0, "1u", 1)
        project.parts[0].value = "PULSE 0 1 100u 1n 1n 10m 20m"
        project.active = "Transient"
        plot = self.run_circuit(project)
        column = plot.names.index("v(out)")
        row = min(plot.rows, key=lambda x: abs(x[0].real-.0011))
        expected = 1-math.exp(-(row[0].real-.0001000005)/.001)
        self.assertAlmostEqual(row[column].real, expected, delta=.005)

    def test_nonlinear_diode(self):
        project = example()
        project.parts[0].value = "DC 1"
        project.parts[2] = Part("D2", "D", 5, 0, "DDEFAULT", 1)
        plot = self.run_circuit(project)
        voltage = plot.rows[0][plot.names.index("v(out)")].real
        self.assertAlmostEqual((1-voltage)/1000, 1e-14*math.expm1(voltage/.025865), delta=2e-6)

    @unittest.skipUnless(QUCS_REFERENCE.is_file(), "Qucs-S upstream verification library unavailable")
    def test_qucs_1n4148(self):
        project = example()
        project.parts[0].value = "DC 1"
        entries = import_diodes(QUCS_REFERENCE, number)
        entry = next(entry for entry in entries if entry.name == "1N4148")
        name = project.register_qucs_diode(entry)
        project.parts[2] = Part("D2", "D", 5, 0, name, 1)
        project = Project.load(project.dump())
        self.assertIn("RS=68.6m", netlist(project))
        plot = self.run_circuit(project)
        voltage = plot.rows[0][plot.names.index("v(out)")].real
        self.assertTrue(.57 < voltage < .64, voltage)


if __name__ == "__main__":
    unittest.main()
