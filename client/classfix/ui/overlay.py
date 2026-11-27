"""Non-activating classroom prompts with explicit, local actions."""

import ctypes
from collections import deque
from time import monotonic, time

from PySide6.QtCore import (QObject, QEasingCurve, QPropertyAnimation, QRect, QRectF, QTimer,
                            Qt, Signal, QVariantAnimation)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton, QSlider,
                               QStyle, QStyleOptionSlider, QVBoxLayout, QWidget)

from ..audio import read_system_volume, set_system_mute, set_system_volume
from ..memory_cleanup import CLEANUP_STEPS, CleanupStatus, optimize_system_memory
from ..performance import open_task_manager
from .jobs import run_job
from .popup_stack import PopupStack


class CornerPopup(QWidget):
    def __init__(self, app, width, height, stack=None):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.stack = stack if stack is not None else PopupStack(app)
        self._moving_screens = False
        self._strip_width = None
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(width, height)
        self.setObjectName("CornerPopup")
        self.setStyleSheet("""
            QWidget#CornerPopup { background: transparent; }
            QLabel { color: #ffffff; font-family: 'Segoe UI','Microsoft YaHei UI'; }
            QLabel#PopupTitle { font-size: 17px; font-weight: 700; }
            QLabel#PopupDetail { color: #d0d0d0; }
            QProgressBar#MemoryMeter { background: #393939; border: 0; border-radius: 4px; }
            QProgressBar#MemoryMeter::chunk { background: #8ab4f5; border-radius: 4px; }
            QPushButton { color: #ffffff; background: #252525; border: 1px solid #666666;
                          border-radius: 6px; padding: 7px 12px; }
            QPushButton:hover { background: #383838; }
            QPushButton:disabled { color: #a0a0a0; border-color: #444444; }
            QSlider::groove:horizontal { height: 7px; background: #3a3a3a; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #f0f0f0; border-radius: 3px; }
            QSlider::handle:horizontal { background: #fff; width: 18px; margin: -6px 0; border-radius: 9px; }
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 105))
        painter.drawRoundedRect(self.rect().adjusted(3, 4, -1, -1), 13, 13)
        painter.setPen(QPen(QColor(88, 88, 88, 245), 1))
        painter.setBrush(QColor(8, 8, 8, 250))
        panel = self._panel_rect()
        painter.drawRoundedRect(panel, 12, 12)
        if self._strip_width is not None:
            edge = self._edge_rect()
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(panel), 12, 12)
            painter.setClipPath(clip)
            painter.fillRect(edge, QColor("#393939"))
            edge.setWidth(self._strip_width)
            painter.fillRect(edge, QColor("#8ab4f5"))

    def _panel_rect(self):
        return self.rect().adjusted(1, 1, -4, -4)

    def _edge_rect(self):
        panel = self._panel_rect()
        return QRect(panel.left(), panel.bottom() - 2, panel.width(), 3)

    def show_at(self, location=0):
        return self.stack.show(self, location)

    def show_without_activation(self):
        self.show()
        self.raise_()
        if hasattr(ctypes, "windll"):
            hwnd = int(self.winId())
            current = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, current | 0x08000000 | 0x00000080)
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)

    def animation_to(self, destination):
        animation = QPropertyAnimation(self, b"pos")
        animation.setDuration(200)
        animation.setStartValue(self.pos())
        animation.setEndValue(destination)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        return animation

    def heading(self, text=""):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName("PopupTitle")
        return label

    def description(self, text=""):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName("PopupDetail")
        label.setWordWrap(True)
        return label

    def body(self, spacing=7):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 13, 18, 13)
        layout.setSpacing(spacing)
        return layout

    def hideEvent(self, event):
        super().hideEvent(event)
        if not self._moving_screens:
            self.stack.remove(self)

    def closeEvent(self, event):
        super().closeEvent(event)
        self.stack.remove(self)


class TimedPopup(CornerPopup):
    def __init__(self, app, width, height, duration, stack):
        super().__init__(app, width, height, stack)
        self.countdown = QVariantAnimation(self)
        self.countdown.setStartValue(1.0)
        self.countdown.setEndValue(0.0)
        self.countdown.setDuration(duration)
        self.countdown.valueChanged.connect(self._paint_countdown)
        self.countdown.finished.connect(lambda: self.stack.slide_out(self))
        self._strip_width = self._edge_rect().width()

    def _paint_countdown(self, fraction):
        edge = self._edge_rect()
        width = round(edge.width() * fraction)
        if width != self._strip_width:
            self._strip_width = width
            self.update(edge)

    def showEvent(self, event):
        super().showEvent(event)
        if self.countdown.state() == self.countdown.State.Paused:
            self.countdown.resume()
        else:
            self.countdown.start()

    def hideEvent(self, event):
        if self._moving_screens and self.countdown.state() == self.countdown.State.Running:
            self.countdown.pause()
        else:
            self.countdown.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.countdown.stop()
        super().closeEvent(event)


def _abbreviate(text, length):
    text = " ".join(text.split())
    return text if len(text) <= length else text[:length - 1] + "…"


class FormatPopup(TimedPopup):
    closed = Signal()
    DISPLAY_MS = 12000

    def __init__(self, app, stack=None):
        super().__init__(app, 420, 184, self.DISPLAY_MS, stack)
        layout = self.body()
        self.title = self.heading()
        self.detail = self.description()
        self.powerpoint_error = self.description()
        close = QPushButton("知道了")
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addWidget(self.description("完整内容见诊断记录"))
        row.addStretch()
        row.addWidget(close)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        layout.addWidget(self.powerpoint_error)
        layout.addStretch()
        layout.addLayout(row)

    def notify_problem(self, problem, location=0):
        title = problem.category_title
        self.title.setText(f"{title} 第{problem.slide}页" if problem.slide > 0 else title)
        evidence = problem.detail or "未读取到本机检测详情"
        self.detail.setText(f"检测依据：{_abbreviate(evidence, 64)}")
        if problem.powerpoint_error:
            self.powerpoint_error.setText(f"PowerPoint 报错：{_abbreviate(problem.powerpoint_error, 54)}")
        elif problem.api_error:
            self.powerpoint_error.setText(f"PowerPoint 放映接口返回 {problem.api_error}")
        else:
            self.powerpoint_error.setText("PowerPoint 报错：未读取到")
        return self.show_at(location)

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closed.emit()


class MediaAlerts(QObject):
    def __init__(self, app, stack):
        super().__init__(stack)
        self.app = app
        self.stack = stack
        self._waiting = deque()
        self._open_count = 0

    def notify_problem(self, problem, location):
        self._waiting.append((problem, location))
        return self._show_next()

    def _show_next(self):
        # Keep queued evidence as data until a card can be created. A slide
        # deck with dozens of warnings need not create dozens of windows.
        if not self._waiting or self._open_count >= 6:
            return None
        problem, location = self._waiting.popleft()
        popup = FormatPopup(self.app, self.stack)
        popup.notify_problem(problem, location)
        self._open_count += 1

        def finished():
            self._open_count -= 1
            popup.deleteLater()
            self._show_next()

        popup.closed.connect(finished, Qt.ConnectionType.SingleShotConnection)
        return popup


class AbsoluteSlider(QSlider):
    def _move_to(self, x):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        control = QStyle.ComplexControl.CC_Slider
        groove = style.subControlRect(control, option, QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(control, option, QStyle.SubControl.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        position = max(0, min(span, round(x) - groove.left() - handle.width() // 2))
        value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), position, span, option.upsideDown)
        self.setSliderPosition(value)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.setSliderDown(True)
        self._move_to(event.position().x())
        event.accept()

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._move_to(event.position().x())
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self._move_to(event.position().x())
            self.setSliderDown(False)
            event.accept()


class VolumePopup(CornerPopup):
    dismissed = Signal()

    def __init__(self, app, stack=None):
        super().__init__(app, 430, 154, stack)
        self.muted = False
        self._volume = 0
        self._confirmed = (0, False)
        self._syncing = False
        self._busy = False
        self._writes = deque()
        self._write_timer = QTimer(self, interval=45, singleShot=True)
        self._write_timer.timeout.connect(self._flush_write)
        self._guidance = "点击或拖动滑条可调整 Windows 系统音量"
        layout = self.body(8)
        self.title = self.heading()
        self.detail = self.description(self._guidance)
        self.slider = AbsoluteSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setSingleStep(2)
        self.slider.valueChanged.connect(self.change_volume)
        self.slider.sliderReleased.connect(self._flush_write)
        self.value = QLabel()
        self.value.setFixedWidth(43)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mute = QPushButton("取消静音")
        self.mute.clicked.connect(self.toggle_mute)
        close = QPushButton("关闭")
        close.clicked.connect(self.dismiss)
        row = QHBoxLayout()
        row.addWidget(self.slider, 1)
        row.addWidget(self.value)
        buttons = QHBoxLayout()
        buttons.addWidget(self.mute)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        layout.addLayout(row)
        layout.addLayout(buttons)

    def notify(self, slide, volume, muted, location=0):
        if not (self._busy or self._writes or self.slider.isSliderDown()):
            self._confirmed = (volume, muted)
            self._volume, self.muted = self._confirmed
            self._sync_controls()
        prefix = f"第{slide}页 · " if slide > 0 else ""
        self._guidance = f"{prefix}点击或拖动滑条可调整 Windows 系统音量"
        self.detail.setText(self._guidance)
        self.show_at(location)

    def _sync_controls(self):
        self._syncing = True
        self.slider.setValue(self._volume)
        self._syncing = False
        self.value.setText(f"{self._volume}%")
        self.title.setText("Windows 系统已静音" if self.muted else "调整 Windows 系统音量")
        self.mute.setText("取消静音" if self.muted else "静音")

    def change_volume(self, value):
        if self._syncing:
            return
        self._volume = value
        if value > 0:
            self.muted = False
        self._sync_controls()
        self._queue_write("volume", value)

    def toggle_mute(self):
        self.muted = not self.muted
        self._sync_controls()
        self._queue_write("mute", self.muted)
        self._flush_write()

    def _queue_write(self, kind, value):
        self._writes = deque(item for item in self._writes if item[0] != kind)
        self._writes.append((kind, value))
        self.detail.setText(self._guidance)
        if not (self._busy or self._write_timer.isActive()):
            self._write_timer.start()

    def _flush_write(self):
        if self._busy or not self._writes:
            return
        self._write_timer.stop()
        self._busy = True
        kind, value = self._writes.popleft()

        def write():
            setter = set_system_volume if kind == "volume" else set_system_mute
            if setter(value):
                return True, None
            return False, read_system_volume()

        def finished(result):
            self._busy = False
            ok, actual = result
            volume, muted = self._confirmed
            if ok:
                self._confirmed = (value, muted and value == 0) if kind == "volume" else (volume, value)
            elif actual is not None and None not in actual:
                self._confirmed = actual
            if self._writes:
                self._write_timer.start()
                return
            if not ok:
                self._volume, self.muted = self._confirmed
                self._sync_controls()
                self.detail.setText("未能调整系统音量，请检查默认声音输出。")

        run_job(write, finished, lambda _name: finished((False, None)))

    def dismiss(self):
        self._flush_write()
        self.dismissed.emit()
        self.close()


class MemoryPopup(CornerPopup):
    def __init__(self, app, stack, snoozed_until="", store_snooze=None):
        super().__init__(app, 460, 268, stack)
        try:
            self._snoozed_until = float(snoozed_until)
        except (TypeError, ValueError):
            self._snoozed_until = 0
        self._store_snooze = store_snooze
        self._next_notice = 0
        self._before_confirmation = None
        layout = self.body()
        self.usage = self.heading()
        self.reading = self.description()
        self.meter = QProgressBar()
        self.meter.setObjectName("MemoryMeter")
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        self.meter.setFixedHeight(8)
        self.meter.setAccessibleName("系统内存占用率")
        self.detail = self.description()
        self.optimize = QPushButton("优化内存")
        self.optimize.clicked.connect(self._optimize)
        self.cancel_optimize = QPushButton("取消")
        self.cancel_optimize.clicked.connect(self._cancel_optimization)
        self.cancel_optimize.hide()
        tasks = QPushButton("任务管理器")
        tasks.clicked.connect(self._tasks)
        self.snooze_button = QPushButton("1 小时内不再提醒")
        self.snooze_button.clicked.connect(self.snooze)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        actions = QHBoxLayout()
        actions.addWidget(self.optimize)
        actions.addWidget(self.cancel_optimize)
        actions.addWidget(tasks)
        actions.addStretch()
        actions.addWidget(self.snooze_button)
        actions.addWidget(close)
        reading = QHBoxLayout()
        reading.addWidget(self.heading("系统内存占用"))
        reading.addStretch()
        reading.addWidget(self.usage)
        layout.addLayout(reading)
        layout.addWidget(self.meter)
        layout.addWidget(self.reading)
        layout.addWidget(self.detail)
        layout.addStretch()
        layout.addLayout(actions)

    def notify(self, percent, available_mib, location=0):
        if self in self.stack.placements:
            return
        if monotonic() < self._next_notice or time() < self._snoozed_until or not self.optimize.isEnabled():
            return
        self.update_reading(percent, available_mib)
        self.detail.setText("课前可按需优化整机内存，操作需要 Windows 管理员授权。")
        self.show_at(location)

    def update_reading(self, percent, available_mib):
        self.meter.setValue(percent)
        self.usage.setText(f"{percent}%")
        self.reading.setText(f"可用 {available_mib} MiB" if available_mib is not None else "可用内存未读到")

    def update_if_open(self, percent, available_mib):
        if self in self.stack.placements:
            self.update_reading(percent, available_mib)

    def snooze(self):
        self._snoozed_until = time() + 3600
        if self._store_snooze is not None:
            self._store_snooze(self._snoozed_until)
        self.close()

    def closeEvent(self, event):
        self._cancel_optimization()
        if self in self.stack.placements:
            self._next_notice = monotonic() + 120
        super().closeEvent(event)

    def _cancel_optimization(self):
        if self._before_confirmation is None:
            return
        self.detail.setText(self._before_confirmation)
        self._before_confirmation = None
        self.optimize.setText("优化内存")
        self.cancel_optimize.hide()

    def _optimize(self):
        if self._before_confirmation is None:
            self._before_confirmation = self.detail.text()
            self.detail.setText("将回收整机工作集和缓存，包括前台程序，可能短时变慢。"
                                "请在课前关闭 PowerPoint 后操作。")
            self.optimize.setText("确认优化")
            self.cancel_optimize.show()
            return

        self._cancel_optimization()
        self.optimize.setEnabled(False)
        self.detail.setText("正在等待管理员授权或 Windows 完成内存优化…")

        def ready(result):
            self.detail.setText(self._optimization_summary(result))
            if result.after is not None:
                self.update_reading(result.after.percent, result.after.available_mib)
            self.optimize.setEnabled(True)

        def failed(_name):
            self.detail.setText("无法读取 Windows 内存优化结果。")
            self.optimize.setEnabled(True)

        run_job(optimize_system_memory, ready, failed)

    @staticmethod
    def _optimization_summary(result):
        before, after = result.before, result.after
        available = [f"{reading.available_mib} MiB" if reading else "尚未检测" for reading in (before, after)]
        usage = [f"{reading.percent}%" if reading else "尚未检测" for reading in (before, after)]
        lines = [f"可用内存 {' → '.join(available)}", f"系统占用 {' → '.join(usage)}"]
        for status, label in ((CleanupStatus.SUCCEEDED, "成功"),
                              (CleanupStatus.SKIPPED, "跳过"),
                              (CleanupStatus.FAILED, "失败")):
            names = [name for name, value in zip(CLEANUP_STEPS, result.steps) if value == status]
            if names:
                lines.append(f"{label}　{'、'.join(names)}")
        if result.message:
            lines.append(result.message)
        return "\n".join(lines)

    def _tasks(self):
        run_job(open_task_manager, lambda _result: None,
                lambda _name: self.detail.setText("未能打开 Windows 任务管理器。"))
