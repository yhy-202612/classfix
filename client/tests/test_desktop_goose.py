import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QProcess

from evetech.ai import AIConfig
from evetech.app import create_window, wire_desktop_goose_tray
from evetech.desktop_goose import DEEPSEEK_URL, DesktopGoose, deepseek_token, resolve_goose_folder
from evetech.monitor import PowerPointWatcher
from evetech.storage import Store
from evetech.ui.tray import TrayIcon


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
        self.assertFalse(controller.ask())
        controller._send_configuration()
        controller.set_slideshow(True)
        controller.set_slideshow(True)
        controller.set_paused(True)
        packets = [call.args[0] for call in controller._send.call_args_list]
        self.assertEqual(len(packets), 3)
        self.assertTrue(all(packet["Keyboard"] and not packet["Goose"] for packet in packets))
        self.assertTrue(all(not packet["Token"] for packet in packets))
        self.assertEqual([packet["Slideshow"] for packet in packets], [False, True, True])
        self.assertTrue(packets[-1]["Paused"])

    def test_goose_folder_resolves_both_levels_of_the_official_archive(self):
        with TemporaryDirectory() as directory:
            outer = Path(directory) / "Desktop Goose v0.31"
            inner = outer / "DesktopGoose v0.31"
            with patch("evetech.desktop_goose.original_goose_available", side_effect=lambda path: path == inner):
                for selected in (directory, outer, inner):
                    self.assertEqual(resolve_goose_folder(selected), str(inner))
                self.assertEqual(resolve_goose_folder(outer / "Assets"), "")

    def test_preview_command_is_sent_only_when_enabled(self):
        settings = {}
        controller = DesktopGoose(settings, lambda: AIConfig("", ""), executable=Path("unused"))
        self.addCleanup(controller.deleteLater)
        controller._send = Mock()
        controller._ready = True
        controller.preview()
        controller._send.assert_not_called()
        settings["desktop_goose_enabled"] = "1"
        controller.preview()
        controller._send.assert_called_once_with({"Action": "preview"})

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

    def test_debug_switch_is_forwarded_without_starting_a_disabled_pet(self):
        settings = {}
        controller = DesktopGoose(settings, lambda: AIConfig("", ""), executable=Path("unused"))
        self.addCleanup(controller.deleteLater)
        self.assertFalse(controller._configuration()["DebugAlwaysShow"])
        settings["desktop_goose_debug_always_show"] = "1"
        self.assertTrue(controller._configuration()["DebugAlwaysShow"])
        self.assertFalse(controller.active())
        settings["desktop_goose_enabled"] = "1"
        self.assertTrue(controller.active())
        self.assertEqual(controller._configuration()["Token"], "")

    def test_tray_switch_persists_and_syncs_with_desktop_pet_settings(self):
        with TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.update_settings({"monitor_enabled": "0", "touch_keyboard_enabled": "1"})
            with (
                patch("evetech.app.cloud.load_cache", return_value={"payload": {}}),
                patch("evetech.app.cloud.cache_fresh", return_value=True),
                patch("evetech.ui.preferences.startup_enabled", return_value=False),
            ):
                window = create_window(directory)
            self.addCleanup(window.deleteLater)
            self.addCleanup(window.watcher.shutdown)
            preferences = window.pages["settings"][0]
            goose = DesktopGoose(window.settings_values, preferences.config, window, executable=Path("unused"))
            window.desktop_goose = goose
            open_settings = Mock()
            window.tray = TrayIcon(window, window.watcher, Mock(), goose, open_settings)
            wire_desktop_goose_tray(window, preferences, goose, open_settings)
            action = window.tray.goose_enabled_action
            self.assertIn(action, window.tray.contextMenu().actions())
            self.assertFalse(action.isChecked())

            action.trigger()
            open_settings.assert_called_once()
            self.assertFalse(action.isChecked())
            self.assertNotIn("desktop_goose_enabled", store.settings())

            with patch("evetech.app.original_goose_available", return_value=True), patch.object(goose, "refresh") as refresh:
                action.trigger()
                self.assertTrue(action.isChecked())
                self.assertTrue(preferences.goose_enabled.isChecked())
                self.assertEqual(store.settings()["desktop_goose_enabled"], "1")
                refresh.assert_called_once()

                action.trigger()
                self.assertFalse(action.isChecked())
                self.assertFalse(preferences.goose_enabled.isChecked())
                self.assertEqual(store.settings()["desktop_goose_enabled"], "0")
                self.assertEqual(store.settings()["touch_keyboard_enabled"], "1")
                self.assertEqual(refresh.call_count, 2)

                preferences.goose_enabled.setChecked(True)
                with (
                    patch("evetech.ui.preferences.original_goose_available", return_value=True),
                    patch("evetech.ui.preferences.set_startup", return_value=True),
                ):
                    preferences.save_goose()
                self.assertTrue(action.isChecked())
                self.assertEqual(store.settings()["desktop_goose_enabled"], "1")

                with (
                    patch.object(window, "save_settings", return_value=False),
                    patch.object(window.tray, "showMessage") as message,
                ):
                    action.trigger()
                    self.assertTrue(action.isChecked())
                    self.assertTrue(goose.goose_enabled())
                    message.assert_called_once()
