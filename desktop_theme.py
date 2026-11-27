"""Colors, Qt styles and toolbar glyphs for the desktop workbench."""
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF

themes = {
    "light": dict(panel="#F5F7F9", well="#FFFFFF", ink="#172735", mute="#4B6171",
                  line="#CCD6DE", hover="#E8EFF5", active="#D9EAF9", focus="#2774B2",
                  run="#155789", runhover="#10476F", grid="#D3DCE3", net="#12776E",
                  copper="#A44C28", sheet="#FCFDFE"),
    "dark": dict(panel="#18242E", well="#101B24", ink="#E6EEF4", mute="#A9BECC",
                 line="#354B5A", hover="#223746", active="#284657", focus="#77B9F0",
                 run="#205F91", runhover="#194F7D", grid="#2B4251", net="#72D4C0",
                 copper="#EFA577", sheet="#111D27"),
}
glyphs = {
    "New":[[(5,2),(13,2),(19,8),(19,21),(5,21),(5,2)],[(13,2),(13,8),(19,8)]],
    "Open":[[(3,7),(9,7),(11,9),(21,9),(18,19),(3,19),(3,7)]],
    "Save":[[(4,3),(18,3),(20,5),(20,21),(4,21),(4,3)],[(8,3),(8,9),(15,9),(15,3)],[(8,21),(8,14),(16,14),(16,21)]],
    "Run":[[(8,4),(20,12),(8,20),(8,4)]],
    "Analysis setup":[[(3,6),(21,6)],[(3,12),(21,12)],[(3,18),(21,18)],[(8,3),(8,9)],[(15,9),(15,15)],[(10,15),(10,21)]],
    "Select":[[(5,3),(19,13),(12,14),(9,21),(5,3)]],
    "Wire":[[(3,18),(3,11),(12,11),(12,5),(21,5)]],
    "Junction":[[(3,12),(21,12)],[(12,3),(12,21)]],
    "Part":[[(2,12),(5,12),(7,8),(10,16),(13,8),(16,16),(18,12),(22,12)]],
    "Ground":[[(12,3),(12,11)],[(4,11),(20,11)],[(7,15),(17,15)],[(10,19),(14,19)]],
    "Label":[[(3,5),(17,5),(21,12),(17,19),(3,19),(3,5)],[(6,12),(14,12)]],
    "Cancel run":[[(6,6),(18,6),(18,18),(6,18),(6,6)]],
    "Fit schematic":[[(3,9),(3,3),(9,3)],[(15,3),(21,3),(21,9)],
                     [(21,15),(21,21),(15,21)],[(9,21),(3,21),(3,15)]],
    "Rotate":[[(20,10),(18,5),(13,3),(8,4),(4,8),(3,13),(5,18),(10,21),(15,20),(19,17)],
              [(15,9),(20,10),(21,5)]],
    "Netlist":[[(5,3),(19,3),(19,21),(5,21),(5,3)],[(8,8),(16,8)],[(8,12),(16,12)],[(8,16),(13,16)]],
    "Show schematic readings":[[(2,12),(6,7),(12,5),(18,7),(22,12),(18,17),(12,19),(6,17),(2,12)],
                               [(9,12),(12,9),(15,12),(12,15),(9,12)]],
    "Plots":[[(3,3),(3,21),(22,21)],[(5,15),(9,8),(13,12),(17,6),(22,11)]],
}
def action_icon(name,color):
    pix = QPixmap(48,48)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.GlobalColor.transparent)
    pen = QPen(QColor(color),1.7,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(pen)
    if name in ("Run", "Cancel run"):
        painter.setBrush(QColor(color))
    for line in glyphs[name]:
        polygon = QPolygonF([QPointF(*p) for p in line])
        if name in ("Run", "Cancel run"):
            painter.drawPolygon(polygon)
        else:
            painter.drawPolyline(polygon)
    if name == "Junction":
        painter.setBrush(QColor(color))
        painter.drawEllipse(QPointF(12,12),2.4,2.4)
    painter.end()
    return QIcon(pix)


STYLESHEET = """
QMainWindow, QDialog { background: @panel@; color: @ink@; }
QMenuBar, QMenu, QToolBar, QStatusBar { background: @panel@; color: @ink@; }
QMenuBar { border-bottom: 1px solid @line@; }
QMenuBar::item { padding: 5px 8px; }
QMenu::item { padding: 5px 26px 5px 22px; }
QMenuBar::item:selected, QMenu::item:selected { background: @active@; }
QMenu::item:disabled { color: @mute@; }
QToolBar { padding: 5px; spacing: 5px; border: 0; }
QToolBar::separator { background: @line@; width: 1px; margin: 5px; }
QStatusBar { border-top: 1px solid @line@; }
QLabel { color: @ink@; }
QScrollArea, QWidget#plotStack, QWidget#plotControls, QWidget#plotViewport { background: @panel@; color: @ink@; }
QScrollArea { border: 1px solid @line@; }
QComboBox, QSpinBox, QDoubleSpinBox { color: @ink@; background: @well@; border: 1px solid @line@; border-radius: 5px; }
QComboBox { padding: 4px 22px 4px 7px; }
QComboBox QAbstractItemView { background: @well@; color: @ink@; selection-background-color: @active@; selection-color: @ink@; }
QLabel#analysisMode { border: 1px solid @line@; border-radius: 5px; padding: 5px 8px; color: @mute@; }
QPushButton, QToolButton { background: @panel@; color: @ink@; border: 1px solid @line@; border-radius: 5px; padding: 5px 8px; }
QPushButton:hover, QToolButton:hover { background: @hover@; }
QPushButton:checked, QToolButton:checked { background: @active@; border-color: @focus@; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus { border-color: @focus@; }
QPushButton:disabled, QToolButton:disabled { color: @mute@; background: @panel@; }
QToolButton#runButton:enabled { background: @run@; color: white; border-color: @run@; font-weight: 600; }
QToolButton#runButton:enabled:hover { background: @runhover@; }
QToolButton#runButton:enabled:focus { border-color: @focus@; }
QLineEdit, QPlainTextEdit { color: @ink@; background: @well@; border: 1px solid @line@; border-radius: 5px; selection-background-color: @active@; selection-color: @ink@; }
QLineEdit { padding: 4px 7px; }
QListWidget, QTreeWidget { background: @well@; color: @ink@; border: 1px solid @line@; }
QListWidget::item, QTreeWidget::item { padding: 4px 5px; }
QListWidget::item:selected, QTreeWidget::item:selected { background: @active@; color: @ink@; }
QDockWidget { color: @ink@; font-weight: 600; }
QDockWidget::title { background: @panel@; padding: 7px; border-bottom: 1px solid @line@; }
QTabWidget::pane { border: 1px solid @line@; }
QTabBar::tab { background: @panel@; color: @mute@; padding: 6px 9px; border: 1px solid @line@; }
QTabBar::tab:selected { background: @active@; color: @ink@; }
"""


def desktop_colors(dark):
    return themes["dark" if dark else "light"]


def apply_desktop_theme(window, dark):
    colors = desktop_colors(dark)
    palette = QPalette(window.palette())
    for role, key in (
        ("Window", "panel"), ("WindowText", "ink"), ("Base", "well"),
        ("AlternateBase", "panel"), ("Text", "ink"), ("Button", "panel"),
        ("ButtonText", "ink"), ("Highlight", "active"), ("HighlightedText", "ink"),
        ("ToolTipBase", "well"), ("ToolTipText", "ink"), ("Mid", "line"),
    ):
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colors[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["mute"]))
    window.setPalette(palette)
    if hasattr(window, "plot_window"):
        window.plot_window.setPalette(palette)
    qss = STYLESHEET
    for key, value in colors.items():
        qss = qss.replace(f"@{key}@", value)
    window.setStyleSheet(qss)
    if hasattr(window, "plot_window"):
        window.plot_window.setStyleSheet(qss)
    for action in window.findChildren(QAction):
        name = {"Open…":"Open", "Analysis setup…":"Analysis setup", "Part…":"Part",
                "Show plot":"Plots"}.get(action.text(), action.text())
        if name in glyphs:
            action.setIcon(action_icon(name, "#FFFFFF" if name == "Run" else colors["ink"]))
            action.setIconVisibleInMenu(True)
