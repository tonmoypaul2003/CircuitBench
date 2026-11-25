# OrCAD 9.2 installation investigation

Inspected on 26 November 2026. Source directory is `C:\Program Files\Orcad`. This report covers the initial static investigation. The [REA evidence bundle](rea-evidence.json) contains ten records linking findings to file paths, hashes, PE tables, and parser coverage.

## Installed tools and boundaries

| Start menu entry | Executable | Evidence for role |
| --- | --- | --- |
| Schematics | `PSpice\psched.exe` | Windows shortcut and file description identify the schematic editor |
| PSpice AD | `PSpice\pspice.exe` | File description identifies the simulator and Probe waveform viewer |
| PSpice Model Editor | `PSpice\modeled.exe` | Shortcut and file description |
| PSpice Design Manager | `PSpice\PDesign.exe` | Shortcut and file description |
| PSpice Simulation Manager | `PSpice\simmgr.exe` | Shortcut and file version metadata |
| PSpice Message Viewer | `PSpice\msgview.exe` | Shortcut and file description |
| PSpice Stimulus Editor | `PSpice\stmed.exe` | Shortcut |

Other executables under the inspected path include the application bridge, marker and simulation server executables, symbol library utility, HTML Help viewer, registration utility, and updater. Capture assets include component libraries and PSpice integration files. The inspected tree contains no Capture, Capture CIS, or Layout executable. Their documentation confirms release coverage only. The presence of server executables does not establish whether or how the visible tools launch them.

## Capture UI and documented workflows

The images below come from the shipped `caprl.htm` release notes. They show the documented Release 9.2 UI. No live screens were acquired.

| Reference | Documented interaction and expected result |
| --- | --- |
| [Property editor spreadsheet](screenshots/pivot.gif) | Pivot swaps the object and property axes. New Column becomes New Row when pivoted. Adding a property creates a new property name along the applicable axis. |
| [Place Part dialog](screenshots/PP_DB.gif) | Typing part of a component name autocompletes against selected libraries. Part selection updates the preview and shows applicable simulation tool indicators. |
| [Part from unselected library](screenshots/PPFUL_DB.gif) | Pressing Enter after an unmatched name searches configured libraries beyond the selected set. A matching unselected-library part can be confirmed for placement. A preference disables that expanded search. |
| [Recently used parts](screenshots/MRU.gif) | Ctrl+M focuses part-name input in the recently used list. The notes document autocomplete there. |
| [Create PSpice Project](screenshots/CPP_DB.gif) | Project creation can take an existing OPJ as a template. The notes describe including profiles, local simulation files, model libraries, include files, markers, and configured resources. |

The release notes describe other Capture and CIS interactions, including viewing Layout footprints in Capture CIS. Database access, footprint prerequisites, command enablement, keyboard focus, cancellation, and persistence need live verification.

## PSpice workflow and data

- `psprl.htm` documents hierarchical and flat netlists. Capture exposes the choice under Project Manager → Tools → Create Netlist → PSpice. PSpice Schematics exposes it under Options → Netlist Setting.
- The same notes describe cross-probing, voltage/current/power markers, and adding corresponding Probe traces. A simulated subcircuit's pin current can be selected via a schematic current marker or added as a Probe trace. Power markers attach at a part's center.
- Probe display templates take trace arguments. The described routes are PSpice → Markers → Plot Window Templates in Capture and Window → Display Control → Templates in PSpice.
- A transient simulation can be paused, adjusted through Simulation → Edit Runtime Settings, and resumed with the new values. Which settings are editable and how they affect solver output remain unverified.
- The release notes list a case where deleting a Capture marker leaves the corresponding trace in Probe. Record that state before choosing a parity expectation for marker deletion.
- The shipped sample at `PSpice\Capture_Samples\anasim\example` has an `ExpressProject` OPJ with a relative DSN, two simulation profile references, an active transient profile, and persisted file-view paths and placement data. It carries both relative resource references and historical absolute view paths. Their resolution and relocation behavior remain open.
- The transient SIM is text with an OrCAD Simulation Server 1.0 header and named general fields referencing the design, project, generated CIR netlist, DAT output and OUT report. The sample directory contains the project, DSN, two SIM files, and two MRK files. Output generation, binary formats, serialization, and round trips remain unverified.

## Native architecture and dependencies

The seven shortcut targets are x86 PE32 executables. Direct imports establish static library dependencies. They leave runtime loading, registration, interprocess communication, solver boundaries, and call order open.

