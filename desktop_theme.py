"""Colors, Qt styles and toolbar glyphs for the desktop workbench."""
from pathlib import Path
import re
import sys

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QApplication, QMenu, QMenuBar

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


THEME_FILE = Path(__file__).with_name("CircuitBench.qss")
_theme_text = ""
_themes = {}


def read_theme(path):
    source = path.read_text(encoding="utf-8-sig")
    colors = {}
    for mode in ("light", "dark"):
        match = re.search(r"/\* "+mode+r" (.*?) \*/",source)
        if not match:
            raise ValueError(f"Missing {mode} palette")
        colors[mode] = dict(re.findall(r"([a-z]+)=(#[0-9a-fA-F]{6})\b",match[1]))
        required = set(re.findall(r"@([a-z]+)@",source)) | {"panel","well","ink","mute","line","active","net","copper","sheet","grid",
                    "boundary","selection","marker","highlight","current","traceviolet","traceberry","traceolive"}
        if required-colors[mode].keys():
            raise ValueError(f"Incomplete {mode} palette")
    return source,colors


def reload_theme():
    global _theme_text,_themes
    override = Path(sys.executable).with_name(THEME_FILE.name) if getattr(sys,"frozen",False) else THEME_FILE
    try:
        _theme_text,_themes = read_theme(override)
    except (OSError,ValueError):
        if override==THEME_FILE:
            raise
        _theme_text,_themes = read_theme(THEME_FILE)


def desktop_colors(dark):
    if not _themes:
        reload_theme()
    return _themes["dark" if dark else "light"]


def apply_desktop_theme(window, dark):
    reload_theme()
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
    qss = _theme_text
    for key, value in colors.items():
        qss = qss.replace(f"@{key}@", value)
    window.setStyleSheet(qss)
    if hasattr(window, "plot_window"):
        window.plot_window.setStyleSheet(qss)
    for menu in (*window.findChildren(QMenu), *window.findChildren(QMenuBar)):
        menu.setPalette(QApplication.palette())
    for action in window.findChildren(QAction):
        name = {"Open…":"Open", "Analysis setup…":"Analysis setup", "Part…":"Part",
                "Show plot":"Plots"}.get(action.text(), action.text())
        if name in glyphs:
            action.setIcon(action_icon(name, "#FFFFFF" if name == "Run" else colors["ink"]))
            action.setIconVisibleInMenu(True)
