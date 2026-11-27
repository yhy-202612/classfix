"""Render the built-in desktop mark into a Windows ICO for PyInstaller."""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evetech.ui.widgets import icon


def write_icon(path):
    app = QApplication([])
    mark = icon("logo", "#ffffff", 156)
    image = QPixmap(256, 256)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#195abc"))
    painter.drawRoundedRect(QRectF(10, 10, 236, 236), 46, 46)
    mark.paint(painter, 48, 48, 160, 160)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Could not render icon")
    prefix = struct.pack("<HHHBBBBHHII", 0, 1, 1, 0, 0, 0, 0, 1, 32, data.size(), 22)
    Path(path).write_bytes(prefix + bytes(data))


if __name__ == "__main__":
    write_icon(sys.argv[1])
