from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QMainWindow, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .widgets import Hero, button, horizontal, icon, label, panel, rule


NAV = (
    ("home", "首页"),
    ("aid", "故障急救"),
    ("video", "PPT 视频诊断"),
    ("audio", "声音诊断"),
    ("check", "课前巡检"),
    ("history", "诊断记录"),
    ("settings", "设置"),
)


class Page(QWidget):
    def __init__(self, title, description):
        super().__init__()
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 26, 34, 30)
        outer.setSpacing(5)
        outer.addWidget(label(title, "title"))
        outer.addWidget(label(description, "muted"))
        outer.addSpacing(15)
        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        outer.addLayout(self.body)
        outer.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClassFix · 课堂故障诊断与急救助手")
        self.setWindowIcon(icon("logo", size=32))
        self.resize(1180, 805)
        self.setMinimumSize(810, 580)
        self.pages = {}
        self.nav_buttons = {}
        self.background_enabled = False
        self.allow_exit = False
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        rail = QWidget()
        rail.setObjectName("Rail")
        rail.setFixedWidth(219)
        self.rail_layout = QVBoxLayout(rail)
        self.rail_layout.setContentsMargins(14, 20, 14, 17)
        self.rail_layout.setSpacing(5)
        mark = label("")
        mark.setPixmap(icon("logo", "#2f70c8", 32).pixmap(QSize(32, 32)))
        brand = horizontal(8)
        brand.addWidget(mark)
        brand.addWidget(label("ClassFix", "brand"), 1)
        self.rail_layout.addLayout(brand)
        self.rail_layout.addWidget(label("课堂故障诊断与急救助手", "small"))
        self.rail_layout.addSpacing(19)
        self.rail_layout.addWidget(label("工作台", "railLabel"))
        self.rail_layout.addSpacing(3)
        self.group = QButtonGroup(self)
        for key, title in NAV:
            item = button(title, lambda _checked=False, route=key: self.navigate(route), "nav", key)
            item.setCheckable(True)
            item.setIconSize(QSize(18, 18))
            self.group.addButton(item)
            self.rail_layout.addWidget(item)
            self.nav_buttons[key] = item
        self.rail_layout.addStretch()
        self.rail_layout.addWidget(rule())
        self.rail_layout.addSpacing(7)
        self.rail_layout.addWidget(label("本地检测 · 默认离线", "small"))
        self.rail_layout.addWidget(label("Classroom Troubleshooting Assistant", "small"))
        outer.addWidget(rail)
        content = QVBoxLayout()
        content.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("Bar")
        bar.setFixedHeight(51)
        tools = QHBoxLayout(bar)
        tools.setContentsMargins(34, 0, 34, 0)
        self.location = label("")
        tools.addWidget(self.location)
        tools.addStretch()
        tools.addWidget(label("Windows 教学设备", "small"))
        tools.addSpacing(12)
        self.bar_extra = horizontal(10)   # 右侧账号栏的插入位
        tools.addLayout(self.bar_extra)
        content.addWidget(bar)
        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        outer.addLayout(content, 1)

    def add_page(self, key, page):
        viewport = QScrollArea()
        viewport.setWidgetResizable(True)
        viewport.setWidget(page)
        self.stack.addWidget(viewport)
        self.pages[key] = (page, viewport)

    def set_account_bar(self, widget):
        """把账号栏挂到顶部栏最右侧（未登录时显示登录入口）。"""
        self.account_bar = widget
        self.bar_extra.addWidget(widget)
        return widget

    def navigate(self, key):
        page, viewport = self.pages[key]
        self.stack.setCurrentWidget(viewport)
        self.location.setText(dict(NAV)[key])
        self.nav_buttons[key].setChecked(True)
        if hasattr(page, "refresh_on_open"):
            page.refresh_on_open()
        return page

    def closeEvent(self, event):
        if self.background_enabled and not self.allow_exit:
            event.ignore()
            self.hide()
            tray = getattr(self, "tray", None)
            if tray and not getattr(self, "_hide_notice_shown", False):
                tray.showMessage("ClassFix 仍在运行", "正在后台监视 PowerPoint。双击托盘图标可重新打开。",
                                 tray.MessageIcon.Information, 3500)
                self._hide_notice_shown = True
            return
        super().closeEvent(event)


class HomePage(Page):
    def __init__(self, navigate, inspect, load_overview=None):
        super().__init__("课堂设备工作台", "快速定位课堂设备故障，检查修复效果。")
        self.load_overview = load_overview
        self.body.addWidget(Hero(inspect))
        overview = horizontal(14)
        health, health_layout = panel()
        health_layout.addWidget(label("设备状态", "heading"))
        self.health = label("尚未检测 · 运行课前检查获取本机状态。", "muted")
        health_layout.addWidget(self.health)
        overview.addWidget(health, 1)
        history, history_layout = panel()
        history_layout.addWidget(label("最近诊断", "heading"))
        self.history = label("还没有诊断记录。", "muted")
        history_layout.addWidget(self.history)
        history_layout.addWidget(button("查看记录", lambda: navigate("history"), "link", "arrow"),
                                 alignment=Qt.AlignmentFlag.AlignLeft)
        overview.addWidget(history, 1)
        self.body.addLayout(overview)
        self.body.addWidget(label("常见故障", "heading"))
        for title, detail, route in (
            ("PPT 视频播不出来", "检查视频链接、容器和音视频编码。", "video"),
            ("教室没有声音", "检查系统音量、默认输出及 PowerPoint 应用音量。", "audio"),
        ):
            card, box = panel(14)
            row = horizontal()
            glyph = label("")
            glyph.setPixmap(icon(route, size=21).pixmap(QSize(21, 21)))
            row.addWidget(glyph)
            lines = QVBoxLayout()
            lines.setSpacing(3)
            lines.addWidget(label(title, "strong"))
            lines.addWidget(label(detail, "small"))
            row.addLayout(lines, 1)
            row.addWidget(button("开始诊断", lambda _checked=False, key=route: navigate(key), "link", "arrow"))
            box.addLayout(row)
            self.body.addWidget(card)


    def refresh_on_open(self):
        if self.load_overview:
            self.load_overview()


class AidPage(Page):
    def __init__(self, video, audio, check):
        super().__init__("故障急救", "选出课堂上正在发生的问题。")
        for title, description, action in (
            ("A  点击视频没有反应", "先看原视频和放映触发方式。", lambda: video("click")),
            ("B  提示无法播放或格式不支持", "优先检查媒体链接和实际编码。", lambda: video("format")),
            ("C  黑屏、卡顿或画面异常", "检查视频参数、播放范围和外接屏幕。", lambda: video("picture")),
            ("D  有画面但没有声音", "检查音频轨道、系统输出和应用音量。", lambda: video("silent")),
            ("电脑整体没有声音", "查看总音量、混音器和输出设备。", audio),
            ("教学屏幕或设备连接异常", "查看显示器、音频和网络状态。", check),
        ):
            card, box = panel(12)
            row = horizontal()
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(label(title, "strong"))
            column.addWidget(label(description, "small"))
            row.addLayout(column, 1)
            row.addWidget(button("进入", action, "link", "arrow"))
            box.addLayout(row)
            self.body.addWidget(card)
