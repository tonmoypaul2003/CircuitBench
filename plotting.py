"""Interactive simulation plots and export."""
import math
from pathlib import Path

from PyQt6.QtCore import QIODevice, QPointF, QRectF, QSaveFile, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPageSize, QShortcut, QKeySequence, QPainter, QPainterPath, QPdfWriter, QPen, QPolygonF
from PyQt6.QtWidgets import (QApplication, QAbstractItemView, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QCheckBox, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSlider, QSpinBox, QToolButton, QMenu,
                             QVBoxLayout, QWidget)

from circuit import number
from plot_math import evaluate, FUNCTIONS
from results import Plot
from canvas import engineering


def display_value(value,unit):
    units = {"voltage":"V","current":"A","power":"W","frequency":"Hz","time":"s"}
    return engineering(value,units[unit]) if unit in units and math.isfinite(value) else f"{value:.7g}"


LINE_STYLES = {"Solid": Qt.PenStyle.SolidLine, "Dashed": Qt.PenStyle.DashLine,
               "Dotted": Qt.PenStyle.DotLine, "Dash-dot": Qt.PenStyle.DashDotLine}
PIN_SHAPES = ("Circle", "Square", "Triangle", "Diamond", "Cross")


class Waveform(QWidget):
    colors = ["#087e74", "#c85528", "#5157ad", "#9b3b70", "#426925"]
    readout = pyqtSignal(str)
    cursor_changed = pyqtSignal()
    zoom_requested = pyqtSignal(int, float, float)
    activated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.plot = None
        self.columns = []
        self.presentation = 0
        self.trace_colors = {}
        self.trace_styles = {}
        self.pin_shapes = []
        self.pin_options = {}
        self.pin_text = {}
        self.pin_hits = []
        self.pin_offsets = {}
        self.pin_drag = None
        self.grid_color = None
        self.x_column = 0
        self.x_scale = "Automatic"
        self.tick_count = 5
        self.cursor_lines = "Vertical"
        self.text_size = 12
        self.text_color = None
        self.tick_precision = 4
        self.zoom = 100
        self.zoom_origin = (0., 0.)
        self.data_bounds = None
        self.reference = None
        self.hidden_cursor = (None, None)
        self.cursor_enabled = True
        self.cursor = None
        self.cursor_column = None
        self.title = ""
        self.x_label = ""
        self.y_label = ""
        self.pins = []
        self.grid_visible = True
        self.grid_density = 4
        self.y_grid_density = 4
        self.background = None
        self.x_limits = None
        self.y_limits = None
        self.screen_axes = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.b_active = False
        self.setMinimumHeight(230)

    def keyPressEvent(self,event):
        steps = {Qt.Key.Key_Left:-1,Qt.Key.Key_Right:1}
        if event.key() in steps and self.cursor_enabled and self.columns and self.plot.rows:
            self.cursor = max(0,min(len(self.plot.rows)-1,(self.cursor or 0)+steps[event.key()]))
            if self.cursor_column not in self.columns:
                self.cursor_column = self.columns[0]
            self.cursor_changed.emit()
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self,event):
        for i,rect in reversed(self.pin_hits):
            if rect.contains(event.position()):
                self.parent_page.manage_points(i)
                return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        self.activated.emit()
        if event.button() == Qt.MouseButton.LeftButton:
            for index,hit in reversed(self.pin_hits):
                if hit.contains(event.position()):
                    self.pin_drag = index,event.position(),self.pin_offsets.get(index,QPointF())
                    return
            self.move_cursor(event.position().x(), event.position().y())
        elif event.button() == Qt.MouseButton.RightButton:
            self.reference = None
            self.b_active = False
            self.clear_cursor()

    def clear_cursor(self):
        self.cursor = self.cursor_column = None
        self.cursor_changed.emit()
        self.readout.emit("")
        self.update()

    def set_cursor_enabled(self, enabled):
        self.cursor_enabled = enabled
        if not enabled:
            self.hidden_cursor = self.cursor, self.cursor_column
            self.clear_cursor()
        else:
            self.cursor, self.cursor_column = self.hidden_cursor
            self.cursor_changed.emit()
            self.update()

    def mouseReleaseEvent(self,event):
        self.pin_drag = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self.pin_drag and event.buttons() & Qt.MouseButton.LeftButton:
            index,origin,offset = self.pin_drag
            self.pin_offsets[index] = offset + event.position()-origin
            self.update()
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.move_cursor(event.position().x(), event.position().y())

    def wheelEvent(self, event):
        if self.screen_axes:
            rect = self.screen_axes[0]
            if rect.contains(event.position()):
                x = (event.position().x()-rect.left())/rect.width()
                y = (rect.bottom()-event.position().y())/rect.height()
                self.zoom_requested.emit(1 if event.angleDelta().y() > 0 else -1, x, y)
                event.accept()
                return
        event.ignore()

    def raw_x(self):
        return [row[self.x_column].real for row in self.plot.rows]

    def logarithmic(self):
        return self.x_scale == "Logarithmic" or (
            self.x_scale == "Automatic" and self.plot.names[self.x_column].lower() == "frequency")

    def trace_pen(self, column, background):
        style, width = self.trace_styles.get(column, ("Solid", 1.5))
        return QPen(self.color(column, background), width, LINE_STYLES[style])

    def draw_pin(self, painter, point, shape):
        x, y = point.x(), point.y()
        if shape == "Circle":
            painter.drawEllipse(point, 4, 4)
        elif shape == "Square":
            painter.drawRect(QRectF(x-4, y-4, 8, 8))
        elif shape == "Cross":
            painter.drawLine(QPointF(x-4, y), QPointF(x+4, y))
            painter.drawLine(QPointF(x, y-4), QPointF(x, y+4))
        else:
            vertices = ((0, -5), (5, 4), (-5, 4)) if shape == "Triangle" else (
                (0, -5), (5, 0), (0, 5), (-5, 0))
            painter.drawPolygon(QPolygonF([QPointF(x+dx, y+dy) for dx, dy in vertices]))

    def move_cursor(self, x, y=None):
        if not self.cursor_enabled or self.screen_axes is None:
            return
        bounds, xs, columns, plotted, xmin, xmax, ymin, ymax = self.screen_axes
        if plotted != self.columns:
            return
        ratio = min(1., max(0., (x-bounds.left())/bounds.width()))
        x_target = xmin + ratio*(xmax-xmin)
        candidates = (i for i in range(len(xs))
                      if xmin <= xs[i] <= xmax and any(ymin <= values[i] <= ymax for values in columns))
        y_target = ymin if y is None else ymin + (bounds.bottom()-y)/bounds.height()*(ymax-ymin)
        if self.x_column == 0:
            sample = min(candidates, key=lambda i: abs(xs[i]-x_target), default=None)
            if sample is None:
                return
            index = min((i for i, values in enumerate(columns) if ymin <= values[sample] <= ymax),
                        key=lambda i: abs(columns[i][sample]-y_target))
        else:
            nearest = min(((i, j) for i in candidates for j, values in enumerate(columns)
                           if ymin <= values[i] <= ymax),
                          key=lambda pair: math.hypot((xs[pair[0]]-x_target)/(xmax-xmin),
                              (columns[pair[1]][pair[0]]-y_target)/(ymax-ymin)), default=None)
            if nearest is None:
                return
            sample, index = nearest
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
            self.pin_hits = []
            self.screen_axes = None
            self.data_bounds = None
        rect = QRectF(0, 0, width, height)
        background = self.chart_background(report)
        ink = QColor("#173d37" if background.lightness() >= 128 else "#f0f7f4")
        ink = self.text_color or ink
        font = painter.font()
        font.setPixelSize(self.text_size)
        painter.setFont(font)
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
                                 f"{label}   {display_value(rows[0][column].real,self.plot.units[column])}")
            return
        logarithmic = self.logarithmic()
        raw_x = self.raw_x()
        xs = [math.log10(x) if x > 0 else math.nan for x in raw_x] if logarithmic else raw_x
        frequency_sweep = self.plot.names[0].lower() == "frequency" and self.x_column == 0
        columns = []
        for column in self.columns:
            values = [row[column] for row in rows]
            if self.plot.units[column] == "expression":
                use_magnitude = any(math.isfinite(value.imag) and value.imag for value in values)
                values = [abs(value) if use_magnitude else value.real for value in values]
            elif self.plot.units[column] == "power":
                values = [value.real for value in values]
            elif self.presentation == 1:
                values = [math.degrees(math.atan2(value.imag, value.real)) for value in values]
            elif self.presentation == 2:
                values = [20*math.log10(max(abs(value), 1e-300)) for value in values]
            elif frequency_sweep:
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
            painter.setPen(self.trace_pen(column, background))
            painter.drawLine(QPointF(legend_x, legend_y-4), QPointF(legend_x+13, legend_y-4))
            painter.drawText(QPointF(legend_x+17, legend_y), name)
            legend_x += label_width
        cursor_footer = 18 if report and self.cursor_enabled and (self.cursor is not None or self.reference) else 0
        left, right, top, bottom = 74, width-26, legend_y+27, height-43-cursor_footer
        valid_x = [x for x in xs if math.isfinite(x)]
        valid_y = [y for values in columns for x, y in zip(xs, values)
                   if math.isfinite(x) and math.isfinite(y)]
        if not valid_x or not valid_y:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             "No finite samples for these axes")
            return
        if right <= left or bottom <= top:
            return

        def padded(low, high):
            pad = max(abs(low)*.05, 1e-12) if low == high else 0
            return low-pad, high+pad

        auto_x = padded(min(valid_x), max(valid_x))
        auto_y = padded(min(valid_y), max(valid_y))
        xmin, xmax = self.x_limits or auto_x
        if self.x_limits and logarithmic:
            xmin, xmax = math.log10(xmin), math.log10(xmax)
        ymin, ymax = self.y_limits or auto_y
        if not report:
            self.data_bounds = (tuple(10**v for v in auto_x) if logarithmic else auto_x, auto_y)
        factor = 100/self.zoom
        fx, fy = self.zoom_origin
        x_span, y_span = xmax-xmin, ymax-ymin
        xmin, xmax = xmin+x_span*fx, xmin+x_span*(fx+factor)
        ymin, ymax = ymin+y_span*fy, ymin+y_span*(fy+factor)
        if not report:
            self.screen_axes = (QRectF(left, top, right-left, bottom-top), xs, columns,
                                self.columns.copy(), xmin, xmax, ymin, ymax)
        if self.grid_visible:
            grid_color = QColor(ink)
            grid_color.setAlpha(42)
            grid_color = self.grid_color or grid_color
            painter.setPen(QPen(grid_color, 1))
            for steps, vertical in ((self.grid_density,True),(self.y_grid_density,False)):
                for tick in range(1,steps):
                    fraction = tick/steps
                    if vertical:
                        x = left+(right-left)*fraction
                        painter.drawLine(QPointF(x,top),QPointF(x,bottom))
                    else:
                        y = bottom-(bottom-top)*fraction
                        painter.drawLine(QPointF(left,y),QPointF(right,y))
        painter.setPen(ink)
        painter.drawRect(QRectF(left,top,right-left,bottom-top))
        xticks = self.grid_density+1 if self.grid_visible else self.tick_count
        yticks = self.y_grid_density+1 if self.grid_visible else self.tick_count
        for i in range(xticks):
            fraction = i/(xticks-1)
            position = left+(right-left)*fraction
            value = xmin+(xmax-xmin)*fraction
            value = 10**value if logarithmic else value
            text = f"{value:.{self.tick_precision}g}"
            painter.drawText(QPointF(position-metrics.horizontalAdvance(text)/2,bottom+17),text)
        for i in range(yticks):
            fraction = i/(yticks-1)
            value = ymin+(ymax-ymin)*fraction
            painter.drawText(QPointF(6,bottom-(bottom-top)*fraction+4),f"{value:.{self.tick_precision}g}")
        xlabel = self.x_label or f"{self.plot.names[self.x_column]} ({self.plot.units[self.x_column]})"
        ylabel = self.y_label or (("Magnitude", "Phase (°)", "Magnitude (dB)")[self.presentation]
                                  if frequency_sweep else (self.plot.names[self.columns[0]]
                                  if len(self.columns) == 1 else ""))
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
            painter.setPen(self.trace_pen(self.columns[index], background))
            path = QPainterPath()
            connected = False
            for x, y in zip(xs, values):
                if not (math.isfinite(x) and math.isfinite(y)):
                    connected = False
                    continue
                point = location(x, y)
                (path.lineTo if connected else path.moveTo)(point)
                connected = True
                if len(xs) == 1:
                    painter.drawEllipse(point, 3, 3)
            painter.drawPath(path)
        painter.restore()
        readouts = []
        cursor_points = []
        if self.cursor_enabled and self.reference and self.reference[1] in self.columns:
            cursor_points.append(("A",*self.reference))
        if self.cursor is not None and self.cursor_column in self.columns:
            cursor_points.append(("B",self.cursor,self.cursor_column))
        coords = []
        for label,index,column in cursor_points:
            value = columns[self.columns.index(column)][index]
            coords.append((raw_x[index],value))
            unit = self.plot.units[column]
            y_text = (f"{value:.7g} dB" if self.presentation==2 and unit not in ("power","expression") else
                      f"{value:.7g}°" if self.presentation==1 and unit not in ("power","expression") else
                      display_value(value,unit))
            readouts.append(f"{label}  {self.plot.names[self.x_column]} = {display_value(raw_x[index],self.plot.units[self.x_column])}   {self.plot.names[column]} = {y_text}")
            painter.setPen(ink)
            if self.cursor_lines != "Horizontal" and xmin<=xs[index]<=xmax:
                x = location(xs[index],ymin).x()
                painter.drawLine(QPointF(x,top),QPointF(x,bottom))
                painter.drawText(QPointF(x+3,top+(13 if label=="A" else 26)),label)
            if ymin<=value<=ymax:
                y = location(xmin,value).y()
                if self.cursor_lines != "Vertical":
                    painter.drawLine(QPointF(left,y),QPointF(right,y))
                    painter.drawText(QPointF(left+5,y-3),label)
                if xmin<=xs[index]<=xmax:
                    painter.drawEllipse(location(xs[index],value),3,3)
        if len(coords)==2:
            dx = coords[1][0]-coords[0][0]
            dy = coords[1][1]-coords[0][1]
            readouts.append(f"ΔX {dx:.7g}   ΔY {dy:.7g}")
            if dx and self.plot.names[self.x_column].lower()=="time":
                readouts.append(f"{1/abs(dx):.7g} Hz")
        readout = "   |   ".join(readouts)
        if report and readout:
            painter.drawText(QPointF(12,height-7),metrics.elidedText(readout,Qt.TextElideMode.ElideRight,width-24))
        elif not report:
            self.readout.emit(readout)
        for pin_index, (column, sample, label) in enumerate(self.pins):
            if column not in self.columns:
                continue
            series = columns[self.columns.index(column)]
            i = int(sample)
            frac = sample-i
            px,value = xs[i],series[i]
            if frac:
                px += frac*(xs[i+1]-px)
                value += frac*(series[i+1]-value)
            if not (xmin <= px <= xmax and ymin <= value <= ymax):
                continue
            point = location(px, value)
            color,digits = self.pin_options.get(pin_index,(None,3))
            size,text_color,fill = self.pin_text.get(pin_index,(self.text_size,None,False))
            painter.save()
            pin_font = painter.font()
            pin_font.setPixelSize(size)
            painter.setFont(pin_font)
            metrics = painter.fontMetrics()
            painter.setPen(QPen(QColor(color) if color else self.color(column, background), 1.5))
            shape = self.pin_shapes[pin_index] if pin_index < len(self.pin_shapes) else "Circle"
            painter.setBrush(painter.pen().color() if fill else Qt.BrushStyle.NoBrush)
            self.draw_pin(painter, point, shape)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if text_color:
                painter.setPen(QColor(text_color))
            x = 10**px if logarithmic else px
            text = f"{label or self.plot.names[column]} ({x:.{digits}f}, {value:.{digits}f})"
            if not report:
                self.pin_hits.append((pin_index,QRectF(point.x()-8,point.y()-8,16,16)))
            text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(30, right-left-12))
            text_width = metrics.horizontalAdvance(text)
            text_x = max(left+4, min(point.x()+7, right-text_width-4))
            text_y = point.y()-7 if point.y() > top+17 else point.y()+17
            offset = self.pin_offsets.get(pin_index)
            if offset:
                text_x = max(left+4,min(text_x+offset.x(),right-text_width-4))
                text_y = max(top+metrics.ascent(),min(text_y+offset.y(),bottom))
                painter.drawLine(point,QPointF(text_x,text_y))
            label_box = QRectF(text_x-3,text_y-metrics.ascent(),text_width+6,metrics.height())
            if not report:
                self.pin_hits.append((pin_index,label_box))
            painter.fillRect(label_box,background)
            painter.drawText(QPointF(text_x, text_y), text)
            painter.restore()


