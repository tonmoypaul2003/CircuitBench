# CircuitBench releases

## 0.1 beta

The first CircuitBench preview includes an independent schematic editor, SPICE model and subcircuit import, and the bundled ngspice 47 simulation engine. It supports DC operating points and sweeps, AC sweeps, and transient runs.

- Draw circuits with conventional device symbols, grouped selection, and voltage and current markers.
- Review results in a separate plot window with subplots, trace expressions, sample pins, and PNG and PDF export.
- Import the bundled ngspice starter pack and the Qucs-S diode catalog on demand.

Download `CircuitBench.exe` and run it on Windows x64. The application, Qt runtime, and ngspice are included in the single file. The beta build is unsigned.

The standalone Windows smoke test exercised model import, a 5 V divider operating point, a 201-point AC sweep, and PNG and PDF export.
