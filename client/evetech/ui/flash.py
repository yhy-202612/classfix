"""在副屏上闪示画面，让老师用眼睛确认投影是否真的投出。

QueryDisplayConfig 只能说明「Windows 认为有第二个输出在工作」，无法证明教学屏上
真的出现了画面（投影输入源选错、投影处于待机、分配器故障都会让画面消失）。
这一步用最直接的方式补上：在副屏上铺满一块高对比度的画面，问老师是否看到。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget


class FlashWindow(QWidget):
    """无边框、置顶、不吃鼠标的全屏单色窗口。"""

    def __init__(self, screen, index, total):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.index = index
        self.total = total
        self.setGeometry(screen.geometry())

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1b6ad0"))
        painter.setPen(QColor("#ffffff"))
        font = QFont()
        font.setPointSize(max(16, min(64, self.width() // 28)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                         "EveTech 投影验证\n屏幕 %d / %d" % (self.index, self.total))
        painter.end()


def target_screens():
    """除主屏之外的屏幕；只有一块屏时返回空列表。"""
    primary = QGuiApplication.primaryScreen()
    return [screen for screen in QGuiApplication.screens() if screen is not primary]


def flash_screens(screens=None, seconds=4):
    """在副屏上闪示指定秒数，返回实际闪示的屏幕数。"""
    chosen = target_screens() if screens is None else list(screens)
    windows = []
    for index, screen in enumerate(chosen, start=1):
        window = FlashWindow(screen, index, len(chosen))
        window.show()
        windows.append(window)
    if windows:
        QTimer.singleShot(int(seconds * 1000), lambda: [item.close() for item in windows])
    return len(windows)
