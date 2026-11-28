import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from circuit import Part, Profile, Project, connectivity, example, netlist, number, source_value
from results import Plot, analysis_deck, load_plot, named_measurements
from plot_math import evaluate, level_crossings
from qucs_library import import_diodes
from spice_library import read_library, read_subcircuits


class CircuitTests(unittest.TestCase):
    def test_saved_measurements_interpolate_and_preserve_single_run_errors(self):
        project = example()
        project.run_measurements = [["mean","Mean","v(out)","1","3"],
                                    ["sample","Value at X","v(out)","2",""],
                                    ["missing","RMS","v(absent)","",""]]
        restored = Project.load(project.dump())
        plot = Plot("Transient",["time","v(out)"],["time","voltage"],[[0,0],[4,8]])
        values = named_measurements(plot,restored.run_measurements)
        self.assertEqual(values["mean"]["value"],4)
        self.assertEqual(values["sample"]["value"],4)
        self.assertIn("unavailable",values["missing"]["error"])

    def test_design_variables_resolve_dependencies_and_reject_cycles(self):
        project = example()
        project.design_variables = {"LOAD":"2 * BASE","BASE":"1k"}
        project.parts[2].value = "{LOAD}"
        restored = Project.load(project.dump())
        self.assertIn("R2 out 0 2000",netlist(restored))
        restored.design_variables["BASE"] = "LOAD"
        with self.assertRaisesRegex(ValueError,"Cyclic design variable"):
            netlist(restored)

    def test_project_local_library_includes_and_cycle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root/"part.lib"
            index = root/"index.lib"
            model.write_text(".model TEST D(IS=1e-12 N=1)\n")
            index.write_text('.include "part.lib"\n')
            models,_,issues = read_library(index,number)
            self.assertEqual(models["TEST"]["parameters"]["IS"],"1e-12")
            self.assertEqual(issues,[])
            index.write_text('.include "../outside.lib"\n')
            with self.assertRaisesRegex(ValueError,"stay within"):
                read_library(index,number)
            index.write_text('.include "part.lib"\n')
            model.write_text('.include "index.lib"\n')
            with self.assertRaisesRegex(ValueError,"Cyclic SPICE include"):
                read_library(index,number)

    def test_simulation_options_validate_nodes_and_emit_cards(self):
        project = example()
        project.simulation_options = {"temperature":"42","ic":{"out":"2"},
                                      "nodeset":{"out":"3"},"reltol":"1m"}
        deck = netlist(Project.load(project.dump()))
        for card in (".temp 42",".ic V(out)=2",".nodeset V(out)=3",".options reltol=0.001"):
            self.assertIn(card,deck)
        project.simulation_options["ic"]["missing"] = "1"
        with self.assertRaisesRegex(ValueError,"Unknown or grounded ic node"):
            netlist(project)

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

    def test_rms_weights_irregular_x_and_crossings_follow_display_coordinates(self):
        plot = Plot("AC",["frequency","p"],["frequency","power"],[[1,0],[10,2],[100,2]])
        self.assertAlmostEqual(evaluate("RMS(p)",plot)[0].real,(372/99)**.5)
        self.assertAlmostEqual(evaluate("SampleRMS(p)",plot)[0].real,(8/3)**.5)
        signed = Plot("Transient",["time","y"],["time","voltage"],[[0,-1],[1,1]])
        self.assertAlmostEqual(evaluate("RMS(y)",signed)[0].real,1/math.sqrt(3))
        self.assertEqual(list(level_crossings([0,1,2],[0,2,0],1,0,2)),[.5,1.5])
        self.assertEqual(list(level_crossings([0,1,2],[0,2,0],3,0,2)),[])

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
            (path / "circuit.cir").write_text(analysis_deck(netlist(project),project.profiles[project.active]), encoding="ascii")
            run = subprocess.run([SOLVER, "-n", "-b", "circuit.cir"], cwd=path, timeout=25,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertTrue((path / "result.raw").exists(), run.stdout + run.stderr)
            return load_plot(path / "result.raw")

    def test_controlled_sources_and_behavioral_voltage(self):
        for kind,gain,expected in (("E","2",4),("U","1m",-2),("F","V1 2",4),
                                   ("H","V1 1k",-2),("B","V={FACTOR}*v(in)",4)):
            with self.subTest(kind=kind):
                project = Project(design_variables={"FACTOR":"2"})
                input_source = Part("V1","V",0,0,"DC 2")
                source = Part(kind+"1",kind,10,0,gain)
                load = Part("R1","R",20,0,"1k")
                bias = Part("R2","R",30,0,"1k")
                project.parts = [input_source,source,load,bias,Part("G1","G",60,0,"")]
                label_pins(project,("in","0"),
                           ("out","0","in","0") if kind in "EU" else ("out","0"),
                           ("out","0"),("in","0"))
                plot = self.run_circuit(project)
                self.assertAlmostEqual(plot.rows[0][plot.names.index("v(out)")].real,expected,places=6)

    def test_xspice_gates_translate_digital_levels(self):
        for gate,first,second,expected in (("AND",5,5,5),("AND",0,5,0),("XOR",5,5,0),
                                           ("OR",0,5,5),("XNOR",5,5,5),("NOR",0,0,5),
                                           ("NAND",5,5,0),("INV",0,5,5),("INV",5,5,0)):
            with self.subTest(gate=gate,first=first,second=second):
                inverter = gate=="INV"
                kind = "Z" if inverter else "Y"
                value = "5 1 3 1n" if inverter else gate+" 5 1 3 1n"
                project = Project(parts=[Part("V1","V",0,0,str(first)),Part("V2","V",10,0,str(second)),
                    Part(kind+"1",kind,20,0,value),Part("R1","R",30,0,"1k"),
                    Part("R2","R",40,0,"1k"),Part("G1","G",60,0,"")])
                signals = ("a","out") if inverter else ("a","b","out")
                label_pins(project,("a","0"),("b","0"),signals,("out","0"),("b","0"))
                plot = self.run_circuit(project)
                self.assertAlmostEqual(plot.rows[0][plot.names.index("v(out)")].real,expected,places=6)

    def test_coupled_inductors_match_transformer_turns_ratio(self):
        project = Project(parts=[Part("V1","V",0,0,"DC 0 AC 1"),
            Part("L1","L",10,0,"1m"),Part("L2","L",20,0,"4m"),
            Part("R1","R",30,0,"10k"),Part("G1","G",60,0,"")])
        label_pins(project,("in","0"),("in","0"),("out","0"),("out","0"))
        project.simulation_options = {"couplings":[["L1","L2","0.99"]]}
        project.active = "AC sweep"
        plot = self.run_circuit(project)
        ratio = abs(plot.rows[0][plot.names.index("v(out)")])
        self.assertAlmostEqual(ratio,.99*math.sqrt(4),places=4)

    def test_subcircuit_instance_override_preserves_other_instances(self):
        project = Project(subcircuits={"LOAD":{"ports":["IN","OUT","REF"],
                        "body":["R1 IN OUT {BIAS}","R2 OUT REF 1k"],"parameters":{"BIAS":"1k"}}})
        ports = ["IN","OUT","REF"]
        project.parts = [Part("V1","V",0,0,"DC 10"),Part("X1","X",14,0,"LOAD",port_names=ports),
                         Part("X2","X",28,0,"LOAD",port_names=ports),Part("R3","R",40,0,"1t"),
                         Part("R4","R",50,0,"1t"),Part("G1","G",60,0,"")]
        label_pins(project,("in","0"),("in","outa","0"),("in","outb","0"),("outa","0"),("outb","0"))
        project.instance_variables = {"X1":{"BIAS":"3k"}}
        plot = self.run_circuit(project)
        self.assertAlmostEqual(plot.rows[0][plot.names.index("v(outa)")].real,2.5,places=6)
        self.assertAlmostEqual(plot.rows[0][plot.names.index("v(outb)")].real,5,places=6)

    def test_advanced_analyses_against_rc_reference(self):
        project = example()
        project.parts[2].kind,project.parts[2].ref,project.parts[2].value = "C","C1","1u"
        project.parts[0].value = "DC 0 AC 1 SIN 0 1 1k 0 0 0"
        project.parts.append(Part("N2","N",-6,-2,"in"))
        project.active = "Study"
        project.profiles["Study"] = Profile("NOISE")
        noise = self.run_circuit(project)
        actual = noise.rows[0][noise.names.index("onoise_spectrum")].real
        expected = math.sqrt(4*1.380649e-23*300.15*1000)/math.hypot(1,2*math.pi*10*.001)
        self.assertAlmostEqual(actual/expected,1,places=3)
        project.profiles["Study"] = Profile("PZ")
        poles = self.run_circuit(project)
        self.assertAlmostEqual(poles.rows[0][poles.names.index("v(pole(1))")].real,-1000,places=4)
        project.profiles["Study"] = Profile("FOURIER",step="1u",stop="20m")
        spectrum = self.run_circuit(project)
        self.assertTrue(any(name.startswith("thd") for name in spectrum.names))
        self.assertLess(spectrum.rows[0][next(i for i,n in enumerate(spectrum.names) if n.startswith("thd"))].real,.1)
        project.profiles["Study"] = Profile("DISTO")
        distortion = self.run_circuit(project)
        self.assertEqual(distortion.names[0],"frequency")
        self.assertAlmostEqual(abs(distortion.rows[0][distortion.names.index("v(out)")]),0,places=10)

    def test_dc_transfer_function_of_divider(self):
        project = example()
        project.active = "Transfer"
        project.profiles["Transfer"] = Profile("TF")
        plot = self.run_circuit(project)
        values = dict(zip(plot.names,plot.rows[0]))
        self.assertAlmostEqual(values["v(transfer_function)"].real,.5)
        self.assertAlmostEqual(values["v(output_impedance_at_v(out))"].real,500)
        self.assertAlmostEqual(values["v(v1#input_impedance)"].real,2000)

    def test_dc_sensitivity_of_divider(self):
        project = example()
        project.profiles["Sensitivity"] = Profile("SENS")
        project.active = "Sensitivity"
        result = self.run_circuit(project)
        self.assertAlmostEqual(result.rows[0][result.names.index("v(r1)")].real,-.0025,places=7)

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
