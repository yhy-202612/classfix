"""Select and annotate a screen region, then ask a configured vision model."""

import math
from urllib.parse import urlsplit

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
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

    def __init__(self, screen, image, english=False):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | TOP_WINDOW)
        self.setWindowTitle(TITLE)
        self.winId()
        self.windowHandle().setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.image = image
        self.origin = None
        self.region = QRect()
        self.move_from = QRect()
        self.english = english
        self.instructions = QLabel("Drag to select a region" if english else "拖动框选截图区域", self)
        self.confirm = QPushButton("Use selection" if english else "使用此区域", self)
        self.confirm.setProperty("role", "primary")
        self.confirm.setEnabled(False)
        self.confirm.setAutoDefault(False)
        self.confirm.clicked.connect(self.use_selection)
        QShortcut(QKeySequence("Return"), self, activated=self.use_selection)
        cancel = QPushButton("Cancel" if english else "取消", self)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        cancel.setMinimumHeight(40)
        self.confirm.setMinimumHeight(40)
        toolbar = QWidget(self)
        toolbar.setObjectName("CaptureToolbar")
        toolbar.setStyleSheet(
            "QWidget#CaptureToolbar { background: #172433; border: 1px solid #43566d; border-radius: 14px; }"
            "QWidget#CaptureToolbar QLabel { color: #f3f7fc; background: transparent; }"
        )
        header = QHBoxLayout(toolbar)
        header.setContentsMargins(16, 8, 8, 8)
        header.addWidget(self.instructions)
        header.addSpacing(12)
        header.addWidget(cancel)
        header.addWidget(self.confirm)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.addWidget(toolbar, 0, Qt.AlignmentFlag.AlignHCenter)
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
            painter.setPen(QPen(QColor("#8accff"), 2))
            painter.drawRect(self.region.adjusted(0, 0, -1, -1))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#d9f1ff"))
            for corner in (self.region.topLeft(), self.region.topRight(),
                           self.region.bottomLeft(), self.region.bottomRight()):
                painter.drawEllipse(corner, 3, 3)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.move_from = QRect(self.region) if self.region.contains(self.origin) else QRect()
            if not self.region.isEmpty():
                corners = (self.region.topLeft(), self.region.topRight(),
                           self.region.bottomLeft(), self.region.bottomRight())
                for index, corner in enumerate(corners):
                    if (self.origin - corner).manhattanLength() <= 9:
                        self.origin = corners[3 - index]
                        self.move_from = QRect()
                        break
            if self.move_from.isEmpty():
                self.region = QRect()
            self.confirm.setEnabled(False)
            self.update()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            end = event.position().toPoint()
            if self.move_from.isEmpty():
                top_left = QPoint(min(self.origin.x(), end.x()), min(self.origin.y(), end.y()))
                bottom_right = QPoint(max(self.origin.x(), end.x()), max(self.origin.y(), end.y()))
                self.region = QRect(top_left, bottom_right).intersected(self.rect())
            else:
                self.region = self.move_from.translated(end - self.origin)
                self.region.moveTopLeft(QPoint(
                    max(0, min(self.region.left(), self.width() - self.region.width())),
                    max(0, min(self.region.top(), self.height() - self.region.height())),
                ))
            self.update()

    def mouseReleaseEvent(self, event):
        if self.origin is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.mouseMoveEvent(event)
        self.origin = None
        valid = self.region.width() >= 4 and self.region.height() >= 4
        self.confirm.setEnabled(valid)
        if not valid:
            self.region = QRect()
        self.instructions.setText(
            ("Move or redraw the selection" if self.english else "可移动或重新框选")
            if valid else ("Drag to select a region" if self.english else "拖动框选截图区域")
        )
        self.update()

    def use_selection(self):
        if not self.confirm.isEnabled():
            return
        image = crop_image(self.image, self.region, self.size())
        self.accept()
        self.selected.emit(image)


