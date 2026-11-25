# OrCAD workflow coverage

CircuitBench is an independent open-source implementation of the schematic-to-simulation workflow. The application source and tests are in this directory. The `reference` folder contains research evidence, including release-note screenshots. The executable contains the independent application and its runtime.

| Workflow | Original evidence | Workbench status and comparison gap |
| --- | --- | --- |
| Place parts and edit properties | Capture release notes and screenshots describe library selection and the property spreadsheet. | Built-in symbols, searchable library and selection-based property form. External symbol libraries and bulk editing remain open. |
| Draw and navigate a schematic | Schematics and Capture are distinct entry points. The original live editing gestures are unverified. | Placement, wired movement, rotation, junctions, pan, zoom and undo/redo are implemented. Live interaction comparison remains open. |
| Select and manage simulation profiles | The installed example OPJ lists two SIM profiles and an active profile. The SIM ties a named profile to its schematic and run files. | Named profiles can be added, duplicated, edited and removed. The active selection survives save and reopen. Native OPJ and SIM import remain open. |
| Prepare a netlist | The release notes document flat and hierarchical generation. Ghidra identifies engine directive handlers. | Deterministic flat generation with versioned project data. Hierarchical elaboration, library includes, and direct original-deck comparison remain open. |
| Run a simulation | REA import evidence `ev_5135300b15a9e1b076d473f4c9b36a77aef64dc71a77508a9d3abeb42a909d0f` links the simulation server with profile writing and engine analysis and pause calls. | Independent ngspice supports operating point, DC sweep, AC and transient analysis. Analytical fixtures verify the implementation. Original-to-Workbench numeric comparisons remain open. |
| Inspect waveform and outputs | Release notes describe Probe traces and templates. Shipped SIM files name DAT and OUT outputs. | Trace selection, magnitude, phase, cursor and CSV export use ASCII ngspice data. Native Probe compatibility and marker cross-probing remain open. |

Sources and limitations are documented in the [installation investigation](reference/README.md) and [native analysis](reference/NATIVE-ANALYSIS.md).

## Next milestones

1. Add a documented symbol-library format and a controlled external SPICE model workflow. Verify ordered pins, subcircuit references, portable paths, and missing-model diagnostics.
2. Add multi-sheet design and hierarchical netlisting. Test scoping, repeated subcircuits, and capture-to-result trace IDs before expanding the import formats.
3. Add schematic markers and improve the run browser with trace metadata. Compare trace selection, cursor measurements, and profile-to-result association with a verified OrCAD instance.
4. Build matched original and Workbench fixtures for the acceptance matrix. Keep original proprietary files out of public releases and publish independently authored test circuits and measured differences.
