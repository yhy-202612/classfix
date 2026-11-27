import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from classfix.monitor import MediaProblem
from classfix.memory_cleanup import CleanupStatus, MemoryCleanup, MemoryReading
from classfix.ui.overlay import FormatPopup, MediaAlerts, MemoryPopup, VolumePopup
from classfix.ui.popup_stack import PopupStack


def synchronous_job(operation, ready, _failed):
    ready(operation())


class OverlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = PopupStack(self.app)
        self.addCleanup(self.stack.deleteLater)

    def popup(self, popup_class):
        popup = popup_class(self.app, self.stack)
        self.addCleanup(popup.close)
        return popup

    def problem(self, slide=2, error=""):
        return MediaProblem(slide, "视频文件或链接缺失", "外部视频路径不存在", error)

    def test_structured_alert_separates_local_evidence_from_powerpoint_error(self):
        popup = self.popup(FormatPopup)
        popup.notify_problem(self.problem(error="无法播放媒体 (0x80004005)"))
        self.assertEqual(popup.title.text(), "视频文件或链接缺失 第2页")
        self.assertIn("外部视频路径不存在", popup.detail.text())
        self.assertEqual(popup.powerpoint_error.text(), "PowerPoint 报错：无法播放媒体 (0x80004005)")

    def test_structured_alert_does_not_invent_page_or_error(self):
        popup = self.popup(FormatPopup)
        popup.notify_problem(self.problem(slide=0))
        self.assertEqual(popup.title.text(), "视频文件或链接缺失")
        self.assertEqual(popup.powerpoint_error.text(), "PowerPoint 报错：未读取到")

    def test_each_report_is_queued_even_when_its_text_is_the_same(self):
        alerts = MediaAlerts(self.app, self.stack)
        problem = self.problem()
        first = alerts.notify_problem(problem, 0)
        repeated = alerts.notify_problem(problem, 0)
        self.assertIsNot(first, repeated)
        for slide in range(4, 12):
            alerts.notify_problem(self.problem(slide=slide), 0)
        self.assertEqual(len(self.stack.placements), 6)
        first.close()
        released = [item.title.text() for item in self.stack.placements]
        self.assertIn("视频文件或链接缺失 第8页", released)
        while self.stack.placements:
            next(iter(self.stack.placements)).close()
        self.assertEqual(alerts._open_count, 0)
        self.assertFalse(alerts._waiting)

    def test_error_bottom_edge_moves_between_seconds_and_slides_out(self):
        popup = self.popup(FormatPopup)
        popup.notify_problem(self.problem())
        QTest.qWait(240)
        popup.countdown.setCurrentTime(300)
        early = popup._strip_width
        popup.countdown.setCurrentTime(420)
        self.assertLess(popup._strip_width, early)
        popup.countdown.setCurrentTime(6000)
        edge = popup._edge_rect()
        canvas = popup.grab().toImage()
        dpr = canvas.devicePixelRatio()
        y = round((edge.top() + 1) * dpr)
        self.assertEqual(canvas.pixelColor(round(32 * dpr), y), QColor("#8ab4f5"))
        self.assertEqual(canvas.pixelColor(round(335 * dpr), y), QColor("#393939"))
        start_x = popup.x()
        popup.countdown.setCurrentTime(popup.DISPLAY_MS)
        self.assertTrue(popup.isVisible())
        QTest.qWait(70)
        self.assertGreater(popup.x(), start_x)
        QTest.qWait(220)
        self.assertFalse(popup.isVisible())

    def test_new_cards_enter_from_right_and_never_overlap_on_reflow(self):
        volume = self.popup(VolumePopup)
        error = self.popup(FormatPopup)
        memory = self.popup(MemoryPopup)
        available = self.app.primaryScreen().availableGeometry()
        volume.notify(2, 10, False)
        self.assertEqual(volume.x(), available.right())
        initial_y = volume.y()
        QTest.qWait(230)
        error.notify_problem(self.problem())
        memory.notify(89, 742)
        positions = {item: set() for item in (volume, error, memory)}

        def check_frames(milliseconds):
            for _ in range(milliseconds // 15):
                QTest.qWait(15)
                visible = [item for item in positions if item.isVisible()]
                for index, item in enumerate(visible):
                    positions[item].add((item.x(), item.y()))
                    for other in visible[index + 1:]:
                        self.assertFalse(item.geometry().intersects(other.geometry()))

        check_frames(930)
        self.assertTrue(all(popup.isVisible() for popup in positions))
        self.assertLess(volume.y(), initial_y)
        self.assertLess(volume.y(), error.y())
        self.assertLess(error.y(), memory.y())
        self.assertEqual(memory.geometry().bottom(), available.bottom() - self.stack.lanes[self.app.primaryScreen()].MARGIN)
        self.assertGreater(len({x for x, _ in positions[error]}), 1)
        before = volume.y()
        error.close()
        check_frames(270)
        self.assertGreater(volume.y(), before)
        self.assertTrue(volume.isVisible())

    def test_memory_meter_updates_after_cleanup_and_stays_visible_until_closed(self):
        popup = self.popup(MemoryPopup)
        with patch("classfix.ui.overlay.monotonic", return_value=100), \
             patch("classfix.ui.overlay.run_job", side_effect=synchronous_job), \
             patch("classfix.ui.overlay.optimize_system_memory") as optimize:
            steps = (CleanupStatus.SUCCEEDED, CleanupStatus.SKIPPED, *([CleanupStatus.FAILED] * 5))
            optimize.return_value = MemoryCleanup(MemoryReading(700, 89), MemoryReading(740, 86), steps)
            popup.notify(89, 700)
            self.assertEqual(popup.meter.value(), 89)
            self.assertEqual(popup.usage.text(), "89%")
            popup.optimize.click()
            optimize.assert_not_called()
            self.assertEqual(popup.optimize.text(), "确认优化")
            popup.cancel_optimize.click()
            optimize.assert_not_called()
            self.assertEqual(popup.optimize.text(), "优化内存")
            popup.optimize.click()
            popup.optimize.click()
            optimize.assert_called_once_with()
            self.assertEqual(popup.meter.value(), 86)
            self.assertTrue(popup.isVisible())
            self.assertIn("700 MiB → 740 MiB", popup.detail.text())
            self.assertIn("成功　全局工作集", popup.detail.text())
            self.assertIn("跳过　文件缓存", popup.detail.text())
            self.assertIn("失败　已修改页", popup.detail.text())
            popup.update_if_open(80, 960)
            self.assertEqual(popup.meter.value(), 80)
            self.assertIn("89% → 86%", popup.detail.text())
            self.assertIn("700 MiB → 740 MiB", popup.detail.text())
            popup.close()
        with patch("classfix.ui.overlay.monotonic", return_value=219):
            popup.notify(90, 650)
            self.assertFalse(popup.isVisible())
        with patch("classfix.ui.overlay.monotonic", return_value=220):
            popup.notify(90, 650)
            self.assertTrue(popup.isVisible())

    def test_memory_hour_snooze_survives_reopening(self):
        saved = []
        popup = MemoryPopup(self.app, self.stack, store_snooze=saved.append)
        self.addCleanup(popup.close)
        with patch("classfix.ui.overlay.time", return_value=1000):
            popup.notify(90, 650)
            popup.snooze_button.click()
            self.assertFalse(popup.isVisible())
        self.assertEqual(saved, [4600])
        reopened = MemoryPopup(self.app, self.stack, str(saved[0]))
        self.addCleanup(reopened.close)
        with patch("classfix.ui.overlay.time", return_value=4599):
            reopened.notify(90, 650)
            self.assertFalse(reopened.isVisible())
        with patch("classfix.ui.overlay.time", return_value=4600):
            reopened.notify(90, 650)
            self.assertTrue(reopened.isVisible())

    def test_failed_volume_write_restores_controls_and_reports_failure(self):
        popup = self.popup(VolumePopup)
        popup.notify(2, 12, True)
        with patch("classfix.ui.overlay.run_job", side_effect=synchronous_job), \
             patch("classfix.ui.overlay.read_system_volume", return_value=(12, True)), \
             patch("classfix.ui.overlay.set_system_volume", return_value=False) as write:
            popup.slider.setValue(55)
            popup._flush_write()
            write.assert_called_once_with(55)
        self.assertEqual(popup.slider.value(), 12)
        self.assertEqual(popup.value.text(), "12%")
        self.assertEqual(popup.mute.text(), "取消静音")
        self.assertIn("未能调整", popup.detail.text())

    def test_volume_bar_accepts_absolute_click_and_smooth_drag(self):
        popup = self.popup(VolumePopup)
        popup.notify(2, 10, False)
        QTest.qWait(230)
        slider = popup.slider
        w, y = slider.width(), slider.height() // 2
        point = QPoint(3 * w // 4, y)
        with patch("classfix.ui.overlay.run_job", side_effect=synchronous_job), \
             patch("classfix.ui.overlay.set_system_volume", return_value=True) as write:
            QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=point)
            self.assertGreater(slider.value(), 69)
            self.assertLess(slider.value(), 81)
            write.assert_called_once_with(slider.value())
            write.reset_mock()
            QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=point)
            for x in (w // 2, w // 4, w // 8):
                QTest.mouseMove(slider, QPoint(x, y))
            self.assertEqual(popup.value.text(), f"{slider.value()}%")
            write.assert_not_called()
            QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(w // 8, y))
            write.assert_called_once_with(slider.value())
            self.assertLess(slider.value(), 15)

    def test_drag_keeps_only_latest_pending_value_while_write_is_in_flight(self):
        popup = self.popup(VolumePopup)
        popup.notify(2, 10, False)
        jobs = []
        with patch("classfix.ui.overlay.run_job", side_effect=lambda *args: jobs.append(args)), \
             patch("classfix.ui.overlay.set_system_volume", return_value=True) as write:
            popup.slider.setValue(30)
            popup._flush_write()
            for value in (34, 55, 75):
                popup.slider.setValue(value)
            popup._flush_write()
            self.assertEqual(len(jobs), 1)
            jobs[0][1](jobs[0][0]())
            popup._flush_write()
            self.assertEqual(len(jobs), 2)
            jobs[1][1](jobs[1][0]())
            self.assertEqual([call.args[0] for call in write.call_args_list], [30, 75])
