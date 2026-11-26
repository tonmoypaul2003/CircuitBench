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

    def test_grid_resize_graph_pin_and_maximize(self):
        plot = Plot("Transient", ["time", "v(low)", "v(high)"], ["time", "voltage", "voltage"],
                    [[0, 1, 8], [.5, 2, 9], [1, 3, 10]])
        self.window.show_plot(plot, self.window.project.dump())
        page = self.window.plot_pages.currentWidget()
        subplot = page.subplots[0]
        subplot.traces.item(1).setCheckState(Qt.CheckState.Checked)
        subplot.height_slider.setValue(400)
        self.application.processEvents()
        self.assertEqual(subplot.wave.height(), 400)
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
        subplot.auto_pin_button.click()
        self.assertEqual(subplot.wave.pins[-1], (2, 1, ""))
        self.assertEqual(subplot.wave.cursor, 1)
        self.assertEqual(subplot.pin_at(1, .97, "End"), 1)
        subplot.wave.background = QColor("#27394d")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resized.png"
            page.export(path, whole_page=False)
            image = QImage(str(path))
            self.assertEqual(image.height(), 880)
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
