"""Light and dark workbench styling; dimensions remain Qt logical pixels.

The dark theme is the light stylesheet with every colour remapped through ``_DARK``.
"""
import re

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont

_state={"dark":False}

_LIGHT_SHEET = """
        QMainWindow, QDialog, QDockWidget, QWidget#TaskWorkspace { background: #f3f5f8; }
        QWidget { color: #253247; }
        QMainWindow::separator { background: #e3e8ef; width: 4px; height: 4px; }
        QMainWindow::separator:hover { background: #9bb9df; }
        QMenuBar, QToolBar { background: #ffffff; border: none; spacing: 4px; }
        QMenuBar { border-bottom: 1px solid #e6eaf0; }
        QMenuBar::item { padding: 6px 12px; background: transparent; }
        QMenuBar::item:selected, QMenu::item:selected { background: #e5efff; color: #164b96; }
        QMenu { background: white; border: 1px solid #ccd5e0; padding: 5px; }
        QMenu::item { padding: 6px 28px 6px 24px; border-radius: 3px; }
        QMenu::separator { height: 1px; background: #e6eaf0; margin: 4px 8px; }
        QToolBar { padding: 3px 6px; border-bottom: 1px solid #dce2eb; }
        QToolBar::separator { background: #dce2eb; width: 1px; margin: 6px 8px; }
        QToolBar QLabel#ToolbarLabel { color: #253247; font-weight: bold; padding: 0 6px 0 2px; }
        QToolBar QLabel#ToolbarCaption { color: #68768a; padding: 0 3px 0 8px; }
        QToolBar QComboBox { min-width: 70px; }
        QToolButton { border: 1px solid transparent; border-radius: 5px; padding: 5px; }
        QToolButton:hover { background: #edf3fd; border-color: #d1dff4; }
        QToolButton:pressed, QToolButton:checked { background: #dce9fb; border-color: #b9d0f0; color: #164b96; }
        QToolBar#Workspaces QToolButton { padding: 3px 4px; font-size: 9pt; }
        QToolButton#PrimaryToolButton { background: #245fab; color: white; font-weight: bold; padding: 5px 12px; }
        QToolButton#PrimaryToolButton:hover { background: #194f96; }
        QToolButton#PrimaryToolButton:disabled { background: #eef1f5; color: #8592a4; }
        QDockWidget::title { background: #edf1f6; padding: 7px 12px; font-weight: bold; color: #3b4a60;
            border-bottom: 1px solid #dce2eb; }
        QLabel#WorkspaceTitle { font-size: 15pt; font-weight: 600; color: #1d2a3d; }
        QLabel#WorkspaceSummary { color: #68768a; }
        QLabel#SectionHeading { color: #68768a; font-size: 8pt; font-weight: bold; padding-top: 6px; }
        QLabel#Hint { color: #68768a; }
        QLabel#Feedback { color: #3b4a60; }
        QToolButton#SectionToggle { font-weight: bold; color: #3b4a60; text-align: left; padding: 6px 4px;
            border: none; border-top: 1px solid #e3e8ef; border-radius: 0; background: transparent; }
        QToolButton#SectionToggle:hover { color: #164b96; background: transparent; }
        QToolButton#SectionToggle:checked { color: #164b96; background: transparent; }
        QScrollArea { border: none; background: #f8fafc; }
        QScrollArea > QWidget > QWidget { background: #f8fafc; }
        QTabWidget::pane { border: none; }
        QTabBar { background: #e9edf3; }
        QTabBar::tab { background: #e9edf3; padding: 7px 14px; border: none; color: #54647a; }
        QTabBar::tab:selected { background: white; color: #1659ad; border-bottom: 2px solid #245fab; }
        QTabBar::tab:hover:!selected { background: #f1f5fa; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextBrowser {
            background: white; border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px 7px;
            selection-background-color: #245fab; selection-color: white;
        }
        QComboBox { min-height: 20px; padding-right: 22px; }
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #9bb9df; }
        QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QSpinBox:focus { border-color: #2563b4; }
        QLineEdit[readOnly="true"] { background: #f5f7fa; }
        QAbstractItemView { background: white; alternate-background-color: #f5f8fc;
            border: 1px solid #dce2eb; selection-background-color: #dce9fb; selection-color: #163e73; }
        QAbstractItemView::item { padding: 3px; }
        QTreeView::item:hover, QTableView::item:hover { background: #f1f6fd; }
        QHeaderView::section { background: #edf1f6; color: #54647a; padding: 6px;
            border: none; border-bottom: 1px solid #dce2eb; }
        QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 4px;
            padding: 6px 12px; min-height: 18px; }
        QPushButton:hover { background: #edf3fd; border-color: #9bb9df; }
        QPushButton:pressed { background: #dce9fb; }
        QScrollBar#ModelTreeScrollBar:vertical { width: 16px; background: #f3f5f8;
            border: 1px solid #dce2eb; border-radius: 4px; margin: 0; }
        QScrollBar#ModelTreeScrollBar::handle:vertical { background: white;
            border: 1px solid #cbd5e1; border-radius: 4px; min-height: 32px; margin: 1px; }
        QScrollBar#ModelTreeScrollBar::handle:vertical:hover { background: #edf3fd; border-color: #9bb9df; }
        QScrollBar#ModelTreeScrollBar::handle:vertical:pressed { background: #dce9fb; }
        QScrollBar#ModelTreeScrollBar::add-line:vertical, QScrollBar#ModelTreeScrollBar::sub-line:vertical {
            height: 0; border: none; background: transparent; }
        QScrollBar#ModelTreeScrollBar::add-page:vertical, QScrollBar#ModelTreeScrollBar::sub-page:vertical {
            background: transparent; }
        QPushButton[primary="true"] { background: #245fab; border-color: #245fab; color: white; font-weight: bold;
            padding: 8px 12px; }
        QPushButton[primary="true"]:hover { background: #194f96; }
        QPushButton:focus, QToolButton:focus { border: 2px solid #2563b4; }
        QWidget:disabled { color: #8592a4; }
        QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled { background: #eef1f5; border-color: #dce2eb; }
        QPushButton[primary="true"]:disabled { background: #eef1f5; color: #8592a4; border-color: #dce2eb; }
        QCheckBox { spacing: 7px; padding: 3px; }
        QGroupBox { border: 1px solid #dce2eb; border-radius: 5px; margin-top: 12px; padding-top: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; font-weight: bold; }
        QStatusBar { background: white; border-top: 1px solid #dce2eb; padding: 2px 6px; color: #3b4a60; }
        QStatusBar#DockFooter { background: #e9edf3; border: none; padding: 0 6px; }
        QStatusBar#DockFooter::item { border: none; }
        QStatusBar QLabel#StatusCaption { color: #68768a; }
        QStatusBar QComboBox { padding: 2px 22px 2px 6px; min-height: 16px; }
        QToolTip { background: #253247; color: white; border: none; padding: 6px; }
        QLabel#ModelColourLegend {
            background: #ffffff; color: #253247;
            border: 1px solid #ccd5e0; border-radius: 4px;
        }
    """

