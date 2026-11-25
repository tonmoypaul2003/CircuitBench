# OrCAD 9.2 native analysis

This pass focuses on the installed PSpice simulator and Probe viewer (`pspice.exe`), the simulation server (`simsrvr.exe`), and its imported engine (`psp_eng.dll`). The [first-pass inventory](README.md) covers the installed editors, managers, documentation screenshots, and example data. Native code was imported as data. The original applications were left unlaunched and their binaries remained untouched.

## Tooling and reproducibility

- Verified `D:\03. Works\ghidra_12.2_PUBLIC` using the distribution metadata (12.2), Windows headless launcher, native x64 decompiler, and actual PE imports into a workspace-local project. The installed Microsoft OpenJDK 25.0.5.1 x64 meets the distribution's minimum of 25. It was available under `C:\Program Files\Microsoft\jdk-25.0.5.102-hotspot` outside the inherited PATH.
- Codex registers a REA MCP server with Node and the cached `rea-agents\scripts\rea.mjs` entry point followed by `mcp`. The same script reports version 6.10.1 and can be invoked directly, with no global `rea` executable. The MCP registration was left intact. The CLI can select Ghidra via `--provider ghidra` with `GHIDRA_INSTALL_DIR` and `JAVA_HOME` set for the invoking process.
- Ghidra needed a writable preferences directory. APPDATA and LOCALAPPDATA were set to a workspace-local directory for the tool processes. REA also needed TMP and TEMP pointed at that directory because the inherited temp path includes a metacharacter refused by its Windows batch launcher.
- [headless import log](ghidra-analysis.log), [evidence exporter](ExportOrcadStatic.java), [REA PE evidence](rea-native-components.json), and the [first-pass REA evidence](rea-evidence.json) preserve the paths, file hashes, and analysis results.

## Static component boundaries

The inspected PSpice front end and engine use Windows PE32 on x86. The front end imports MFC42, Win32 windowing and drawing, OLE, the Microsoft C and C++ runtimes, and `psp_wobj.dll`. The C++-decorated symbols and RTTI provide class and function naming evidence. The front end uses multiple menu, dialog and accelerator resources, listed in the first-pass report.

| Consumer | Static relationship | Evidence |
| --- | --- | --- |
| `pspice.exe` | Imports `psp_wobj.dll`, including the decorated symbol for `createWTraceMarkerSet`. Its direct import table has no engine dependency. | `ev_8bbcd780d231c42d932c17bc04363fc5622bd2cd0733eec4c3fdecac516b224a` |
| `simsrvr.exe` | Imports `PSP_ENG.dll`, `SimSetup.dll`, `psp_wobj.dll`, `AlsSrvr.dll`, OLE services, and C/C++ runtime. It imports engine analysis, command, pause, callback and parameter functions by name. | `ev_5135300b15a9e1b076d473f4c9b36a77aef64dc71a77508a9d3abeb42a909d0f` |
| `appmgr.exe` | Imports the shared waveform/object DLL and MFC42. | `ev_678b201c7cb83e11ee00117ea032ba9fe7a693cf88ddde0c0c2a327e4e0179c1` |
| `psp_eng.dll` | Exports 53 entries. Its direct imports include Win32 services and Microsoft runtimes. | `ev_4a3d7ecd6921d58fe67999301ebeebf771e9818ea693bb978fb987361149e80b`, `ev_5a2cdc37a902f250df18898533027aff4d9a216aaa900f85654019173c1c28a6` |
| `psp_wobj.dll` | Exports 117 entries. Imports MFC42, OLE, and the `AlsSrvr.dll` library. | `ev_9c0ce2d7e48a079cfe260cc10df7a1d009149a362acf2dd711f0e8d7196b9397`, `ev_69490bec2ea92d0a9c609e4304f7b9cfeb8fcb17737d0fe04f524da043383a93` |

The linkage supports treating the simulation server as an engine-facing component. The order in which executables launch, any dynamic loading, how they communicate, and how an individual menu command reaches the server remain open. OLE dependencies establish an available facility and do not identify the transport used by a particular workflow.

