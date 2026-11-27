# CircuitBench

Independent Python/PyQt6 schematic editor and ngspice front end. The [architecture proposal](reference/ARCHITECTURE-PROPOSAL.md) records how its workflow relates to the investigation.

Workbench source and tests are licensed under [GPL-3.0-only](LICENSE). See the [workflow coverage and roadmap](PARITY.md) for the original evidence, current functionality, and comparison gaps.

## Run

Open **CircuitBench.exe** on Windows x64. **Help → About CircuitBench** shows the installed version. The executable contains Python, Qt, ngspice 47, and the optional starter libraries. It extracts its runtime to a temporary directory at launch. Save schematics and simulation results separately in a location where you have write access.

To work from source, install PyQt6 with `python -m pip install -r requirements.txt` and run `python app.py` from this folder. The source tree uses the same bundled ngspice. Set `NGSPICE` or use **Simulation → Choose ngspice executable** to override it.

Use **File → Examples → Load voltage divider example**, then choose **Netlist** to inspect the circuit or press **F5** to run the operating point. A 10 V source feeding equal resistors produces 5 V on `v(out)`.

For a frequency-response example, choose **File → Examples → Load RC filter AC example**, confirm **AC sweep** in the Analysis box, and press **F5**. To build your own AC circuit, press **P** and place **Vphase** or **Iphase**. Both start with `DC 0 AC 1 0`, which specifies zero DC bias and a small-signal excitation of unit amplitude at 0°. Use **Analysis setup** to set sweep limits and points per decade.

Search the part library for **Vsin**, **Vpulse**, **Vpwl**, **Vexp**, or their current-source counterparts. Double-click a placed source and open **Source parameters** to edit DC bias, AC magnitude and phase, or the selected transient waveform using named inputs. AC phase and SIN phase affect different analyses. [ngspice source syntax](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf)

- Drag a component from the library onto the schematic, or choose it and click to place it. A two-terminal part dropped onto a clear wire segment is inserted in series. Edit the selection in Properties. **W** routes wires between two grid points, **J** adds a junction, **R** rotates an unwired part, and **Esc** returns to selection. Scroll to zoom and middle-drag to pan.
- In **Select**, drag from empty space to draw a rectangle. **Shift-drag** extends the selection from anywhere and **Shift-click** toggles individual items. Drag a selected item to move the group, use arrow keys to nudge it, or press **Delete** to remove it. Connected wires preview their new route as you drag, and grid guides show alignment with nearby parts. Moves that would short or disconnect devices are rejected.
- Search **OpAmp** or **OP07** to place the bundled five-pin operational amplifier. Placement embeds the SPICE model in the schematic. Wire **IN+**, **IN−**, **V+**, **V−**, and **OUT**.
- Press **P** to search for a part, **Enter** to select it for placement, or **F** to fit the schematic.
- A crossing joins only at an explicit junction or an electrical endpoint. A shared net label connects matching names. Ground maps to SPICE node 0.
- Choose an analysis in Properties and configure it with **Profile**. **Run** generates a frozen project snapshot, a plain schematic netlist, and a solver input deck. Each job keeps its waveform, logs, and status in a unique run directory.
- Use the **Simulation** menu to create or duplicate named profiles, including DC source sweeps, and reopen prior results and logs through **Run history**.
- Use **Markers → Voltage** on a wire or pin, **Differential voltage** on the positive and negative nodes in that order, or **Current into pin** on a device terminal. Marked signals appear automatically on the next run. Use the same menu to hide, remove, or clear markers. Voltage markers are anchored to sheet positions, so check them after rewiring.
- Use **View → Theme** to choose System, Light, or Dark. The desktop remembers your choice. Open **View → Measurement markers** to select markers from the list, or click them on the schematic in **Select** mode. Shift-click to select multiple markers, press **Delete** to remove them, and use **Undo** to restore them.
- Operating-point runs label the schematic with node voltages and current arrows. The **Readings** panel lists the same values and directions. Select a reading to highlight its net on the schematic. Current readings also mark the measured terminal. Use **View → Show schematic readings** to toggle the labels. Operating-point and DC sweep results keep the schematic in view. Use **Simulation → Show plot** to open the result.
- In **Readings → Voltage label appearance**, set the color, text size, and horizontal or vertical offset for all nodes or for an individual node. Offsets are measured in pixels. These settings are saved with the schematic.
- Each run creates a page in the separate **CircuitBench Simulation** window. Use **Add plot page**, **Remove plot page**, **Add trace**, and **Remove trace** to manage what is visible. Trace colors and AC magnitude, dB, or phase are per page. Click or drag on the plot to read copyable sample values. Right-click to clear the current sample, or turn off **Cursor** to keep it hidden on the plot and in exports. Saved pins remain visible. CSV export uses the selected page's result vectors.

