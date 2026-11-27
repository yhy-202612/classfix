"""PowerPoint / WPS 演示双检测的判定矩阵（平台无关：底层全部打桩）。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from classfix.preflight import (inspect_preflight, powerpoint_check, wps_check, wps_version)

PPT = Path(r"C:\Program Files\Microsoft Office\Root\Office16\POWERPNT.EXE")
WPP = Path(r"C:\Program Files\Kingsoft\WPS Office\12.1.0.16929\office6\wpp.exe")


def processes(*names):
    return [SimpleNamespace(info={"name": name}) for name in names]


def run_check(check, *, powerpnt=None, server=None, alternative=None, wps=None, running=(),
              wps_running=False, hijack=None, version=""):
    with patch("classfix.preflight.powerpnt_path", return_value=powerpnt), \
         patch("classfix.preflight.com_server_path", return_value=server), \
         patch("classfix.preflight.office_alternative", return_value=alternative), \
         patch("classfix.preflight.wps_path", return_value=wps), \
         patch("classfix.preflight.wps_running", return_value=wps_running), \
         patch("classfix.preflight.com_taken_by_wps", return_value=hijack), \
         patch("classfix.preflight.wps_version", return_value=version), \
         patch("classfix.preflight.psutil.process_iter", return_value=processes(*running)):
        return check()


class PowerPointCheckTest(unittest.TestCase):
    def test_running_powerpoint_is_ok(self):
        check = run_check(powerpoint_check, powerpnt=PPT, server=Path("POWERPNT.EXE"), running=["POWERPNT.EXE"])
        self.assertEqual(check.key, "office")
        self.assertEqual(check.status, "ok")
        self.assertIn("正在运行", check.detail)

    def test_installed_with_com_registration_is_ok(self):
        check = run_check(powerpoint_check, powerpnt=PPT, server=Path("POWERPNT.EXE"))
        self.assertEqual(check.status, "ok")

    def test_installed_without_com_is_warning(self):
        check = run_check(powerpoint_check, powerpnt=PPT, server=None)
        self.assertEqual(check.status, "warning")
        self.assertIn("COM", check.detail)

    def test_only_wps_installed_is_warning_not_problem(self):
        """只装 WPS 的教室机不该被判成「有问题」——WPS 也能正常放课件。"""
        check = run_check(powerpoint_check, wps=WPP, alternative=Path("wpp.exe"))
        self.assertEqual(check.status, "warning")
        self.assertIn("WPS", check.detail)
        self.assertIn("wpp.exe", check.detail)

    def test_nothing_installed_is_problem(self):
        check = run_check(powerpoint_check)
        self.assertEqual(check.status, "problem")


class WpsCheckTest(unittest.TestCase):
    def test_missing_wps_is_unknown_not_problem(self):
        check = run_check(wps_check)
        self.assertEqual(check.key, "wps")
        self.assertEqual(check.status, "unknown")
        self.assertIn("未检测到 WPS", check.detail)

    def test_installed_wps_reports_version_path_and_capability(self):
        check = run_check(wps_check, wps=WPP, version="12.1.0.16929", wps_running=False)
        self.assertEqual(check.status, "ok")
        self.assertIn("WPS 演示 12.1.0.16929", check.detail)
        self.assertIn("wpp.exe", check.detail)
        self.assertIn("当前未运行", check.detail)
        self.assertIn("Microsoft PowerPoint 为准", check.detail)

    def test_running_wps_and_hijacked_com_is_warning(self):
        check = run_check(wps_check, wps=WPP, version="12.1.0.16929", wps_running=True,
                          hijack=Path("wps.exe"))
        self.assertEqual(check.status, "warning")
        self.assertIn("当前正在运行", check.detail)
        self.assertIn("接管", check.detail)

    def test_hijack_without_install_reports_warning(self):
        check = run_check(wps_check, wps=None, hijack=Path("wps.exe"))
        self.assertEqual(check.status, "warning")
        self.assertIn("wps.exe", check.detail)

    def test_wps_version_extracted_from_path(self):
        self.assertEqual(wps_version(WPP), "12.1.0.16929")
        self.assertEqual(wps_version(Path(r"D:\WPS Office\office6\wpp.exe")), "")


class PreflightIntegrationTest(unittest.TestCase):
    def test_report_contains_both_office_and_wps_checks(self):
        fake = SimpleNamespace(key="x", title="t", status="ok", detail="d", extra="")
        audio = SimpleNamespace(checks=[fake], findings=[])
        with patch("classfix.preflight.inspect_audio", return_value=audio), \
             patch("classfix.preflight.display_check", return_value=fake), \
             patch("classfix.preflight.network_check", return_value=fake), \
             patch("classfix.preflight.peripheral_check", return_value=fake), \
             patch("classfix.preflight.powerpoint_check",
                   return_value=SimpleNamespace(key="office", title="Microsoft PowerPoint",
                                                status="ok", detail="d", extra="")), \
             patch("classfix.preflight.wps_check",
                   return_value=SimpleNamespace(key="wps", title="WPS 演示",
                                                status="ok", detail="d", extra="")):
            report = inspect_preflight()
        keys = [check.key for check in report.checks]
        self.assertIn("office", keys)
        self.assertIn("wps", keys)
        self.assertLess(keys.index("office"), keys.index("wps"))

    def test_only_wps_machine_produces_wps_finding(self):
        audio = SimpleNamespace(checks=[], findings=[])
        ok = SimpleNamespace(key="x", title="t", status="ok", detail="d", extra="")
        office = SimpleNamespace(key="office", title="Microsoft PowerPoint", status="warning",
                                 detail="未安装 Microsoft PowerPoint（本机由 wpp.exe 占用）", extra="")
        wps = SimpleNamespace(key="wps", title="WPS 演示", status="ok",
                              detail="已安装 WPS 演示 12.1", extra="")
        with patch("classfix.preflight.inspect_audio", return_value=audio), \
             patch("classfix.preflight.display_check", return_value=ok), \
             patch("classfix.preflight.network_check", return_value=ok), \
             patch("classfix.preflight.peripheral_check", return_value=ok), \
             patch("classfix.preflight.powerpoint_check", return_value=office), \
             patch("classfix.preflight.wps_check", return_value=wps):
            report = inspect_preflight()
        titles = [finding.title for finding in report.findings]
        self.assertIn("放映监视只支持 Microsoft PowerPoint", titles)


if __name__ == "__main__":
    unittest.main()