## Workflow correlation

| Workflow | Shipped evidence | Native evidence and remaining question |
| --- | --- | --- |
| Select profile and run | The example OPJ links a DSN and AC and transient SIM profiles and selects the transient profile. The SIM names the circuit input and expected DAT and OUT outputs. | Simulation server imports `PSpiceDoAnalysis` at engine address `100c74a0` and `PSpiceSetCmdArgs`. The argument assignments and the UI-to-server call chain require further tracing. |
| Pause and edit settings | Release notes describe pausing a transient run, changing settings, and resuming. | Server imports `PSpicePause` at `100c7880` along with parameter relevance, get/set and validation functions. The ABI-level controls are verified. Their event order and each setting's numerical effect remain open. |
| Probe traces and cross-probing | Release notes document plots, trace arguments, and schematic markers. | The viewer imports waveform and marker objects. Which call sites correspond to individual commands, and whether and when a marker update changes a trace, require further verification. |
| Library indexing | The installation includes sample and component libraries. | The engine exports `PSpiceBuildLibIndex` at `100c7260`. The export alone does not establish when an index is rebuilt or what it contains. |

Live UI evidence remains uncollected. The supplied installation carries the provenance concern recorded in the first pass, and REA's available native-window observation route is for Mach-O rather than this Windows PE. The workflow correlation is based on shipped documentation and static code boundaries.

## Ghidra code and control flow

The headless analyzer saved both imported programs after their 120-second analysis limits. The read-only export pass then produced function inventories, defined strings, external symbols, selected string cross-references, CFG edges, and bounded decompilation. Coverage is incomplete, especially for indirect calls and undefined or unreferenced strings. Both program hashes agree with the corresponding REA PE records.

| Location | Verified static observation | Interpretation and limits |
| --- | --- | --- |
| Engine `100c74a0` | The REA export address resolves to Ghidra's `PSpiceDoAnalysis`. It has four pointer arguments, early failure branches, a conditional working-directory change, a three-argument call at `100c7830`, a loop with internal thunk calls, and a conditional directory restoration and cleanup path. | Analysis orchestration boundary. The exact meaning of each argument and each internal thunk remains unresolved. |
| Engine `100c7880` | `PSpicePause` invokes `100c6120` then returns the result of `100b8e80` with a global-state address. `PSpiceSetCallback` at `100c71f0` stores its argument globally. | Explicit pause and callback entry points. Synchronization and resume behavior remain unresolved. |
| Engine `100ee470` | Multiple references to transient diagnostics are in one recovered function, including convergence failure, minimum step size, iteration limit and completion. | Evidence of transient step and iteration control. The integration formula, error estimator and exact retry strategy are unresolved. |
| Engine `10015c70`, `100dbb10` | Recorded references to `.TRAN` and to diagnostics for `.SUBCKT` and `.ENDS`, respectively. | Evidence of directive handling and subcircuit validation. This does not establish the schematic-to-netlist emitter or its full grammar. |
| Engine `1011ef64`, `1010c9b4` | Defined strings report GMIN stepping and a pivot diagnostic. | GMIN continuation and matrix pivoting are candidates for further tracing. These strings do not establish the default nonlinear solver, matrix storage, or pivot algorithm. |
| Viewer `00634444`, `00639218` | Plot Window Templates text has references, and a separate bracketed template heading also has a reference. | Corroborates the documented Probe feature. Handler ownership and persistence format remain unresolved. |

[Engine cross-references](static/psp_eng.dll/feature-xrefs.tsv) · [Engine control-flow edges](static/psp_eng.dll/cfg.tsv) · [Viewer cross-references](static/pspice.exe/feature-xrefs.tsv) · [Export log](ghidra-export.log)

No CIR, NET, OUT or DAT fixture was found in the inspected installation tree. The SIM file names expected input and output artifacts, but it supplies no generated netlist or waveform bytes. Format compatibility and numerical parity need matched original runs. The direct Ghidra project and the REA PE bundle were used together through hash and address joins. The recorded REA CLI bridge attempt failed at startup, so no integrated bridge result is attributed to that attempt.
