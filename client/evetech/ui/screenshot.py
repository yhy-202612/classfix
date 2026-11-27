"""Select and annotate a screen region, then ask a configured vision model."""

import math
from urllib.parse import urlsplit

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..ai import AIError, SCREENSHOT_QUESTION_LIMIT, ask_screenshot, validate_config
from .jobs import run_job


TITLE = "截图问 AI"
TOP_WINDOW = Qt.WindowType.WindowStaysOnTopHint
SMOOTH = Qt.TransformationMode.SmoothTransformation
ASPECT = Qt.AspectRatioMode.KeepAspectRatio
MAX_IMAGE_EDGE = 1600


def crop_image(image, selection, display_size):
    """Convert the selection's logical coordinates to the screenshot's physical pixels."""
    sx = image.width() / display_size.width()
    sy = image.height() / display_size.height()
    left = math.floor(selection.x() * sx)
    top = math.floor(selection.y() * sy)
    right = math.ceil((selection.x() + selection.width()) * sx)
    bottom = math.ceil((selection.y() + selection.height()) * sy)
    return image.copy(QRect(left, top, right - left, bottom - top).intersected(image.rect()))


class RegionPicker(QDialog):
    selected = Signal(QImage)

    def __init__(self, screen, image):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | TOP_WINDOW)
        self.setWindowTitle(TITLE)
        self.winId()
        self.windowHandle().setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.image = image
        self.origin = None
        self.region = QRect()
        instructions = QLabel("拖动框选要截图的区域", self)
        instructions.setStyleSheet("background: #202733; color: white; padding: 9px 16px; border-radius: 6px;")
        instructions.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        cancel = QPushButton("取消", self)
        cancel.setMinimumHeight(34)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        header = QHBoxLayout()
        header.addStretch()
        header.addWidget(instructions)
        header.addWidget(cancel)
        header.addStretch()
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addStretch()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.image)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 145))
        if not self.region.isEmpty():
            painter.save()
            painter.setClipRect(self.region)
            painter.drawImage(self.rect(), self.image)
            painter.restore()
            painter.setPen(QPen(QColor("#73b5ff"), 2))
            painter.drawRect(self.region.adjusted(0, 0, -1, -1))
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.region = QRect()
            self.update()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            end = event.position().toPoint()
            top_left = QPoint(min(self.origin.x(), end.x()), min(self.origin.y(), end.y()))
            bottom_right = QPoint(max(self.origin.x(), end.x()), max(self.origin.y(), end.y()))
            self.region = QRect(top_left, bottom_right).intersected(self.rect())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.origin is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.mouseMoveEvent(event)
        self.origin = None
        if self.region.width() < 4 or self.region.height() < 4:
            self.region = QRect()
            self.update()
            return
        image = crop_image(self.image, self.region, self.size())
        self.accept()
        self.selected.emit(image)


