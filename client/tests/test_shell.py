import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from classfix.app import create_window
from classfix.storage import Store
from classfix.ui.theme import apply_theme
from classfix.ui.window import NAV
from test_pptx import make_deck


class ShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        self.enterContext(patch("classfix.app.cloud.load_cache", return_value={
            "fetched": time.time(), "payload": {},
        }))

    def test_navigation_uses_real_widgets(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        window = create_window(Path(directory.name))
        window.show()
        self.app.processEvents()
        for key, title in NAV:
            with self.subTest(route=key):
                window.nav_buttons[key].click()
                self.assertEqual(window.location.text(), title)
                self.assertIs(window.stack.currentWidget(), window.pages[key][1])
        window.close()

    def click(self, parent, text):
        self.app.processEvents()
        buttons = [widget for widget in parent.findChildren(QPushButton)
                   if widget.isVisible() and widget.text() == text]
        self.assertEqual(len(buttons), 1, text)
        buttons[0].click()

    def wait_for(self, predicate):
        event_loop = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(lambda: event_loop.quit() if predicate() else None)
        poll.start(20)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(event_loop.quit)
        deadline.start(8000)
        event_loop.exec()
        self.assertTrue(predicate(), "UI worker did not return within eight seconds")

    def test_video_wizard_retest_and_history_survive_reopen(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            deck = base / "lesson.pptx"
            make_deck(deck)
            window = create_window(base)
            window.show()
            window.nav_buttons["aid"].click()
            aid = window.pages["aid"][0]
            self.app.processEvents()
            entries = [widget for widget in aid.findChildren(QPushButton)
                       if widget.isVisible() and widget.text() == "进入"]
            entries[1].click()
            video = window.pages["video"][0]
            self.assertEqual(video.case, "format")
            video.set_file("deck_path", deck)
            self.click(video, "检查文件并继续")
            self.wait_for(lambda: video.stage == 2)
            # stage 变化后仍需一轮事件循环才会真正显示控件，直接取可见项会抢跑。
            self.wait_for(lambda: any(widget.isVisible() for widget in video.findChildren(QComboBox)))
            combo = next(widget for widget in video.findChildren(QComboBox) if widget.isVisible())
            combo.setCurrentIndex(2)
            self.click(video, "继续")
            self.click(video, "能正常播放")
            self.click(video, "上一步")
            self.click(video, "能正常播放  ✓")
            self.click(video, "找不到文件或链接")
            self.wait_for(lambda: video.stage == 4)
            self.assertEqual(video.latest.findings[0].title, "视频引用失效")
            self.click(video, "仍有故障")
            (base / "clip with space.mp4").write_bytes(b"restored link")
            self.click(video, "重新检测")
            self.wait_for(lambda: not video.busy)
            self.assertEqual(video.initial.facts["media_status"], "missing")
            self.assertEqual(video.latest.facts["media_status"], "present")
            self.assertTrue(any(f.title == "先恢复课堂播放" for f in video.latest.findings))
            self.click(video, "已恢复")
            window.close()
            reopened = create_window(base)
            reopened.show()
            reopened.nav_buttons["history"].click()
            self.assertIn("已恢复", reopened.pages["history"][0].list.item(0).text())
            item, = Store(base).records()
            self.assertEqual((item.latest.revision, item.recovery), (1, "restored"))
            reopened.close()

    def test_home_one_click_diagnostics_updates_overview(self):
        with tempfile.TemporaryDirectory() as temporary:
            window = create_window(Path(temporary))
            window.show()
            home = window.pages["home"][0]
            self.click(home, "开始检查")
            page = window.pages["check"][0]
            self.wait_for(lambda: page.latest is not None or page.error)
            self.assertFalse(page.error, page.error)
            self.assertEqual(page.latest.kind, "check")
            window.nav_buttons["home"].click()
            self.assertIn("巡检结果", home.health.text())
            window.close()

    def test_check_row_folds_extra_details_until_expanded(self):
        """「不存在」的音频输出收在 extra 里，只有点了「查看全部」才显示。"""
        from classfix.model import Check
        from classfix.ui.report import check_row
        from PySide6.QtWidgets import QLabel

        check = Check("audio_devices", "音频输出设备", "ok", "2 个可用，共 3 个已知输出。", "• 旧设备 · 不存在")
        picked = []
        folded = check_row(check, False, picked.append)
        self.assertIn("2 个可用，共 3 个已知输出。", [item.text() for item in folded.findChildren(QLabel)])
        self.assertNotIn("• 旧设备 · 不存在", [item.text() for item in folded.findChildren(QLabel)])
        links = [item for item in folded.findChildren(QPushButton) if "查看全部" in item.text()]
        self.assertEqual(len(links), 1)
        links[0].click()
        self.assertEqual(picked, ["audio_devices"])
        shown = check_row(check, True, picked.append)
        self.assertIn("• 旧设备 · 不存在", [item.text() for item in shown.findChildren(QLabel)])
        self.assertTrue([item for item in shown.findChildren(QPushButton) if "收起" in item.text()])
        static = check_row(check)
        self.assertIn("• 旧设备 · 不存在", [item.text() for item in static.findChildren(QLabel)])

    def test_account_bar_is_mounted_in_top_bar(self):
        """右侧账号栏：未登录时就在顶部栏里，并显示登录入口。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        window = create_window(Path(directory.name))
        self.addCleanup(window.close)
        bar = getattr(window, "account_bar", None)
        self.assertIsNotNone(bar, "顶部栏没有挂载账号栏")
        self.assertTrue(bar.isVisibleTo(window))
        self.assertEqual(bar.primary.text(), "未登录")
        self.assertEqual(bar.action.text(), "登录")

    def test_account_bar_turns_into_account_entry_after_sign_in(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        window = create_window(Path(directory.name))
        self.addCleanup(window.close)
        bar = window.account_bar
        bar.account.token = "t"
        bar.account.phone = "13800001234"
        bar.account.points = 18
        bar.refresh()
        self.assertEqual(bar.primary.text(), "138****1234")
        self.assertIn("18 积分", bar.secondary.text())
        self.assertEqual(bar.action.text(), "账号")
