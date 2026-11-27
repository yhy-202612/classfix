from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

ICONS = {
    "home": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    "aid": '<path d="M13 2 3 14h8l-1 8 11-12h-8z"/>',
    "video": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
    "audio": '<path d="M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 0 1 0 8m2.5-10.5a9 9 0 0 1 0 13"/>',
    "check": '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 5V3m8 2V3M8 13l3 3 5-6"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-7M3 4v6h6m3-3v5l3 2"/>',
    "settings": '<circle cx="12" cy="12" r="4"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2"/>',
    "arrow": '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    "logo": '<path d="M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v11H3zM8 21h8m-4-5v5M6 10h3l2-3 2 6 2-3h3"/>',
}


def icon(name, color="#285c99", size=18):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g></svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(svg.encode()).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def label(text, role="", wrap=True):
    widget = QLabel()
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setText(text)
    widget.setWordWrap(wrap)
    if role:
        widget.setProperty("role", role)
    return widget


def button(text, action, role="", glyph=None):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if role:
        widget.setProperty("role", role)
    if glyph:
        widget.setIcon(icon(glyph, "#ffffff" if role == "primary" else "#477eba"))
    widget.clicked.connect(action)
    return widget


def panel(margins=18, spacing=9):
    widget = QFrame()
    widget.setObjectName("Panel")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    return widget, layout


def horizontal(spacing=10):
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    return layout


def rule():
    widget = QFrame()
    widget.setObjectName("Rule")
    widget.setFixedHeight(1)
    return widget


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()
        if item.layout() is not None:
            clear(item.layout())


class Hero(QWidget):
    def __init__(self, action):
        super().__init__()
        self.setMinimumHeight(187)
        content = QVBoxLayout(self)
        content.setContentsMargins(26, 23, 26, 23)
        content.setSpacing(9)
        title = label("一键课前检查", "title")
        title.setStyleSheet("color: #fff")
        subtitle = label("检查声音、显示连接、网络和 PowerPoint。")
        subtitle.setStyleSheet("color: #d6e8ff")
        content.addWidget(title)
        content.addWidget(subtitle)
        content.addSpacing(4)
        launch = button("开始检查", action, glyph="arrow")
        launch.setStyleSheet("QPushButton {color:#194e9c;background:#fff;border:1px solid #fff;padding:8px 15px;} QPushButton:hover {background:#e4efff;}")
        content.addWidget(launch, alignment=Qt.AlignmentFlag.AlignLeft)
        content.addStretch()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#123d83"))
        gradient.setColorAt(1, QColor("#235fb3"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 12, 12)
        if self.width() < 580:
            return
        x, y = self.width() - 116, self.height() / 2
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#5488cd"), 1))
        for radius in (52, 69, 86):
            painter.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))
        painter.setPen(QPen(QColor("#d0e5ff"), 1.9))
        painter.drawRoundedRect(QRectF(x - 30, y - 22, 60, 39), 5, 5)
        painter.drawLine(int(x), int(y + 17), int(x), int(y + 28))
        painter.drawLine(int(x - 13), int(y + 28), int(x + 13), int(y + 28))
        points = [(-21, -2), (-11, -2), (-5, -11), (3, 7), (9, -2), (21, -2)]
        for first, second in zip(points, points[1:]):
            painter.drawLine(int(x + first[0]), int(y + first[1]), int(x + second[0]), int(y + second[1]))