class AnnotationCanvas(QWidget):
    history_changed = Signal(bool)

    def __init__(self, image, parent):
        super().__init__(parent)
        if max(image.width(), image.height()) > MAX_IMAGE_EDGE:
            image = image.scaled(MAX_IMAGE_EDGE, MAX_IMAGE_EDGE, ASPECT, SMOOTH)
        self.image = image.convertToFormat(QImage.Format.Format_RGB888)
        self.strokes = []
        self.drawing = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(220, 160)
        self.setAccessibleName("截图标注区")
        self.pen = QPen(QColor("#e72a3f"), max(3, min(image.width(), image.height()) / 100),
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    def image_rect(self):
        size = self.image.size().scaled(self.size(), ASPECT)
        return QRect(QPoint((self.width() - size.width()) // 2, (self.height() - size.height()) // 2), size)

    def image_point(self, point):
        display = self.image_rect()
        x = (point.x() - display.x()) * self.image.width() / max(1, display.width())
        y = (point.y() - display.y()) * self.image.height() / max(1, display.height())
        return QPointF(min(max(0, x), self.image.width() - 1), min(max(0, y), self.image.height() - 1))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#222a36"))
        self.paint_image(painter, self.image_rect())
        painter.end()

    def paint_image(self, painter, target):
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, self.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(target.x(), target.y())
        painter.scale(target.width() / self.image.width(), target.height() / self.image.height())
        painter.setClipRect(self.image.rect())
        painter.setPen(self.pen)
        for stroke in self.strokes:
            if stroke.elementCount() == 1:
                painter.drawPoint(stroke.currentPosition())
            else:
                painter.drawPath(stroke)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position().toPoint()):
            self.drawing = True
            self.strokes.append(QPainterPath(self.image_point(event.position())))
            self.history_changed.emit(True)
            self.update()

    def mouseMoveEvent(self, event):
        if self.drawing:
            self.strokes[-1].lineTo(self.image_point(event.position()))
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.mouseMoveEvent(event)
            self.drawing = False

    def undo(self):
        if self.strokes:
            self.drawing = False
            self.strokes.pop()
            self.history_changed.emit(bool(self.strokes))
            self.update()

    def to_png(self):
        composed = QImage(self.image.size(), QImage.Format.Format_RGB888)
        painter = QPainter(composed)
        self.paint_image(painter, composed.rect())
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        saved = composed.save(buffer, "PNG")
        buffer.close()
        if not saved:
            raise AIError("截图编码失败，请重新截取。")
        return bytes(data)


class ScreenshotQuestion(QDialog):
    def __init__(self, image, config, on_retake, open_ai_settings):
        super().__init__(None, Qt.WindowType.Window)
        self.config = config
        self.closed = False
        self.finished.connect(self._mark_closed)
        self.setWindowTitle(f"EveTech · {TITLE}")
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("在截图上拖动圈画要问的地方"), 1)
        self.undo = QPushButton("撤销圈画")
        self.undo.setEnabled(False)
        toolbar.addWidget(self.undo)
        self.retake = QPushButton("重新截图")

        def retake():
            self.reject()
            on_retake()

        self.retake.clicked.connect(retake)
        toolbar.addWidget(self.retake)
        layout.addLayout(toolbar)

        row = QHBoxLayout()
        self.canvas = AnnotationCanvas(image, self)
        self.canvas.history_changed.connect(self.undo.setEnabled)
        self.undo.clicked.connect(self.canvas.undo)
        row.addWidget(self.canvas, 5)
        details = QVBoxLayout()
        self.question = QPlainTextEdit()
        self.question.setAccessibleName("问题")
        self.question.setPlaceholderText("例如：圈出的这一步为什么成立？")
        self.question.setMaximumHeight(100)
        details.addWidget(self.question)
        self.status = QLabel("确认后仅发送这张截图和你的问题。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.status)
        buttons = QHBoxLayout()
        settings = QPushButton("AI 设置")
        settings.clicked.connect(open_ai_settings)
        self.send = QPushButton("发送截图和问题")
        self.send.setProperty("role", "primary")
        self.send.clicked.connect(self.submit)
        buttons.addWidget(settings)
        buttons.addWidget(self.send, 1)
        details.addLayout(buttons)
        self.answer = QPlainTextEdit()
        self.answer.setAccessibleName("AI 回答")
        self.answer.setPlaceholderText("AI 回答会显示在这里")
        self.answer.setReadOnly(True)
        details.addWidget(self.answer, 1)
        row.addLayout(details, 3)
        layout.addLayout(row, 1)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

    def _mark_closed(self):
        self.closed = True

    def submit(self):
        question = self.question.toPlainText().strip()
        if not question or len(question) > SCREENSHOT_QUESTION_LIMIT:
            self.status.setText("请输入 1 到 2000 个字符的问题。")
            self.question.setFocus()
            return
        config = self.config()
        try:
            validate_config(config)
            url = urlsplit(config.url)
            target = url.netloc
            if url.hostname == "api.deepseek.com" and not config.token:
                raise AIError("请在 AI 设置中填写 DeepSeek API 密钥并保存。")
        except AIError as error:
            self.status.setText(str(error))
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle("确认发送截图")
        confirm.setTextFormat(Qt.TextFormat.PlainText)
        confirm.setText(f"将预览中的截图和问题发送给 {target}（模型 {config.model}）。")
        confirm.setInformativeText("请确认截图中没有你不想分享的内容。")
        send = confirm.addButton("发送", QMessageBox.ButtonRole.AcceptRole)
        cancel = confirm.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        confirm.setDefaultButton(cancel)
        confirm.exec()
        if confirm.clickedButton() != send:
            return

        try:
            png = self.canvas.to_png()
        except AIError as error:
            self.status.setText(str(error))
            return
        self.status.setText("正在等待 AI 回答…")
        self.answer.clear()
        self.send.setEnabled(False)
        self.retake.setEnabled(False)
        self.canvas.setEnabled(False)
        self.undo.setEnabled(False)
        self.question.setReadOnly(True)

        def operation():
            try:
                return True, ask_screenshot(config, question, png)
            except AIError as error:
                return False, str(error)

        run_job(operation, lambda result: self._completed(*result),
                lambda _name: self._completed(False, "请求未完成，请重试。"))

    def _completed(self, success, text):
        if self.closed:
            return
        self.send.setEnabled(True)
        self.retake.setEnabled(True)
        self.canvas.setEnabled(True)
        self.undo.setEnabled(bool(self.canvas.strokes))
        self.question.setReadOnly(False)
        self.status.setText("已收到回答。" if success else text)
        if success:
            self.answer.setPlainText(text)


class ScreenshotTool:
    def __init__(self, config, open_ai_settings, on_error):
        self.config = config
        self.open_ai_settings = open_ai_settings
        self.on_error = on_error
        self.dialog = None
        self.screen = None
        self.pending = False

    def start(self, *, screen=None):
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        if self.pending:
            return
        self.screen = screen or QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        self.pending = True
        # Let the tray context menu disappear before grabbing pixels.
        QTimer.singleShot(180, self.capture)

    def capture(self):
        self.pending = False
        if self.screen not in QApplication.screens():
            self.on_error("找不到要截图的显示器。")
            return
        pixels = self.screen.grabWindow(0)
        if pixels.isNull():
            self.on_error("无法截取此显示器。")
            return
        image = pixels.toImage()
        image.setDevicePixelRatio(1)
        picker = RegionPicker(self.screen, image)
        picker.selected.connect(self.preview)
        self.track(picker)
        picker.showFullScreen()
        picker.raise_()
        picker.activateWindow()

    def preview(self, image):
        screen = self.screen
        dialog = ScreenshotQuestion(image, self.config, lambda: self.start(screen=screen), self.open_ai_settings)
        area = screen.availableGeometry()
        dialog.resize(min(1040, area.width() - 32), min(620, area.height() - 48))
        dialog.move(area.center() - QPoint(dialog.width() // 2, dialog.height() // 2))
        self.track(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.question.setFocus()

    def track(self, dialog):
        self.dialog = dialog

        def forget():
            self.dialog = None
            dialog.deleteLater()

        dialog.finished.connect(forget)