### Build reusable subcircuits

1. Draw and save the amplifier, filter, or other circuit as a `.cwb`. Add net labels to identify the intended signals. A circuit that needs external power should expose those power nets too.
2. Choose **Draw → Build reusable subcircuit** and select the saved schematic. Name the block and map each external port to a schematic net. Names such as `In+` and `Out-` are accepted. Ports mapped to the same net are internally connected, so a filter can expose both `In-` and `Out-` on one return wire. Use **Source schematic** to see the selected net highlighted. Under **Block symbol**, drag a pin to a side or use **Move up** and **Move down** to adjust port order.
3. Save the reusable block as a `.cwb`. In the destination circuit, choose **Import → Import CircuitBench subcircuit**. Find the block in the component library and place and wire it like any other part.

The destination schematic embeds the reusable block and its dependencies. You can nest reusable blocks and include imported SPICE models. Map the source schematic's ground net to a port if the block needs an external return. An unmapped ground stays global SPICE ground. To change a block already in use, edit its source and import it with a new name.

### Build and export a plot

- Use **Add subplot** and choose **Stacked** or **Two columns** to arrange plots on the same tab. Click a graph to make its subplot active. The **Plot 1**, **Plot 2**, etc. selector controls where expressions are inserted and which plot is exported or removed. Each subplot has its own traces, colors, display mode, and labels.
- Click or drag near a trace, then use **Pin on graph** to save the cursor's last sample. For manual placement, select a trace and use **Pin by X** to enter a coordinate and optional label.
- Use **Fit data** to clear manual limits and zoom. Scroll over the graph or use the right-side **+**, **−**, and slider to zoom from 25% to 800%. **Fit data** returns to 100%. **Axis limits** provides per-axis fit, manual ranges, engineering suffixes, tick count and precision, and linear or logarithmic X. Choose **Linear** for a range that starts at zero.
- To inspect a **V–I curve**, select the device voltage as **X signal** and the device or pin current as the trace. The plot, cursor, pins, and exports use those coordinates.
- Choose a marker shape before pinning a sample. In **Appearance**, edit the selected trace's line pattern, width, and color, change the title and axis names, or choose plot and grid colors.
- Use the lower expression box to create as many derived traces as needed, one at a time. For example, enter `gain = db(v(out) / v(in))`, `double = multiply(v(out), 2)`, or `log_frequency = log10(frequency)`. Functions include `log`, `ln`, `loge` (natural log), `log10`, `db` (20 log₁₀ of magnitude), `abs`, `mag`, `real`, `imag`, `phase` (degrees), `sqrt`, `exp`, `sin`, `cos`, `pow`, and `multiply`. Undefined samples are shown as gaps. Use `trace("signal name")` for a signal name containing spaces or punctuation.
- **Export plot** writes the selected subplot. **Export page** writes every subplot in the current tab. Choose PNG or PDF in the save dialog. PDF reports contain up to two rows of plots per physical page. Exports include the current trace colors, labels, pins, and cursor. Choose **Save layout for active profile** to reuse plot settings when rerunning the analysis.

## Tests

Run `python -m unittest -v`. Core tests need only Python. The electrical tests run with ngspice and cover the divider operating point, RC step response and AC cutoff, and diode current. The offscreen PyQt tests exercise editing, undo/redo, rendering, and the Run action through waveform loading and persistence.

The Windows verification environment uses PyQt6 6.12.1 and the ngspice 47 console executable.

## Current scope

The default palette includes passives, independent sources, ground, net labels, one generic diode and generic BJTs, MOSFETs, JFETs and voltage-controlled switches. Qucs-S diode files are loaded on demand.

Discrete transistors have conventional schematic symbols. BJTs use collector at the top, base at the left, and emitter at the bottom. FETs use drain at the top, gate at the left, source at the bottom, and a right-hand bulk pin on four-terminal MOSFETs. Select a device to see its exact wire targets. Older connected devices retain their original wire endpoints, indicated by dotted leads on the new symbols.

**Import → Import model libraries** accepts multiple files in one selection, including SPICE `.MODEL` cards, self-contained `.SUBCKT` definitions, and supported Qucs-S diode records. Compatible definitions are imported and skipped items are listed in Diagnostics. SPICE library definitions are embedded in the project. For Qucs-S, placing a device embeds its translated model.