# controls whose native look is light-only; styled only in dark mode so the light theme is unchanged
_DARK_ONLY = """
        QScrollBar:vertical { background: #f3f5f8; width: 12px; margin: 0; }
        QScrollBar:horizontal { background: #f3f5f8; height: 12px; margin: 0; }
        QScrollBar::handle:vertical { background: #cbd5e1; border-radius: 5px; min-height: 28px; margin: 2px; }
        QScrollBar::handle:horizontal { background: #cbd5e1; border-radius: 5px; min-width: 28px; margin: 2px; }
        QScrollBar::handle:hover { background: #9bb9df; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
        QScrollBar::add-page, QScrollBar::sub-page { background: none; }
        QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; border: 1px solid #cbd5e1;
            border-radius: 3px; background: white; }
        QRadioButton::indicator { border-radius: 8px; }
        QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: #245fab; border-color: #245fab; }
        QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #9bb9df; }
"""

# light colour -> dark colour (lower-case hex; "white" is handled separately)
_DARK={
    "#f3f5f8":"#1b2029","#253247":"#d8e0ec","#e3e8ef":"#2b3340","#9bb9df":"#46689a","#ffffff":"#232a35",
    "#e6eaf0":"#2f3744","#e5efff":"#27405f","#164b96":"#8ab8f2","#ccd5e0":"#3a4452","#dce2eb":"#323b49",
    "#68768a":"#93a0b3","#edf3fd":"#2a3748","#d1dff4":"#3b5272","#dce9fb":"#2f4766","#b9d0f0":"#4a6a96",
    "#245fab":"#3a79c7","#194f96":"#2f68b0","#eef1f5":"#262d38","#8592a4":"#6a778a","#edf1f6":"#262e3a",
    "#3b4a60":"#b8c4d6","#1d2a3d":"#eef3fa","#f8fafc":"#1f252f","#e9edf3":"#202733","#54647a":"#a2afc2",
    "#1659ad":"#8ab8f2","#f1f5fa":"#2a323f","#cbd5e1":"#3d4757","#2563b4":"#5b9cec","#f5f7fa":"#1f252e",
    "#f5f8fc":"#202733","#163e73":"#cfe2fb","#f1f6fd":"#28323f",
}
_HEX=re.compile(r"#[0-9a-fA-F]{6}\b|\bwhite\b")


