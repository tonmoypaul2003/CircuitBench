import os
from pathlib import Path
import tempfile
import unittest
from test_workbench import QUCS_REFERENCE, SOLVER, STARTER_ROOT
from results import Plot

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPoint, QPointF, Qt, QTimer
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox
    from app import QApplication, Editor, example
except ModuleNotFoundError as error:
    if error.name != "PyQt6":
        raise
    QApplication = None


@unittest.skipUnless(QApplication, "Install PyQt6 to run GUI tests")
class GuiTests(unittest.TestCase):
    def test_study_forms_select_temperature_without_code(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QPlainTextEdit
        from design_studies import trial_setup

        self.window.project = example()
        defaults = {"target":"R1","min":"1k","max":"3k","count":21,"signal":"v(out)",
                    "goal":"5","pass_min":"4.5","pass_max":"5.5"}

        def submit(dialog):
            self.assertFalse(dialog.findChildren(QPlainTextEdit))
            combos = dialog.findChildren(QComboBox)
            combos[0].setCurrentIndex(combos[0].findData("@temperature"))
            self.assertIn("20",[field.text() for field in dialog.findChildren(QLineEdit)])
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
            return dialog.result()

        with patch.object(QDialog,"exec",submit):
            for mode in ("Monte Carlo","Optimization"):
                settings = trial_setup(self.window,mode,defaults,["R1","@temperature"])
                self.assertEqual(settings["target"],"@temperature")
                self.assertEqual(settings["max"],"80")

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = Editor()
        self.window.show()
        self.application.processEvents()

    def tearDown(self):
        self.window.saved = self.window.project.dump()
        self.window.close()

    def pixel(self, x, y):
        canvas = self.window.canvas
        origin = canvas.origin()
        return QPoint(round(origin.x() + x*canvas.cell), round(origin.y() + y*canvas.cell))

    def click(self, x, y):
        QTest.mouseClick(self.window.canvas, Qt.MouseButton.LeftButton, pos=self.pixel(x, y))

    def drag(self, start, end, modifier=Qt.KeyboardModifier.NoModifier):
        canvas = self.window.canvas
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, modifier, self.pixel(*start))
        QTest.mouseMove(canvas, self.pixel(*end))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, modifier, self.pixel(*end))

    def test_study_layout_roundtrip_and_report(self):
        from unittest.mock import patch
        from circuit import Project
        from studies import save_layout, report
        editor = self.window
        editor.project = example()
        editor.project.active = "Transient"
        plot = Plot("Transient", ["time", "v(out)"], ["time", "voltage"], [[0, 0], [1, 2]])
        editor.show_plot(plot, editor.project.dump())
        page = editor.plot_pages.currentWidget()
        page.add_expression("twice = multiply(v(out), 2)")
        page.add_subplot([2])
        page.tiles.setCurrentIndex(1)
        page.subplots[1].cursor_button.setChecked(False)
        save_layout(editor)
        saved = Project.load(editor.project.dump())
        editor.show_plot(plot, saved.dump())
        restored = editor.plot_pages.currentWidget()
        self.assertEqual(restored.plot.names[-1], "twice")
        self.assertEqual(restored.subplots[1].wave.columns, [2])
        self.assertFalse(restored.subplots[1].wave.cursor_enabled)
        self.assertEqual(restored.tiles.currentIndex(), 1)
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "report.pdf"
            with patch("studies.report_selection",return_value=({"Schematic and settings"},[restored])), patch("studies.QFileDialog.getSaveFileName", return_value=(str(pdf), "")):
                report(editor)
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))

    def test_transient_power_and_spectrum(self):
        import math
        from unittest.mock import patch
        from studies import measurements, transient_power
        editor = self.window
        editor.project = example()
        editor.project.active = "Transient"
        with patch("studies.choose", return_value="R1"), patch("studies.prompt", side_effect=lambda *a: a[-1]):
            transient_power(editor)
        power = editor.plot_pages.currentWidget()
        self.assertAlmostEqual(power.plot.rows[-1][1], .025)
        self.assertIn("average 0.025 W", power.message.text())
        rows = [[i/1024, math.sin(2*math.pi*32*i/1024) + .1*math.sin(2*math.pi*64*i/1024)]
                for i in range(1025)]
        editor.add_plot_page(Plot("Transient", ["time", "out"], ["time", "voltage"], rows), None)
        with patch("studies.choose", return_value="out"), patch("studies.prompt", return_value="0,1"):
            measurements(editor, True)
        spectrum = editor.plot_pages.currentWidget()
        self.assertAlmostEqual(spectrum.plot.rows[32][1], 1)
        self.assertIn("10%", spectrum.message.text())

    def test_parameter_sweep_preserves_project(self):
        import time
        from circuit import Profile
        from results import load_plot
        editor = self.window
        editor.project = example()
        editor.project.profiles["DC sweep"] = Profile("DC")
        editor.project.analysis_plan = dict(enabled=["DC sweep"], parametric=True,
                                            ref="R1",values=["1k","3k"],profile="Operating point")
        before = editor.project.dump()
        with tempfile.TemporaryDirectory() as folder:
            editor.path = Path(folder)/"Divider.cwb"
            editor.run()
            deadline = time.monotonic()+15
            while editor.process and time.monotonic()<deadline:
                QTest.qWait(10)
            self.assertIsNone(editor.process)
            runs = sorted(editor.runs_root().glob("*/*/Divider_*/status.json"))
            runs = [r.parent for r in runs]
            self.assertEqual(len(runs),3,editor.log.toPlainText())
            outputs = []
            for run in runs:
                result = load_plot(run/"result.raw")
                if "Parametric" in run.name:
                    outputs.append(result.rows[0][result.names.index("v(out)")].real)
            self.assertEqual(outputs,[5,2.5])
        self.assertEqual(editor.project.dump(),before)

    def test_plot_expression_crossings_cursor_and_last_page(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QPushButton
        editor = self.window
        page = editor.add_plot_page(Plot("Transient",["time","v(out)"],["time","voltage"],
                                         [[0,0],[1,2],[2,0]]),None,[1])
        editor.open_plots()
        self.application.processEvents()
        page.expression.setText("level = MAX(v(out))/2")
        page.expression.setFocus()
        QTest.keyClick(page.expression,Qt.Key.Key_Return)
        self.assertEqual(editor.plot_pages.count(),1)
        self.assertEqual(page.plot.names.count("level"),1)
        sub = page.subplots[0]
        with patch("plotting.QInputDialog.getItem",side_effect=[("v(out)",True),("level",True)]), patch("plotting.QInputDialog.getInt",return_value=(2,True)), patch("PyQt6.QtWidgets.QMessageBox.information"):
            sub.intersections()
        self.assertEqual([p[1] for p in sub.wave.pins],[.5,1.5])
        sub.wave.grab()
        QTest.keyClick(sub.wave,Qt.Key.Key_Right)
        buttons = {b.text():b for b in sub.findChildren(QPushButton)}
        buttons["Set A"].click()
        self.assertIsNotNone(sub.wave.reference)
        buttons["Set A"].click()
        self.assertIsNone(sub.wave.reference)
        buttons["Set B"].click()
        buttons["Set B"].click()
        self.assertIsNone(sub.wave.cursor)
        editor.close_plot_page(0)
        editor.duplicate_plot_page()
        self.assertEqual(editor.plot_pages.count(),1)
        self.assertFalse(editor.plot_pages.currentWidget().plot.rows)

    def test_set_b_locks_independently_of_a_and_live_cursor(self):
        from PyQt6.QtWidgets import QPushButton

        editor = self.window
        page = editor.add_plot_page(Plot("Transient",["time","v(out)"],["time","voltage"],
                                         [[0,0],[1,2],[2,0],[3,1]]),None,[1])
        editor.open_plots()
        self.application.processEvents()
        sub = page.subplots[0]
        wave = sub.wave
        wave.grab()
        rect = wave.screen_axes[0]
        start = QPoint(round(rect.left()+4),round(rect.center().y()))
        end = QPoint(round(rect.right()-4),round(rect.center().y()))
        buttons = {b.text():b for b in sub.findChildren(QPushButton)}
        QTest.mouseClick(wave,Qt.MouseButton.LeftButton,pos=start)
        buttons["Set A"].click()
        a = wave.reference
        buttons["Set B"].click()
        QTest.mouseClick(wave,Qt.MouseButton.LeftButton,pos=end)
        b = wave.fixed_b
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertNotEqual(a,b)
        QTest.mouseClick(wave,Qt.MouseButton.LeftButton,pos=start)
        self.assertEqual((wave.reference,wave.fixed_b),(a,b))
        buttons["Set B"].click()
        self.assertIsNone(wave.fixed_b)
        self.assertEqual(wave.reference,a)

    def test_measurement_pin_cap_prioritizes_selected_minimum(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox
        from studies import measurements

        editor = self.window
        page = editor.add_plot_page(
            Plot("Transient", ["time", "v(out)"], ["time", "voltage"],
                 [[0, 0], [1, 2], [2, 1], [3, 4]]), None, [1])
        editor.open_plots()

        def select_minimum(dialog):
            for check in dialog.findChildren(QCheckBox):
                check.setChecked(check.text() == "Minimum")
            return 1

        with patch("studies.choose", return_value="v(out)"), patch("studies.prompt", return_value="1,3"), \
                patch("studies.QDialog.exec", select_minimum), \
                patch("studies.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                patch("studies.QInputDialog.getInt", return_value=(1, True)):
            measurements(editor)
        self.assertEqual(page.wave.pins, [(1, 2, "Minimum")])

    def test_waveform_rms_only_pins_first_crossing(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox
        from studies import measurements

        editor = self.window
        page = editor.add_plot_page(Plot("AC",["frequency","i(l1)"],["frequency","current"],
                                         [[1,0],[10,2],[100,2]]), None,[1])
        editor.open_plots()

        def select_rms(dialog):
            for check in dialog.findChildren(QCheckBox):
                check.setChecked(check.text()=="RMS")
            return 1

        with (patch("studies.choose",return_value="i(l1)"),patch("studies.prompt",return_value="1,100"),
              patch("studies.QDialog.exec",select_rms),
              patch("studies.QMessageBox.question",return_value=QMessageBox.StandardButton.Yes),
              patch("studies.QInputDialog.getInt",return_value=(1,True))):
            measurements(editor)
        self.assertEqual([name for _,_,name in page.wave.pins],["RMS"])
        from plot_math import evaluate
        reading = evaluate("RMS(i(l1))",page.plot)[0].real
        self.assertAlmostEqual(page.wave.pins[0][1],reading/2)
        self.assertFalse(page.wave.pin_levels)
        self.assertIn(f"RMS: {reading:.7g}",page.message.text())

    def test_waveform_transient_cycle_measurements_offer_valid_edges(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox
        from studies import measurements

        editor = self.window
        page = editor.add_plot_page(Plot("Transient",["time","v(out)"],["time","voltage"],
                                         [[0,0],[1,2],[2,0],[3,2],[4,0]]), None,[1])
        editor.open_plots()

        def select_cycle(dialog):
            expected = {"90-10% fall (s)","Period (s)","Frequency (Hz)","Duty cycle (%)"}
            self.assertTrue(expected <= {check.text() for check in dialog.findChildren(QCheckBox)})
            for check in dialog.findChildren(QCheckBox):
                check.setChecked(check.text() in expected)
            return 1

        with (patch("studies.choose",return_value="v(out)"),patch("studies.prompt",return_value="0,4"),
              patch("studies.QDialog.exec",select_cycle),
              patch("studies.QMessageBox.question",return_value=QMessageBox.StandardButton.No)):
            measurements(editor)
        self.assertIn("Period (s): 2",page.message.text())
        self.assertIn("Frequency (Hz): 0.5",page.message.text())
        self.assertIn("Duty cycle (%): 50",page.message.text())

    def test_waveform_measurements_integrate_visible_segments_only(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox
        from studies import measurements

        editor = self.window
        page = editor.add_plot_page(
            Plot("Transient", ["time", "v(out)"], ["time", "voltage"],
                 [[0, 0], [1, 10], [2, 0]]), None, [1])
        page.subplots[0].set_axis_limits((0, 2), (0, 2))
        editor.open_plots()
        with patch("studies.choose", return_value="v(out)"), patch("studies.prompt", return_value="0,2"), \
                patch("studies.QDialog.exec", return_value=1), \
                patch("studies.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            measurements(editor)
        self.assertIn("Maximum: 2, Mean: 1, RMS: 5.773503", page.message.text())
        self.assertFalse(page.wave.pins)
        for console in (editor.log,editor.results_log):
            output = console.toPlainText()
            self.assertIn("Waveform measurements",output)
            self.assertIn("Maximum: 2",output)
            self.assertIn("Mean: 1",output)
            self.assertIn("RMS: 5.773503",output)
            self.assertNotIn("Sample count:",output)

    def test_nested_temperature_sweep_records_axes_and_values(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import json
        from PyQt6.QtCore import QElapsedTimer

        editor = self.window
        editor.project = example()
        editor.project.design_variables = {"LOAD":"1k"}
        editor.project.parts[2].value = "{LOAD}"
        editor.project.analysis_plan = {"enabled":[],"parametric":True,"profile":"Operating point",
                                       "ref":"$LOAD","values":["1k","3k"],
                                       "second_ref":"@temperature","second_values":["20","50"]}
        with TemporaryDirectory(dir=".") as directory:
            editor.path = Path(directory).resolve()/"sweep.cwb"
            editor.run()
            timer = QElapsedTimer()
            timer.start()
            while editor.process is not None and timer.elapsed()<7000:
                self.application.processEvents()
                QTest.qWait(15)
            self.assertIsNone(editor.process)
            records = [json.loads((folder/"dataset.json").read_text()) for folder in editor.sweep_runs]
            self.assertEqual([r["sweep"] for r in records],[
                {"$LOAD":"1k","@temperature":"20"},{"$LOAD":"1k","@temperature":"50"},
                {"$LOAD":"3k","@temperature":"20"},{"$LOAD":"3k","@temperature":"50"}])
            self.assertEqual([r["samples"] for r in records],[1,1,1,1])
            self.assertIn("v(out)",[v["name"] for v in records[0]["vectors"]])
            self.assertEqual(editor.project.design_variables,{"LOAD":"1k"})
            self.assertEqual(editor.plot_pages.currentWidget().plot.rows,
                             [[1,5],[2,5],[3,7.5],[4,7.5]])

    def test_design_trials_record_reproducible_yield_and_optimization(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from design_studies import run_trials,trial_values
        from desktop04 import history_root

        editor = self.window
        editor.project = example()
        settings = dict(target="R1",min="1k",max="3k",count=3,signal="v(out)",goal="2.5",
                        seed=13,distribution="uniform",pass_min="2",pass_max="6")
        with TemporaryDirectory(dir=".") as directory:
            editor.path = Path(directory).resolve()/"study.cwb"
            with patch("design_studies.trial_setup",return_value=settings):
                run_trials(editor,"Monte Carlo")
                run_trials(editor,"Optimization")
            records = {record["mode"]:record for record in (
                json.loads(path.read_text()) for path in history_root(editor).glob("*/*/study.json"))}
            self.assertEqual([row[0] for row in records["Monte Carlo"]["samples"]],trial_values(settings,"Monte Carlo"))
            self.assertEqual([row[0] for row in records["Optimization"]["samples"]],[1000,2000,3000])
            for (_,value),expected in zip(records["Optimization"]["samples"],[5,10/3,2.5]):
                self.assertAlmostEqual(value,expected,places=8)
            self.assertIn("Best tested value 3000",editor.plot_pages.currentWidget().message.text())
            self.assertEqual(editor.project.parts[1].value,"1k")

    @unittest.skipUnless(SOLVER, "Bundled ngspice is required")
    def test_live_tuning_previews_without_editing_until_applied(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QDialog, QLabel, QSlider
        from design_studies import tune

        window = self.window
        window.solver = str(SOLVER)
        window.project = example()
        previews = []

        def interact(dialog, accept):
            def change():
                dialog.findChild(QSlider).setValue(100)

            def inspect_preview():
                labels = [label.text() for label in dialog.findChildren(QLabel)]
                if any("v(out) = 7.5" in label for label in labels):
                    previews.append(window.project.parts[2].value)
                    dialog.done(int(accept))
            poll = QTimer(dialog)
            poll.setInterval(20)
            poll.timeout.connect(inspect_preview)
            dialog.finished.connect(poll.stop)
            deadline = QTimer(dialog)
            deadline.setSingleShot(True)
            deadline.timeout.connect(dialog.reject)
            dialog.finished.connect(deadline.stop)
            QTimer.singleShot(0, change)
            poll.start()
            deadline.start(5000)
            return original_exec(dialog)

        original_exec = QDialog.exec
        with (patch("design_studies.QInputDialog.getItem", return_value=("R2", True)),
              patch("design_studies.QInputDialog.getText", return_value=("1k, 3k", True))):
            for accept in (False, True):
                with patch.object(QDialog, "exec", lambda dialog: interact(dialog, accept)):
                    tune(window)
                self.assertEqual(float(window.project.parts[2].value) if accept
                                 else window.project.parts[2].value, 3000 if accept else "1k")
        self.assertEqual(previews, ["1k", "1k"])

    def test_intersections_respect_visible_limits_and_cap(self):
        from unittest.mock import patch
        page = self.window.add_plot_page(
            Plot("Transient",["time","v(out)","level"],["time","voltage","voltage"],
                 [[0,0,1],[1,2,1],[2,0,1],[3,2,1],[4,0,1]]),None,[1,2])
        self.window.open_plots()
        sub = page.subplots[0]
        sub.set_axis_limits((1,4),(0,2))
        sub.wave.grab()
        with patch("plotting.QInputDialog.getItem",side_effect=[("v(out)",True),("level",True)]), \
                patch("plotting.QInputDialog.getInt",return_value=(1,True)), \
                patch("PyQt6.QtWidgets.QMessageBox.information"):
            sub.intersections()
        self.assertEqual([pin[1] for pin in sub.wave.pins],[1.5])
        sub.delete_pins({0})
        self.assertFalse(sub.wave.pins)

    def test_source_visibility_is_saved_without_changing_netlist(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox
        from circuit import Project, netlist

        editor = self.window
        editor.project = example()
        source = editor.project.parts[0]
        source.value = "DC 10 AC 2 15 SIN 1 2 1k 0 0 30"
        editor.refresh()
        editor.select(source)
        before = netlist(editor.project)

        def edit_dialog(dialog):
            controls = {check.accessibleName():check for check in dialog.findChildren(QCheckBox)}
            controls["Show AC phase (°) on canvas"].setChecked(False)
            controls["Show Damping (1/s) on canvas"].setChecked(False)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
            return 1

        with patch.object(QDialog,"exec",edit_dialog):
            editor.edit_source()

        restored = Project.load(editor.project.dump())
        self.assertEqual(restored.parts[0].label_style["hidden_parameters"],["SIN:4","ac_phase"])
        self.assertEqual(netlist(restored),before)
        editor.canvas.grab()
        keys = {key for key,_ in editor.canvas.label_hits}
        self.assertIn(("part",0,"param:ac"),keys)
        self.assertIn(("part",0,"param:SIN:2"),keys)
        self.assertNotIn(("part",0,"param:ac_phase"),keys)
        self.assertNotIn(("part",0,"param:SIN:4"),keys)

    def test_model_parameter_visibility_is_per_part(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QDialog
        from circuit import Project

        editor = self.window
        editor.project.add("D",(1,1))
        editor.project.add("D",(5,1))
        diode,other = editor.project.parts
        editor.refresh()
        editor.select(diode)

        def edit_dialog(dialog):
            controls = {item.text():item for item in dialog.findChildren(QCheckBox)}
            controls["Show individual parameters"].setChecked(True)
            controls[next(label for label in controls if label.startswith("IS ="))].setChecked(False)
            return 1

        with patch.object(QDialog,"exec",edit_dialog):
            editor.edit_label_appearance()

        restored = Project.load(editor.project.dump())
        self.assertEqual(restored.parts[0].label_style["hidden_parameters"],["model:IS"])
        self.assertNotIn("hidden_parameters",other.label_style)

    def test_schematic_label_drag_preserves_connections(self):
        from circuit import Project, netlist
        self.window.project = example()
        self.window.project.markers = [{"kind":"V","points":[[5,-2]]}]
        self.window.refresh()
        canvas = self.window.canvas
        before = netlist(self.window.project)
        self.window.project.parts[1].label_style["offset"] = [0.25,0]
        for target in (("part",1,"name"),("part",1,"value"),("marker",0)):
            canvas.grab()
            hit = next(r for key,r in canvas.label_hits if key==target)
            start = hit.center().toPoint()
            end = start+QPoint(30,18)
            QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(canvas,end)
            QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=end)
        restored = Project.load(self.window.project.dump())
        style = restored.parts[1].label_style
        self.assertEqual(style["offset"],[0.25,0])
        self.assertGreater(style["name_offset"][0],0.25)
        self.assertGreater(style["value_offset"][0],0.25)
        self.assertGreater(restored.markers[0]["label_style"]["offset"][0],0)
        self.assertEqual(netlist(restored),before)

    def test_click_jitter_does_not_move_part(self):
        self.window.project.add("R", (0, 0))
        self.window.refresh()
        canvas = self.window.canvas
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=self.pixel(.45, 0))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=self.pixel(.60, 0))
        self.assertEqual((self.window.selected.x, self.window.selected.y), (0, 0))
        self.assertFalse(self.window.undo_stack)

    def test_bundled_opamp_is_searchable_and_embedded_on_placement(self):
        self.window.search.setText("opamp")
        self.assertEqual(self.window.library.count(), 1)
        self.assertNotIn("OP07", self.window.project.subcircuits)
        self.window.choose_first_part()
        self.click(0, 0)
        part = self.window.project.parts[0]
        self.assertEqual((part.kind, part.value, part.port_names), ("X", "OP07", ["3", "2", "7", "4", "6"]))
        self.assertIn("OP07", self.window.project.subcircuits)
        self.assertEqual(part.pins(), [(-3, -1), (-3, 1), (0, -3), (0, 3), (3, 0)])
        self.assertEqual(type(self.window.project).load(self.window.project.dump()).parts[0].pins(), part.pins())
        self.assertFalse(self.window.canvas.grab().isNull())
        self.window.restore(self.window.undo_stack, self.window.redo_stack)
        self.assertNotIn("OP07", self.window.project.subcircuits)

    def test_reusable_cwb_import_and_symbol(self):
        from subcircuit_designer import SubcircuitDesigner

        source = example()
        source.subcircuit_design = {"name": "DEMO_BLOCK", "ports": [
            {"name": "OUT", "net": "out", "side": "Right"},
            {"name": "RETURN", "net": "0", "side": "Bottom"},
        ]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "demo.cwb"
            source.save(path)
            dialog = SubcircuitDesigner(self.window, source, path)
            self.assertEqual(dialog.rows()[0]["net"], "out")
            self.assertFalse(dialog.preview.grab().isNull())
            self.assertFalse(dialog.source_preview.grab().isNull())
            self.assertEqual(self.window.import_library_files([path]), (1, []))
            self.window.search.setText("DEMO_BLOCK")
            self.window.choose_first_part()
            self.click(0, 0)
            part = self.window.project.parts[0]
            self.assertEqual(part.port_names, ["OUT", "RETURN"])
            self.assertEqual(part.pins(), [(3, 0), (0, 3)])
            self.assertFalse(self.window.canvas.grab().isNull())
            self.assertEqual(type(source).load(self.window.project.dump()).parts[0].pins(), part.pins())

    def test_box_select_move_delete_and_undo(self):
        self.window.replace(example())
        original = self.window.project.dump()
        self.drag((-9, -4), (7, 4))
        self.assertEqual(len(self.window.selected_parts), 5)
        self.assertEqual(len(self.window.selected_wires), 3)
        self.drag((0, -2), (1, -1))
        self.assertEqual((self.window.project.parts[1].x, self.window.project.parts[1].y), (1, -1))
        self.assertIn("R2 out 0 1k", self.window.preview())
        self.window.delete()
        self.assertFalse(self.window.project.parts)
        self.assertFalse(self.window.project.wires)
        self.window.restore(self.window.undo_stack, self.window.redo_stack)
        self.window.restore(self.window.undo_stack, self.window.redo_stack)
        self.assertEqual(self.window.project.dump(), original)

    def test_select_shift_drag_from_component(self):
        self.window.replace(example())
        self.click(-6, 0)
        self.assertEqual(self.window.selected_parts, {0})
        self.drag((0, -2), (6, 1), Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.window.selected_parts, {0, 1, 2, 3})
        canvas = self.window.canvas
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                         self.pixel(0, -2))
        self.assertEqual(self.window.selected_parts, {0, 2, 3})
        self.assertEqual(set(self.window.mode_actions), {"Select", "Wire", "Junction"})

    def test_bulk_import_and_place_multiple_device_families(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = (Path(folder)/name for name in ("bjt.lib", "mos.mod"))
            first.write_text(".MODEL 2NTEST NPN (IS=1e-14\n+ BF=120)\n")
            second.write_text(".MODEL NMTEST NMOS (LEVEL=1 VTO=1 KP=1m)\n")
            self.assertEqual(self.window.import_library_files([first, second]), (2, []))
        self.window.search.setText("2NTEST")
        self.window.choose_first_part()
        self.click(0, 0)
        self.window.search.setText("NMTEST")
        self.window.choose_first_part()
        self.click(10, 0)
        self.assertEqual([part.port_names for part in self.window.project.parts],
                         [["C", "B", "E"], ["D", "G", "S", "B"]])

    @unittest.skipUnless(STARTER_ROOT.is_dir(), "Optional upstream model pack is unavailable")
    def test_import_local_starter_pack(self):
        self.window.import_starter_pack()
        self.assertIn("Imported 8 new definitions", self.window.statusBar().currentMessage())
        self.assertIn("BC546B", self.window.project.device_models)
        self.assertIn("OP07", self.window.project.subcircuits)

    def test_place_edit_wire_and_undo(self):
        self.window.set_mode("R")
        self.assertEqual(self.window.canvas.cursor().shape(), Qt.CursorShape.CrossCursor)
        self.click(0, 0)
        self.assertEqual(len(self.window.project.parts), 1)
        self.window.value.setText("2k")
        self.window.apply.click()
        self.assertEqual(self.window.project.parts[0].value, "2k")
        self.window.set_mode("Wire")
        self.assertEqual(self.window.canvas.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.click(2, 0)
        self.click(4, 2)
        self.assertEqual(len(self.window.project.wires), 2)
        self.window.restore(self.window.undo_stack, self.window.redo_stack)
        self.assertFalse(self.window.project.wires)
        self.window.restore(self.window.redo_stack, self.window.undo_stack)
        self.assertEqual(len(self.window.project.wires), 2)

    def test_component_list_selects_and_reveals_component(self):
        editor = self.window
        editor.project = example()
        editor.project.parts[0].x = 1000
        editor.refresh()
        self.assertEqual(editor.component_list.count(),3)
        editor.component_list.setCurrentRow(0)
        part = editor.project.parts[0]
        self.assertIs(editor.selected,part)
        self.assertIs(editor.component_highlight,part)
        self.assertEqual(editor.ref.text(),"V1")
        self.assertEqual(editor.canvas.offset.x(),-1000*editor.canvas.cell)
        self.click(part.x,part.y)
        self.assertIsNone(editor.component_highlight)

    def test_measurement_dialog_prefills_vector_and_analysis_interval(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QPushButton, QTableWidget
        from design_studies import edit_run_measurements

        editor = self.window
        editor.project = example()
        editor.project.active = "AC sweep"
        def submit(dialog):
            next(button for button in dialog.findChildren(QPushButton) if button.text()=="Add row").click()
            table = dialog.findChild(QTableWidget)
            self.assertEqual(table.cellWidget(0,2).currentText(),"v(out)")
            self.assertEqual(table.cellWidget(0,3).text(),"10")
            self.assertEqual(table.cellWidget(0,4).text(),"100k")
            table.cellWidget(0,1).setText("Peak output")
            table.cellWidget(0,4).setText("50k")
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
            return dialog.result()
        with patch.object(QDialog,"exec",submit):
            edit_run_measurements(editor)
        self.assertEqual(editor.project.run_measurements,[["Peak output","Maximum","v(out)","10","50k"]])

    def test_simulation_options_offer_nodes_and_disable_irrelevant_fields(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTableWidget
        from design_studies import edit_options

        editor = self.window
        editor.project = example()
        def submit(dialog):
            table = dialog.findChild(QTableWidget)
            self.assertFalse(table.cellWidget(0,1).isEnabled())
            self.assertEqual(table.cellWidget(0,3).placeholderText(),"27")
            table.cellWidget(0,0).setCurrentText("Initial voltage")
            table.cellWidget(0,1).setCurrentText("out")
            self.assertTrue(table.cellWidget(0,1).isEnabled())
            self.assertFalse(table.cellWidget(0,2).isEnabled())
            table.cellWidget(0,3).setText("2")
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
            return dialog.result()
        with patch.object(QDialog,"exec",submit):
            edit_options(editor)
        self.assertEqual(editor.project.simulation_options,{"ic":{"out":"2"}})

    @unittest.skipUnless(SOLVER, "Bundled ngspice is required")
    def test_fourier_harmonics_use_a_table_and_diagnostics_are_copyable(self):
        from PyQt6.QtWidgets import QTableWidget
        from circuit import Profile
        from smoke_test import run_profile
        editor = self.window
        editor.solver = str(SOLVER)
        editor.project = example()
        editor.project.parts[0].value = "DC 0 SIN 0 1 1k 0 0 0"
        editor.project.profiles["Fourier test"] = Profile("FOURIER",stop="10m")
        editor.project.run_measurements = [["Output peak","Maximum","v(out)","",""]]
        with tempfile.TemporaryDirectory() as directory:
            run_profile(editor,self.application,Path(directory),"Fourier test")
            page = editor.plot_pages.currentWidget()
            self.assertEqual(page.plot.names,["frequency","harmonic magnitude"])
            self.assertEqual(page.wave.x_scale,"Linear")
            self.assertEqual(page.plot.rows[1][0],1000)
            self.assertIsNone(page.findChild(QTableWidget))
            table_page = editor.plot_pages.widget(editor.plot_pages.currentIndex()-1)
            table = table_page.findChild(QTableWidget)
            self.assertGreaterEqual(table.rowCount(),9)
            self.assertEqual(table.item(1,1).text(),"1000")
            log = editor.log.toPlainText()
            self.assertIn("Fourier test — completed",log)
            self.assertIn("Output peak = ",log)
            self.assertNotIn("Norm. Mag",log)
            self.assertIn("Run: ",log)
            results = editor.results_log.toPlainText()
            self.assertNotIn("time [time]",results)
            self.assertIn("Waveform channels",results)
            self.assertIn("Output peak = ",results)
            self.assertIn("Harmonic\tFrequency (Hz)",results)
            self.assertIn("1\t1000",results)
            editor.close_plot_page(editor.plot_pages.indexOf(page))
            self.assertIsNone(editor.wave.plot)
            editor.close_plot_page(editor.plot_pages.indexOf(table_page))
            editor.log.show_timestamps(True)
            self.assertRegex(editor.log.toPlainText(),r"\[\d{2}:\d{2}:\d{2}\] Fourier test")
            editor.log.show_timestamps(False)
            self.assertEqual(editor.log.toPlainText(),log)
            editor.log.clear()
            self.assertEqual(editor.log.toPlainText(),"")
            editor.log.show_timestamps(True)
            self.assertEqual(editor.log.toPlainText(),"")

    def test_example_preview_and_render(self):
        self.window.replace(example())
        self.assertIn("R2 out 0 1k", self.window.preview())
        self.assertFalse(self.window.canvas.grab().isNull())

    def test_sheet_first_workspace_and_context_panels(self):
        self.assertFalse(self.window.library_dock.isVisible())
        self.assertFalse(self.window.properties_dock.isVisible())
        self.assertFalse(self.window.output_dock.isVisible())
        self.window.focus_library()
        self.assertTrue(self.window.library_dock.isVisible())
        self.window.replace(example())
        self.window.select(self.window.project.parts[0])
        self.window.edit_selection()
        self.assertTrue(self.window.properties_dock.isVisible())
        self.window.fit_view()
        self.assertGreater(self.window.canvas.cell, 0)
        self.window.preview()
        self.assertTrue(self.window.output_dock.isVisible())

    @unittest.skipUnless(QUCS_REFERENCE.is_file(), "Qucs-S verification library unavailable")
    def test_find_and_place_qucs_diode(self):
        self.assertFalse(self.window.catalog)
        self.assertGreater(self.window.import_library_files([QUCS_REFERENCE])[0], 0)
        self.window.category.setCurrentIndex(2)
        self.window.search.setText("1N4148")
        self.assertGreater(self.window.library.count(), 0)
        self.window.choose_first_part()
        self.click(0, 0)
        part = self.window.project.parts[0]
        self.assertEqual(part.kind, "D")
        self.assertIn(part.value, self.window.project.qucs_models)
        self.window.set_mode("Wire")
        self.assertTrue(self.window.mode_actions["Wire"].isChecked())
        self.assertIsNone(self.window.placement_model)

    def test_ac_source_and_independent_plot_pages(self):
        self.window.search.setText("AC current source")
        self.window.choose_first_part()
        self.click(0, 0)
        self.assertEqual(self.window.project.parts[0].kind, "I")
        self.assertEqual(self.window.project.parts[0].value, "DC 0 AC 1 0")
        plot = Plot("AC Analysis", ["frequency", "v(in)", "v(out)"],
                    ["frequency", "voltage", "voltage"], [[10, 1, .8-.1j], [100, 1, .3-.4j]])
        self.window.show_plot(plot, self.window.project.dump())
        first = self.window.plot_pages.currentWidget()
        self.assertEqual(first.wave.columns, [2])
        first.traces.item(1).setCheckState(Qt.CheckState.Unchecked)
        first.traces.item(0).setCheckState(Qt.CheckState.Checked)
        self.window.duplicate_plot_page()
        second = self.window.plot_pages.currentWidget()
        second.presentation.setCurrentIndex(1)
        second.traces.item(0).setCheckState(Qt.CheckState.Unchecked)
        second.traces.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertEqual((first.wave.presentation, first.wave.columns), (0, [1]))
        self.assertEqual((second.wave.presentation, second.wave.columns), (1, [2]))
        readouts = []
        second.wave.readout.connect(readouts.append)
        self.application.processEvents()
        second.wave.move_cursor(74)
        self.assertFalse(second.wave.grab().isNull())
        self.assertTrue(any("v(out)" in text and "frequency = 10" in text for text in readouts))

    def test_subplot_expression_pin_and_export(self):
        plot = Plot("AC Analysis", ["frequency", "v(in)", "v(out)"],
                    ["frequency", "voltage", "voltage"], [[10, 1, .8-.1j], [100, 1, .3-.4j]])
        self.window.show_plot(plot, self.window.project.dump())
        page = self.window.plot_pages.currentWidget()
        first = page.subplots[0]
        second = page.add_subplot([])
        column = page.add_expression("gain = db(v(out)/v(in))")
        self.assertNotIn(column, first.wave.columns)
        self.assertIn(column, second.wave.columns)
        self.assertEqual(second.pin_at(column, 99, "Cutoff"), 100)
        second.wave.title, second.wave.y_label = "Filtered response", "Gain (dB)"
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            single, full = directory / "single.png", directory / "full.png"
            page.export(single, whole_page=False)
            page.export(full)
            page.export(directory / "full.pdf")
            self.assertEqual(single.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertGreater(int.from_bytes(full.read_bytes()[20:24]),
                               int.from_bytes(single.read_bytes()[20:24]))
            self.assertTrue((directory / "full.pdf").read_bytes().startswith(b"%PDF-"))
        page.remove_subplot()
        self.assertEqual(page.subplots, [first])

    def test_grid_zoom_graph_pin_and_maximize(self):
        plot = Plot("Transient", ["time", "v(low)", "v(high)"], ["time", "voltage", "voltage"],
                    [[0, 1, 8], [.5, 2, 9], [1, 3, 10]])
        self.window.show_plot(plot, self.window.project.dump())
        page = self.window.plot_pages.currentWidget()
        subplot = page.subplots[0]
        subplot.traces.item(1).setCheckState(Qt.CheckState.Checked)
        subplot.zoom_slider.setValue(200)
        self.application.processEvents()
        self.assertEqual(subplot.wave.zoom, 200)
        self.assertEqual(subplot.zoom_readout.text(), "200%")
        with_grid = subplot.wave.grab().toImage()
        subplot.grid_density.setValue(12)
        self.assertEqual(subplot.wave.grid_density, 12)
        self.assertNotEqual(with_grid, subplot.wave.grab().toImage())
        subplot.grid_button.click()
        self.assertFalse(subplot.wave.grid_visible)
        self.assertFalse(subplot.grid_density.isEnabled())
        self.assertNotEqual(with_grid, subplot.wave.grab().toImage())
        subplot.set_axis_limits((.25, .75), (0, 10))
        with self.assertRaises(ValueError):
            subplot.set_axis_limits((2, 1), None)
        subplot.wave.grab()
        self.assertEqual(subplot.wave.screen_axes[4:], (.25, .75, 0, 10))
        self.assertFalse(subplot.auto_pin_button.isEnabled())
        bounds = subplot.wave.screen_axes[0]
        point = QPointF(bounds.center().x(), bounds.top()+bounds.height()*.1)
        QTest.mouseClick(subplot.wave, Qt.MouseButton.LeftButton, pos=point.toPoint())
        self.assertEqual(subplot.wave.cursor, 1)
        self.assertFalse(subplot.wave.pins)
        subplot.pin_shape.setCurrentText("Triangle")
        subplot.auto_pin_button.click()
        self.assertEqual(subplot.wave.pin_shapes[-1], "Triangle")
        self.assertEqual(subplot.wave.pins[-1], (2, 1, ""))
        self.assertEqual(subplot.wave.cursor, 1)
        subplot.cursor_button.click()
        self.assertIsNone(subplot.wave.cursor)
        self.assertFalse(subplot.auto_pin_button.isEnabled())
        QTest.mouseClick(subplot.wave, Qt.MouseButton.LeftButton, pos=point.toPoint())
        self.assertIsNone(subplot.wave.cursor)
        self.assertEqual(len(subplot.wave.pins), 1)
        subplot.cursor_button.click()
        QTest.mouseClick(subplot.wave, Qt.MouseButton.LeftButton, pos=point.toPoint())
        self.assertEqual(subplot.wave.cursor, 1)
        self.assertEqual(subplot.pin_at(1, .97, "End"), 1)
        subplot.wave.background = QColor("#27394d")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resized.png"
            page.export(path, whole_page=False)
            image = QImage(str(path))
            self.assertEqual(image.height(), 810)
            self.assertEqual(image.pixelColor(10, 100), QColor("#27394d"))
        self.assertTrue(self.window.plot_window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)

    def test_place_source_and_voltage_markers(self):
        self.window.replace(example())
        self.window.search.setText("Vsin")
        self.window.choose_first_part()
        self.click(0, 4)
        self.assertTrue(self.window.project.parts[-1].value.startswith("SIN "))
        self.window.set_mode("Mark VD")
        self.click(-2, -2)
        self.click(5, -2)
        self.assertEqual(self.window.project.markers, [{"kind": "VD", "points": [[-2, -2], [5, -2]]}])
        self.window.remove_last_marker()
        self.assertFalse(self.window.project.markers)
        self.window.restore(self.window.undo_stack, self.window.redo_stack)
        self.assertEqual(len(self.window.project.markers), 1)

    def test_select_and_delete_measurement_markers(self):
        self.window.replace(example())
        self.window.project.markers = [
            {"kind": "V", "points": [[5, -2]]},
            {"kind": "VD", "points": [[-6, -2], [5, 2]]},
            {"kind": "I", "ref": "R2", "pin": 0},
        ]
        self.window.refresh()
        self.click(-6, -2)
        self.assertEqual(self.window.selected_markers, {1})
        self.assertTrue(self.window.marker_list.item(1).isSelected())
        QTest.mouseClick(self.window.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                         self.pixel(5, -2))
        self.assertEqual(len(self.window.selected_markers), 2)
        QTest.keyClick(self.window.canvas, Qt.Key.Key_Delete)
        self.assertEqual(len(self.window.project.markers), 1)
        self.window.restore(self.window.undo_stack, self.window.redo_stack)
        self.assertEqual(len(self.window.project.markers), 3)
        self.window.marker_dock.show()
        item = self.window.marker_list.item(0)
        QTest.mouseClick(self.window.marker_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.window.marker_list.visualItemRect(item).center())
        self.assertEqual(self.window.selected_markers, {0})
        QTest.keyClick(self.window.marker_list, Qt.Key.Key_Delete)
        self.assertEqual([marker["kind"] for marker in self.window.project.markers], ["VD", "I"])

    def test_xy_axes_and_tiled_layout(self):
        plot = Plot("DC sweep", ["sweep", "v(d)", "i(v1)"], ["voltage", "voltage", "current"],
                    [[0j, 0j, 0j], [1+0j, .6+0j, .001+0j], [2+0j, .7+0j, .01+0j]])
        page = self.window.add_plot_page(plot, None, [2])
        subplot = page.subplots[0]
        subplot.x_axis.setCurrentIndex(1)
        subplot.wave.resize(550, 320)
        subplot.wave.grab()
        self.assertEqual(subplot.wave.screen_axes[1], [0, .6, .7])
        self.assertEqual(subplot.pin_at(2, .58), .6)
        subplot.wave.trace_styles[2] = ("Dashed", 3)
        self.assertEqual(subplot.wave.trace_pen(2, None).widthF(), 3)
        subplot.wheel_zoom(1, .75, .5)
        self.assertEqual(subplot.wave.zoom, 125)
        subplot.set_axis_limits(None, None)
        self.assertEqual(subplot.wave.zoom, 100)
        subplot.zoom_out_button.click()
        self.assertEqual(subplot.wave.zoom, 80)
        subplot.wave.grab()
        self.assertLess(subplot.wave.screen_axes[4], 0)
        self.assertGreater(subplot.wave.screen_axes[5], .7)
        subplot.zoom_in_button.click()
        self.assertEqual(subplot.wave.zoom, 100)
        self.assertEqual(self.window.plot_window.windowTitle(), "CircuitBench Simulation")
        page.add_subplot([1])
        page.tiles.setCurrentIndex(1)
        self.assertIs(page.stack.itemAtPosition(0, 1).widget(), page.subplots[1])

    def test_drop_on_wire_inserts_component_and_keeps_undo(self):
        project = self.window.project
        project.wires.append(((0, -6), (0, 6)))
        self.window.set_mode("R")
        self.assertTrue(self.window.drop_part((0, 0)))
        self.assertEqual(project.parts[0].pins(), [(0, -2), (0, 2)])
        self.assertEqual(project.wires, [((0, -6), (0, -2)), ((0, 6), (0, 2))])
        self.window.restore(self.window.undo_stack, self.window.redo_stack)
        self.assertFalse(self.window.project.parts)
        self.assertEqual(self.window.project.wires, [((0, -6), (0, 6))])

    def test_voltage_label_appearance_keeps_readings_and_round_trips(self):
        self.window.replace(example())
        project = self.window.project
        self.window.readings = {"nodes": [{"net": "out", "point": [5, -2], "value": 5}], "currents": []}
        self.window.readings_revision = project.dump(include_label_styles=False)
        self.window.refresh()
        style = {"color": "#ae2060", "size": 20, "x": 35, "y": -25}
        project.voltage_labels["*"] = style.copy()

        def set_node_style():
            dialog = self.application.activeModalWidget()
            dialog.findChild(QComboBox).setCurrentIndex(1)
            dialog.findChildren(QCheckBox)[0].setChecked(False)
            dialog.findChildren(QSpinBox)[0].setValue(24)
            dialog.accept()

        QTimer.singleShot(0, set_node_style)
        self.window.edit_voltage_labels()
        self.assertEqual(project.voltage_labels["out"], {**style, "size": 24})
        self.assertEqual(self.window.active_readings["nodes"][0]["value"], 5)
        self.assertFalse(self.window.canvas.grab().isNull())
        self.assertEqual(type(project).load(project.dump()).voltage_labels, project.voltage_labels)
        self.window.restore(self.window.undo_stack, self.window.redo_stack)
        self.assertEqual(self.window.project.voltage_labels, {"*": style})
        self.assertIsNotNone(self.window.active_readings)

    @unittest.skipUnless(SOLVER, "Set NGSPICE to run the GUI simulation workflow")
    def test_run_displays_result_and_persists_snapshot(self):
        self.window.replace(example())
        with tempfile.TemporaryDirectory() as folder:
            self.window.path = Path(folder) / "divider.cwb"
            self.window.run_action.trigger()
            for _ in range(100):
                if self.window.process is None:
                    break
                QTest.qWait(50)
            if self.window.process is not None:
                self.window.cancel()
                QTest.qWait(250)
                self.fail("ngspice did not finish within five seconds")
            plot = self.window.wave.plot
            self.assertIsNotNone(plot, self.window.log.toPlainText())
            self.assertAlmostEqual(plot.rows[0][plot.names.index("v(out)")].real, 5)
            readings = self.window.active_readings
            self.assertAlmostEqual(next(node["value"] for node in readings["nodes"] if node["net"] == "out"), 5)
            self.assertAlmostEqual(readings["currents"][0]["value"], -.005)
            output_row = next(i for i, node in enumerate(readings["nodes"]) if node["net"] == "out")
            before = self.window.canvas.grab().toImage()
            self.window.bias_list.setCurrentRow(output_row)
            self.assertEqual(self.window.bias_list.currentItem().data(Qt.ItemDataRole.UserRole), ("out", None))
            self.assertNotEqual(before, self.window.canvas.grab().toImage())
            self.click(18, 12)
            self.assertFalse(self.window.bias_list.selectedItems())
            self.assertEqual(before, self.window.canvas.grab().toImage())
            part = self.window.project.parts[readings["currents"][0]["part"]]
            pin = part.pins()[readings["currents"][0]["pin"]]
            self.window.bias_list.setCurrentRow(len(readings["nodes"]))
            self.assertEqual(self.window.bias_list.currentItem().data(Qt.ItemDataRole.UserRole),
                             (self.window.reading_nets[pin], pin))
            self.assertFalse(self.window.plot_window.isVisible())
            self.window.open_plots()
            self.assertTrue(self.window.plot_window.isVisible())
            self.assertFalse(self.window.canvas.grab().isNull())
            self.window.checkpoint()
            self.window.project.parts[1].value = "2k"
            self.window.refresh()
            self.assertIsNone(self.window.active_readings)
            self.window.restore(self.window.undo_stack, self.window.redo_stack)
            self.assertEqual(self.window.active_readings, readings)
            self.assertTrue((self.window.run_dir / "project.cwb").is_file())
            self.assertIn('"completed"', (self.window.run_dir / "status.json").read_text())
            self.window.wave.plot = None
            self.window.load_saved_run(self.window.run_dir)
            self.assertEqual(self.window.wave.plot.rows, plot.rows)