| Executable | Direct imported libraries | REA evidence ID |
| --- | --- | --- |
| pspice.exe | psp_wobj.dll, orcadfnc.dll, MFC42.DLL, MSVCRT.dll, KERNEL32.dll, USER32.dll, GDI32.dll, comdlg32.dll, ADVAPI32.dll, SHELL32.dll, COMCTL32.dll, oledlg.dll, ole32.dll, OLEAUT32.dll, MSVCP60.dll | `ev_8bbcd780d231c42d932c17bc04363fc5622bd2cd0733eec4c3fdecac516b224a` |
| psched.exe | CHKPTDLG.dll, AlsSrvr.dll, MFC42.DLL, MSVCRT.dll, KERNEL32.dll, USER32.dll, GDI32.dll, comdlg32.dll, WINSPOOL.DRV, ADVAPI32.dll, SHELL32.dll, MSVCP60.dll, MPR.dll | `ev_cdd3da5f0f9baeb04091257174a7b2c87c5e825831bb7fc322d26f341768a414` |
| modeled.exe | psp_wobj.dll, orcadfnc.dll, MFC42.DLL, MSVCRT.dll, KERNEL32.dll, USER32.dll, GDI32.dll, comdlg32.dll, ADVAPI32.dll, SHELL32.dll, COMCTL32.dll, oledlg.dll, ole32.dll, OLEAUT32.dll, MSVCP60.dll | `ev_0dceb3673b9249fb7449f957443432c0cfdabb946bcdaddfd06b8a8b4f8c75a0` |
| PDesign.exe | dm_eng.dll, MFC42.DLL, MSVCRT.dll, KERNEL32.dll, USER32.dll, GDI32.dll, ADVAPI32.dll, SHELL32.dll, COMCTL32.dll, MSVCP60.dll | `ev_a33ffa9ca4ef64fd291086072e2125bc6ff83dd4debe74b28c2437592aa646e5` |
| simmgr.exe | orcadfnc.dll, MSVCRT.dll, MFC42.DLL, KERNEL32.dll, USER32.dll, ADVAPI32.dll, SHELL32.dll, COMCTL32.dll, OLEAUT32.dll, MSVCP60.dll | `ev_705bfe75435aa8c618468e1abaae73d748b3bf293c9e3183daef36fbb8988391` |
| msgview.exe | MSVCRT.dll, MFC42.DLL, KERNEL32.dll, USER32.dll, GDI32.dll, ADVAPI32.dll, SHELL32.dll, COMCTL32.dll, MSVCP60.dll | `ev_38db2234eef45454c218f87284f6944b950a405fbf88c0036d605af838dfa21f` |
| stmed.exe | comdlg32.dll, KERNEL32.dll, WINSPOOL.DRV, USER32.dll, GDI32.dll, ole32.dll, OLEAUT32.dll, ADVAPI32.dll, psp_wobj.dll, orcadfnc.dll, MSVCRT.dll, MSVCP60.dll, SHELL32.dll | `ev_7de91ffcbce1e692b8c2a4bc9f4300346abd029b00c8152d080950372c2c3198` |

All seven import Microsoft C/C++ runtime libraries. Six import MFC42. PSpice, Model Editor, and Stimulus Editor import `psp_wobj.dll`, while Design Manager imports `dm_eng.dll`. PSpice and Model Editor also carry a TYPELIB resource. This supports investigating shared libraries and possible automation interfaces. Actual interface use remains unverified.

| Executable | Menu resource names | Dialog resource names | Accelerator resource names | REA evidence ID |
| --- | ---: | ---: | ---: | --- |
| pspice.exe | 9 | 74 | 3 | `ev_f5a1899f81015dd9b1ba1d0eafbd64043c4a1f2c6683cf7e44417a06a90a749c` |
| psched.exe | 3 | 17 | 4 | `ev_0472f9aa2fee7f87835f8dda5ec7c1bf3ec678503698b50ab99600a7ef6716a5` |
| modeled.exe | 7 | 52 | 2 | `ev_23247b597624e8ffb9a13416fe51c11d2953947e38e3cae434426bf9a5f95c66` |

These counts describe resource directory entries. They cannot establish how many menus, dialogs, or shortcut groups users encounter. REA bound the PSpice executable by SHA-256 and its native inventory call returned `provider_unavailable`. Ghidra, IDA, and Kuna were unconfigured and Hopper was unsupported on this host. The portable PE parsers produced the evidence above without a native provider.

## Open questions and acceptance checks

| Area | Current evidence | Check needed before parity can be claimed |
| --- | --- | --- |
| Main windows and navigation | Partial. Shipped Capture dialog screenshots and several documented command paths. | Record each verified original main window, menus, toolbars, modal dialogs, focus, shortcuts, and enabled states. |
| Schematic editing | Partial. Capture part placement documentation and separate Schematics executable. | Place and wire parts, edit properties, undo, save and reopen. Check Capture and Schematics independently. |
| Project and profile data | Partial. One OPJ and one SIM inspected. | Change active profile, relocate a project, compare files before and after a save, and verify missing-file behavior. |
| Simulation and Probe | Partial. Release notes and sample references. | Run the supplied transient and AC cases. Compare generated files, numerical results, marker association, units, axes, error states, and pause/resume where supported. |
| Supporting tools | Static role and import inventory. | Exercise model and stimulus editing, design and simulation management, and diagnostic viewing. Capture input and output artifacts. |
| Architecture | Static. Native resources and direct PE imports. | Use a configured REA provider for targeted code inspection and a verified runtime to correlate module and file behavior. |
| Runtime evidence | Unresolved. Three primary executables returned Authenticode `NotSigned`, and the root NFO contains a crack reference. These observations do not independently establish which files were modified. The supplied installation was left unlaunched. | Obtain a trusted matching installation or a verified isolated instance. Include licensed Capture/CIS/Layout binaries if these are in scope. |
| Implementation and comparison | Pending. The investigation has not reached live verification, and no application has been implemented. | Use the verified workflows and numeric/file fixtures for functional comparison. A visually indistinguishable reproduction requires ownership of, or permission to reproduce, the original interface. |