def _dark_sheet(sheet):
    def swap(match):
        text=match.group(0).lower()
        if text=="white":
            return "#232a35"
        return _DARK.get(text,text)
    sheet=_HEX.sub(swap,sheet)
    # primary buttons and the tooltip keep light text on their (already dark) backgrounds
    return sheet.replace("QToolTip { background: #d8e0ec; color: #232a35;","QToolTip { background: #d8e0ec; color: #1b2029;")


# 3D viewport defaults per theme and overlay colours (light, dark)
VIEWPORT={"light":{"background":"#fbfbfd","edge_color":"#4a6572"},
          "dark":{"background":"#161b23","edge_color":"#9db4c2"}}
OVERLAY={"grid":("#aac0d8","#3c4c60"),"grid_light":("#c8d7e6","#3c4c60"),"u":("#c0392b","#f2837a"),
         "v":("#23834a","#5fd08d"),"n":("#1565c0","#6aa9f5"),"label":("#0d47a1","#8ab8f2"),
         "mark":("#ff8f00","#ffb84d"),"error":("#b42318","#ff8a80"),"outcome":("#555555","#aab4c3"),
         "ok":("#15803d","#4ade80"),"fail":("#b91c1c","#f87171"),"warn":("#a16207","#fbbf24"),
         "idle":("#64748b","#93a0b3"),"run":("#2563eb","#6aa9f5"),"muted":("#7a8799","#93a0b3")}


def overlay(name):
    return OVERLAY[name][1 if _state["dark"] else 0]


def sync_viewport(window,*,refresh=False):
    """Swap viewport colours that still hold the other theme's defaults; user-chosen colours stay."""
    viewport=getattr(window,"viewport",None)
    if viewport is None:
        return
    new=VIEWPORT["dark" if _state["dark"] else "light"]
    old=VIEWPORT["light" if _state["dark"] else "dark"]
    style=viewport.visualization
    dark=_state["dark"]
    old_values={"background":{"white","#ffffff","#fbfbfd","#f0f3f8","#e9edf3"} if dark else {old["background"]},
                "edge_color":{old["edge_color"],"#536477","#344559"} if dark else {old["edge_color"]}}
    changes={key:new[key] for key in new if str(getattr(style,key)).lower() in old_values[key]}
    try:
        from dataclasses import replace
        if changes:
            viewport.set_visualization(replace(style,**changes))
        elif refresh and getattr(viewport,"_scene",None) is not None:
            viewport.set_visualization(style)
    except Exception:
        pass
    panel=getattr(window,"panels",{}).get("Visualization") if hasattr(window,"panels") else None
    sync=getattr(panel,"sync_from_viewport",None) or getattr(panel,"refresh",None)
    if callable(sync):
        try:sync()
        except Exception:pass


def is_dark():
    return _state["dark"]


def saved_dark():
    """Saved choice; with none saved, follow the operating-system colour scheme."""
    try:
        settings=QSettings("ANY","ANYfem-Qt")
        if settings.contains("dark_mode"):
            return settings.value("dark_mode",False,type=bool)
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.styleHints().colorScheme()==Qt.ColorScheme.Dark
    except Exception:
        return False


def set_dark(window,dark,*,persist=True):
    _state["dark"]=bool(dark)
    if persist:
        QSettings("ANY","ANYfem-Qt").setValue("dark_mode",bool(dark))
    apply_theme(window)
    sync_viewport(window,refresh=True)
    for widget in window.findChildren(object):
        if hasattr(widget,"update") and type(widget).__name__ in ("HistoryPlot","Wordmark"):
            widget.update()


def apply_theme(window):
    font=QFont(window.font())
    font.setPointSizeF(10)
    window.setFont(font)
    window.setStyleSheet(_dark_sheet(_LIGHT_SHEET+_DARK_ONLY) if _state["dark"] else _LIGHT_SHEET)