class AnnotationCanvas(QWidget):
    history_changed = Signal(bool)

    def __init__(self, image, parent, english=False):
        super().__init__(parent)
        if max(image.width(), image.height()) > MAX_IMAGE_EDGE:
            image = image.scaled(MAX_IMAGE_EDGE, MAX_IMAGE_EDGE, ASPECT, SMOOTH)
        self.image = image.convertToFormat(QImage.Format.Format_RGB888)
        self.strokes = []
        self.drawing = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(220, 160)
        self.setAccessibleName("Screenshot annotation" if english else "截图标注区")
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

    def clear(self):
        self.drawing = False
        self.strokes.clear()
        self.history_changed.emit(False)
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
    def __init__(self, image, config, on_retake, open_ai_settings, english=False):
        super().__init__(None, Qt.WindowType.Window)
        self.config = config
        self.english = english
        self.closed = False
        self.finished.connect(self._mark_closed)
        tr = self._text
        self.setWindowTitle("EveTech · " + tr(TITLE, "Circle Screen"))
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(tr("圈出要提问的地方", "Mark the part you want to ask about")), 1)
        self.undo = QPushButton(tr("撤销", "Undo"))
        self.undo.setEnabled(False)
        toolbar.addWidget(self.undo)
        self.clear = QPushButton(tr("清空圈画", "Clear marks"))
        self.clear.setEnabled(False)
        toolbar.addWidget(self.clear)
        self.retake = QPushButton(tr("重新截图", "Retake"))

        def retake():
            self.reject()
            on_retake()

        self.retake.clicked.connect(retake)
        toolbar.addWidget(self.retake)
        layout.addLayout(toolbar)

        row = QHBoxLayout()
        self.canvas = AnnotationCanvas(image, self, english)
        self.canvas.history_changed.connect(self.undo.setEnabled)
        self.canvas.history_changed.connect(self.clear.setEnabled)
        self.undo.clicked.connect(self.canvas.undo)
        self.clear.clicked.connect(self.canvas.clear)
        QShortcut(QKeySequence.StandardKey.Undo, self.canvas, activated=self.canvas.undo)
        row.addWidget(self.canvas, 5)
        details = QVBoxLayout()
        self.question = QPlainTextEdit()
        self.question.setAccessibleName(tr("问题", "Question"))
        self.question.setPlaceholderText(tr("圈出的这一步为什么成立？", "Why does the circled step work?"))
        self.question.setMaximumHeight(100)
        details.addWidget(self.question)
        self.status = QLabel(tr("确认后仅发送这张截图和你的问题。", "Review the screenshot and your question before sending."))
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.status)
        buttons = QHBoxLayout()
        settings = QPushButton(tr("AI 设置", "AI settings"))
        settings.clicked.connect(open_ai_settings)
        self.send = QPushButton(tr("发送截图和问题", "Send screenshot"))
        self.send.setProperty("role", "primary")
        self.send.clicked.connect(self.submit)
        buttons.addWidget(settings)
        buttons.addWidget(self.send, 1)
        details.addLayout(buttons)
        self.answer = QPlainTextEdit()
        self.answer.setAccessibleName(tr("AI 回答", "AI answer"))
        self.answer.setPlaceholderText(tr("AI 回答会显示在这里", "The answer will appear here."))
        self.answer.setReadOnly(True)
        details.addWidget(self.answer, 1)
        row.addLayout(details, 3)
        layout.addLayout(row, 1)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

    def _mark_closed(self):
        self.closed = True

    def _text(self, chinese, english):
        return english if self.english else chinese

    def submit(self):
        tr = self._text
        question = self.question.toPlainText().strip()
        if not question or len(question) > SCREENSHOT_QUESTION_LIMIT:
            self.status.setText(tr("请输入 1 到 2000 个字符的问题。", "Enter a question of 1–2,000 characters."))
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
            self.status.setText(tr(str(error), "Check your model, endpoint and API key in AI settings."))
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle(tr("确认发送截图", "Confirm screenshot sharing"))
        confirm.setTextFormat(Qt.TextFormat.PlainText)
        confirm.setText(tr(f"将预览中的截图和问题发送给 {target}（模型 {config.model}）。",
                           f"Send the previewed screenshot and question to {target} ({config.model})?"))
        confirm.setInformativeText(tr("请确认截图中没有你不想分享的内容。",
                                      "Check the preview for anything you want to keep private."))
        send = confirm.addButton(tr("发送", "Send"), QMessageBox.ButtonRole.AcceptRole)
        cancel = confirm.addButton(tr("取消", "Cancel"), QMessageBox.ButtonRole.RejectRole)
        confirm.setDefaultButton(cancel)
        confirm.exec()
        if confirm.clickedButton() != send:
            return

        try:
            png = self.canvas.to_png()
        except AIError as error:
            self.status.setText(tr(str(error), "Could not encode the image. Retake the screenshot."))
            return
        self.status.setText(tr("正在等待 AI 回答…", "Waiting for an answer…"))
        self.answer.clear()
        self.send.setEnabled(False)
        self.retake.setEnabled(False)
        self.canvas.setEnabled(False)
        self.undo.setEnabled(False)
        self.clear.setEnabled(False)
        self.question.setReadOnly(True)

        def operation():
            try:
                return True, ask_screenshot(config, question, png)
            except AIError as error:
                return False, tr(str(error), "The AI request failed. Check your settings or try again.")

        run_job(operation, lambda result: self._completed(*result),
                lambda _name: self._completed(False, tr("请求未完成，请重试。", "The request did not complete. Try again.")))

    def _completed(self, success, text):
        if self.closed:
            return
        self.send.setEnabled(True)
        self.retake.setEnabled(True)
        self.canvas.setEnabled(True)
        self.undo.setEnabled(bool(self.canvas.strokes))
        self.clear.setEnabled(bool(self.canvas.strokes))
        self.question.setReadOnly(False)
        self.status.setText(self._text("已收到回答。", "Answer received.") if success else text)
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

    def start(self, *, screen=None, english=False):
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        if self.pending:
            return
        self.screen = screen or QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        self.english = english
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
        picker = RegionPicker(self.screen, image, self.english)
        picker.selected.connect(self.preview)
        self.track(picker)
        picker.showFullScreen()
        picker.raise_()
        picker.activateWindow()

    def preview(self, image):
        screen = self.screen
        english = self.english
        dialog = ScreenshotQuestion(image, self.config, lambda: self.start(screen=screen, english=english),
                                    self.open_ai_settings, english)
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
