import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QProcess

from classfix.ai import AIConfig
from classfix.desktop_goose import DEEPSEEK_URL, DesktopGoose, deepseek_token
from classfix.monitor import PowerPointWatcher


class DesktopGooseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QCoreApplication.instance() or QApplication([])

    def test_diagnostic_provider_token_is_only_shared_with_official_deepseek(self):
        self.assertEqual(deepseek_token(AIConfig(DEEPSEEK_URL, "model", "example-token")), "example-token")
        self.assertEqual(deepseek_token(AIConfig("https://api.deepseek.com/v1/chat/completions", "m", "t")), "t")
        for url in (
            "https://example.com/chat/completions",
            "https://api.deepseek.com.example.com/chat/completions",
            "https://api.deepseek.com@example.com/chat/completions",
            "http://api.deepseek.com/chat/completions",
            "https://api.deepseek.com/chat/completions?redirect=1",
        ):
            with self.subTest(url=url):
                self.assertEqual(deepseek_token(AIConfig(url, "model", "different-provider-key")), "")

    def test_keyboard_can_run_with_goose_disabled_and_gets_slideshow_updates(self):
        config = AIConfig(DEEPSEEK_URL, "deepseek-flash", "example-token")
        controller = DesktopGoose({"touch_keyboard_enabled": "1"}, lambda: config, executable=Path("unused"))
        self.addCleanup(controller.deleteLater)
        process = Mock()
        process.state.return_value = QProcess.ProcessState.Running
        controller.process = process
        controller._send = Mock()

        self.assertTrue(controller.active())
        controller._send_configuration()
        controller.set_slideshow(True)
        controller.set_slideshow(True)
        controller.set_paused(True)
        packets = [call.args[0] for call in controller._send.call_args_list]
        self.assertEqual(len(packets), 3)
        self.assertTrue(all(packet["Keyboard"] and not packet["Goose"] for packet in packets))
        self.assertEqual([packet["Slideshow"] for packet in packets], [False, True, True])
        self.assertTrue(packets[-1]["Paused"])

    def test_slideshow_signal_does_not_need_a_video_or_window_handle(self):
        watcher = PowerPointWatcher(lambda: "")
        self.addCleanup(watcher.shutdown)
        seen = []
        watcher.slideshow_changed.connect(seen.append)
        source = SimpleNamespace(SlideShowWindows=SimpleNamespace(Count=1))
        self.assertEqual(watcher._inspect_slide_shows(source), 0)
        self.assertEqual(watcher._inspect_slide_shows(source), 0)
        source.SlideShowWindows.Count = 0
        self.assertEqual(watcher._inspect_slide_shows(source), 0)
        self.assertEqual(seen, [True, False])
