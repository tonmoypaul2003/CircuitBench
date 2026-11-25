# PSpice Schematics UI reference

Target: `C:\Program Files\Orcad\PSpice\psched.exe`. The screenshot supplied in chat shows a blank schematic with a broad dotted sheet, numbered and lettered sheet zones, a top command bar, a narrow left tool strip, and a coordinate/status line. It shows the initial visual hierarchy only. Icons, command state transitions, toolbar customization, coordinate units, and interaction behavior cannot be established from that frame.

REA's PE resource record `ev_0472f9aa2fee7f87835f8dda5ec7c1bf3ec678503698b50ab99600a7ef6716a5` identifies static menus, dialogs, and accelerators. Menu strings recovered from the referenced byte ranges include Draw → Place Part, Wire, Get New Part and Rewire, View → Fit and Pan & Zoom, and Analysis → Simulate. The executable was analyzed as a file. No live session or tooltip behavior was inferred. [REA evidence](rea-evidence.json)

## Workbench changes

Workbench prioritizes its drawing sheet at startup. Its top bar holds project, view and simulation actions, with drawing actions on the left. The component library opens with **P**, properties open by double-clicking a part in Select mode, and netlist or simulation actions reveal the output panel. The View menu toggles all three panels. The sheet has an independent color palette, numbered and lettered zones, and live grid coordinates. Existing Qucs-S model browsing and ngspice simulation use the same project workflows. [Workbench preview](screenshots/workbench-sheet.png)

## Ghidra evidence

Ghidra 12.2 imported the same x86 PE32 file into the local project at image base 0x00400000. Its SHA-256 matches the REA artifact. The 60-second auto-analysis reached its limit, after which the export and project save succeeded. Recovered external imports include MFC 4.2 document/view, control-bar and status-bar APIs. Defined strings at `00708bf2`, `007081e0`, and `00709442` describe drawing a wire, toggling toolbars, and fitting the page, respectively. This corroborates command intent. The bounded export did not resolve command-to-handler links for these strings. [Ghidra log](ghidra-psched.log) · [Strings](static/psched.exe/strings.tsv) · [Imports](static/psched.exe/externals.tsv)
