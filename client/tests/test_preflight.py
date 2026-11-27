import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evetech.preflight import (DisplayPath, _looks_like_wps, describe_layout, display_check,
                                inspect_preflight, network_check, office_check, powerpnt_path,
                                peripheral_check)
from evetech.pnp import Peripheral


@unittest.skipUnless(sys.platform == "win32", "Windows device integration")
class PreflightTest(unittest.TestCase):
    def test_live_read_only_preflight(self):
        report = inspect_preflight()
        self.assertEqual({check.key for check in report.checks},
                         {"audio_default", "audio_volume", "audio_devices", "display", "network",
                          "office", "wps", "peripherals"})
        self.assertNotEqual(office_check().status, "unknown")
        for check in report.checks:
            self.assertTrue(check.detail)

    def test_windows_network_states_are_distinct(self):
        for bits, status in ((None, "unknown"), (0, "problem"), (32, "warning"), (64, "ok")):
            with patch("evetech.preflight.network_bits", return_value=bits):
                self.assertEqual(network_check().status, status)

    def test_unknown_camera_status_is_not_reported_as_healthy(self):
        with patch("evetech.preflight.present_peripherals", return_value=[Peripheral("Camera", None, False)]):
            self.assertEqual(peripheral_check().status, "warning")

    def test_real_display_has_explanation(self):
        check = display_check()
        self.assertIn(check.status, ("ok", "warning", "problem", "unknown"))
        self.assertTrue(check.detail)
        self.assertTrue("尚未检测" in check.detail or "输出" in check.detail or "投出" in check.detail)