class PlotPage(QWidget):
    def __init__(self, plot, selected=None):
        super().__init__()
        self.wave = Waveform()
        self.wave.parent_page = self
        self.wave.plot = plot
        self.traces = QListWidget()
        self.traces.setMaximumWidth(130)
        self.traces.setMinimumWidth(82)
        self.presentation = QComboBox()
        self.presentation.addItems(["Magnitude / real", "Phase (degrees)", "Magnitude (dB)"])
        self.presentation.setEnabled(plot.names[0].lower() == "frequency")
        readout = QLineEdit()
        readout.setReadOnly(True)
        readout.setPlaceholderText("Click or drag on the plot to read a sample · right-click to clear")
        self.wave.readout.connect(readout.setText)
        add = QPushButton("Add trace…")
        add.clicked.connect(self.add_trace)
        drop = QPushButton("Remove trace")
        drop.clicked.connect(self.remove_trace)
        self.cursor_button = QPushButton("Cursor")
        self.cursor_button.setCheckable(True)
        self.cursor_button.setChecked(self.wave.cursor_enabled)
        self.cursor_button.setEnabled("operating point" not in plot.title.lower())
        self.cursor_button.setToolTip("Enable the sample cursor, or hide it from the plot and exports")
        self.cursor_button.toggled.connect(self.wave.set_cursor_enabled)
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
        self.grid_density.setRange(1, 24)
        self.grid_density.setValue(self.wave.grid_density)
        self.grid_density.setAccessibleName("X grid intervals")
        self.grid_density.valueChanged.connect(self.set_grid_density)
        self.y_grid_density = QSlider(Qt.Orientation.Vertical)
        self.y_grid_density.setRange(1,24)
        self.y_grid_density.setValue(self.wave.y_grid_density)
        self.y_grid_density.setAccessibleName("Y grid intervals")
        def set_y_grid(value):
            self.wave.y_grid_density = value
            self.wave.update()
        self.y_grid_density.valueChanged.connect(set_y_grid)
        self.limits_button = QPushButton("Axis limits…")
        self.limits_button.clicked.connect(self.edit_limits)
        self.pin_shape = QComboBox()
        self.pin_shape.addItems(PIN_SHAPES)
        self.pin_shape.setAccessibleName("Pin marker shape")
        self.x_axis = QComboBox()
        self.x_axis.addItems(plot.names)
        self.x_axis.setAccessibleName("Horizontal axis signal")
        self.x_axis.setMaximumWidth(155)
        self.x_axis.currentIndexChanged.connect(self.set_x_axis)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        anchor = QPushButton("Set A")
        anchor.setToolTip("Anchor the current sample as A, then click another point for B")
        def set_anchor():
            self.wave.setFocus()
            if self.wave.reference is not None:
                self.wave.reference = None
                self.wave.update()
            elif self.wave.cursor is not None:
                self.wave.reference = self.wave.cursor, self.wave.cursor_column
                self.wave.update()
        anchor.clicked.connect(set_anchor)
        toolbar.addWidget(anchor)
        set_b = QPushButton("Set B")
        set_b.setToolTip("Keep A fixed and select the second point on the trace")
        def select_b():
            self.cursor_button.setChecked(True)
            self.wave.b_active = not self.wave.b_active
            if self.wave.b_active:
                self.wave.setFocus()
                readout.setText("Click the waveform or use the arrow keys to place B")
                self.wave.update()
            else:
                self.wave.clear_cursor()
        set_b.clicked.connect(select_b)
        toolbar.addWidget(set_b)
        toolbar.addWidget(add)
        toolbar.addWidget(drop)
        toolbar.addWidget(self.presentation)
        x_label = QLabel("X signal")
        x_label.setBuddy(self.x_axis)
        toolbar.addWidget(x_label)
        toolbar.addWidget(self.x_axis)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        graph_tools = QHBoxLayout()
        fit = QPushButton("Fit data")
        fit.setToolTip("Reset axis limits and zoom to show all data")
        fit.clicked.connect(lambda: self.set_axis_limits(None, None))
        appearance = QToolButton()
        appearance.setText("Appearance")
        appearance.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        appearance_menu = QMenu(appearance)
        for label, callback in (("Trace style…", self.edit_trace_style), ("Labels…", self.edit_labels),
                                ("Chart text…", self.edit_chart_text), ("Background…", self.choose_background), ("Grid color…", self.choose_grid_color)):
            appearance_menu.addAction(label, callback)
        appearance.setMenu(appearance_menu)
        for button in (self.limits_button, fit, appearance, self.grid_button):
            graph_tools.addWidget(button)
        graph_tools.addStretch()
        layout.addLayout(graph_tools)
        pin_tools = QHBoxLayout()
        for item in (self.cursor_button, self.auto_pin_button, self.pin_shape, pin, remove_pin):
            pin_tools.addWidget(item)
        manage = QPushButton("Pinned points…")
        manage.clicked.connect(lambda: self.manage_points())
        crossing = QPushButton("Intersections…")
        crossing.clicked.connect(self.intersections)
        pin_tools.addWidget(manage)
        pin_tools.addWidget(crossing)
        guides = self.guides = QComboBox()
        guides.addItems(["Vertical","Horizontal","Both"])
        guides.setAccessibleName("Cursor guide orientation")
        def set_guides(text):
            self.wave.cursor_lines = text
            self.wave.update()
        guides.currentTextChanged.connect(set_guides)
        pin_tools.addWidget(guides)
        pin_tools.addStretch()
        layout.addLayout(pin_tools)
        content = QHBoxLayout()
        content.addWidget(self.traces)
        chart = QGridLayout()
        chart.addWidget(self.y_grid_density,0,0)
        chart.addWidget(self.wave,0,1)
        chart.addWidget(self.grid_density,1,1)
        chart.setColumnStretch(1,1)
        content.addLayout(chart,1)
        zoom_tools = QVBoxLayout()
        self.zoom_slider = QSlider(Qt.Orientation.Vertical)
        self.zoom_slider.setRange(25, 800)
        self.zoom_slider.setInvertedControls(False)
        self.zoom_slider.setSingleStep(25)
        self.zoom_slider.setPageStep(100)
        self.zoom_slider.setAccessibleName("Plot zoom")
        self.zoom_slider.setToolTip("Zoom the axes. Use the wheel over the plot to zoom around the pointer.")
        self.zoom_slider.setValue(100)
        self.zoom_anchor = (.5, .5)
        self.zoom_slider.valueChanged.connect(self.set_zoom)
        self.wave.zoom_requested.connect(self.wheel_zoom)
        self.zoom_readout = QLabel("100%")
        self.zoom_in_button = QPushButton("+")
        self.zoom_out_button = QPushButton("−")
        for button, label, direction in ((self.zoom_in_button, "Zoom in", 1),
                                         (self.zoom_out_button, "Zoom out", -1)):
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setFixedWidth(28)
            button.clicked.connect(lambda checked=False, step=direction: self.wheel_zoom(step, .5, .5))
        can_zoom = "operating point" not in plot.title.lower()
        for control in (self.zoom_in_button, self.zoom_out_button, self.zoom_slider):
            control.setEnabled(can_zoom)
        zoom_tools.addWidget(QLabel("Zoom"), alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_tools.addWidget(self.zoom_in_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_tools.addWidget(self.zoom_slider, 1, Qt.AlignmentFlag.AlignHCenter)
        zoom_tools.addWidget(self.zoom_out_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_tools.addWidget(self.zoom_readout, alignment=Qt.AlignmentFlag.AlignHCenter)
        content.addLayout(zoom_tools)
        layout.addLayout(content, 1)
        layout.addWidget(readout)
        operating_point = "operating point" in plot.title.lower()
        self.x_axis.setEnabled(not operating_point)
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
        if column >= self.x_axis.count():
            self.x_axis.addItem(self.wave.plot.names[column])
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
            self.wave.pin_shapes.append(self.pin_shape.currentText())
            self.wave.update()

    def toggle_grid(self, enabled):
        self.wave.grid_visible = enabled
        for slider in (self.grid_density,self.y_grid_density):
            slider.setEnabled(enabled)
            slider.setVisible(enabled)
        self.wave.update()

    def set_grid_density(self, density):
        self.wave.grid_density = density
        self.wave.update()

    def set_zoom(self, value):
        old_span, span = 100/self.wave.zoom, 100/value
        low, high = sorted((0., 1-span))
        self.wave.zoom_origin = tuple(
            max(low, min(high, origin + anchor*(old_span-span)))
            for origin, anchor in zip(self.wave.zoom_origin, self.zoom_anchor))
        self.wave.zoom = value
        self.zoom_readout.setText(f"{value}%")
        self.zoom_in_button.setEnabled(self.zoom_slider.isEnabled() and value < self.zoom_slider.maximum())
        self.zoom_out_button.setEnabled(self.zoom_slider.isEnabled() and value > self.zoom_slider.minimum())
        self.wave.update()

    def wheel_zoom(self, direction, x, y):
        current = self.wave.zoom
        value = round(current*1.25**direction)
        if (current-100)*(value-100) < 0:
            value = 100
        self.zoom_anchor = (x, y)
        try:
            self.zoom_slider.setValue(value)
        finally:
            self.zoom_anchor = (.5, .5)

    def set_x_axis(self, index):
        if index < 0:
            return
        self.wave.x_column = index
        self.wave.x_scale = "Automatic"
        if index:
            self.presentation.setCurrentIndex(0)
        self.presentation.setEnabled(index == 0 and self.wave.plot.names[0].lower() == "frequency")
        self.wave.clear_cursor()
        self.set_axis_limits(None, None)

    def edit_chart_text(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Chart text")
        layout = QFormLayout(dialog)
        size = QSpinBox()
        size.setRange(6,32)
        size.setValue(self.wave.text_size)
        layout.addRow("Text size (px)",size)
        color = self.wave.text_color
        button = QPushButton("Choose text color…")
        def pick():
            nonlocal color
            choice = QColorDialog.getColor(color or QColor("#173d37"),dialog)
            if choice.isValid():
                color = choice
        button.clicked.connect(pick)
        layout.addRow(button)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addRow(controls)
        if dialog.exec():
            self.wave.text_size = size.value()
            self.wave.text_color = color
            self.wave.update()

    def choose_grid_color(self):
        color = QColorDialog.getColor(self.wave.grid_color or QColor("#9aada4"), self, "Grid color")
        if color.isValid():
            self.wave.grid_color = color
            self.wave.update()

    def choose_background(self):
        color = QColorDialog.getColor(self.wave.chart_background(), self, "Plot background")
        if color.isValid():
            self.wave.background = color
            self.wave.update()

    def set_axis_limits(self, x_limits, y_limits):
        logarithmic = self.wave.logarithmic()
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
        self.wave.zoom_origin = (0., 0.)
        self.zoom_slider.setValue(100)
        self.wave.update()

    def edit_limits(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Axes")
        form = QFormLayout(dialog)
        scale = QComboBox()
        scale.addItems(["Automatic", "Linear", "Logarithmic"])
        scale.setCurrentText(self.wave.x_scale)
        form.addRow("X scale", scale)
        inputs = []
        fallback = self.wave.data_bounds or ((0, 1), (0, 1))
        visible = self.wave.screen_axes
        view = None
        if visible:
            left, right, bottom, top = visible[4:]
            view = ((10**left, 10**right) if self.wave.logarithmic() else (left, right),
                    (bottom, top))
        for index, (axis, limits) in enumerate((("X", self.wave.x_limits), ("Y", self.wave.y_limits))):
            auto = QCheckBox(f"Fit {axis} to data")
            auto.setChecked(limits is None)
            form.addRow(auto)
            low, high = (QLineEdit(f"{value:.12g}") for value in (view or fallback)[index])
            for field, label in ((low, "minimum"), (high, "maximum")):
                field.setEnabled(not auto.isChecked())
                auto.toggled.connect(lambda enabled, field=field: field.setDisabled(enabled))
                form.addRow(f"{axis} {label}", field)
            inputs.append((auto, low, high))
        ticks, precision = QSpinBox(), QSpinBox()
        ticks.setRange(2, 25)
        ticks.setValue(self.wave.tick_count)
        precision.setRange(2, 7)
        precision.setValue(self.wave.tick_precision)
        form.addRow("Ticks per axis (grid off)", ticks)
        xticks,yticks = QSpinBox(),QSpinBox()
        for caption,control,slider in (("X ticks (grid on)",xticks,self.grid_density),
                                       ("Y ticks (grid on)",yticks,self.y_grid_density)):
            control.setRange(2,25)
            control.setValue(slider.value()+1)
            form.addRow(caption,control)
        form.addRow("Tick significant digits", precision)
        message = QLabel("SPICE suffixes work in limits, for example 1k or 10m. Use Linear for an X range that starts at zero.")
        message.setWordWrap(True)
        form.addRow(message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def accept():
            old = self.wave.x_scale
            self.wave.x_scale = scale.currentText()
            try:
                if self.wave.logarithmic() and not any(x > 0 and math.isfinite(x) for x in self.wave.raw_x()):
                    raise ValueError("Logarithmic X requires at least one positive sample.")
                limits = [None if auto.isChecked() else (number(low.text().strip()), number(high.text().strip()))
                          for auto, low, high in inputs]
                self.set_axis_limits(*limits)
            except ValueError as error:
                self.wave.x_scale = old
                message.setText(str(error))
                return
            self.wave.tick_count, self.wave.tick_precision = ticks.value(), precision.value()
            self.grid_density.setValue(xticks.value()-1)
            self.y_grid_density.setValue(yticks.value()-1)
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
        indices = [i for i in indices if math.isfinite(rows[i][self.wave.x_column].real)
                   and (not self.wave.logarithmic() or rows[i][self.wave.x_column].real > 0)]
        if not indices:
            raise ValueError("This trace has no samples on the chosen X scale")
        sample = min(indices, key=lambda index: abs(rows[index][self.wave.x_column].real-x))
        self.wave.pins.append((column, sample, label.strip()))
        self.wave.pin_shapes.append(self.pin_shape.currentText())
        self.wave.update()
        return rows[sample][self.wave.x_column].real

    def pin_point(self):
        item = self.traces.currentItem()
        if item is None or item.isHidden():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Pin {item.text()}")
        form = QFormLayout(dialog)
        coordinate, label = QLineEdit(), QLineEdit()
        coordinate.setText(f"{self.wave.plot.rows[0][self.wave.x_column].real:g}")
        label.setMaxLength(80)
        form.addRow(f"{self.wave.plot.names[self.wave.x_column]} value", coordinate)
        form.addRow("Point label (optional)", label)
        message = QLabel("Snaps to the nearest available sample.")
        form.addRow(message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def apply():
            try:
                self.pin_at(item.data(Qt.ItemDataRole.UserRole), number(coordinate.text().strip()), label.text())
            except ValueError as error:
                message.setText(str(error))
                return
            dialog.accept()

        buttons.accepted.connect(apply)
        dialog.exec()

    def manage_points(self, selected=0):
        dialog = QDialog(self)
        dialog.setWindowTitle("Pinned points")
        layout = QVBoxLayout(dialog)
        points = QListWidget()
        label = QLineEdit()
        digits = QSpinBox()
        digits.setRange(0,12)
        digits.setPrefix("Decimals  ")
        color_button = QPushButton("Point color…")
        color = None
        text_color = None
        font_size = QSpinBox()
        font_size.setRange(6,48)
        font_size.setPrefix("Text size  ")
        fill = QCheckBox("Filled point")
        text_button = QPushButton("Text color…")
        def pick_text():
            nonlocal text_color
            color = QColorDialog.getColor(QColor(text_color or "#173d37"),dialog)
            if color.isValid():
                text_color = color.name()
        text_button.clicked.connect(pick_text)
        layout.addWidget(points)
        layout.addWidget(font_size)
        layout.addWidget(text_button)
        layout.addWidget(fill)
        layout.addWidget(digits)
        layout.addWidget(color_button)
        layout.addWidget(label)
        def refresh():
            points.clear()
            for col,sample,name in self.wave.pins:
                points.addItem(name or f"{self.wave.plot.names[col]} at sample {sample:g}")
        refresh()
        def select(row):
            nonlocal color,text_color
            size,text_color,filled = self.wave.pin_text.get(row,(self.wave.text_size,None,False))
            font_size.setValue(size)
            fill.setChecked(filled)
            label.setText(self.wave.pins[row][2] if row>=0 else "")
            color,precision = self.wave.pin_options.get(row,(None,3))
            digits.setValue(precision)
        def pick_color():
            nonlocal color
            chosen = QColorDialog.getColor(QColor(color or "#087e74"),dialog)
            if chosen.isValid():
                color = chosen.name()
        color_button.clicked.connect(pick_color)
        points.currentRowChanged.connect(select)
        points.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        def apply():
            row = points.currentRow()
            if row>=0:
                col,sample,_ = self.wave.pins[row]
                self.wave.pins[row] = col,sample,label.text()
                self.wave.pin_options[row] = color,digits.value()
                self.wave.pin_text[row] = font_size.value(),text_color,fill.isChecked()
                refresh()
                points.setCurrentRow(row)
                self.wave.update()
        def delete_points(all_points=False):
            removed = set(range(points.count())) if all_points else {i.row() for i in points.selectedIndexes()}
            self.delete_pins(removed)
            refresh()
            if points.count():
                points.setCurrentRow(0)
        for title,callback in (("Apply label",apply),("Delete selected",delete_points),
                               ("Delete all",lambda:delete_points(True))):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False,f=callback:f())
            layout.addWidget(button)
        delete = QShortcut(QKeySequence("Delete"),points)
        delete.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        delete.activated.connect(delete_points)
        points.setCurrentRow(selected)
        dialog.exec()

    def intersections(self):
        from PyQt6.QtWidgets import QMessageBox
        w = self.wave
        w.repaint()
        if not w.screen_axes or len(w.columns)<2:
            QMessageBox.information(self,"Intersections","Display two waveform traces first.")
            return
        _,xs,series,cols,xmin,xmax,ymin,ymax = w.screen_axes
        names = [w.plot.names[c] for c in cols]
        first,ok = QInputDialog.getItem(self,"Intersections","First trace",names,editable=False)
        if not ok:
            return
        second,ok = QInputDialog.getItem(self,"Intersections","Second trace",[n for n in names if n!=first],editable=False)
        if not ok:
            return
        maximum,ok = QInputDialog.getInt(self,"Intersections","Maximum points to add",10,1,10000)
        if not ok:
            return
        a,b = names.index(first),names.index(second)
        count = 0
        existing = {(col,sample) for col,sample,_ in w.pins}
        for i,(v,u) in enumerate(zip(series[a],series[b])):
            d = v-u
            if not math.isfinite(d) or not math.isfinite(xs[i]):
                continue
            f = 0.
            if d:
                if i+1>=len(xs) or not math.isfinite(xs[i+1]):
                    continue
                end = series[a][i+1]-series[b][i+1]
                if not math.isfinite(end) or d*end>=0:
                    continue
                f = d/(d-end)
            elif ((i and series[a][i-1]==series[b][i-1]) or
                  (i+1<len(xs) and series[a][i+1]==series[b][i+1])):
                continue
            x,y = xs[i],v
            if f:
                x += f*(xs[i+1]-x)
                y += f*(series[a][i+1]-y)
            if not (xmin<=x<=xmax and ymin<=y<=ymax):
                continue
            pin = (cols[a],i+f,f"X{len(w.pins)+1}")
            if pin[:2] not in existing:
                existing.add(pin[:2])
                w.pins.append(pin)
                w.pin_shapes.append(self.pin_shape.currentText())
                count+=1
                if count>=maximum:
                    break
        w.update()
        QMessageBox.information(self,"Intersections",f"Added {count} isolated crossing points.")

    def delete_pins(self,removed):
        w = self.wave
        keep = [i for i in range(len(w.pins)) if i not in removed]
        w.pins = [w.pins[i] for i in keep]
        w.pin_shapes = [w.pin_shapes[i] if i<len(w.pin_shapes) else "Circle" for i in keep]
        for field in ("pin_options","pin_offsets","pin_text"):
            values = getattr(w,field)
            setattr(w,field,{j:values[i] for j,i in enumerate(keep) if i in values})
        w.update()

    def remove_pin(self):
        if self.wave.pins:
            self.delete_pins({len(self.wave.pins)-1})

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

    def edit_trace_style(self):
        item = self.traces.currentItem()
        if item is None or item.checkState() != Qt.CheckState.Checked:
            return
        column = item.data(Qt.ItemDataRole.UserRole)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Trace style · {item.text()}")
        form = QFormLayout(dialog)
        style, width = self.wave.trace_styles.get(column, ("Solid", 1.5))
        pattern = QComboBox()
        pattern.addItems(LINE_STYLES)
        pattern.setCurrentText(style)
        weight = QDoubleSpinBox()
        weight.setRange(.5, 8)
        weight.setSingleStep(.5)
        weight.setValue(width)
        weight.setSuffix(" px")
        color = self.wave.trace_colors.get(column)
        color_button = QPushButton(color.name() if color else "Choose color…")

        def choose_color():
            nonlocal color
            selected = QColorDialog.getColor(color or self.wave.color(column), dialog, "Trace color")
            if selected.isValid():
                color = selected
                color_button.setText(color.name())

        color_button.clicked.connect(choose_color)
        form.addRow("Line pattern", pattern)
        form.addRow("Line width", weight)
        form.addRow("Color", color_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            self.wave.trace_styles[column] = pattern.currentText(), weight.value()
            if color:
                self.wave.trace_colors[column] = color
                item.setForeground(color)
            self.wave.update()


class PlotReportPage(QWidget):
    def __init__(self, plot, selected=None):
        super().__init__()
        self.title = plot.title
        self.revision = None
        self.plot = Plot(plot.title, plot.names.copy(), plot.units.copy(), [list(row) for row in plot.rows])
        self.expressions = []
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
        self.stack = QGridLayout(content)
        self.tiles = QComboBox()
        self.tiles.addItems(["Stacked", "Two columns"])
        self.tiles.setAccessibleName("Subplot layout")
        self.tiles.currentIndexChanged.connect(self.arrange_subplots)
        controls.insertWidget(1, self.tiles)
        scroller.setWidget(content)
        layout.addWidget(scroller, 1)
        insert_bar = QHBoxLayout()
        self.expression = QLineEdit()
        self.expression.setPlaceholderText("Expression, e.g. gain = db(v(out) / v(in))")
        self.expression.setMaxLength(575)
        self.expression.returnPressed.connect(self.insert_expression)
        insert = QPushButton("Add expression")
        insert.clicked.connect(self.insert_expression)
        functions = QComboBox()
        functions.addItems(["Function…","RMS","MAX","MIN","MEAN",*sorted(FUNCTIONS)])
        def insert_function(index):
            if index:
                self.expression.insert(functions.itemText(index)+"()")
                self.expression.cursorBackward(False,1)
                self.expression.setFocus()
                functions.setCurrentIndex(0)
        functions.activated.connect(insert_function)
        self.variable = QComboBox()
        self.variable.setEditable(True)
        self.variable.addItems(self.plot.names)
        self.variable.setMaximumWidth(150)
        variable_button = QPushButton("Insert variable")
        def insert_variable():
            name = self.variable.currentText().strip()
            if name:
                self.expression.insert("trace("+repr(name)+")")
                self.expression.setFocus()
        variable_button.clicked.connect(insert_variable)
        insert_bar.addWidget(self.variable)
        insert_bar.addWidget(variable_button)
        insert_bar.addWidget(functions)
        insert_bar.addWidget(self.expression, 1)
        insert_bar.addWidget(insert)
        layout.addLayout(insert_bar)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.add_subplot(selected)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

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
        for button in subplot.findChildren(QPushButton):
            button.setAutoDefault(False)
        subplot.wave.title = f"Plot {len(self.subplots)+1}"
        self.subplots.append(subplot)
        subplot.wave.activated.connect(lambda: self.active.setCurrentIndex(self.subplots.index(subplot)))
        self.arrange_subplots()
        self.active.addItem(f"Plot {len(self.subplots)}")
        self.active.setCurrentIndex(len(self.subplots)-1)
        return subplot

    def arrange_subplots(self):
        columns = self.tiles.currentIndex()+1
        for index, subplot in enumerate(self.subplots):
            self.stack.addWidget(subplot, index//columns, index%columns)
        self.stack.setColumnStretch(0, 1)
        self.stack.setColumnStretch(1, int(columns == 2))

    def remove_subplot(self):
        if len(self.subplots) == 1:
            self.message.setText("Keep one plot on each page.")
            return
        index = self.active.currentIndex()
        subplot = self.subplots.pop(index)
        self.stack.removeWidget(subplot)
        subplot.deleteLater()
        self.active.removeItem(index)
        self.arrange_subplots()
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
        self.variable.addItem(name)
        self.expressions.append(text)
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
        width, chart_height = 810, 365
        columns = min(len(waves), self.tiles.currentIndex()+1) if whole_page else 1
        rows = [waves[i:i+columns] for i in range(0, len(waves), columns)]
        output = QSaveFile(str(path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        painter = QPainter()
        if suffix == ".pdf":
            device = QPdfWriter(output)
            device.setTitle(self.title)
            device.setResolution(96)
            device.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            chunks = [rows[i:i+2] for i in range(0, len(rows), 2)]
        else:
            device = QImage(width*2, (40+len(rows)*chart_height)*2, QImage.Format.Format_RGB32)
            device.fill(QColor("#ffffff"))
            chunks = [rows]
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
                            painter.viewport().height()/(40+len(chunk)*chart_height))
                painter.scale(scale, scale)
                painter.drawText(QPointF(12, 24), self.title if len(chunks) == 1
                                 else f"{self.title}  ·  {index}/{len(chunks)}")
                chart_width = width/columns
                for row_index, row in enumerate(chunk):
                    for column, wave in enumerate(row):
                        painter.save()
                        painter.translate(column*chart_width, 40+row_index*chart_height)
                        wave.draw(painter, int(chart_width)-5, chart_height-5)
                        painter.restore()
                painter.restore()
        finally:
            painter.end()
        if suffix == ".png" and not device.save(output, "PNG"):
            raise OSError("Could not encode the PNG")
        del device
        if not output.commit():
            raise OSError(output.errorString())



