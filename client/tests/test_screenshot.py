import base64
import json
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from evetech.ai import AIConfig, AIError, ask_screenshot
from evetech.ui.screenshot import RegionPicker, ScreenshotQuestion, crop_image


class VisionEndpoint(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()
        response = {"choices": [{"finish_reason": self.server.finish, "message": {"content": "The marking is red."}}]}
        self.wfile.write(json.dumps(response).encode("utf-8"))


def image(width, height, color):
    result = QImage(width, height, QImage.Format.Format_RGB888)
    result.fill(QColor(color))
    return result


class ScreenshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_scaled_crop_can_select_the_bottom_right_pixel(self):
        source = image(400, 250, "white")
        source.setPixelColor(399, 249, QColor("blue"))
        region = crop_image(source, QRect(100, 75, 100, 50), QSize(200, 125))
        self.assertEqual(region.size(), QSize(200, 100))
        self.assertEqual(region.pixelColor(199, 99), QColor("blue"))

    def test_drag_annotation_confirmation_and_image_request(self):
        screen = self.app.primaryScreen()
        source = image(screen.size().width(), screen.size().height(), "white")
        source.setPixelColor(2, 2, QColor("blue"))
        picker = RegionPicker(screen, source)
        crops = []
        picker.selected.connect(crops.append)
        picker.show()
        QTest.mousePress(picker, Qt.MouseButton.LeftButton, pos=QPoint(170, 160))
        QTest.mouseMove(picker, QPoint(70, 60), 5)
        QTest.mouseRelease(picker, Qt.MouseButton.LeftButton, pos=QPoint(70, 60))
        self.assertEqual(len(crops), 1)
        self.assertEqual(crops[0].size(), QSize(101, 101))

        with ThreadingHTTPServer(("127.0.0.1", 0), VisionEndpoint) as server:
            server.requests = []
            server.finish = "stop"
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = AIConfig(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "vision")
                dialog = ScreenshotQuestion(crops[0], lambda: config, lambda: None, lambda: None)
                self.addCleanup(dialog.close)
                dialog.resize(750, 450)
                dialog.show()
                canvas = dialog.canvas
                rect = canvas.image_rect()
                middle = rect.center()
                start = middle - QPoint(rect.width() // 4, 0)
                end = middle + QPoint(rect.width() // 4, 0)
                for _ in range(2):
                    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
                    QTest.mouseMove(canvas, end, 5)
                    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
                    preview = QImage.fromData(canvas.to_png(), "PNG")
                    self.assertLess(preview.pixelColor(50, 50).green(), 100)
                    if _ == 0:
                        dialog.undo.click()
                        self.assertEqual(QImage.fromData(canvas.to_png()).pixelColor(50, 50), QColor("white"))

                dialog.question.setPlainText("What is circled?")
                self.confirm_request(dialog, QMessageBox.ButtonRole.RejectRole)
                self.assertEqual(server.requests, [])
                self.confirm_request(dialog, QMessageBox.ButtonRole.AcceptRole)
                for _ in range(100):
                    if dialog.answer.toPlainText():
                        break
                    QTest.qWait(20)
                self.assertEqual(dialog.answer.toPlainText(), "The marking is red.")
                request, = server.requests
                self.assertEqual(request["model"], "vision")
                contents = request["messages"][1]["content"]
                self.assertEqual(contents[0], {"type": "text", "text": "What is circled?"})
                png = base64.b64decode(contents[1]["image_url"]["url"].split(",", 1)[1])
                self.assertEqual(png, canvas.to_png())
                self.assertEqual(QImage.fromData(png).size(), QSize(101, 101))
                server.finish = "content_filter"
                with self.assertRaisesRegex(AIError, "未能回答"):
                    ask_screenshot(config, "What is circled?", png)
            finally:
                server.shutdown()
                thread.join(timeout=2)

    def confirm_request(self, dialog, role):
        def click():
            prompt = self.app.activeModalWidget()
            self.assertIsInstance(prompt, QMessageBox)
            self.assertIn("127.0.0.1:", prompt.text())
            next(button for button in prompt.buttons() if prompt.buttonRole(button) == role).click()

        QTimer.singleShot(0, click)
        dialog.send.click()
