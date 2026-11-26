"""Interactive simulation plots and export."""
import math
from pathlib import Path

from PyQt6.QtCore import QIODevice, QPointF, QRectF, QSaveFile, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPageSize, QPainter, QPainterPath, QPdfWriter, QPen
from PyQt6.QtWidgets import (QApplication, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSlider,
                             QVBoxLayout, QWidget)

from plot_math import evaluate
from results import Plot


class Waveform(QWidget):
    colors = ["#087e74", "#c85528", "#5157ad", "#9b3b70", "#426925"]
    readout = pyqtSignal(str)
    cursor_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.plot = None
        self.columns = []
        self.presentation = 0
        self.trace_colors = {}
        self.cursor = None
        self.cursor_column = None
        self.title = ""
        self.x_label = ""
        self.y_label = ""
        self.pins = []
        self.grid_visible = True
        self.grid_density = 4
        self.custom_height = None
        self.background = None
        self.x_limits = None
        self.y_limits = None
        self.screen_axes = None
        self.setMinimumHeight(230)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.move_cursor(event.position().x(), event.position().y())
        elif event.button() == Qt.MouseButton.RightButton:
            self.cursor = None
            self.cursor_column = None
            self.cursor_changed.emit()
            self.readout.emit("")
            self.update()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.move_cursor(event.position().x(), event.position().y())

    def move_cursor(self, x, y=None):
        if self.screen_axes is None:
            return
        bounds, xs, columns, plotted, xmin, xmax, ymin, ymax = self.screen_axes
        if plotted != self.columns:
            return
        ratio = min(1., max(0., (x-bounds.left())/bounds.width()))
        x_target = xmin + ratio*(xmax-xmin)
        candidates = (i for i in range(len(xs))
                      if xmin <= xs[i] <= xmax and any(ymin <= values[i] <= ymax for values in columns))
        sample = min(candidates, key=lambda i: abs(xs[i]-x_target), default=None)
        if sample is None:
            return
        y_target = ymin if y is None else ymin + (bounds.bottom()-y)/bounds.height()*(ymax-ymin)
        index = min((i for i, values in enumerate(columns) if ymin <= values[sample] <= ymax),
                    key=lambda i: abs(columns[i][sample]-y_target))
        self.cursor, self.cursor_column = sample, plotted[index]
        self.cursor_changed.emit()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self.draw(painter, self.width(), self.height(), report=False)

    def chart_background(self, report=False):
        dark = QApplication.instance().property("circuitbenchDark")
        return self.background or QColor("#ffffff" if report else "#1b2d28" if dark else "#f7faf8")

    def color(self, column, background=None):
        base = QColor(self.colors[(column-1) % len(self.colors)])
        if (background or self.chart_background()).lightness() < 128:
            base = base.lighter(165)
        return self.trace_colors.get(column, base)

    def draw(self, painter, width, height, *, report=True):
        painter.save()
        try:
            self.draw_chart(painter, width, height, report)
        finally:
            painter.restore()

    def draw_chart(self, painter, width, height, report):
        if not report:
            self.screen_axes = None
        rect = QRectF(0, 0, width, height)
        background = self.chart_background(report)
        ink = QColor("#173d37" if background.lightness() >= 128 else "#f0f7f4")
        painter.fillRect(rect, background)
        painter.setPen(ink)
        if self.plot is None or not self.columns:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Add a trace to plot")
            return
        rows = self.plot.rows
        title = self.title or self.plot.title
        painter.drawText(QPointF(12, 18), title)
        if "operating point" in self.plot.title.lower():
            for index, column in enumerate(self.columns[:max(0, (height-42)//18)]):
                label = self.plot.names[column]
                painter.setPen(self.color(column, background))
                painter.drawText(QPointF(18, 38+index*18),
                                 f"{label}   {rows[0][column].real:.7g} {self.plot.units[column]}")
            return
        logarithmic = self.plot.names[0].lower() == "frequency" and all(r[0].real > 0 for r in rows)
        xs = [math.log10(row[0].real) if logarithmic else row[0].real for row in rows]
        columns = []
        for column in self.columns:
            values = [row[column] for row in rows]
            if self.plot.units[column] == "expression":
                use_magnitude = any(math.isfinite(value.imag) and value.imag for value in values)
                values = [abs(value) if use_magnitude else value.real for value in values]
            elif self.presentation == 1:
                values = [math.degrees(math.atan2(value.imag, value.real)) for value in values]
            elif self.presentation == 2:
                values = [20*math.log10(max(abs(value), 1e-300)) for value in values]
            elif logarithmic:
                values = [abs(value) for value in values]
            else:
                values = [value.real for value in values]
            columns.append(values)
        metrics = painter.fontMetrics()
        legend_x, legend_y = 12, 36
        for column in self.columns:
            name = metrics.elidedText(self.plot.names[column], Qt.TextElideMode.ElideRight, max(45, width-58))
            label_width = metrics.horizontalAdvance(name) + 32
            if legend_x + label_width > width-8 and legend_x > 12:
                legend_x, legend_y = 12, legend_y+16
            if legend_y > height-110:
                painter.setPen(ink)
                painter.drawText(QPointF(12, legend_y), "Additional traces…")
                break
            painter.setPen(QPen(self.color(column, background), 2))
            painter.drawLine(QPointF(legend_x, legend_y-4), QPointF(legend_x+13, legend_y-4))
            painter.drawText(QPointF(legend_x+17, legend_y), name)
            legend_x += label_width
        cursor_footer = 18 if report and self.cursor is not None else 0
        left, right, top, bottom = 74, width-26, legend_y+27, height-43-cursor_footer
        bounds = [(min(valid), max(valid)) for values in columns
                  if (valid := [value for value in values if math.isfinite(value)])]
        if not bounds or right <= left or bottom <= top:
            return
        ymin, ymax = self.y_limits or (min(x[0] for x in bounds), max(x[1] for x in bounds))
        xmin, xmax = min(xs), max(xs)
        if self.x_limits:
            xmin, xmax = self.x_limits
            if logarithmic:
                xmin, xmax = math.log10(xmin), math.log10(xmax)
        if ymax == ymin:
            pad = max(abs(ymin)*.05, 1e-12)
            ymin, ymax = ymin-pad, ymax+pad
        if not report:
            self.screen_axes = (QRectF(left, top, right-left, bottom-top), xs, columns,
                                self.columns.copy(), xmin, xmax, ymin, ymax)
        if self.grid_visible:
            grid_color = QColor(ink)
            grid_color.setAlpha(42)
            painter.setPen(QPen(grid_color, 1))
            for tick in range(1, self.grid_density):
                x = left+(right-left)*tick/self.grid_density
                y = top+(bottom-top)*tick/self.grid_density
                painter.drawLine(QPointF(x, top), QPointF(x, bottom))
                painter.drawLine(QPointF(left, y), QPointF(right, y))
        painter.setPen(ink)
        painter.drawRect(QRectF(left, top, right-left, bottom-top))
        for i in range(5):
            x = left + (right-left)*i/4
            y = bottom - (bottom-top)*i/4
            xlabel = xmin + (xmax-xmin)*i/4
            if logarithmic:
                xlabel = 10**xlabel
            painter.drawText(QPointF(x-10, bottom+17), f"{xlabel:.3g}")
            painter.drawText(QPointF(6, y+4), f"{ymin+(ymax-ymin)*i/4:.3g}")
        xlabel = self.x_label or f"{self.plot.names[0]} ({self.plot.units[0]})"
        ylabel = self.y_label or (("Magnitude", "Phase (°)", "Magnitude (dB)")[self.presentation]
                                  if logarithmic else "")
        painter.drawText(QPointF(left, height-9-cursor_footer), xlabel)
        painter.drawText(QPointF(left, top-6), ylabel)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        def location(x, y):
            xr = (x-xmin)/(xmax-xmin) if xmax != xmin else .5
            return QPointF(left + (right-left)*xr, bottom - (bottom-top)*(y-ymin)/(ymax-ymin))

        graph = QRectF(left, top, right-left, bottom-top)
        painter.save()
        painter.setClipRect(graph)
        for index, values in enumerate(columns):
            painter.setPen(QPen(self.color(self.columns[index], background), 1.5))
            path = QPainterPath()
            connected = False
            for x, y in zip(xs, values):
                if not math.isfinite(y):
                    connected = False
                    continue
                point = location(x, y)
                (path.lineTo if connected else path.moveTo)(point)
                connected = True
                if len(xs) == 1:
                    painter.drawEllipse(point, 3, 3)
            painter.drawPath(path)
        painter.restore()
        if self.cursor is not None and xmin <= xs[self.cursor] <= xmax:
            sample = self.cursor
            position = location(xs[sample], ymin).x()
            painter.setPen(ink)
            painter.drawLine(QPointF(position, top), QPointF(position, bottom))
            for values in columns:
                if ymin <= values[sample] <= ymax:
                    painter.drawEllipse(location(xs[sample], values[sample]), 3, 3)
            readout = "  |  ".join(
                [f"{self.plot.names[0]} = {rows[sample][0].real:.10g} {self.plot.units[0]}"]
                + [f"{self.plot.names[col]} = {values[sample]:.10g}"
                   for col, values in zip(self.columns, columns)])
            if report:
                visible = metrics.elidedText(readout, Qt.TextElideMode.ElideRight, width-24)
                painter.drawText(QPointF(12, height-7), visible)
            else:
                self.readout.emit(readout)
        for column, sample, label in self.pins:
            if column not in self.columns:
                continue
            value = columns[self.columns.index(column)][sample]
            if not (xmin <= xs[sample] <= xmax and ymin <= value <= ymax):
                continue
            point = location(xs[sample], value)
            painter.setPen(QPen(self.color(column, background), 1.5))
            painter.drawEllipse(point, 4, 4)
            text = f"{label or self.plot.names[column]} ({rows[sample][0].real:.6g}, {value:.6g})"
            text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(30, right-left-12))
            text_width = metrics.horizontalAdvance(text)
            text_x = max(left+4, min(point.x()+7, right-text_width-4))
            text_y = point.y()-7 if point.y() > top+17 else point.y()+17
            painter.fillRect(QRectF(text_x-3, text_y-metrics.ascent(), text_width+6, metrics.height()),
                             background)
            painter.drawText(QPointF(text_x, text_y), text)


class PlotPage(QWidget):
    def __init__(self, plot, selected=None):
        super().__init__()
        self.wave = Waveform()
        self.wave.plot = plot
        self.traces = QListWidget()
        self.traces.setMaximumWidth(230)
        self.presentation = QComboBox()
        self.presentation.addItems(["Magnitude / real", "Phase (degrees)", "Magnitude (dB)"])
        self.presentation.setEnabled(plot.names[0].lower() == "frequency")
        readout = QLineEdit()
        readout.setReadOnly(True)
        readout.setPlaceholderText("Click or drag on the plot to read a sample · right-click to clear")
        self.wave.readout.connect(readout.setText)
        color = QPushButton("Trace color…")
        color.clicked.connect(self.choose_color)
        add = QPushButton("Add trace…")
        add.clicked.connect(self.add_trace)
        drop = QPushButton("Remove trace")
        drop.clicked.connect(self.remove_trace)
        labels = QPushButton("Labels…")
        labels.clicked.connect(self.edit_labels)
        self.auto_pin_button = QPushButton("Pin on graph")
        self.auto_pin_button.setToolTip("Pin the sample at the cursor's last position")
        self.auto_pin_button.clicked.connect(self.pin_cursor)
        self.wave.cursor_changed.connect(self.update_pin_button)
        pin = QPushButton("Pin by X…")
        pin.clicked.connect(self.pin_point)
        remove_pin = QPushButton("Remove last pin")
        remove_pin.clicked.connect(self.remove_pin)
        self.grid_button = QPushButton("Grid")
        self.grid_button.setCheckable(True)
        self.grid_button.setChecked(self.wave.grid_visible)
        self.grid_button.toggled.connect(self.toggle_grid)
        self.grid_density = QSlider(Qt.Orientation.Horizontal)
        self.grid_density.setRange(2, 16)
        self.grid_density.setValue(self.wave.grid_density)
        self.grid_density.setFixedWidth(90)
        self.grid_density.setAccessibleName("Grid density")
        self.grid_density.valueChanged.connect(self.set_grid_density)
        self.limits_button = QPushButton("Axis limits…")
        self.limits_button.clicked.connect(self.edit_limits)
        background = QPushButton("Background…")
        background.clicked.connect(self.choose_background)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(add)
        toolbar.addWidget(drop)
        toolbar.addWidget(color)
        toolbar.addWidget(self.presentation)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        graph_tools = QHBoxLayout()
        for button in (labels, self.limits_button, background, self.auto_pin_button, pin, remove_pin,
                       self.grid_button):
            graph_tools.addWidget(button)
        graph_tools.addWidget(self.grid_density)
        graph_tools.addStretch()
        layout.addLayout(graph_tools)
        content = QHBoxLayout()
        content.addWidget(self.traces)
        content.addWidget(self.wave, 1)
        resize = QVBoxLayout()
        heading = QLabel("Height")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.height_slider = QSlider(Qt.Orientation.Vertical)
        self.height_slider.setRange(229, 900)
        self.height_slider.setSingleStep(10)
        self.height_slider.setPageStep(50)
        self.height_slider.setAccessibleName("Graph height")
        self.height_slider.setToolTip("Plot height. The bottom position uses automatic height.")
        self.height_slider.setValue(229)
        self.height_slider.valueChanged.connect(self.set_graph_height)
        self.height_readout = QLabel("Auto")
        self.height_readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        resize.addWidget(heading)
        resize.addWidget(self.height_slider, 1, Qt.AlignmentFlag.AlignHCenter)
        resize.addWidget(self.height_readout)
        content.addLayout(resize)
        layout.addLayout(content, 1)
        layout.addWidget(readout)
        operating_point = "operating point" in plot.title.lower()
        start = 0 if operating_point else 1
        preferred = next((i for i in range(start, len(plot.names)) if plot.names[i].lower() == "v(out)"), start)
        default = set(selected if selected is not None else
                      range(start, len(plot.names)) if operating_point else [preferred])
        for column in range(start, len(plot.names)):
            self.append_trace(column, column in default)
        first = next((i for i in range(self.traces.count())
                      if self.traces.item(i).checkState() == Qt.CheckState.Checked), -1)
        self.traces.setCurrentRow(first)
        self.traces.itemChanged.connect(self.update_plot)
        self.presentation.currentIndexChanged.connect(self.update_plot)
        self.update_plot()

    def append_trace(self, column, visible):
        item = QListWidgetItem(self.wave.plot.names[column])
        item.setData(Qt.ItemDataRole.UserRole, column)
        item.setCheckState(Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
        self.traces.addItem(item)
        item.setHidden(not visible)
        if visible:
            self.traces.setCurrentItem(item)
        self.update_plot()

    def update_pin_button(self):
        self.auto_pin_button.setEnabled(self.wave.cursor is not None
                                       and self.wave.cursor_column in self.wave.columns)

    def pin_cursor(self):
        sample, column = self.wave.cursor, self.wave.cursor_column
        if sample is not None and column in self.wave.columns:
            self.wave.pins.append((column, sample, ""))
            self.wave.update()

    def toggle_grid(self, enabled):
        self.wave.grid_visible = enabled
        self.grid_density.setEnabled(enabled)
        self.wave.update()

    def set_grid_density(self, density):
        self.wave.grid_density = density
        self.wave.update()

    def set_graph_height(self, height):
        height = None if height == self.height_slider.minimum() else height
        self.wave.custom_height = height
        self.height_readout.setText(f"{height} px" if height else "Auto")
        self.wave.setMinimumHeight(height or 230)
        self.wave.setMaximumHeight(height or 16777215)
        self.wave.update()

    def choose_background(self):
        color = QColorDialog.getColor(self.wave.chart_background(), self, "Plot background")
        if color.isValid():
            self.wave.background = color
            self.wave.update()

    def set_axis_limits(self, x_limits, y_limits):
        logarithmic = (self.wave.plot.names[0].lower() == "frequency"
                       and all(row[0].real > 0 for row in self.wave.plot.rows))
        for axis, limits in (("X", x_limits), ("Y", y_limits)):
            if limits is None:
                continue
            lower, upper = limits
            if not (math.isfinite(lower) and math.isfinite(upper)
                    and lower < upper and math.isfinite(upper-lower)):
                raise ValueError(f"{axis} minimum must be smaller than its maximum, with a finite range.")
            if axis == "X" and logarithmic and (lower <= 0 or math.log10(lower) >= math.log10(upper)):
                raise ValueError("Frequency limits must be positive and distinct on the logarithmic scale.")
        self.wave.x_limits, self.wave.y_limits = x_limits, y_limits
        self.wave.update()

    def edit_limits(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Axis limits")
        form = QFormLayout(dialog)
        inputs = []
        for axis, limits in (("X", self.wave.x_limits), ("Y", self.wave.y_limits)):
            values = limits or (None, None)
            for edge, value in zip(("minimum", "maximum"), values):
                field = QLineEdit("" if value is None else str(value))
                field.setPlaceholderText("Auto")
                inputs.append(field)
                form.addRow(f"{axis} {edge}", field)
        message = QLabel("Leave both limits of an axis blank to use its automatic range.")
        message.setWordWrap(True)
        form.addRow(message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def accept():
            limits = []
            for first, last in zip(inputs[::2], inputs[1::2]):
                lower, upper = first.text().strip(), last.text().strip()
                try:
                    limits.append((float(lower), float(upper)) if lower or upper else None)
                except ValueError:
                    message.setText("Enter two numeric limits for an axis, or leave both blank.")
                    return
            try:
                self.set_axis_limits(*limits)
            except ValueError as error:
                message.setText(str(error))
                return
            dialog.accept()

        buttons.accepted.connect(accept)
        dialog.exec()

    def edit_labels(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Plot labels")
        form = QFormLayout(dialog)
        names = ("title", "x_label", "y_label")
        fields = [QLineEdit(getattr(self.wave, name)) for name in names]
        for label, field in zip(("Title", "Horizontal axis", "Vertical axis"), fields):
            field.setMaxLength(100)
            form.addRow(label, field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            for name, field in zip(names, fields):
                setattr(self.wave, name, field.text().strip())
            self.wave.update()

    def pin_at(self, column, x, label=""):
        if "operating point" in self.wave.plot.title.lower():
            raise ValueError("Pin points on a sweep or transient plot")
        if column not in self.wave.columns or not math.isfinite(x):
            raise ValueError("Choose a visible trace and a finite X coordinate")
        rows = self.wave.plot.rows
        indices = [i for i, row in enumerate(rows) if math.isfinite(row[column].real)
                   and math.isfinite(row[column].imag)]
        if not indices:
            raise ValueError("This trace has no finite samples")
        sample = min(indices, key=lambda index: abs(rows[index][0].real-x))
        self.wave.pins.append((column, sample, label.strip()))
        self.wave.update()
        return rows[sample][0].real

    def pin_point(self):
        item = self.traces.currentItem()
        if item is None or item.isHidden():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Pin {item.text()}")
        form = QFormLayout(dialog)
        coordinate, label = QLineEdit(), QLineEdit()
        coordinate.setText(f"{self.wave.plot.rows[0][0].real:g}")
        label.setMaxLength(80)
        form.addRow(f"{self.wave.plot.names[0]} value", coordinate)
        form.addRow("Point label (optional)", label)
        message = QLabel("Snaps to the nearest available sample.")
        form.addRow(message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def apply():
            try:
                self.pin_at(item.data(Qt.ItemDataRole.UserRole), float(coordinate.text()), label.text())
            except ValueError as error:
                message.setText(str(error))
                return
            dialog.accept()

        buttons.accepted.connect(apply)
        dialog.exec()

    def remove_pin(self):
        if self.wave.pins:
            self.wave.pins.pop()
            self.wave.update()

    def update_plot(self):
        items = [self.traces.item(i) for i in range(self.traces.count())]
        checked = [item for item in items if item.checkState() == Qt.CheckState.Checked]
        for item in items:
            item.setHidden(item.checkState() != Qt.CheckState.Checked)
        self.wave.columns = [item.data(Qt.ItemDataRole.UserRole) for item in checked]
        self.wave.presentation = self.presentation.currentIndex()
        self.limits_button.setEnabled(bool(checked) and "operating point" not in self.wave.plot.title.lower())
        self.update_pin_button()
        if not self.wave.columns:
            self.wave.readout.emit("")
        self.wave.update()

    def add_trace(self):
        available = [self.traces.item(i) for i in range(self.traces.count())
                     if self.traces.item(i).checkState() != Qt.CheckState.Checked]
        if not available:
            return
        name, ok = QInputDialog.getItem(self, "Add trace", "Signal",
                                        [item.text() for item in available], editable=False)
        if ok:
            item = next(item for item in available if item.text() == name)
            item.setCheckState(Qt.CheckState.Checked)
            self.traces.setCurrentItem(item)

    def remove_trace(self):
        item = self.traces.currentItem()
        if item is not None:
            item.setCheckState(Qt.CheckState.Unchecked)

    def choose_color(self):
        item = self.traces.currentItem()
        if item is not None and item.checkState() == Qt.CheckState.Checked:
            column = item.data(Qt.ItemDataRole.UserRole)
            color = QColorDialog.getColor(self.wave.trace_colors.get(column, QColor("#087e74")), self)
            if color.isValid():
                self.wave.trace_colors[column] = color
                item.setForeground(color)
                self.wave.update()


class PlotReportPage(QWidget):
    def __init__(self, plot, selected=None):
        super().__init__()
        self.title = plot.title
        self.revision = None
        self.plot = Plot(plot.title, plot.names.copy(), plot.units.copy(), [list(row) for row in plot.rows])
        self.subplots = []
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.active = QComboBox()
        self.active.setAccessibleName("Active subplot")
        controls.addWidget(self.active)
        for label, action in (("Add subplot", self.add_subplot), ("Remove subplot", self.remove_subplot),
                              ("Export plot…", lambda: self.choose_export(False)),
                              ("Export page…", lambda: self.choose_export(True))):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, callback=action: callback())
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        content = QWidget()
        self.stack = QVBoxLayout(content)
        scroller.setWidget(content)
        layout.addWidget(scroller, 1)
        insert_bar = QHBoxLayout()
        self.expression = QLineEdit()
        self.expression.setPlaceholderText("Expression, e.g. gain = db(v(out) / v(in))")
        self.expression.setMaxLength(575)
        self.expression.returnPressed.connect(self.insert_expression)
        insert = QPushButton("Add expression")
        insert.clicked.connect(self.insert_expression)
        insert_bar.addWidget(self.expression, 1)
        insert_bar.addWidget(insert)
        layout.addLayout(insert_bar)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.add_subplot(selected)

    @property
    def wave(self):
        return self.subplots[self.active.currentIndex()].wave

    @property
    def traces(self):
        return self.subplots[self.active.currentIndex()].traces

    @property
    def presentation(self):
        return self.subplots[self.active.currentIndex()].presentation

    def add_subplot(self, selected=None):
        if len(self.subplots) == 12:
            self.message.setText("A page can contain up to 12 subplots.")
            return
        subplot = PlotPage(self.plot, selected)
        subplot.wave.title = f"Plot {len(self.subplots)+1}"
        self.subplots.append(subplot)
        self.stack.addWidget(subplot, 1)
        self.active.addItem(f"Plot {len(self.subplots)}")
        self.active.setCurrentIndex(len(self.subplots)-1)
        return subplot

    def remove_subplot(self):
        if len(self.subplots) == 1:
            self.message.setText("Keep one plot on each page.")
            return
        index = self.active.currentIndex()
        subplot = self.subplots.pop(index)
        self.stack.removeWidget(subplot)
        subplot.deleteLater()
        self.active.removeItem(index)
        for index in range(self.active.count()):
            self.active.setItemText(index, f"Plot {index+1}")

    def add_expression(self, text):
        expression = text.strip()
        if "=" in expression:
            name, expression = (value.strip() for value in expression.split("=", 1))
        else:
            name = expression
        if not name or len(name) > 75:
            raise ValueError("Use a trace name of up to 75 characters, for example gain = db(v(out))")
        if any(name.casefold() == existing.casefold() for existing in self.plot.names):
            raise ValueError(f"A trace named {name} already exists")
        if (len(self.plot.names)+1)*len(self.plot.rows) > 2_000_000:
            raise ValueError("The plot has reached the sample limit")
        values = evaluate(expression, self.plot)
        column = len(self.plot.names)
        self.plot.names.append(name)
        self.plot.units.append("expression")
        for row, value in zip(self.plot.rows, values):
            row.append(value)
        for index, subplot in enumerate(self.subplots):
            subplot.append_trace(column, visible=index == self.active.currentIndex())
        return column

    def insert_expression(self):
        try:
            column = self.add_expression(self.expression.text())
        except (ArithmeticError, ValueError) as error:
            self.message.setText(str(error))
            return
        self.message.setText(f"Added {self.plot.names[column]}")
        self.expression.clear()

    def choose_export(self, whole_page):
        filename, file_filter = QFileDialog.getSaveFileName(
            self, "Export plot page" if whole_page else "Export active plot",
            filter="PNG image (*.png);;PDF report (*.pdf)")
        if not filename:
            return
        target = Path(filename)
        if not target.suffix:
            target = target.with_suffix(".pdf" if file_filter.startswith("PDF") else ".png")
        try:
            self.export(target, whole_page)
        except (OSError, ValueError) as error:
            self.message.setText(str(error))
        else:
            self.message.setText(f"Exported {target.name}")

    def export(self, path, whole_page=True):
        waves = [subplot.wave for subplot in self.subplots] if whole_page else [self.wave]
        suffix = path.suffix.lower()
        if suffix not in (".png", ".pdf"):
            raise ValueError("Choose a PNG or PDF file")
        width = 810
        charts = [(wave, wave.custom_height or 365) for wave in waves]
        output = QSaveFile(str(path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        painter = QPainter()
        if suffix == ".pdf":
            device = QPdfWriter(output)
            device.setTitle(self.title)
            device.setResolution(96)
            device.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            chunks = [[]]
            for wave, height in charts:
                chunk = chunks[-1]
                if chunk and (len(chunk) == 2 or sum(item[1] for item in chunk)+height > 950):
                    chunks.append([])
                chunks[-1].append((wave, height))
        else:
            device = QImage(width*2, (40+sum(height for _, height in charts))*2, QImage.Format.Format_RGB32)
            device.fill(QColor("#ffffff"))
            chunks = [charts]
        if not painter.begin(device):
            raise OSError("Could not open the export paint device")
        try:
            font = self.font()
            font.setPixelSize(13)
            painter.setFont(font)
            for index, chunk in enumerate(chunks, 1):
                if index > 1 and not device.newPage():
                    raise OSError("Could not create a PDF page")
                painter.save()
                scale = min(painter.viewport().width()/width,
                            painter.viewport().height()/(40+sum(height for _, height in chunk)))
                painter.scale(scale, scale)
                painter.drawText(QPointF(12, 24), self.title if len(chunks) == 1
                                 else f"{self.title}  ·  {index}/{len(chunks)}")
                top = 39
                for wave, height in chunk:
                    painter.save()
                    painter.translate(0, top)
                    wave.draw(painter, width, height-5)
                    painter.restore()
                    top += height
                painter.restore()
        finally:
            painter.end()
        if suffix == ".png" and not device.save(output, "PNG"):
            raise OSError("Could not encode the PNG")
        del device
        if not output.commit():
            raise OSError(output.errorString())


