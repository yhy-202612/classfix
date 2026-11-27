from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .widgets import icon


class TrayIcon(QSystemTrayIcon):
    def __init__(self, window, watcher, quit_application, desktop_goose=None, open_goose_settings=None):
        super().__init__(icon("logo", size=32), window)
        self.window = window
        self.watcher = watcher
        self.setToolTip("ClassFix · 等待 PowerPoint")
        menu = QMenu()
        self.open_action = QAction("打开 ClassFix", self)
        self.open_action.triggered.connect(self.open_window)
        self.monitor_action = QAction("监视 PowerPoint", self)
        self.monitor_action.setCheckable(True)
        self.monitor_action.setChecked(True)
        self.monitor_action.toggled.connect(watcher.set_enabled)
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(quit_application)
        menu.addAction(self.open_action)
        menu.addAction(self.monitor_action)
        if desktop_goose is not None:
            goose_menu = menu.addMenu("ClassFix 桌宠")

            def ask():
                if not desktop_goose.ask():
                    open_goose_settings()

            goose_menu.addAction("问鹅", ask)
            pause = goose_menu.addAction("暂停鹅出场")
            pause.setCheckable(True)
            pause.toggled.connect(desktop_goose.set_paused)
            goose_menu.addAction("桌鹅与触摸键盘设置…", open_goose_settings)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.setContextMenu(menu)
        self.activated.connect(self._activated)
        watcher.status.connect(self.setToolTip)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_window()

    def open_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