class OfficeDetectionTest(unittest.TestCase):
    """WPS Office 会把 App Paths\\POWERPNT.EXE 指向 wps.exe，不能当成「已装 PowerPoint」。

    将安装目录指向临时目录，避免本机真实 Office 安装影响模拟结果。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        self.enterContext(patch.dict("evetech.preflight.os.environ", {
            name: self.tmp.name
            for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
        }))

    def _touch(self, name):
        path = Path(self.tmp.name) / name
        path.write_text("", encoding="utf-8")
        return path

    def test_registry_entry_pointing_to_wps_is_not_powerpoint(self):
        wps = self._touch("wps.exe")
        patch("evetech.preflight._app_paths_value", return_value=wps).start()
        patch("evetech.preflight.office_alternative", return_value=wps).start()
        patch("evetech.preflight.psutil.process_iter", return_value=[]).start()
        patch("evetech.preflight.com_server_path", return_value=None).start()
        self.assertIsNone(powerpnt_path())
        check = office_check()
        # 只装 WPS 的机器不再判为「故障」：WPS 也能正常放映课件，只是自动监视不可用
        self.assertEqual(check.status, "warning")
        self.assertIn("wps.exe", check.detail)
        self.assertNotIn("找到 PowerPoint 安装文件", check.detail)

    def test_real_powerpnt_registration_is_reported_ok(self):
        real = self._touch("POWERPNT.EXE")
        patch("evetech.preflight._app_paths_value", return_value=real).start()
        patch("evetech.preflight.psutil.process_iter", return_value=[]).start()
        patch("evetech.preflight.com_server_path", return_value=real).start()
        self.assertEqual(powerpnt_path(), real)
        self.assertEqual(office_check().status, "ok")

    def test_installed_powerpoint_without_com_registration_is_warning(self):
        real = self._touch("POWERPNT.EXE")
        patch("evetech.preflight._app_paths_value", return_value=real).start()
        patch("evetech.preflight.psutil.process_iter", return_value=[]).start()
        patch("evetech.preflight.com_server_path", return_value=None).start()
        check = office_check()
        self.assertEqual(check.status, "warning")
        self.assertIn("COM", check.detail)

    def test_com_registration_hijacked_by_other_server_is_problem(self):
        real = self._touch("POWERPNT.EXE")
        hijack = self._touch("wps.exe")
        patch("evetech.preflight._app_paths_value", return_value=real).start()
        patch("evetech.preflight.psutil.process_iter", return_value=[]).start()
        patch("evetech.preflight.com_server_path", return_value=hijack).start()
        check = office_check()
        self.assertEqual(check.status, "problem")
        self.assertIn("wps.exe", check.detail)

    def test_running_powerpoint_process_is_ok_without_install_path(self):
        patch("evetech.preflight._app_paths_value", return_value=None).start()
        patch("evetech.preflight.office_alternative", return_value=None).start()
        patch("evetech.preflight.com_server_path", return_value=None).start()
        patch("evetech.preflight.psutil.process_iter",
              return_value=[SimpleNamespace(info={"name": "POWERPNT.EXE"})]).start()
        self.assertEqual(office_check().status, "ok")

    def test_missing_office_reports_problem_then_explains_alternative(self):
        wps = self._touch("wpp.exe")
        patch("evetech.preflight._app_paths_value", return_value=None).start()
        patch("evetech.preflight.office_alternative", return_value=wps).start()
        patch("evetech.preflight.psutil.process_iter", return_value=[]).start()
        patch("evetech.preflight.com_server_path", return_value=None).start()
        check = office_check()
        # 只装 WPS 的机器判为「需要注意」而不是「存在问题」，具体能力说明见 wps_check()
        self.assertEqual(check.status, "warning")
        self.assertIn("WPS", check.detail)

    def test_local_server_command_line_is_parsed(self):
        from evetech.preflight import _server_executable
        self.assertEqual(_server_executable('"C:\\Program Files\\Microsoft Office\\Root\\Office16\\POWERPNT.EXE" /automation'),
                         Path("C:\\Program Files\\Microsoft Office\\Root\\Office16\\POWERPNT.EXE"))
        self.assertEqual(_server_executable("C:\\Office\\POWERPNT.EXE /automation"),
                         Path("C:\\Office\\POWERPNT.EXE"))
        self.assertIsNone(_server_executable(""))


class DisplayLayoutTest(unittest.TestCase):
    """Win+P 模式判定：把「教学屏有没有投出」从猜测变成确定结论。"""

    @staticmethod
    def path(name, connector="HDMI", active=True, internal=False, source=1, available=True):
        return DisplayPath(name, connector, active, internal, (0, 0, source), available)

    def test_extend_and_clone_are_distinguished(self):
        extend = [self.path("内置屏", "内置", True, True, 1), self.path("投影", "HDMI", True, False, 2)]
        self.assertEqual(describe_layout(extend)[0], "扩展")
        clone = [self.path("内置屏", "内置", True, True, 1), self.path("投影", "HDMI", True, False, 1)]
        self.assertEqual(describe_layout(clone)[0], "复制")

    def test_internal_only_with_idle_external_means_not_projected(self):
        mode, external, idle = describe_layout([
            self.path("内置屏", "内置", True, True, 1),
            self.path("DELL U2417H", "DisplayPort", False, False, 2),
        ])
        self.assertEqual(mode, "仅电脑屏幕")
        self.assertEqual(external, [])
        self.assertEqual([item.name for item in idle], ["DELL U2417H"])

    def test_second_screen_only_counts_as_projected(self):
        mode, external, idle = describe_layout([self.path("投影")])
        self.assertEqual(mode, "仅第二屏幕")
        self.assertEqual(len(external), 1)
        self.assertEqual(idle, [])

    def test_no_active_output(self):
        mode, external, idle = describe_layout([self.path("投影", active=False)])
        self.assertEqual(mode, "未输出")
        self.assertEqual(external, [])
        self.assertEqual(len(idle), 1)

    def test_connected_but_not_projected_is_reported_as_problem(self):
        with patch("evetech.preflight.sys.platform", "win32"), \
             patch("evetech.preflight.read_display_paths", return_value=[
                 self.path("内置屏", "内置", True, True, 1),
                 self.path("DELL U2417H", "DisplayPort", False, False, 2)]):
            check = display_check()
        self.assertEqual(check.status, "problem")
        self.assertIn("已连接", check.detail)
        self.assertIn("Win+P", check.detail)

    def test_projected_layout_is_ok(self):
        with patch("evetech.preflight.sys.platform", "win32"), \
             patch("evetech.preflight.read_display_paths", return_value=[
                 self.path("内置屏", "内置", True, True, 1),
                 self.path("投影", "HDMI", True, False, 1)]):
            check = display_check()
        self.assertEqual(check.status, "ok")
        self.assertIn("复制", check.detail)

    def test_unreadable_display_config_stays_unknown(self):
        with patch("evetech.preflight.sys.platform", "win32"), \
             patch("evetech.preflight.read_display_paths", return_value=None):
            self.assertEqual(display_check().status, "unknown")

    def test_ghost_targets_are_not_counted_as_connected(self):
        """真机实测（只有 1 块屏的 Windows 10）：QDC_ALL_PATHS 会带回 9 条历史 target。

        它们 available=False，既没信号也不会被 Win+P 切换。若把它们算成「已连接但没投出」，
        单屏机器就会报 problem，把老师引去按 Win+P 找一块不存在的投影。
        """
        paths = [self.path("DELL U2417H", "DisplayPort", True, False, 1)]
        paths += [self.path("显示目标 %d" % n, "HDMI", False, False, 0, available=False)
                  for n in range(198195, 198195 + 8)]
        mode, external, idle = describe_layout(paths)
        self.assertEqual(mode, "仅第二屏幕")
        self.assertEqual(len(external), 1)
        self.assertEqual(idle, [])
        with patch("evetech.preflight.sys.platform", "win32"), \
             patch("evetech.preflight.read_display_paths", return_value=paths):
            check = display_check()
        self.assertEqual(check.status, "warning")
        self.assertNotIn("个外接输出已连接", check.detail)
        self.assertIn("另有 8 个历史显示目标", check.detail)
        self.assertNotIn("显示目标 198195", check.detail)

    def test_ghost_targets_do_not_hide_a_real_idle_projector(self):
        paths = [self.path("内置屏", "内置", True, True, 1),
                 self.path("教室投影", "HDMI", False, False, 2),
                 self.path("显示目标 265988", "DisplayPort", False, False, 0, available=False)]
        with patch("evetech.preflight.sys.platform", "win32"), \
             patch("evetech.preflight.read_display_paths", return_value=paths):
            check = display_check()
        self.assertEqual(check.status, "problem")
        self.assertIn("1 个外接输出已连接", check.detail)


class WpsDisguiseTest(unittest.TestCase):
    """WPS 会用同一个 ProgID 冒充 PowerPoint，Version 也照抄，只能靠安装路径分辨。"""

    def test_kingsoft_and_wps_paths_are_recognised(self):
        self.assertTrue(_looks_like_wps(r"C:\Users\l\AppData\Local\Kingsoft\WPS Office\12.1\office6"))
        self.assertTrue(_looks_like_wps(r"D:\Program Files\WPS Office\office6"))
        self.assertTrue(_looks_like_wps("/opt/kingsoft/wps-office"))

    def test_real_office_paths_are_not_flagged(self):
        self.assertFalse(_looks_like_wps(r"C:\Program Files\Microsoft Office\root\Office16"))
        self.assertFalse(_looks_like_wps(""))
        self.assertFalse(_looks_like_wps(None))

    def test_paths_that_merely_contain_wps_are_not_flagged(self):
        """…\\WPSHARE_Backup\\Office16\\POWERPNT.EXE 是真 PowerPoint，不能因为路径含 wps 就判成 WPS。"""
        self.assertFalse(_looks_like_wps(r"C:\Tools\WPSHARE_Backup\Office16\POWERPNT.EXE"))

    def test_wps_executables_are_recognised_by_name(self):
        self.assertTrue(_looks_like_wps(r"C:\anything\office6\wpp.exe"))
        self.assertTrue(_looks_like_wps(r"D:\somewhere\wps.exe"))

    def test_single_segment_wps_directory_is_recognised(self):
        self.assertTrue(_looks_like_wps(r"D:\Programs\wps\office6"))
        self.assertTrue(_looks_like_wps(r"D:\Programs\WPSOffice\office6"))
