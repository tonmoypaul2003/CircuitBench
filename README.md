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

### Parameterized designs and simulation studies

- Open **Edit > Design variables** to define named expressions such as `{"BASE":"1k","LOAD":"2 * BASE"}`. Enter `{LOAD}` as a passive value. Select a reusable block and use **Edit > Instance parameters** to override its exposed variables for that instance.
- In **Analysis setup**, select one or more analyses. A component sweep can vary a passive reference, a design variable such as `$LOAD`, or `@temperature`. Add a second target to run every combination, up to 64. Each run saves a `dataset.json` manifest with the analysis mode, axis, vectors, units, sample count and sweep values.
- **Analysis setup** offers noise density, pole-zero and DC sensitivity referenced to ground, Fourier of a selected transient node, and small-signal distortion. Fourier harmonics and THD appear in the solver log. The distortion plot displays the final spectrum written by ngspice.
- The part library includes controlled and behavioral sources and XSPICE gates with analog inputs and outputs. For current-controlled sources, enter the sensing voltage-source reference and gain, such as `V1 2`. Behavioral values use `V=` or `I=` followed by an expression, such as `V=2*v(in)`. In **Simulation > Simulation options**, use `{"couplings":[["L1","L2","0.99"]]}` to couple a transformer’s two inductors. The same editor accepts temperature, initial conditions, node sets, and solver tolerances. Imported model libraries can follow relative includes within the selected library directory.
- **Simulation > Design studies** includes a live tuning slider with Apply and Cancel, seeded Monte Carlo with a yield window, and bounded grid optimization. Each study varies one parameter. Yield and optimization use the selected signal’s final sample. Completed studies appear in **Session History** and retain their settings, results and solver errors.

**Automatic waveform measurements** saves named metrics with the schematic. Select a metric, enter a result vector and optional X bounds, and run an analysis or nested sweep. The run log and each run's `dataset.json` show the calculated values or the reason a reading is unavailable. **DC Transfer Function** in Analysis Setup reports the small-signal gain, input impedance and output impedance for the selected source and node.

### Numerical waveform analysis

The plot function menu includes `derivative(v(out))` for slew rate, `cumintegral(i(V1))` for accumulated charge, `unwrap(phase(v(out)))` for continuous phase in degrees, and `groupdelay(v(out)/v(in))` for group delay in seconds on a frequency sweep. Differentiation and integration use the simulation's original X values, including uneven spacing. Unwrapping assumes adjacent phase samples differ by less than 180° before wrapping. Cumulative integration skips gaps and resumes from the last total. Use the plot's linear X scale to inspect DC-inclusive spectra or the logarithmic scale for wide frequency ranges.

**Measure waveform** also offers signed slope extrema, mean slope and absolute integral. When both −3 dB edges are visible, it can report geometric center frequency and loaded Q. RMS continues to weight each sample equally.

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

## Desktop v0.4 beta

Analysis Setup controls the selections shown in the toolbar. Transient profiles accept a start time. A parametric batch produces one summary of the final sample versus component value. Use **Sweep runs** to open an individual waveform.

Double-click a pin to edit its label, color and coordinate precision. Intersection labels start at X1 with three decimals. **Simulation report** lets you choose schematic settings, data points, available operating-point readings and open plot pages for A4 PDF export.

Open **Simulation → Session History** for a maximized tree or **View → Session History** for the docked tree. **Simulation → New session** starts a new group for the current schematic. Session history uses the saved schematic path. Earlier flat run folders remain available through **Import → Open saved waveform**.

Select or drag annotation text on the canvas and press Delete to remove it. **Label appearance** in Edit and Properties works with annotations and component symbols. **Show schematic readings** toggles the net labels and operating-point overlay. Use the power marker on a two-terminal component. Positive watts indicate absorption and negative watts indicate delivery. AC uses average real power for peak-amplitude phasors.

## Desktop v0.4.2 beta

Power markers use lowercase **p**. Marker units are W for power, V for voltage and A for current. The schematic shows SI-scaled values after a matching operating-point run, and plot readouts show scaled values when inspecting samples.

In either session tree, Ctrl-click or Shift-click to select multiple runs or sessions. Press **Delete** or choose **Delete selected** and confirm. Deleting a session removes its saved runs. **Sweep runs** shows a table with component value, base analysis and session. **Saved waveforms** lists available runs for the current schematic. History and plot tabs use descriptive display labels while retaining the existing run directories.

## Desktop v0.4.3 beta

Drag a component or marker label in Select mode to reposition it. Labels start to the left. Select a part, marker or annotation and use **Label appearance** in Properties or Edit for text size and color. Parts have separate reference and value toggles. Markers can hide their ID or measurement and use filled or hollow points.

A denser plot grid adds axis ticks. The cursor guide dropdown offers vertical, horizontal or both directions, and the readout shows A, B and their differences. **Appearance → Chart text** controls the plot font. **Pinned points** controls per-point label size, text color and fill.

## Desktop v0.4.5 beta

**Intersections** asks for a maximum count and inserts up to that many new crossings inside the displayed axes. **Measure waveform** uses the active subplot, its axis assignments and its displayed trace values. Measurement pins use visible samples and honor a maximum count. Use Ctrl-click or Shift-click in **Pinned points** to select several pins, then press Delete, or use **Delete all**.

The grid shows separate sliders beside the X and Y axes. Axis Limits can also set the grid-on tick count for each axis and the common grid-off tick count. Each supports 2–25 ticks. Parametric plots include all available marker traces for each sweep value, and operating-point plots keep marker readings visible.

Press **F6** to switch between schematic and simulation, or **Shift+F6** to return from simulation. Use **Ctrl+M** for voltage, **Ctrl+Shift+M** for differential voltage, **Ctrl+Alt+I** for current and **Ctrl+Alt+P** for power.

## Desktop theme refresh

The light and dark themes use slate panels and quiet grid lines. The blue Run control stays prominent, and drawing tools use circuit-specific line icons alongside their names. Simulation defaults to teal and copper traces, while explicit chart and schematic colors retain precedence. Use **View → Theme** to choose System, Light or Dark.

### Customize the desktop theme

Edit **CircuitBench.qss** to change the shared Qt widget rules and the light and dark palettes. The palette also supplies the default schematic, plot, and toolbar icon colors. From a Windows release, place a copy of the file next to `CircuitBench.exe` and restart the app or reselect **View → Theme**. The executable includes defaults, so the stylesheet is optional for normal use. Menus and dropdowns use the theme’s rounded selection highlights.

### Control which parameters appear on the schematic

In **Source parameters**, the checkbox next to each input controls its canvas label. Changing one switches the source from its compact value string to individual labels. DC bias, AC magnitude, AC phase, waveform type and each waveform input can be shown independently.

For any part, open **Properties → Label appearance** and enable **Show individual parameters** to choose fields. Available fields include passive values and stored model or subcircuit parameters. Turn off **Show parameters** to hide the whole group or turn off **Show individual parameters** to restore the compact label. Drag individual labels in Select mode. Visibility and placement are saved per component.

### Accent color

Choose **View → Theme → Accent color** to tint the application, schematic and default traces from one saved color. Light and dark modes derive their own shades. **Reset accent color** restores the stylesheet palette. Per-plot and per-component color overrides continue to take precedence. Individual parameter labels use abbreviated names on the canvas, while editing dialogs retain the full parameter names.
