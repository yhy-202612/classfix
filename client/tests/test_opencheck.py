import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evetech.model import Finding, Report, SYMPTOMS
from evetech import opencheck as oc


class ClassifyMessageTest(unittest.TestCase):
    def test_real_error_sentences_map_to_families(self):
        cases = {
            "PowerPoint 发现 循环 中的内容有问题。PowerPoint 可尝试修复此演示文稿。": "结构有问题",
            "PowerPoint 无法打开该文件，因为文件格式不受支持。": "损坏或扩展名",
            "该演示文稿受密码保护，需要输入密码。": "密码",
            "该文件正在使用，无法打开。": "被其它程序占用",
            "此文件来自 Internet 位置，可能不安全（受保护的视图）。": "受保护的视图",
            "内存不足，无法完成操作。": "内存或资源不足",
            "远程过程调用失败。": "组件注册坏了",
            "无法找到链接的文件。": "外部文件找不到",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                title, action, confidence, priority = oc.classify_message(text)
                self.assertIn(expected, title)
                self.assertTrue(action)
                self.assertIn(confidence, ("有检测依据", "可能"))
                self.assertLess(priority, 30)

    def test_unknown_and_empty_are_none(self):
        self.assertIsNone(oc.classify_message(""))
        self.assertIsNone(oc.classify_message(None))
        self.assertIsNone(oc.classify_message("一切正常"))


class ClassifyHresultTest(unittest.TestCase):
    def test_known_codes(self):
        self.assertIn("通用拒绝访问", oc.classify_hresult(0x80004005)[0])
        self.assertIn("文件找不到", oc.classify_hresult(-2147024894)[0])   # 0x80070002 signed
        self.assertIn("没有读取权限", oc.classify_hresult(0x80070005)[0])

    def test_unknown_code(self):
        self.assertIsNone(oc.classify_hresult(0x12345678))
        self.assertIsNone(oc.classify_hresult(None))


class DialogFamilyTest(unittest.TestCase):
    def test_families(self):
        self.assertEqual(oc.classify_dialog("是否修复此演示文稿？（修复/取消）"), "repair")
        self.assertEqual(oc.classify_dialog("PowerPoint 无法打开该文件"), "cannot_open")
        self.assertEqual(oc.classify_dialog("该文件正在使用"), "busy")
        self.assertEqual(oc.classify_dialog("缺失字体：方正小标宋"), "font")
        self.assertEqual(oc.classify_dialog(""), "")
        self.assertEqual(oc.classify_dialog("普通窗口标题"), "")


class WerParseTest(unittest.TestCase):
    REPORT = "\r\n".join([
        "Version=1",
        "EventType=APPCRASH",
        "AppName=POWERPNT.EXE",
        "Sig[0].Name=Application Name",
        "Sig[0].Value=POWERPNT.EXE",
        "Sig[3].Name=Faulting Module Name",
        "Sig[3].Value=oolic.dll",
        "Sig[6].Name=Exception Code",
        "Sig[6].Value=c0000005",
        "ReportIdentifier=12345",
    ])

    def test_fields(self):
        parsed = oc.parse_wer(self.REPORT)
        self.assertEqual(parsed["app"], "POWERPNT.EXE")
        self.assertEqual(parsed["event"], "APPCRASH")
        self.assertEqual(parsed["module"], "oolic.dll")
        self.assertEqual(parsed["code"], "c0000005")

    def test_garbage_does_not_raise(self):
        self.assertEqual(oc.parse_wer("no signatures here")["module"], "")


class BinaryStringsTest(unittest.TestCase):
    def test_utf16_paths_are_recovered(self):
        blob = ("C:\\Program Files\\Addin\\bad.dll".encode("utf-16-le")
                + b"\x00\x00\x00" + ("D:\\课件\\第3讲.pptx".encode("utf-16-le")))
        found = oc.strings_from_binary(blob)
        self.assertTrue(any("bad.dll" in item for item in found))
        self.assertTrue(any(".pptx" in item for item in found))

    def test_noise_returns_empty(self):
        self.assertEqual(oc.strings_from_binary(b"\x01\x02\x03"), ())
        self.assertEqual(oc.strings_from_binary(b""), ())


class ResiliencyDiffTest(unittest.TestCase):
    def test_new_items(self):
        before = {"DisabledItems": ("a.dll",)}
        after = {"DisabledItems": ("a.dll", "b.dll"), "StartUpItems": ("c.paq",)}
        fresh = oc.diff_resiliency(before, after)
        self.assertIn(("DisabledItems", "b.dll"), fresh)
        self.assertIn(("StartUpItems", "c.paq"), fresh)
        self.assertNotIn(("DisabledItems", "a.dll"), fresh)
        self.assertEqual(oc.diff_resiliency(before, before), [])


class VerdictTest(unittest.TestCase):
    def test_failed_but_safe_opens_is_environment(self):
        com = oc.ComResult(state="failed", message="PowerPoint 无法打开该文件")
        safe = oc.SafeResult(state="opened", seconds=4.0)
        headline, _why, confidence, priority = oc.verdict(com, safe, oc.EnvEvidence(state="ok"))
        self.assertIn("加载项", headline)
        self.assertEqual(priority, 4)

    def test_failed_and_safe_failed_is_file(self):
        com = oc.ComResult(state="failed", message="内容有问题")
        safe = oc.SafeResult(state="failed", seconds=3.0)
        headline, _why, _c, priority = oc.verdict(com, safe, oc.EnvEvidence(state="ok"))
        self.assertIn("文件本身", headline)
        self.assertEqual(priority, 3)

    def test_ok_with_bad_shapes(self):
        com = oc.ComResult(state="ok", slides=10, bad_shapes=((3, "图片 7", "err"),))
        headline, _why, _c, _p = oc.verdict(com, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertIn("渲染", headline)

    def test_ok_but_environment_dirty(self):
        com = oc.ComResult(state="ok", slides=10)
        env = oc.EnvEvidence(state="ok", crashes=({"module": "x.dll", "code": "c0000005"},))
        headline, _why, _c, _p = oc.verdict(com, oc.SafeResult(state="skipped"), env)
        self.assertIn("异常痕迹", headline)

    def test_ok_and_clean(self):
        com = oc.ComResult(state="ok", slides=10)
        headline, _why, _c, _p = oc.verdict(com, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertIn("能正常打开", headline)

    def test_timeout_and_skipped(self):
        self.assertIn("超时", oc.verdict(oc.ComResult(state="timeout"), oc.SafeResult(), oc.EnvEvidence())[0])
        self.assertIsNone(oc.verdict(oc.ComResult(state="skipped"), oc.SafeResult(), oc.EnvEvidence()))

    def test_slides_lost_is_reported(self):
        com = oc.ComResult(state="ok", slides=8, expected=10)
        headline, _why, _c, _p = oc.verdict(com, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertIn("页数", headline)


class ChecksAndFindingsTest(unittest.TestCase):
    def test_com_check_states(self):
        self.assertEqual(oc._com_check(oc.ComResult(state="skipped")).status, "unknown")
        failed = oc._com_check(oc.ComResult(state="failed", hresult=0x80004005, message="拒绝"))
        self.assertEqual(failed.status, "problem")
        self.assertIn("0x80004005", failed.detail)
        ok = oc._com_check(oc.ComResult(state="ok", slides=3, seconds=1.2))
        self.assertEqual(ok.status, "ok")
        bad = oc._com_check(oc.ComResult(state="ok", slides=3, bad_slides=((2, "err"),)))
        self.assertEqual(bad.status, "problem")

    def test_env_check_states(self):
        self.assertEqual(oc._env_check(oc.EnvEvidence(state="skipped")).status, "unknown")
        self.assertEqual(oc._env_check(oc.EnvEvidence(state="ok")).status, "ok")
        dirty = oc._env_check(oc.EnvEvidence(state="ok", disabled=("x.dll",)))
        self.assertEqual(dirty.status, "warning")
        self.assertIn("x.dll", dirty.detail)

    def test_safe_check_states(self):
        com = oc.ComResult(state="failed")
        self.assertEqual(oc._safe_check(oc.SafeResult(state="opened"), com).status, "warning")
        self.assertEqual(oc._safe_check(oc.SafeResult(state="failed"), com).status, "problem")
        self.assertEqual(oc._safe_check(oc.SafeResult(state="skipped"), com).status, "unknown")

    def test_integrity_check(self):
        self.assertEqual(oc._integrity_check((1, 2, "a"), (1, 2, "a")).status, "ok")
        self.assertEqual(oc._integrity_check((1, 2, "a"), (1, 2, "b")).status, "problem")

    def test_findings_are_actionable(self):
        com = oc.ComResult(state="failed", hresult=0x80004005, message="PowerPoint 发现内容有问题")
        items = oc._findings(com, oc.SafeResult(state="failed"), oc.EnvEvidence(state="ok"))
        self.assertTrue(items)
        for finding in items:
            self.assertIsInstance(finding, Finding)
            self.assertTrue(finding.title and finding.evidence and finding.action)
        head = items[0]
        self.assertIn("文件本身", head.title)


class EvidenceContractTest(unittest.TestCase):
    """每条规则都必须能被机器检查（沿用《All Code Is Slop》的纠错口径）。"""

    def test_dialog_families_are_priority_ordered(self):
        # 「无法打开…找不到…」必须先判 cannot_open，不能被 link 抢走
        self.assertEqual(oc.classify_dialog("PowerPoint 无法打开该文件，找不到链接的媒体。"), "cannot_open")
        self.assertEqual(oc.classify_dialog("该演示文稿受密码保护"), "password")
        self.assertEqual(oc.classify_dialog("此文件来自 Internet，处于受保护的视图"), "protected")
        self.assertEqual(oc.classify_dialog("完全不相干的文字"), "")

    def test_watcher_text_only_returns_failures(self):
        watcher = oc.DialogWatcher()
        watcher.seen = ["正在加载字体", "PowerPoint 无法打开该文件"]
        self.assertEqual(watcher.text(), "PowerPoint 无法打开该文件")
        self.assertEqual(watcher.family(), "cannot_open")
        watcher.seen = ["正在加载字体", "完成"]
        self.assertEqual(watcher.text(), "")
        self.assertEqual(watcher.family(), "")

    def test_com_probe_degrades_when_copy_fails(self):
        original = oc._copy_for_probe

        def boom(_deck):
            raise OSError("disk full")

        oc._copy_for_probe = boom
        try:
            with mock.patch.object(oc.sys, "platform", "win32"):
                result = oc.com_probe("whatever.pptx")
        finally:
            oc._copy_for_probe = original
        self.assertEqual(result.state, "aborted")
        self.assertIn("disk", result.message)
        # 复制失败不能说成「PowerPoint 打不开」
        check = oc._com_check(result)
        self.assertEqual(check.status, "unknown")
        self.assertIn("检测没能开始", check.detail)
        action = oc._findings(result, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))[0]
        self.assertNotIn("禁用加载项", action.action)
        self.assertIn("复制", action.action)

    def test_headline_and_action_agree(self):
        password = oc._findings(oc.ComResult(state="failed", message="该演示文稿受密码保护"),
                                oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertIn("密码", password[0].title)
        self.assertIn("密码", password[0].action)

        addin = oc._findings(oc.ComResult(state="failed", message="PowerPoint 无法打开该文件"),
                             oc.SafeResult(state="opened"), oc.EnvEvidence(state="ok"))
        self.assertIn("加载项", addin[0].title)
        self.assertIn("加载项", addin[0].action)

    def test_dialog_finding_is_not_duplicated(self):
        com = oc.ComResult(state="failed", message="PowerPoint 无法打开该文件", dialog="PowerPoint 无法打开该文件")
        items = oc._findings(com, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        titles = [item.title for item in items]
        self.assertEqual(len(titles), len(set(titles)))
        self.assertFalse(any(title.startswith("PowerPoint 提示") for title in titles))

    def test_fresh_resiliency_becomes_a_finding(self):
        com = oc.ComResult(state="ok", slides=10, expected=10)
        items = oc._findings(com, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"),
                             [("DisabledItems", r"C:\addons\bad.dll")])
        fresh = [item for item in items if "检测期间" in item.title]
        self.assertEqual(len(fresh), 1)
        self.assertIn("bad.dll", fresh[0].evidence)
        # 组件缺失时不能把别的程序造成的改动脉络说成「本次打开导致」
        blocked = oc._findings(oc.ComResult(state="blocked", message="缺组件"),
                               oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"),
                               [("DisabledItems", r"C:\addons\bad.dll")])
        self.assertFalse(any("检测期间" in item.title for item in blocked))

    def test_env_read_failure_is_unknown_not_ok(self):
        check = oc._env_check(oc.EnvEvidence(state="ok", note="读取事件日志失败：拒绝访问"))
        self.assertEqual(check.status, "unknown")
        self.assertIn("拒绝访问", check.detail)

    def test_timeout_verdict_uses_safe_mode(self):
        com = oc.ComResult(state="timeout")
        slow = oc.verdict(com, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertIn("超时", slow[0])
        broken = oc.verdict(com, oc.SafeResult(state="failed"), oc.EnvEvidence(state="ok"))
        self.assertIn("文件本身", broken[0])
        addin = oc.verdict(com, oc.SafeResult(state="opened"), oc.EnvEvidence(state="ok"))
        self.assertIn("加载项", addin[0])
        # 标题说加载项时，动作也必须是加载项
        finding = oc._findings(com, oc.SafeResult(state="opened"), oc.EnvEvidence(state="ok"))[0]
        self.assertIn("加载项", finding.action)

    def test_zero_slides_counts_as_lost(self):
        self.assertTrue(oc.quant_slides_lost(oc.ComResult(state="ok", slides=0, expected=10)))
        self.assertTrue(oc.quant_slides_lost(oc.ComResult(state="ok", slides=7, expected=10)))
        self.assertFalse(oc.quant_slides_lost(oc.ComResult(state="ok", slides=10, expected=10)))
        self.assertFalse(oc.quant_slides_lost(oc.ComResult(state="ok", slides=3, expected=0)))

    def test_damaged_dialog_text_is_a_failure(self):
        com = oc.ComResult(state="ok", slides=5, expected=5, dialog="文件已损坏，无法显示")
        result = oc.verdict(com, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertIn("失败提示", result[0])

    def test_resiliency_snapshot_is_not_truncated(self):
        blobs = [r"C:\addons\addon%d.dll" % index for index in range(12)]

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class FakeWinreg:
            HKEY_CURRENT_USER = "HKCU"

            def OpenKey(self, _root, _path):
                return FakeKey()

            def EnumValue(self, _key, index):
                if index >= len(blobs):
                    raise OSError("no more values")
                return (str(index), blobs[index].encode("utf-16-le"), 3)

            def QueryValueEx(self, _key, _name):
                raise OSError("missing value")

        with mock.patch.dict(sys.modules, {"winreg": FakeWinreg()}):
            with mock.patch.object(oc.sys, "platform", "win32"):
                snapshot = oc.resiliency_snapshot()
        self.assertGreaterEqual(len(snapshot["DisabledItems"]), 9)

    def test_shows_presentation_ignores_preexisting_processes(self):
        original = oc._windows
        oc._windows = lambda only_dialogs=True, processes=(): [
            (r"C:\tmp\probe-a.pptx - PowerPoint", [], 100),
        ]
        try:
            with mock.patch.object(oc.sys, "platform", "win32"):
                self.assertTrue(oc._shows_presentation("probe-a.pptx"))
                self.assertFalse(oc._shows_presentation("probe-a.pptx", {100}))
                self.assertFalse(oc._shows_presentation("other.pptx"))
        finally:
            oc._windows = original

    def test_subkey_strings_enumerate_registry_blobs(self):
        blob = r"C:\addons\bad.dll".encode("utf-16-le")

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class FakeRegistry:
            HKEY_CURRENT_USER = "HKCU"

            def OpenKey(self, _root, _path):
                return FakeKey()

            def EnumValue(self, _key, index):
                if index:
                    raise OSError("no more values")
                return ("0", blob, 3)

        self.assertEqual(oc._subkey_strings(FakeRegistry(), "any"), [r"C:\addons\bad.dll"])


class InspectOpenTest(unittest.TestCase):
    def test_missing_file_is_reported_without_raising(self):
        report = oc.inspect_open("/nonexistent/课件.pptx")
        self.assertIsInstance(report, Report)
        self.assertEqual(report.checks[0].status, "problem")

    def test_wrong_extension_is_unknown(self):
        report = oc.inspect_open("/tmp/x.doc")
        self.assertEqual(report.kind, "open")
        self.assertEqual(report.checks[0].status, "unknown")

    @unittest.skipIf(sys.platform == "win32", "非 Windows 上才走 skipped 分支")
    def test_non_windows_returns_unknown_not_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "a.pptx"
            deck.write_bytes(b"PK\x03\x04 not a real deck")
            report = oc.inspect_open(deck, safe=False, env=False)
        self.assertEqual(report.kind, "open")
        self.assertEqual(report.checks[0].status, "unknown")
        self.assertIn("Windows", report.checks[0].detail)

    @mock.patch.object(oc, "com_probe", return_value=oc.ComResult(state="failed"))
    def test_all_four_checks_are_always_present(self, _probe):
        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "a.pptx"
            deck.write_bytes(b"PK\x03\x04 deck")
            report = oc.inspect_open(deck, safe=False, env=False)
        self.assertEqual([check.key for check in report.checks],
                         ["open_com", "open_env", "open_safe", "open_integrity"])
        self.assertEqual(report.checks[1].status, "unknown")
        self.assertIn("未做", report.checks[1].detail)
        self.assertIn("本次未做安全模式对比", report.checks[2].detail)

    @mock.patch.object(oc, "com_probe", return_value=oc.ComResult(state="failed"))
    def test_open_report_reaches_ai_with_its_own_context(self, _probe):
        from evetech import ai
        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "a.pptx"
            deck.write_bytes(b"PK\x03\x04 deck")
            report = oc.inspect_open(deck, safe=False, env=False)
        payload = ai.evidence(report, "none", "none")
        self.assertEqual(payload["diagnosis"], SYMPTOMS["open"])
        keys = [item["key"] for item in payload["checks"]]
        self.assertIn("open_com", keys)
        self.assertEqual(keys, ["open_com", "open_env", "open_safe", "open_integrity"])

    def test_early_exit_reports_still_carry_open_context(self):
        from evetech import ai
        for path in ("/tmp/x.doc", "/nonexistent/课件.pptx"):
            payload = ai.evidence(oc.inspect_open(path), "none", "none")
            with self.subTest(path=path):
                self.assertEqual(payload["diagnosis"], SYMPTOMS["open"])
                self.assertEqual([item["key"] for item in payload["checks"]], ["open_deck"])

    def test_lost_slides_are_explained_in_detail(self):
        check = oc._com_check(oc.ComResult(state="ok", slides=0, expected=10))
        self.assertEqual(check.status, "problem")
        self.assertIn("少了 10 页", check.detail)

    def test_expected_slides_counts_parts(self):
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "a.pptx"
            with zipfile.ZipFile(deck, "w") as archive:
                archive.writestr("ppt/slides/slide1.xml", "<x/>")
                archive.writestr("ppt/slides/slide2.xml", "<x/>")
                archive.writestr("ppt/slides/_rels/slide1.xml.rels", "<x/>")
            self.assertEqual(oc.expected_slides(deck), 2)
            self.assertEqual(oc.expected_slides(Path(folder) / "missing.pptx"), 0)


class InvariantTest(unittest.TestCase):
    def test_copy_for_probe_leaves_original_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "课件.pptx"
            deck.write_bytes(b"PK\x03\x04payload")
            before = oc.file_signature(deck)
            copy, workdir = oc._copy_for_probe(deck)
            try:
                self.assertTrue(copy.is_file())
                self.assertNotEqual(copy.parent, deck.parent)
                self.assertEqual(oc.file_signature(deck), before)
                copy.write_bytes(b"changed")
                self.assertEqual(oc.file_signature(deck), before)
            finally:
                import shutil
                shutil.rmtree(workdir, ignore_errors=True)

    def test_signature_changes_when_file_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "a.pptx"
            deck.write_bytes(b"one")
            first = oc.file_signature(deck)
            deck.write_bytes(b"two")
            self.assertNotEqual(oc.file_signature(deck)[2], first[2])


class MissingComponentTest(unittest.TestCase):
    """真机实测（CF-002）：缺 pywin32 时不能把好课件报成「打不开」。"""

    def _direct_without_pythoncom(self):
        watcher = mock.Mock()
        watcher.text.return_value = ""
        with mock.patch.dict(sys.modules, {"pythoncom": None}):
            return oc._com_open_direct("x.pptx", 3, watcher)

    def test_import_error_is_blocked_not_failed(self):
        result = self._direct_without_pythoncom()
        self.assertEqual(result.state, "blocked")
        self.assertIn("pythoncom", result.message)
        self.assertIsNone(oc.verdict(result, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok")))

    def test_blocked_check_is_unknown_and_never_says_will_not_open(self):
        result = oc.ComResult(state="blocked", message="检测组件缺失（No module named 'pythoncom'）")
        check = oc._com_check(result)
        self.assertEqual(check.status, "unknown")
        self.assertIn("不是课件的问题", check.detail)
        self.assertNotIn("打开失败", check.detail)

    def test_blocked_produces_one_actionable_finding(self):
        result = oc.ComResult(state="blocked", message="检测组件缺失（No module named 'pythoncom'）",
                              expected=3)
        findings = oc._findings(result, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertEqual(len(findings), 1)
        self.assertIn("组件缺失", findings[0].title)
        self.assertIn("pywin32", findings[0].action)
        # 绝不能落到「本机能打开」或「页数变少」
        self.assertNotIn("能正常打开", findings[0].title)
        self.assertNotIn("丢失", findings[0].title)

    def test_blocked_does_not_blame_the_deck_for_a_dialog(self):
        """组件缺失时若桌面上恰好有别人的 PowerPoint 对话框，不能算到这份课件头上。"""
        result = oc.ComResult(state="blocked", message="检测组件缺失",
                              dialog="PowerPoint 无法读取这个文件", expected=3)
        findings = oc._findings(result, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertEqual([item.title for item in findings], ["检测组件缺失，本次没能验证课件"])

    def test_blocked_still_reports_environment_evidence(self):
        result = oc.ComResult(state="blocked", message="检测组件缺失", expected=3)
        findings = oc._findings(result, oc.SafeResult(state="skipped"),
                                oc.EnvEvidence(state="ok", disabled=("addon.dll",)))
        titles = [item.title for item in findings]
        self.assertIn("检测组件缺失，本次没能验证课件", titles)
        self.assertTrue(any("禁用加载项" in title for title in titles))

    def test_blocked_does_not_run_safe_mode_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "a.pptx"
            deck.write_bytes(b"PK\x03\x04deck")
            with mock.patch.object(oc, "com_probe",
                                   return_value=oc.ComResult(state="blocked", message="缺组件", expected=1)), \
                    mock.patch.object(oc, "safe_mode_probe",
                                      side_effect=AssertionError("blocked 时不该再开 PowerPoint")):
                report = oc.inspect_open(deck, safe=True, env=False)
        safe_check = [check for check in report.checks if check.key == "open_safe"][0]
        self.assertEqual(safe_check.status, "unknown")


class SafeModeTargetTest(unittest.TestCase):
    """WPS 会接管 POWERPNT.EXE 与 COM 入口，安全模式对比对它不成立。"""

    @classmethod
    def setUpClass(cls):
        __import__("psutil")   # 先按真实平台加载：下面会临时把全局 sys.platform 改成 win32

    def test_wps_com_server_is_rejected(self):
        with mock.patch.object(oc.sys, "platform", "win32"), \
                mock.patch("evetech.preflight.powerpnt_path", return_value=None), \
                mock.patch("evetech.preflight.com_server_path",
                           return_value=Path("C:/Kingsoft/WPS Office/office6/wps.exe")):
            self.assertEqual(oc.powerpoint_exe(), "")

    def test_com_server_must_really_be_powerpnt(self):
        """LocalServer32 里注册什么都可能，不能拿别的 exe 去跑 /safe。"""
        with tempfile.TemporaryDirectory() as folder:
            other = Path(folder) / "SomeTool.exe"
            other.write_bytes(b"MZ")
            with mock.patch.object(oc.sys, "platform", "win32"), \
                    mock.patch("evetech.preflight.powerpnt_path", return_value=None), \
                    mock.patch("evetech.preflight.com_server_path", return_value=other):
                self.assertEqual(oc.powerpoint_exe(), "")

    def test_wps_substring_in_a_real_office_path_is_not_wps(self):
        """…\\WPSHARE_Backup\\Office16\\POWERPNT.EXE 是真 PowerPoint，不能因为路径含 wps 被判成 WPS。"""
        self.assertFalse(oc._looks_like_wps(Path("C:/Tools/WPSHARE_Backup/Office16/POWERPNT.EXE")))
        self.assertTrue(oc._looks_like_wps(Path("C:/Kingsoft/WPS Office/office6/wpp.exe")))
        self.assertTrue(oc._looks_like_wps(Path("C:/somewhere/wps.exe")))

    def test_real_powerpoint_com_server_is_used(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "POWERPNT.EXE"
            target.write_bytes(b"MZ")
            with mock.patch.object(oc.sys, "platform", "win32"), \
                    mock.patch("evetech.preflight.powerpnt_path", return_value=target), \
                    mock.patch("evetech.preflight.com_server_path", return_value=None):
                self.assertEqual(oc.powerpoint_exe(), str(target))

    def test_only_wps_skips_instead_of_waiting(self):
        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "a.pptx"
            deck.write_bytes(b"PK\x03\x04deck")
            with mock.patch.object(oc.sys, "platform", "win32"), \
                    mock.patch.object(oc, "powerpoint_exe", return_value=""), \
                    mock.patch.object(oc, "_wps_present", return_value=True), \
                    mock.patch("subprocess.Popen", side_effect=AssertionError("不该启动进程")):
                result = oc.safe_mode_probe(deck, seconds=25)
        self.assertEqual(result.state, "skipped")
        self.assertIn("WPS", result.dialog)
        self.assertLess(result.seconds, 1.0)

    def test_process_that_exits_immediately_stops_the_wait(self):
        class FakeProcess:
            returncode = 1

            def poll(self):
                return 1

            def terminate(self):
                pass

        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "a.pptx"
            deck.write_bytes(b"PK\x03\x04deck")
            with mock.patch.object(oc.sys, "platform", "win32"), \
                    mock.patch.object(oc, "powerpoint_exe", return_value="C:/Office/POWERPNT.EXE"), \
                    mock.patch.object(oc, "_powerpnt_pids", return_value=set()), \
                    mock.patch.object(oc, "_shows_presentation", return_value=False), \
                    mock.patch("subprocess.Popen", return_value=FakeProcess()):
                result = oc.safe_mode_probe(deck, seconds=25)
        self.assertEqual(result.state, "skipped")
        self.assertIn("立即退出", result.dialog)
        self.assertLess(result.seconds, 3.0)


class ComVendorTest(unittest.TestCase):
    """WPS 会接管 PowerPoint.Application，结论必须注明是谁给的。"""

    def _open_with(self, app_path):
        import types

        class FakeComError(Exception):
            pass

        class FakeApp:
            Path = app_path
            DisplayAlerts = 0

            class Presentations:
                @staticmethod
                def Open(*_args):
                    raise FakeComError("无法打开该文件")

        pythoncom = types.ModuleType("pythoncom")
        pythoncom.com_error = FakeComError
        pythoncom.CoInitialize = lambda: None
        pythoncom.CoUninitialize = lambda: None
        win32com = types.ModuleType("win32com")
        client = types.ModuleType("win32com.client")
        client.DispatchEx = lambda _name: FakeApp()
        win32com.client = client
        watcher = mock.Mock()
        watcher.text.return_value = ""
        with mock.patch.dict(sys.modules, {"pythoncom": pythoncom,
                                           "win32com": win32com,
                                           "win32com.client": client}):
            return oc._com_open_direct("x.pptx", 3, watcher)

    def test_wps_answer_is_labelled(self):
        result = self._open_with("C:/Kingsoft/WPS Office/office6")
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.vendor, "wps")
        check = oc._com_check(result)
        self.assertIn("WPS", check.detail)

    def test_real_office_answer_is_not_labelled(self):
        result = self._open_with("C:/Program Files/Microsoft Office/root/Office16")
        self.assertEqual(result.vendor, "")
        self.assertNotIn("WPS", oc._com_check(result).detail)

    def test_wps_failure_is_not_reported_as_a_broken_deck(self):
        """WPS 打不开 ≠ 课件坏了：真 PowerPoint 可能能打开，不能下「文件损坏」的结论。"""
        com = oc.ComResult(state="failed", message="无法读取", hresult=0x80004005,
                           expected=5, vendor="wps")
        combined = oc.verdict(com, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertIn("WPS", combined[0])
        self.assertNotIn("文件损坏", combined[0])
        self.assertEqual(oc._com_check(com).status, "warning")

    def test_wps_success_is_not_reported_as_powerpoint(self):
        com = oc.ComResult(state="ok", slides=5, expected=5, vendor="wps")
        combined = oc.verdict(com, oc.SafeResult(state="skipped"), oc.EnvEvidence(state="ok"))
        self.assertIn("WPS", combined[0])
        self.assertNotIn("本机 PowerPoint 能正常打开", combined[0])


if __name__ == "__main__":
    unittest.main()
