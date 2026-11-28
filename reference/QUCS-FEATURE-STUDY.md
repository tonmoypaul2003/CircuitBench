# Qucs and Qucs-S feature study for CircuitBench

Reviewed 2026-11-28 in Asia/Dhaka. Target baseline is CircuitBench Desktop 0.4.7 beta at `2abb16d`.

## What was inspected

| Evidence | Findings and boundary |
| --- | --- |
| Installed Qucs 0.0.19 package | The supplied portable Win32 directory contains a 32-bit Qt 4-era editor, its Qucsator simulator, 77 schematic examples, and 14 component library files. The bundle also contains FreeHDL, ASCO and ADMS. The README lists Icarus Verilog and Octave as optional integrations. |
| Binary identity | REA opened `bin/qucs.exe` as x86 PE. SHA-256 is `bd0e7f9abc38cb72d55b60147387db3b0cff48c1633af82d53af3f5b6d85fabd`. REA artifact evidence `ev_53a023192ca8e38acab6a5bea906200739ec8226aedb8c0879763b7c8370b4c3` and summary `ev_6806fc9492b1906b340b2c9f71c333c1e14323a7ba07ddbf9702d8116412051f` cover the root binary. A bounded ASCII search found Smith diagram, truth diagram, optimization and matching-circuit strings. These strings corroborate feature names. |
| Legacy documentation | The installed English help has standalone pages on subcircuits, optimization, matching, digital simulation, equations, Octave and schematic format. Examples include group delay, filters, oscillators, mixer circuits, noise, diode harmonic balance and switching converters. |
| Qucs-S | Reviewed source at `f29648979cf1ef831a5837f669809576f14a67c6`, including schematic, diagram, project, component and solver files and example trees. Qucs-S supports ngspice, Xyce, SpiceOpus and Qucsator. [Pinned Qucs-S source](https://github.com/ra3xdh/qucs_s/tree/f29648979cf1ef831a5837f669809576f14a67c6) |
| Remaining coverage | REA native function enumeration returned `provider_unavailable` on this connection. The legacy `qucsator.exe -v` launch returned Windows “Illegal System DLL Relocation.” The inventory covers product workflows and implementation systems. Individual machine-code functions, legacy GUI actions at runtime, numerical parity, and uninitialized Qucs-S submodules were outside the verified coverage. |

Original Qucs 0.0.19 and Qucs-S are distinct targets. Qucsator is the legacy bundled solver. Qucs-S offers ngspice as a recommended external backend. CircuitBench already runs ngspice 47, making the Qucs-S ngspice path the practical reference for new simulation work. An isolated ngspice mention in the legacy executable does not establish a working ngspice backend in that build. [Original Qucs](https://github.com/Qucs/qucs) · [Qucs-S backend overview](https://github.com/ra3xdh/qucs_s#about-qucs-s)

## Implementation behavior traced in Qucs-S

| Flow | Observed implementation | CircuitBench design opportunity |
| --- | --- | --- |
| Schematic to solver | Schematic and per-component netlisting feed Qucs-S's simulator layer. The abstraction validates inputs and assembles subcircuit definitions. Ngspice builds an analysis control stream, maps requested node and branch variables, and handles pre/post-run hooks for sweeps, equations and analyses. | Keep the independent CircuitBench connectivity compiler. Expand the analysis plan with typed requests, validation and explicit vector selection. Preserve the emitted deck in each session. [ngspice backend](https://github.com/ra3xdh/qucs_s/blob/f29648979cf1ef831a5837f669809576f14a67c6/qucs/extsimkernels/ngspice.cpp#L73) · [Shared solver layer](https://github.com/ra3xdh/qucs_s/blob/f29648979cf1ef831a5837f669809576f14a67c6/qucs/extsimkernels/abstractspicekernel.cpp#L118) |
| Solver to dataset | The process adapter captures output. The common layer parses raw results and analysis-specific outputs for OP, Fourier, noise, PZ, sensitivity and sweeps, then normalizes variable names and writes Qucs datasets. A separate routine resamples external data on a common grid. | Keep native sample grids per run in a result model with named axes, quantity, units, complex values, analysis and sweep identity. Resample explicitly when combining incompatible grids. [Process and conversion](https://github.com/ra3xdh/qucs_s/blob/f29648979cf1ef831a5837f669809576f14a67c6/qucs/extsimkernels/externsimdialog.cpp#L147) · [Dataset conversion](https://github.com/ra3xdh/qucs_s/blob/f29648979cf1ef831a5837f669809576f14a67c6/qucs/extsimkernels/abstractspicekernel.cpp#L1307) |
| Variables and models | Equations can contribute solver-side script. The converter handles circuit elements, controlled sources, devices, equation-defined devices and subcircuits through explicit translation paths. External model inclusion and dialect differences require handling at the solver boundary. | Maintain separate circuit parameters and postprocessing expressions. Resolve dependencies and scopes, generate validated SPICE, and reserve simulator-specific features for explicit capabilities. [Converter](https://github.com/ra3xdh/qucs_s/blob/f29648979cf1ef831a5837f669809576f14a67c6/qucs/extsimkernels/qucs2spice.cpp) · [Equation implementation](https://github.com/ra3xdh/qucs_s/blob/f29648979cf1ef831a5837f669809576f14a67c6/qucs/components/equation.cpp) |
| Results to diagrams | The source separates diagrams, graphs, markers and legends. Diagram subclasses include Cartesian, polar, Smith, pole-zero, 3D, tables, timing and truth tables. | Keep one result model and add presentation adapters. Carry source/run identity into legends, pin exports and reports. [Diagram classes](https://github.com/ra3xdh/qucs_s/tree/f29648979cf1ef831a5837f669809576f14a67c6/qucs/diagrams) |
| Design and projects | There are distinct modules for file IO, rendering, selection, project navigation and schematic validation. Qucs-S also contains standalone synthesis and calculation tools and external-interface examples. | Extend the existing canvas/session model with hierarchy navigation and provenance. Generate editable CircuitBench schematics from calculators and compare synthesized responses by simulation. [Schematic files](https://github.com/ra3xdh/qucs_s/blob/f29648979cf1ef831a5837f669809576f14a67c6/qucs/schematic_file.cpp) · [Example tree](https://github.com/ra3xdh/qucs_s/tree/f29648979cf1ef831a5837f669809576f14a67c6/examples) |

## Feature inventory and implementation candidates

**Existing** means CircuitBench has the workflow described. **Extend** means there is a baseline to build on. **Add** is a proposed CircuitBench system. Individual features within a row can have different backend requirements.

### Schematic editing and project organization

| System | CircuitBench opportunity |
| --- | --- |
| Project and document workspace | **Extend.** Multiple schematic tabs, recent circuits/projects, open related schematic/result/netlist files, dock layout persistence, project-local dependencies and search. Preserve Session Tree as the run record. |
| Geometry editing | **Extend.** Clipboard for partial schematics, duplicate with unique refs, mirrored symbols, safer connected rotation, selectable orthogonal/arbitrary wire routing, more explicit junction insertion and dangling wire hints. Preserve CircuitBench's connectivity checks. |
| Selection and usability | **Extend.** Custom shortcuts, box zoom, zoom to selected components, fit page, component and net search with next/previous navigation, context actions and consistent keyboard access. |
| Net naming and hierarchy | **Extend.** Global versus scoped labels, power rails, buses, bus taps, subsheet instances, parent/child cross-probing and explicit instance paths for readings. Treat names and connectors as electrical data even when their labels are hidden. |
| Electrical rules | **Extend.** Classify isolated islands, floating terminals, conflicting sources, unresolved models, duplicate identifiers and unsupported backend devices. Navigate directly from each message to its schematic location. |
| Subcircuit design | **Extend.** Parameter schemas and defaults, per-instance overrides, body/port symbol editing, bulk pin mapping, recursive dependencies, model revision pinning, update workflow and invalid port mapping diagnostics. Preserve embedded library revisions. |
| Component library | **Extend.** One searchable catalog, categories, vendor/family filters, pinout previews, model replacement/swap, result provenance, favorites, tags and import reports. The legacy bundle illustrates filters for diodes, BJTs, JFETs, MOSFETs, op amps, regulators, bridges, substrates and ideal devices. |
| Schematic presentation | **Extend.** Pages and frames, richer drawing primitives, leaders, images, selectable Engineering/SI notation and SVG export. CircuitBench already supports annotation text and PNG/PDF export. Incorporate report-ready schematic snapshots. |
| Examples and templates | **Extend.** Ship small original, tested circuits by topic, with expected waveforms and a lesson. Start with op-amp filters, BJT characterization, rectifiers, oscillators, switching converters and transmission-line examples. |
| Lifecycle and exchange | **Add.** Project backup and recovery, portable project bundle, linked-versus-embedded model policy, export of complete job artifacts and a reproducible command-line simulation entry point. |

### Components, models and interoperability

| System | CircuitBench opportunity |
| --- | --- |
| Controlled sources | **Add to the schematic palette.** VCVS, VCCS, CCVS and CCCS with pin/sensing semantics and named gain. Some controlled-source syntax is already accepted inside imported subcircuits. |
| Behavioral sources | **Add.** B-source expressions, controlled switches/relays, equation-defined elements, time-dependent resistance and nonlinear dependent sources. Use a separate compiler for circuit expressions and prevent graph-time functions from leaking into solver expressions. |
| Magnetics and transformers | **Add.** Coupled inductors with dot orientation, transformer windings, multiwinding couplings, and an optional nonlinear magnetic-core workflow. Begin with model-independent coupled inductors. |
| RF components | **Add.** Ports and reference impedances, matched loads, ideal/balanced transmission lines, lossy models, coax, microstrip/coupled lines, attenuators, baluns and lumped RF models. Validate each model against the supported solver version. |
| Semiconductor model breadth | **Extend.** MESFET, advanced MOSFET families, power diodes, vendor op-amp/macromodel collections, temperature models and pinout-aware replacement. Complex device model fields should be editable only where the selected backend supports them. |
| Model linking | **Add.** Project-relative include and library-section support, dependency previews, continuation lines, cycle detection, diagnostics and an explicit compatibility mode for supported vendor dialects. CircuitBench currently embeds supported self-contained SPICE definitions. |
| Netlist import/export | **Extend.** Guided SPICE/netlist import with an unresolved-device report and a staged Qucs `.sch` importer. Initial mapping should cover R/L/C, sources, wires, labels, ports and basic analyses. Keep unknown fields visible for repair. Consider CDL export only if device and pin mappings are testable. |
| Symbol exchange | **Add.** Custom library symbols with stable pin IDs and optional mapping from validated external symbols. Import symbol geometry and simulator semantics as separate concerns. |
| Mixed-signal extensions | **Add later.** XSPICE logic and analog/digital bridges, code-model settings and clocked test benches. Use the bundled engine's documented module support and verify module binaries and packaging first. |
| Compact-model extension | **Add later.** A versioned OpenVAF/OSDI integration for compatible ngspice models, linked compiled models and a verified toolchain matrix. ADMS in the old bundled release is a separate historical route. |

### Simulation and automated design

| System | CircuitBench opportunity |
| --- | --- |
| Baseline analyses | **Existing.** OP, DC sweep, AC and transient, analysis selection, parameter sweeps, run logs and marker plots. Extend with linear/decade/octave/list sweeps where supported, explicit initial conditions and saved per-analysis options. |
| Sweep engine | **Extend.** Named design variables, temperature sweeps, two-axis/nested sweeps, sweep-linked measurements, stepped datasets, parallel cancellation semantics and consistent results for different time grids. Track the baseline and every parameter assignment in the session. |
| Initial conditions and options | **Add.** Typed `.ic`, `.nodeset`, temperature, numerical tolerances, iteration limits and transient integration options with validation and convergence messages. |
| Noise | **Add.** Input- and output-referred spectral density, integrated noise, plot/table display and source contributions when the backend provides them. Use a resistor test at known temperature and verify spectral units. |
| Pole-zero and transfer functions | **Add.** Typed node pairs, transfer gain and impedance outputs, plots of complex poles/zeros, and frequency checks against known RC and second-order fixtures. Check available solver syntax before exposing each analysis. |
| Sensitivity | **Add.** DC and AC output sensitivities where ngspice supports them, ranking per variable, units/normalization and finite-difference checks against a baseline. |
| Distortion and spectral analysis | **Extend.** CircuitBench already computes a windowed spectrum. Add solver Fourier/THD, distortion analysis, window/steady-state controls, and explicit frequency-domain normalization. Treat general harmonic balance as a backend-specific capability. |
| S-parameters | **Add.** Port analyzer and reference impedance setup, S-parameter matrices, S11/S21 readouts and validation against matched loads and through connections. Verify ngspice feature/version/noise limits before enabling. |
| Transient device power | **Extend.** CircuitBench already reports instantaneous power, average and peak. Add energy integral, period-averaged losses, branch power across sweeps and RMS values with sign provenance. |
| Monte Carlo and yield | **Add.** Seeded runs, normal/uniform distributions, per-part/global variations, correlation groups, pass/fail requirements, histograms and percentiles. Record sampled values and solver failures. The Qucs-S source has dedicated Monte Carlo and parameter sweep components. |
| Tuning | **Add.** Parameter sliders, validated ranges, debounce, cancellation, original/restored value, optional commit back to the project and a pinned baseline comparison. |
| Optimization | **Add after measurements and sweeps.** Stable variables, constraints, objectives, limits, run ledger and reproducible seeds. Use ASCO only as a legacy workflow reference. Implement a bounded optimizer around CircuitBench's simulation adapter and show objectives and convergence. |
| Design synthesis | **Add.** Active/passive filter and attenuator calculators, RF matching network synthesis, network transforms and transmission-line geometry/electrical conversion. Generate editable schematics and compare the predicted and simulated response. Use implementation and release notes as references. [Synthesis and RF changes](https://github.com/ra3xdh/qucs_s/blob/f29648979cf1ef831a5837f669809576f14a67c6/NEWS.md) |
| RF system design | **Add later.** Receiver stages, gain/noise/dynamic range budgets, mixer characterization and power combining. These require separate measurement semantics and validation from circuit-level AC. |
| Backend selection | **Add later.** Keep ngspice primary. Add an adapter contract with supported components, analyses and parsers per backend before considering Xyce, SpiceOpus or QucsatorRF. Preserve engine and version metadata on each run. |

### Results, reports and extensions

| System | CircuitBench opportunity |
| --- | --- |
| Dataset schema | **Extend.** Multiple axes, complex quantities, units, labels, vector provenance, signal/analysis identity, conversion errors and primary scale. Avoid backend UI code reaching into solver-specific filenames. |
| Plot families | **Extend.** Cartesian and V–I plots already exist. Add polar, Smith impedance/admittance, pole-zero, stacked digital timing, truth tables, numeric tables and parameter surfaces. |
| RF result tools | **Add.** Touchstone import/export and viewer, reference impedance, group delay, VSWR, return/insertion loss, stability metrics and matching overlays. Distinguish imported data from simulated S-parameters. |
| Annotations and cursors | **Extend.** Persistent plot notes, linked cursors across plots, snap-to-trace/frequency/threshold, measurement units, trace-local annotations and run-qualified pin/export values. Keep the existing visible-range measurement handling. |
| Expressions and automation | **Extend.** CircuitBench already has derived traces. Add dependency-ordered reusable expressions, complex and vector functions, engineering domain functions, group delay, integration/differentiation and explicit cross-run alignment. |
| Data exchange | **Extend.** CSV has a baseline. Add typed dataset exports and optional Python/Octave notebooks from an immutable session snapshot. Import normal result datasets before allowing writeback; never run a script just because the project contains it. |
| Validation and reports | **Extend.** Templated filter/transient/RF reports, pinned comparisons, convergence warnings, plot snapshots, unit-aware statistics and a manifest of input model hashes, project revision and engine version. |
| Large datasets and responsiveness | **Extend.** Display decimation, indexed sample picking, plot virtualization and cancellation. Keep untouched raw samples for measurements and export. |
| Accessibility and localization | **Extend.** Shortcut editor, keyboard plot and panel navigation, accessible direction/status information and translatable strings. Preserve advanced equations and numeric literals in a locale-independent format. |
| Digital and HDL workflow | **Add later.** Event traces, logic gates, truth/timing viewers, HDL testbench generation and explicit external simulation. The legacy FreeHDL/ADMS package and Qucs-S XSPICE/OpenVAF examples are separate integration models. |

## Suggested implementation sequence

1. **Named variables and a typed results schema.** Add project/sheet/instance scopes and nested sweeps with stored parameter assignments. Keep raw and derived signals linked to runs. These changes support noise, RF charts, tuning and optimization without scaling the current specialized parsing paths.
2. **Controlled/behavioral sources and model/diagnostics upgrades.** Add controlled sources, initial conditions, grounded/port-aware checks and permitted project-local model includes. Start with netlist golden tests and analytic circuit tests.
3. **Noise, pole-zero and sensitivity.** Implement each as a profile with its own settings and parser. Validate units and compare against analytical or independently generated fixtures.
4. **RF starter pack.** Deliver Touchstone viewing, Smith rendering, group delay and a matched-port test fixture. Then add S-parameter simulation and selected synthesis tools. A viewer has value even before full RF simulation.
5. **Tuning, Monte Carlo and optimization.** Reuse the parameter/result contracts. Keep imported models and external tools opt-in. Put mixed-signal, advanced RF, native module loading and additional engines behind capability checks.

The architecture and feature order above are CircuitBench proposals. Qucs-S source is a reference for behavior and design boundaries. Any incorporation of code or models needs a per-file license and provenance review, particularly because CircuitBench declares GPL-3.0-only and the upstream repository has its own copyright and licensing terms. [Qucs-S license](https://github.com/ra3xdh/qucs_s/blob/f29648979cf1ef831a5837f669809576f14a67c6/COPYING)
