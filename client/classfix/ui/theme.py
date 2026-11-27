from PySide6.QtGui import QColor, QPalette

FONT = "'Segoe UI', 'Microsoft YaHei UI'"

BASE = """
* {{ font-family: {font}; font-size: 14px; }}
QWidget {{ color: {ink}; }}
QMainWindow, QWidget#Root, QScrollArea, QWidget#Page {{ background: {bg}; }}
QWidget#Rail {{ background: {rail}; border-right: 1px solid {line}; }}
QWidget#Bar {{ background: {panel}; border-bottom: 1px solid {line}; }}
QFrame#Panel {{ background: {panel}; border: 1px solid {line}; border-radius: 12px; }}
QFrame#Rule {{ background: {line}; max-height: 1px; border: 0; }}
/* ---- 弹窗与浮层：必须显式给底色，否则深色主题下会沿用系统浅色底而字已变浅 ---- */
QDialog, QMessageBox, QInputDialog, QProgressDialog, QFileDialog {{ background: {bg}; color: {ink}; }}
QMessageBox QLabel, QDialog QLabel {{ color: {ink}; background: transparent; }}
QMessageBox QPushButton, QDialog QPushButton {{ min-width: 76px; }}
QMenu {{ background: {panel}; border: 1px solid {line}; padding: 5px; }}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 6px; color: {ink}; }}
QMenu::item:selected {{ background: {selected}; color: {accent}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 4px 8px; }}
QToolTip {{ background: {panel}; color: {ink}; border: 1px solid {line}; padding: 6px 8px; }}
QStatusBar {{ background: {panel}; color: {muted}; border-top: 1px solid {line}; }}
QStatusBar::item {{ border: 0; }}
QLabel[role="brand"] {{ font-size: 23px; font-weight: 700; }}
QLabel[role="title"] {{ font-size: 29px; font-weight: 700; }}
QLabel[role="heading"] {{ font-size: 18px; font-weight: 700; }}
QLabel[role="strong"] {{ font-weight: 650; }}
QLabel[role="muted"], QLabel[role="small"], QLabel[role="railLabel"] {{ color: {muted}; }}
QLabel[role="small"], QLabel[role="railLabel"], QLabel[role="tag"] {{ font-size: 12px; }}
QLabel[role="tag"] {{ font-weight: 700; color: {accent}; }}
QLabel[role="avatar"] {{ border-radius: 15px; background: {selected}; color: {accent};
  font-size: 12px; font-weight: 700; }}
QLabel[status="ok"] {{ color: {good}; }}
QLabel[status="warning"] {{ color: {warn}; }}
QLabel[status="problem"] {{ color: {bad}; }}
QLabel[status="unknown"] {{ color: {muted}; }}
QPushButton {{ background: {panel}; border: 1px solid {line}; border-radius: 7px;
  padding: 8px 13px; min-height: 20px; font-weight: 550; }}
QPushButton:hover {{ background: {hover}; border-color: {edge}; }}
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QListWidget:focus {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {muted}; background: {rail}; }}
QPushButton[role="primary"] {{ background: {solid}; border-color: {solid}; color: #fff; }}
QPushButton[role="primary"]:hover {{ background: {solid_hover}; border-color: {solid_hover}; }}
QPushButton[role="primary"]:disabled {{ background: {rail}; border-color: {line}; color: {muted}; }}
QPushButton[role="nav"] {{ text-align: left; padding: 8px 12px; background: transparent;
  border: 1px solid transparent; color: {nav}; }}
QPushButton[role="nav"]:hover {{ background: {hover}; }}
QPushButton[role="nav"]:checked {{ background: {selected}; color: {accent}; font-weight: 700; }}
QPushButton[role="link"] {{ background: transparent; border-color: transparent; color: {accent}; }}
QPushButton[role="link"]:hover {{ background: {hover}; }}
QPushButton[role="account"] {{ text-align: left; padding: 5px 11px; border-radius: 9px; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {edge}; min-height: 32px; border-radius: 3px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QLineEdit, QComboBox, QListWidget, QPlainTextEdit {{
  background: {panel}; border: 1px solid {line}; border-radius: 7px; padding: 8px; }}
QComboBox QAbstractItemView {{ background: {panel}; color: {ink}; selection-background-color: {selected}; }}
QListWidget::item {{ padding: 7px; border-radius: 5px; }}
QListWidget::item:selected {{ background: {selected}; color: {accent}; }}
QProgressBar {{ background: {line}; border: 0; border-radius: 3px; min-height: 6px; max-height: 6px; }}
QProgressBar::chunk {{ background: {solid}; border-radius: 3px; }}
QRadioButton, QCheckBox {{ spacing: 8px; padding: 4px; }}
QTabWidget::pane {{ border: 0; }}
QTabBar::tab {{ background: {panel}; padding: 9px 14px; border-bottom: 2px solid {line}; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom-color: {accent}; }}
"""

LIGHT = dict(ink="#16253a", bg="#f4f7fb", rail="#edf2f8", line="#dce5f0", panel="#ffffff",
             muted="#52647b", accent="#1554b5", solid="#185abf", solid_hover="#124a9f",
             good="#126845", warn="#854e08", bad="#ae3232", hover="#f0f5fd", edge="#bacbe0",
             selected="#dceaff", nav="#334863")
DARK = dict(ink="#e5eefc", bg="#111a28", rail="#161f30", line="#31415a", panel="#1b283a",
            muted="#a8bad1", accent="#9dc5ff", solid="#235bb1", solid_hover="#286bd0",
            good="#78d9aa", warn="#ffd18b", bad="#ffa5a5", hover="#223650", edge="#4d6688",
            selected="#213c5f", nav="#ccd9ec")


def palette_for(dark=False):
    """和样式表同源的调色板。

    Windows 11 跟随系统深色时，Fusion 会自己换一套暗色调色板；只要有一处控件没被样式表覆盖，
    就会出现「深色窗口里冒出一个白底弹窗」。把调色板也一起设掉，原生绘制的部分（对话框窗口底、
    菜单、工具提示、状态栏）就始终跟主题一致。
    """
    colors = DARK if dark else LIGHT
    role = QPalette.ColorRole
    palette = QPalette()
    palette.setColor(role.Window, QColor(colors["bg"]))
    palette.setColor(role.WindowText, QColor(colors["ink"]))
    palette.setColor(role.Base, QColor(colors["panel"]))
    palette.setColor(role.AlternateBase, QColor(colors["rail"]))
    palette.setColor(role.Text, QColor(colors["ink"]))
    palette.setColor(role.Button, QColor(colors["panel"]))
    palette.setColor(role.ButtonText, QColor(colors["ink"]))
    palette.setColor(role.BrightText, QColor(colors["bad"]))
    palette.setColor(role.Highlight, QColor(colors["selected"]))
    palette.setColor(role.HighlightedText, QColor(colors["ink"]))
    palette.setColor(role.ToolTipBase, QColor(colors["panel"]))
    palette.setColor(role.ToolTipText, QColor(colors["ink"]))
    palette.setColor(role.PlaceholderText, QColor(colors["muted"]))
    palette.setColor(role.Link, QColor(colors["accent"]))
    for disabled in (role.Text, role.ButtonText, role.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, disabled, QColor(colors["muted"]))
    return palette


def apply_theme(app, dark=False):
    app.setStyle("Fusion")
    app.setPalette(palette_for(dark))
    app.setStyleSheet(BASE.format(font=FONT, **(DARK if dark else LIGHT)))
