"""放映接口实测的分档判定测试（WPS 也测，不再拒测）。

回归背景（1.3.1 及更早）：probe_presentation_api() 一发现放映接口被 WPS 接管，就直接
返回「不是 Microsoft PowerPoint，本课无法监视放映」——用户点「实测放映接口」得到的是
「拒绝测试」，可他真正想知道的是「这台机器能不能把课件放出来」。现在改成：不论
PowerPoint 还是 WPS，都真起一次放映再退出，按「能否放映」给结论；只有读不到安装路径
且交互登记也不可信时才判不可用。WPS 仍给不出放映窗口页码（monitor 依赖
SlideShowWindows），这一点保留在文案里说明。

本文件用假 COM 对象在 Linux 上真跑清理路径，不需要 Windows、不需要 comtypes。
"""

import unittest

from evetech.preflight import _run_slide_show_probe, probe_presentation_api, probe_verdict


class FakeError(Exception):
    """带 hresult 的假 COM 异常（真实 comtypes.COMError 不能脱离 Windows 构造）。"""

    def __init__(self, code=0x80004005):
        super().__init__("boom")
        self.hresult = code


class FakeView:
    def __init__(self, fail=False):
        self.fail = fail
        self.exited = 0

    def Exit(self):
        self.exited += 1
        if self.fail:
            raise FakeError()


class FakeShowWindow:
    def __init__(self, view):
        self.View = view


class FakeWindows:
    """SlideShowWindows：Count 与 Item(i) 两条退出路径都覆盖。"""

    def __init__(self, count=0, view=None):
        self.Count = count
        self._view = view or FakeView()
        self.item_calls = 0

    def Item(self, index):
        self.item_calls += 1
        return FakeShowWindow(self._view)


class FakeSlides:
    def __init__(self, fail=False):
        self.fail = fail
        self.added = []

    def Add(self, index, layout):
        if self.fail:
            raise FakeError()
        self.added.append((index, layout))


class FakeSettings:
    def __init__(self, fail=False):
        self.fail = fail
        self.ran = 0

    def Run(self):
        self.ran += 1
        if self.fail:
            raise FakeError()


class FakePresentation:
    def __init__(self, run_fail=False, slides_fail=False, exit_fail=False):
        self.view = FakeView(exit_fail)
        self.SlideShowWindow = FakeShowWindow(self.view)
        self.Slides = FakeSlides(slides_fail)
        self.SlideShowSettings = FakeSettings(run_fail)
        self.Saved = False
        self.closed = 0
        self.close_fail = False

    def Close(self):
        self.closed += 1
        if self.close_fail:
            raise FakeError()


class FakePresentations:
    def __init__(self, presentation):
        self.presentation = presentation
        self.added = 0

    def Add(self):
        self.added += 1
        return self.presentation


class FakeApplication:
    def __init__(self, presentation=None, count=0, with_presentations=True):
        self.presentation = presentation
        self.Presentations = FakePresentations(presentation) if with_presentations else None
        self.SlideShowWindows = FakeWindows(count)


class SlideShowProbeTest(unittest.TestCase):
    def probe(self, application):
        return _run_slide_show_probe(application, settle=0)

    def test_runs_a_real_show_then_exits_and_closes(self):
        presentation = FakePresentation()
        application = FakeApplication(presentation)

        ok, note = self.probe(application)

        self.assertTrue(ok)
        self.assertEqual(note, "")
        self.assertEqual(presentation.SlideShowSettings.ran, 1, "必须真的 Run 一次放映")
        self.assertEqual(presentation.Slides.added, [(1, 12)], "空白文稿要补一页，否则放不出来")
        self.assertTrue(presentation.Saved, "Close 前要先标记已保存，避免弹保存对话框")
        self.assertEqual(presentation.view.exited, 1, "退出放映")
        self.assertEqual(presentation.closed, 1, "关掉临时文稿，不留残窗")

    def test_skips_new_show_when_one_is_already_running(self):
        presentation = FakePresentation()
        application = FakeApplication(presentation, count=1)

        ok, note = self.probe(application)

        self.assertTrue(ok)
        self.assertEqual(note, "already")
        self.assertEqual(application.Presentations.added, 0, "不要打扰正在放映的课件")
        self.assertEqual(presentation.closed, 0)

    def test_reports_com_code_when_run_fails_and_still_cleans_up(self):
        presentation = FakePresentation(run_fail=True)
        application = FakeApplication(presentation)

        ok, note = self.probe(application)

        self.assertFalse(ok)
        self.assertEqual(note, "0x80004005")
        self.assertEqual(presentation.closed, 1, "失败也要关掉临时文稿")

    def test_add_page_failure_is_tolerated(self):
        presentation = FakePresentation(slides_fail=True)
        application = FakeApplication(presentation)

        ok, _note = self.probe(application)

        self.assertTrue(ok, "加页失败不该判放映不可用")
        self.assertEqual(presentation.SlideShowSettings.ran, 1)

    def test_falls_back_to_window_collection_when_view_exit_fails(self):
        presentation = FakePresentation(exit_fail=True)
        application = FakeApplication(presentation)

        ok, _note = self.probe(application)

        self.assertTrue(ok)
        self.assertEqual(presentation.view.exited, 1, "第一条退出路径先试过")
        self.assertEqual(application.SlideShowWindows.item_calls, 1, "再退回 SlideShowWindows")

    def test_reports_missing_presentations_collection(self):
        application = FakeApplication(with_presentations=False)

        ok, note = self.probe(application)

        self.assertFalse(ok)
        self.assertIn("Presentations", note)


class VerdictTest(unittest.TestCase):
    def test_powerpoint_ok_mentions_monitor_available(self):
        ok, text = probe_verdict("powerpoint", "16.0", True)
        self.assertTrue(ok)
        self.assertIn("Microsoft PowerPoint", text)
        self.assertIn("监视", text)

    def test_wps_ok_is_a_pass_not_a_refusal(self):
        ok, text = probe_verdict("wps", "12.1", True)
        self.assertTrue(ok, "WPS 能放映就该判通过，不能因为不是 PowerPoint 而拒测")
        self.assertIn("WPS", text)
        self.assertIn("人工确认", text, "监视不可用这一点仍要讲清楚")

    def test_wps_ok_when_show_already_running(self):
        ok, text = probe_verdict("wps", "12.1", True, "already")
        self.assertTrue(ok)
        self.assertIn("WPS", text)

    def test_powerpoint_failure_points_at_office_repair(self):
        ok, text = probe_verdict("powerpoint", "16.0", False, "0x80004005")
        self.assertFalse(ok)
        self.assertIn("0x80004005", text)
        self.assertIn("Office", text)

    def test_wps_failure_asks_for_manual_check(self):
        ok, text = probe_verdict("wps", "12.1", False, "0x80004005")
        self.assertFalse(ok)
        self.assertIn("手动放映", text)

    def test_unknown_vendor_failure_asks_to_install(self):
        ok, text = probe_verdict("unknown", "", False, "0x80004005")
        self.assertFalse(ok)
        self.assertIn("PowerPoint", text)
        self.assertIn("WPS", text)


class NonWindowsTest(unittest.TestCase):
    def test_reports_platform_limitation_without_touching_com(self):
        import sys

        if sys.platform == "win32":
            self.skipTest("只在非 Windows 上验证这条早退分支")
        ok, text = probe_presentation_api()
        self.assertFalse(ok)
        self.assertIn("Windows", text)


if __name__ == "__main__":
    unittest.main()
