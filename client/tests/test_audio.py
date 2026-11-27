import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

from classfix.audio import (AudioState, Endpoint, Session, build_audio_report, read_audio,
                            set_system_mute, set_system_volume)
from classfix.model import Report


class AudioTest(unittest.TestCase):
    def test_muted_endpoint_and_misrouted_session(self):
        default = Endpoint("1", "Speaker", "可用")
        other = Endpoint("2", "HDMI", "可用")
        state = AudioState([default, other], default, volume=45, muted=True,
                           sessions=[Session("2", True, False, 65)])
        report = build_audio_report(state)
        self.assertEqual(report.findings[0].title, "系统静音或音量过低")
        self.assertTrue(any(f.title == "PowerPoint 正在使用另一个输出" for f in report.findings))
        self.assertEqual(report.facts["active_outputs"], 2)

    def test_unknown_and_missing_default_are_distinct(self):
        self.assertEqual(build_audio_report(AudioState()).checks[0].status, "unknown")
        self.assertEqual(build_audio_report(AudioState(default_missing=True)).checks[0].status, "problem")

    def test_failure_feedback_adds_alternate_path(self):
        report = build_audio_report(AudioState(), "failed")
        self.assertTrue(any(f.title == "验证音响供电和输入" for f in report.findings))

    def test_absent_outputs_collapse_into_extra(self):
        """Windows 长期保留已拔掉的设备记录，默认列表不能被十几个「不存在」淹没。"""
        endpoints = [Endpoint("1", "喇叭/耳机（Realtek）", "可用"),
                     Endpoint("2", "DELL U2417H", "可用"),
                     Endpoint("3", "耳机（BULL-OWSG1）", "未插入"),
                     Endpoint("4", "内部 AUX 插座", "不存在"),
                     Endpoint("5", "SMART TV UHD", "不存在")]
        report = build_audio_report(AudioState(endpoints, endpoints[0], volume=50, muted=False))
        check = next(item for item in report.checks if item.key == "audio_devices")
        self.assertIn("2 个可用，共 5 个已知输出", check.detail)
        self.assertIn("喇叭/耳机（Realtek）", check.detail)
        self.assertIn("耳机（BULL-OWSG1）", check.detail)
        self.assertNotIn("内部 AUX 插座", check.detail)
        self.assertIn("2 个当前不存在的历史输出已折叠", check.detail)
        self.assertEqual(len(check.extra.splitlines()), 2)
        self.assertIn("SMART TV UHD", check.extra)

    def test_all_outputs_absent_keeps_extra_only(self):
        endpoints = [Endpoint("1", "耳机", "不存在"), Endpoint("2", "HDMI", "不存在")]
        report = build_audio_report(AudioState(endpoints, None, default_missing=True))
        check = next(item for item in report.checks if item.key == "audio_devices")
        self.assertNotIn("耳机\n", check.detail)
        self.assertIn("2 个当前不存在的历史输出已折叠", check.detail)
        self.assertEqual(check.extra.count("\n"), 1)

    def test_check_extra_survives_dict_round_trip(self):
        report = build_audio_report(AudioState([Endpoint("1", "耳机", "不存在")], None, default_missing=True))
        again = Report.from_dict(json.loads(json.dumps(report.to_dict(), ensure_ascii=False)))
        self.assertEqual(again.checks, report.checks)

    @unittest.skipUnless(sys.platform == "win32", "Windows Core Audio integration")
    def test_core_audio_real_read_on_worker(self):
        with ThreadPoolExecutor() as workers:
            state = workers.submit(read_audio).result(timeout=15)
        self.assertIsNotNone(state.endpoints, state.errors)
        self.assertTrue(state.default or state.default_missing, state.errors)
        if state.default:
            self.assertIsInstance(state.muted, bool)
            self.assertTrue(0 <= state.volume <= 100)

    @unittest.skipUnless(sys.platform == "win32", "Windows Core Audio integration")
    def test_volume_setter_round_trip(self):
        state = read_audio()
        if state.volume is None or state.muted is None:
            self.skipTest("No default output endpoint")
        self.assertTrue(set_system_volume(state.volume))
        self.assertTrue(set_system_mute(state.muted))