The SPICE importer supports numeric diode, BJT, MOSFET, VDMOS, JFET and voltage-controlled switch models. Subcircuits retain the declared order of 1–16 ports and use labeled symbols. Supported bodies include passive parts, DC sources, linear controlled sources, supported local semiconductor models, and simple arithmetic over numeric parameters. Nested subcircuits and arbitrary behavioral or simulator directives are outside this subset. [Qucs-S SPICE subcircuits](https://qucs-s-help.readthedocs.io/en/latest/subckts-and-ext-models/spice-models/using-spice-subckt-models.html)

Use **Import → Import local ngspice starter pack** to add the included bipolar, JFET, power MOSFET and op-amp models. **Import → Import bundled Qucs-S diodes** loads the included diode catalog on demand. The Import menu also links to the upstream collections. [ngspice model collections](https://ngspice.sourceforge.io/modelparams.html) · [Qucs-S library repository](https://github.com/ra3xdh/qucs_s/tree/current/library)

The verification catalog comes from the [Qucs-S Diodes library at commit 941b4b2](https://github.com/ra3xdh/qucs_s/blob/941b4b2a2774d5db581dfb3538c0b4f83165dcfc/library/Diodes.lib). The Draw menu and Fit command use the installed PSpice Schematics' menu resources as workflow references. See REA evidence record `ev_0472f9aa2fee7f87835f8dda5ec7c1bf3ec678503698b50ab99600a7ef6716a5` in the [installation evidence](reference/rea-evidence.json).

## Build the Windows executable

Use 64-bit Python 3.15 and install `requirements-build.txt`. Run `.\build-windows.ps1` from this folder to build `CircuitBench.exe`. The executable embeds the `runtime` directory and uses the native windowed bootloader. The [runtime notices](runtime/licenses/NOTICES.md) identify the embedded components. [PyInstaller usage](https://www.pyinstaller.org/en/stable/usage.html)

## Desktop simulation studies

Open **Simulation → Simulation studies** or use **Simulation studies** in the plot window.

- Click a trace, press **Set A**, then click or drag to position B. The readout gives the differences and, on a time axis, the reciprocal time difference. **Cursor** hides both lines for printing. **Save layout for active profile** stores the current page in the schematic. Save the project to keep it across sessions.
- **Measure waveform** uses a selected interval and can pin the contributing samples. **Frequency spectrum** resamples transient data onto a uniform grid and uses a Hann window. Its distortion estimate assumes a bin-centered fundamental that is the strongest tone. The window should contain several whole periods of the settled signal.
- **Analysis → Analysis setup** enables several analyses for one Run. Click a profile to edit its settings, or enable **Parametric Sweep**, select a base analysis and enter component values. Each job saves its snapshot, netlist, waveform and logs under **Simulation Runs** beside the schematic (in the user home folder for unsaved projects).
- Use **Set A** on the first sample and **Set B** to place the second. Trace expressions accept RMS, MAX, MIN and MEAN over finite samples. **Measure waveform** lets you select statistics for an interval.
- Use **View → Canvas dots**, **Draw → Text annotation** and **File → Export → Schematic diagram** to prepare PNG or PDF diagrams.
- **Compare saved runs** overlays a common signal and lists value or profile changes. **Show operating-point power** annotates the schematic with signed watts. **Transient component power** plots instantaneous absorbed power and measures its average and peak over the chosen interval.
- **Export simulation report** writes a PDF from the selected result's schematic snapshot, profile, component values, subplots and measurement summary.
- In **Draw**, use **Schematic diagnostics** to locate connection problems. Use **Add part revision to library** to archive a reusable block in a folder, then **Browse reusable part library** to search and preview revisions before importing. Double-click an embedded schematic block to inspect its internal circuit and follow nested instances. The designer summarizes external pins, electrical nets and shared connections.

## Desktop v0.3 beta plot controls

Use **File → Examples** for the built-in circuits, **File → Export** for schematic diagrams, active plots, plot pages and CSV data, and **Import** for models, reusable parts and saved waveforms.

Click a waveform and use the left and right arrows to step through samples. **Set A** anchors the current sample. **Set B** activates the second cursor. Press the same button again to remove its marker. **Intersections** locates isolated crossings of two visible traces by interpolation between plotted samples. **Pinned points** lets you select a pin, replace its complete label or delete it.

The expression bar accepts typed formulas, offers the supported math functions, and can insert a selected or typed variable name. Press Enter to add the expression to the current subplot. After closing the last plot page, **Add plot page** can reopen its waveform.
