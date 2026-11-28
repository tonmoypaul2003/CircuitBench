"""Verify the packaged editor and its embedded simulator."""
import json
import math
from pathlib import Path
import sys
import tempfile
import time
import traceback

from PyQt6.QtCore import QEventLoop, QTimer

from circuit import Profile, example


def run_profile(window, application, folder, profile):
    window.project.active = profile
    window.path = folder / "demo.cwb"
    window.wave.plot = None
    window.run()
    deadline = time.monotonic() + 20
    while window.process is not None and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(25, loop.quit)
        loop.exec()
    application.processEvents()
    if window.process is not None:
        window.cancel()
        raise RuntimeError("Packaged solver timed out")
    if window.wave.plot is None:
        raise RuntimeError(window.log.toPlainText())
    return window.wave.plot


def verify(application, window, report):
    result = {"frozen": bool(getattr(sys, "frozen", False)), "platform": application.platformName()}
    try:
        if not window.solver or Path(window.solver).name != "ngspice_con.exe":
            raise RuntimeError("The bundled ngspice was not selected")
        payload = Path(__file__).resolve().parent / "runtime"
        if not Path(window.solver).resolve().is_relative_to(payload):
            raise RuntimeError("The solver was resolved outside the application payload")
        if application.windowIcon().isNull() or window.canvas.grab().isNull():
            raise RuntimeError("The application icon or drawing canvas failed to load")
        window.import_starter_pack()
        count, issues = window.import_library_files([payload / "models" / "qucs-diodes.lib"])
        if issues or count == 0 or "IRFP240" not in window.project.device_models or "OP07" not in window.project.subcircuits:
            raise RuntimeError("Bundled model-library import failed")
        result["qucs_models_imported"] = count
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            window.project = example()
            operating = run_profile(window, application, folder, "Operating point")
            midpoint = operating.rows[0][operating.names.index("v(out)")].real
            if not math.isclose(midpoint, 5., abs_tol=1e-6):
                raise RuntimeError(f"Incorrect DC divider midpoint: {midpoint}")
            ac = run_profile(window, application, folder, "AC sweep")
            ac_column = ac.names.index("v(out)")
            if len(ac.rows) < 2 or not math.isclose(abs(ac.rows[0][ac_column]), .5, abs_tol=1e-6):
                raise RuntimeError("Incorrect AC divider sweep")
            plot = window.plot_pages.currentWidget()
            plot.export(folder / "plot.png")
            plot.export(folder / "plot.pdf")
            if not (folder / "plot.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("PNG export failed")
            if not (folder / "plot.pdf").read_bytes().startswith(b"%PDF-"):
                raise RuntimeError("PDF export failed")
            result.update(midpoint_volts=midpoint, ac_samples=len(ac.rows), png_export=True, pdf_export=True)
            window.project.design_variables = {"LOAD": "3k"}
            window.project.parts[2].value = "{LOAD}"
            operating = run_profile(window, application, folder, "Operating point")
            if not math.isclose(operating.rows[0][operating.names.index("v(out)")].real, 7.5):
                raise RuntimeError("Design variable did not change the divider")
            window.project.profiles["Noise"] = Profile("NOISE")
            noise = run_profile(window, application, folder, "Noise")
            if len(noise.rows) != 201 or noise.rows[0][noise.names.index("onoise_spectrum")].real <= 0:
                raise RuntimeError("Bundled noise analysis failed")
            metadata = json.loads((window.run_dir / "dataset.json").read_text(encoding="utf8"))
            if metadata["mode"] != "NOISE":
                raise RuntimeError("Missing typed noise result metadata")
            result.update(design_variable_volts=7.5, noise_samples=len(noise.rows))
        result["status"] = "passed"
    except Exception:
        result.update(status="failed", traceback=traceback.format_exc())
    report.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return 0 if result["status"] == "passed" else 1
