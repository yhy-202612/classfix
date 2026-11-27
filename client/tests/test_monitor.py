import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QLockFile
from PySide6.QtWidgets import QApplication

from classfix.app import wire_monitor_popups
from classfix.instance import SingleInstance
from classfix.monitor import MediaProblem, PowerPointWatcher, inspect_presentation
from classfix.pptx import Probe
from classfix.startup import set_startup
from PySide6.QtTest import QTest

from classfix.ui.overlay import FormatPopup, MediaAlerts, VolumePopup
from classfix.ui.popup_stack import PopupStack
from test_pptx import make_deck


class Collection:
    def __init__(self, *items):
        self.items = items
        self.Count = len(items)

    def Item(self, index):
        return self.items[index - 1]


class Value:
    def __init__(self, **values):
        self.__dict__.update(values)


class View:
    def __init__(self, slide, state):
        self.Slide = slide
        self.state = state

    def Player(self, _shape_id):
        return Value(State=self.state)


class UnavailablePlayerView(View):
    def Player(self, _shape_id):
        raise RuntimeError("player is unavailable before the video is clicked")


class MonitorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def watcher_for_state(self, player_state):
        presentation = Value(FullName="C:/lesson.pptx")
        shape = Value(Type=16, MediaType=3, Id=42)
        slide = Value(SlideIndex=7, Parent=presentation, Shapes=Collection(shape))
        window = Value(View=View(slide, player_state), HWND=0)
        powerpoint = Value(SlideShowWindows=Collection(window))
        watcher = PowerPointWatcher(lambda: "", interval=60000)
        self.addCleanup(watcher.shutdown)
        return watcher, powerpoint

    def test_playing_video_with_low_volume_requests_overlay(self):
        watcher, powerpoint = self.watcher_for_state(0)
        received = []
        watcher.low_volume.connect(lambda *items: received.append(items))
        with patch("classfix.monitor.read_system_volume", return_value=(12, False)):
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(received, [(7, 12, False, 0)])

    def test_entering_video_slide_while_muted_requests_overlay_once(self):
        watcher, powerpoint = self.watcher_for_state(3)
        received = []
        watcher.low_volume.connect(lambda *items: received.append(items))
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(received, [(7, 0, True, 0)])

    def test_video_shape_still_triggers_when_powerpoint_player_is_unavailable(self):
        watcher, powerpoint = self.watcher_for_state(3)
        slide = powerpoint.SlideShowWindows.Item(1).View.Slide
        powerpoint.SlideShowWindows.Item(1).View = UnavailablePlayerView(slide, 3)
        received = []
        watcher.low_volume.connect(lambda *items: received.append(items))
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(received, [(7, 0, True, 0)])

    def test_scanned_video_page_is_fallback_when_slideshow_exposes_no_media_shape(self):
        watcher, powerpoint = self.watcher_for_state(3)
        slide = powerpoint.SlideShowWindows.Item(1).View.Slide
        slide.Shapes = Collection()
        watcher._video_slides["c:/lesson.pptx"] = {7}
        received = []
        watcher.low_volume.connect(lambda *items: received.append(items))
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(received, [(7, 0, True, 0)])

    def test_same_page_keeps_volume_popup_when_live_video_shape_becomes_available(self):
        watcher, powerpoint = self.watcher_for_state(0)
        slide = powerpoint.SlideShowWindows.Item(1).View.Slide
        shapes, slide.Shapes = slide.Shapes, Collection()
        watcher._video_slides["c:/lesson.pptx"] = {7}
        showing, hides = [], []
        watcher.low_volume.connect(lambda *items: showing.append(items))
        watcher.playback_ok.connect(lambda: hides.append(1))
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)
            slide.Shapes = shapes
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(showing, [(7, 0, True, 0)])
        self.assertEqual(hides, [])

    def test_media_warning_stacks_without_closing_the_volume_control(self):
        watcher, powerpoint = self.watcher_for_state(0)
        stack = PopupStack(self.app)
        self.addCleanup(stack.deleteLater)
        alerts = MediaAlerts(self.app, stack)
        volume = VolumePopup(self.app, stack)
        self.addCleanup(volume.close)
        wire_monitor_popups(watcher, alerts, volume)
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)
        watcher._scan_finished("c:/lesson.pptx", (1, 1), (
            [MediaProblem(8, "外部视频链接失效", "找不到原始视频")], {7, 8}, {}))
        warning = next(popup for popup in stack.placements if isinstance(popup, FormatPopup))
        self.addCleanup(warning.close)
        QTest.qWait(680)
        self.assertTrue(volume.isVisible())
        self.assertTrue(warning.isVisible())
        self.assertLess(volume.geometry().bottom(), warning.y())
        self.assertIn("外部视频链接失效", warning.title.text())
        watcher.playback_ok.emit()
        self.assertTrue(volume.isVisible())
        volume.dismiss()
        self.assertFalse(volume.isVisible())
        self.assertTrue(warning.isVisible())

    def test_native_error_keeps_literal_text_without_inventing_a_slide(self):
        watcher, _ = self.watcher_for_state(3)
        received = []
        watcher.media_problem.connect(lambda problem, _location: received.append(problem))
        with patch("classfix.monitor.read_powerpoint_media_errors", return_value=("无法播放媒体。",)):
            watcher._report_powerpoint_errors(0)
            watcher._report_powerpoint_errors(0)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].slide, 0)
        self.assertEqual(received[0].powerpoint_error, "无法播放媒体。")

    def test_non_video_page_of_same_deck_never_triggers(self):
        watcher, powerpoint = self.watcher_for_state(3)
        powerpoint.SlideShowWindows.Item(1).View.Slide.Shapes = Collection()
        watcher._video_slides["c:/lesson.pptx"] = {9}  # 视频在第 9 页，当前停在第 7 页
        received = []
        watcher.low_volume.connect(lambda *items: received.append(items))
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(received, [])

    def test_video_page_of_another_deck_never_triggers(self):
        watcher, powerpoint = self.watcher_for_state(3)
        powerpoint.SlideShowWindows.Item(1).View.Slide.Shapes = Collection()
        watcher._video_slides["d:/another-lesson.pptx"] = {3}  # 另一个课件有视频，但没在放映
        received = []
        watcher.low_volume.connect(lambda *items: received.append(items))
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(received, [])

    def test_low_volume_overlay_hides_when_moving_to_normal_volume(self):
        watcher, powerpoint = self.watcher_for_state(3)
        received, hides = [], []
        watcher.low_volume.connect(lambda *items: received.append(items))
        watcher.playback_ok.connect(lambda: hides.append(1))
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(len(received), 1)
        slide = powerpoint.SlideShowWindows.Item(1).View.Slide
        slide.Shapes = Collection()
        slide.SlideIndex = 8
        with patch("classfix.monitor.read_system_volume", return_value=(60, False)):
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(len(hides), 1)

    def test_low_volume_overlay_hides_when_next_video_page_has_normal_volume(self):
        # 换到另一张「也有视频」的页面时不能把 _low_volume_active 直接清零，
        # 否则音量已正常也不会隐藏上一页的提示。
        watcher, powerpoint = self.watcher_for_state(3)
        received, hides = [], []
        watcher.low_volume.connect(lambda *items: received.append(items))
        watcher.playback_ok.connect(lambda: hides.append(1))
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(len(received), 1)
        powerpoint.SlideShowWindows.Item(1).View.Slide.SlideIndex = 8
        with patch("classfix.monitor.read_system_volume", return_value=(60, False)):
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(len(hides), 1)
        self.assertEqual(len(received), 1)

    def test_same_video_page_warns_only_once_while_slideshow_runs(self):
        watcher, powerpoint = self.watcher_for_state(3)
        received = []
        watcher.low_volume.connect(lambda *items: received.append(items))
        slide = powerpoint.SlideShowWindows.Item(1).View.Slide
        shapes = slide.Shapes
        with patch("classfix.monitor.read_system_volume", return_value=(0, True)):
            watcher._inspect_slide_shows(powerpoint)   # 第 7 页：提示一次
            watcher._inspect_slide_shows(powerpoint)   # 同页重复轮询：不再提示
            slide.Shapes = Collection()
            slide.SlideIndex = 8                       # 翻到无视频页
            watcher._inspect_slide_shows(powerpoint)
            slide.Shapes = shapes
            slide.SlideIndex = 7                       # 回到第 7 页，仍不应重复提示
            watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(received, [(7, 0, True, 0)])

    def test_not_ready_player_reports_exact_slide(self):
        watcher, powerpoint = self.watcher_for_state(1)
        received = []
        watcher.media_problem.connect(lambda problem, _location: received.append(problem))
        watcher._inspect_slide_shows(powerpoint)
        powerpoint.SlideShowWindows.Item(1).View.state = 3
        watcher._inspect_slide_shows(powerpoint)
        self.assertEqual(received[0].slide, 7)
        self.assertEqual(received[0].category_title, "PowerPoint 播放状态异常")
        self.assertEqual(received[0].powerpoint_error, "")

    def test_saved_deck_deduplicates_problems_by_slide(self):
        with tempfile.TemporaryDirectory() as temporary:
            deck = Path(temporary) / "lesson.pptx"
            make_deck(deck)
            with patch("classfix.monitor.find_ffprobe", return_value="ffprobe.exe"), \
                 patch("classfix.monitor.probe_asset", return_value=Probe("ok", "", "avi", "", ("mpeg4",), ("mp3",), ("yuv420p",))):
                problems = inspect_presentation(deck)
            self.assertEqual([(item.slide, item.category_title) for item in problems],
                             [(1, "视频兼容性风险"), (2, "外部视频链接失效")])
            self.assertIn("mpeg4", problems[0].detail)
            self.assertTrue(all(not item.powerpoint_error for item in problems))

    def test_corner_popups_use_required_text_and_volume_control(self):
        stack = PopupStack(self.app)
        self.addCleanup(stack.deleteLater)
        format_popup = FormatPopup(self.app, stack)
        self.addCleanup(format_popup.close)
        format_popup.notify_problem(MediaProblem(5, "不支持的视频格式！", "codec"))
        self.assertEqual(format_popup.title.text(), "不支持的视频格式！ 第5页")
        self.assertEqual(format_popup.windowOpacity(), 1.0)
        QTest.qWait(230)
        available = format_popup.screen().availableGeometry()
        margin = stack.lanes[format_popup.screen()].MARGIN
        self.assertEqual(format_popup.geometry().right(), available.right() - margin)
        self.assertEqual(format_popup.geometry().bottom(), available.bottom() - margin)
        self.assertTrue(format_popup.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(format_popup.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        background = format_popup.grab().toImage().pixelColor(8, 8)
        self.assertGreater(background.alpha(), 220)
        self.assertLess(background.red(), 30)
        self.assertLess(background.green(), 30)
        self.assertLess(background.blue(), 30)

    def test_per_user_startup_shortcut_can_be_added_and_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / "ClassFix.lnk"
            with patch("classfix.startup.shortcut_path", return_value=link):
                self.assertTrue(set_startup(True))
                self.assertTrue(link.is_file())
                self.assertTrue(set_startup(False))
                self.assertFalse(link.exists())

    def test_single_instance_lock_rejects_second_process_role(self):
        with tempfile.TemporaryDirectory() as temporary:
            name = "ClassFix-Test-" + Path(temporary).name
            lock = Path(temporary) / "instance.lock"
            first = SingleInstance(name, lock, lambda _message: None)
            second = SingleInstance(name, lock, lambda _message: None)
            self.assertEqual(first.listen(), "primary")
            self.assertEqual(first.lock.staleLockTime(), 5000)
            # 第二个进程应当「叫醒」既有实例后退出，而不是静默失败
            self.assertEqual(second.listen(), "handoff")
            first.server.close()
            first.lock.unlock()

    def test_blocked_lock_reports_instead_of_silent_exit(self):
        """旧版本占着锁、又没有同名本地服务时，必须报 blocked（界面据此提示用户）。"""
        with tempfile.TemporaryDirectory() as temporary:
            name = "ClassFix-Test-blocked-" + Path(temporary).name
            lock = Path(temporary) / "instance.lock"
            holder = QLockFile(str(lock))
            self.assertTrue(holder.tryLock(0))
            blocked = SingleInstance(name, lock, lambda _message: None)
            self.assertEqual(blocked.listen(), "blocked")
            self.assertIsNotNone(blocked.lock_owner())
            holder.unlock()
