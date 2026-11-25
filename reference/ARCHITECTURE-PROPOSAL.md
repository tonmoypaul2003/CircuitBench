# Independent EDA application proposal

**Status** · Ready for architecture review. Application implementation begins after review. This proposal draws on the [workflow investigation](README.md) and [REA–Ghidra correlation](NATIVE-ANALYSIS.md). The original's live canvas behavior, proprietary file formats, and numerical parity remain open.

## Scope and workflow

Build a Python desktop application with a distinct interface. A schematic canvas occupies the center, with searchable symbols and project navigation beside it. The selected object's properties and simulation profiles are accessible from the workspace. An output area presents validation diagnostics, run logs, operating-point tables and waveform plots. Use original symbols based on conventional electrical notation.

1. Create or open a project. Place, rotate and move symbols on a snapped grid. Pan and zoom without changing schematic coordinates.
2. Route wires between pins and junctions, assign net names and ground references, and inspect connectivity by selecting a net. Edit device properties and undo or redo schematic operations.
3. Validate the schematic and preview the generated SPICE netlist. Navigate from a diagnostic to the relevant component, pin or wire.
4. Choose a profile for DC operating point, AC sweep or transient analysis and run it against a frozen schematic revision. Show progress, cancellation, logs and the resulting data.
5. Inspect operating-point values or add voltage/current traces. For AC, display magnitude and phase from complex values. For transient, display time-domain traces. Reopen saved results with enough provenance to identify stale runs.

Canvas gestures, layouts and shortcuts above are proposed product behavior. The [Capture screenshots](screenshots/PP_DB.gif) and documentation establish specific comparison points for library search, property editing and profile workflows.

## Components and contracts

| Component | Responsibility and boundary |
| --- | --- |
| Desktop UI | Candidate Python/Qt graphics view and dockable panels. Canvas objects render domain state. Editing produces document commands with reversible before/after state. Plotting reads typed result data. |
| Project store | Versioned JSON with stable IDs and atomic save. Store schematic, symbol references, relative model references, profiles and view preferences. Track result runs separately and preserve unknown-version files on failed open. |
| Symbol library | Versioned records with graphics, pin IDs, offsets, electrical roles, explicit SPICE pin order and typed device properties. Start with ground, labels, R/L/C, independent V/I sources and diode plus explicit model binding. |
| Connectivity compiler | Build a graph from electrical endpoints, explicit junctions and labels. A wire crossing is disconnected unless an explicit junction joins it. Resolve ground to node `0`. Report dangling pins, conflicting labels and unreachable ground before simulation. Retain source IDs for diagnostics and trace selection. |
| Netlist generator | Serialize typed devices and profile settings to deterministic SPICE text. Validate unique designators, ordered terminals, model binding and numeric units. Generate only supported directives and resolve model includes from declared project paths. |
| Solver adapter | Run a pinned independent ngspice executable as a subprocess in a dedicated per-run directory. Pass arguments without a shell. Keep netlist, engine version, options, status, raw output and diagnostics. Isolate simultaneous jobs and retain incomplete output on cancellation. |
| Results store | Decode a bounded, tested ngspice output format, preserve native sample axes and real or complex data, and map vectors to circuit IDs. Expose operating-point tables, overlay traces, cursors and CSV export. |

Ngspice is the proposed solver because its documented batch interface supports the required analyses and raw output while keeping the editor separate from the numerical engine. Initial runs use `.op`, `.ac` and `.tran`; the result adapter must verify the actual operating-point and raw output conventions against the pinned build. Interactive pause and continuation are outside the initial milestone. [Ngspice manual](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf)

## Incremental implementation and verification

| Milestone | Automated acceptance checks |
| --- | --- |
| Document and canvas | Place, move, rotate, wire and edit. Undo/redo returns the same topology. Save/reopen preserves stable IDs and settings. Test zoom and pan invariance, pin hit testing, explicit junctions, T connections, and disconnected crossings. |
| Connectivity and netlisting | Golden tests for stable node names and device ordering. Exercise labels, missing ground, dangling connections, duplicate designators, pin order, unsupported devices, and model paths. |
| Analysis runner | Execute against the pinned solver and verify exit and diagnostic handling, failed or cancelled runs, interrupted writes, isolated concurrent runs, and real/complex result decoding. |
| Electrical references | Equal-resistor divider under 10 V gives 5 V at the midpoint with correct source-current sign. First-order RC step agrees with the analytical rise at known times, including 63.212% of the final value at one time constant. RC low-pass AC response follows the complex transfer function, including magnitude 0.707107 and phase −45° at cutoff. Add floating-node and nonlinear diode fixtures. |
| Workflow comparison | Run matching circuits and profiles on a verified original instance and the independent app. Compare connectivity, supported parameters, convergence classification, physical units and numerical vectors. Compare transient outputs on an agreed time grid with interpolation and clearly recorded tolerances. |

For initial electrical tests, use well-conditioned circuits with fixed solver settings. Specify absolute and relative tolerances per quantity alongside the fixture. Start with 1 µV for the divider, 0.5% of final voltage for RC transient at specified sample times, and 0.1 dB / 0.5° at cutoff for AC. Revisit only with recorded numerical evidence. A separate directory under the writable workspace will hold application source and tests once the architecture is reviewed.

## Review decisions and remaining investigation

- Review the Python desktop approach, independent ngspice integration, and scope of the first milestone.
- The proposal assumes independently defined project and results formats. Evaluate import compatibility separately after collecting verified fixtures and defining the supported subset.
- Use a verified original instance to fill the remaining UI, generated-netlist and waveform-format gaps. The investigation presently supports a functional design and analytical reference tests. Original parity remains unmeasured.
