"""更新模块的离线测试：不联网、不依赖 Qt。"""

import hashlib
import inspect
import io
import json
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evetech import VERSION
from evetech.update import (PACKAGE_META, UpdateError, check_url, cleanup_scripts, download,
                             fetch_manifest, install_root, is_newer, last_update_failure,
                             launch_updater, local_package_info, package_root, parse_manifest,
                             sha256_of, stage, stale_scripts, unpack, updater_script, version_tuple)

_parts = (version_tuple(VERSION) + (0, 0, 0))[:3]
# 测试里要验证「有新版可装」，而 VERSION 会随发版变化，这里派生一个永远更高的版本号
NEW = "%d.%d.%d" % (_parts[0], _parts[1], _parts[2] + 1)
PAYLOAD = b"evetech-test-payload" * 4096
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class VersionTests(unittest.TestCase):
    def test_tuple(self):
        self.assertEqual(version_tuple("1.2.0"), (1, 2, 0))
        self.assertEqual(version_tuple("v1.2.0-beta"), (1, 2, 0))
        self.assertEqual(version_tuple("2"), (2,))

    def test_newer(self):
        self.assertTrue(is_newer("1.2.0", "1.1.7"))
        self.assertTrue(is_newer("1.10.0", "1.9.9"))
        self.assertFalse(is_newer("1.1.7", "1.1.7"))
        self.assertFalse(is_newer("1.1.6", "1.1.7"))
        # 1.2 与 1.2.0 视为同一版本
        self.assertFalse(is_newer("1.2", "1.2.0"))

    def test_bad_version(self):
        with self.assertRaises(UpdateError):
            version_tuple("")


class ManifestTests(unittest.TestCase):
    def payload(self, **overrides):
        data = {"version": NEW, "url": "https://aiclass.openplays.cn/classfix/EveTech-%s.zip" % NEW,
                "sha256": DIGEST, "size": len(PAYLOAD), "notes": ["第一条", "第二条"]}
        data.update(overrides)
        return json.dumps(data, ensure_ascii=False)

    def test_ok(self):
        manifest = parse_manifest(self.payload())
        self.assertEqual(manifest["version"], NEW)
        self.assertTrue(manifest["available"])
        self.assertIn("第一条", manifest["notes"])

    def test_same_version_not_available(self):
        self.assertFalse(parse_manifest(self.payload(version=VERSION))["available"])

    def test_missing_fields(self):
        for field in ("version", "url", "sha256"):
            data = json.loads(self.payload())
            data.pop(field)
            with self.assertRaises(UpdateError):
                parse_manifest(json.dumps(data))

    def test_bad_digest_and_size(self):
        with self.assertRaises(UpdateError):
            parse_manifest(self.payload(sha256="abc"))
        with self.assertRaises(UpdateError):
            parse_manifest(self.payload(size=0))
        with self.assertRaises(UpdateError):
            parse_manifest(self.payload(size=10 ** 12))

    def test_plain_http_rejected(self):
        with self.assertRaises(UpdateError):
            parse_manifest(self.payload(url="http://example.com/a.zip"))

    def test_check_url(self):
        self.assertEqual(check_url("https://a.example/x.zip"), "https://a.example/x.zip")
        self.assertEqual(check_url("http://127.0.0.1:8080/x.zip"), "http://127.0.0.1:8080/x.zip")
        for bad in ("http://a.example/x.zip", "ftp://a.example/x.zip", "", "https://"):
            with self.assertRaises(UpdateError):
                check_url(bad)


class _Handler(BaseHTTPRequestHandler):
    body = PAYLOAD
    status = 200

    def do_GET(self):
        if self.path.endswith("update.json"):
            data = json.dumps({"version": NEW, "url": "http://127.0.0.1:%d/pkg.zip" % self.server.server_port,
                               "sha256": DIGEST, "size": len(PAYLOAD), "notes": "note"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_fetch_manifest(self):
        manifest = fetch_manifest(self.base + "/update.json")
        self.assertEqual(manifest["version"], NEW)
        self.assertTrue(manifest["available"])

    def test_fetch_manifest_http_error(self):
        _Handler.status = 500
        try:
            with self.assertRaises(UpdateError):
                fetch_manifest(self.base + "/broken.bin")
        finally:
            _Handler.status = 200

    def test_download_ok(self):
        seen = []
        target = download(self.base + "/pkg.zip", self.root / "a.zip", DIGEST, len(PAYLOAD),
                          on_progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertTrue(seen and seen[-1][0] == len(PAYLOAD))
        self.assertFalse((self.root / "a.zip.part").exists())

    def test_download_digest_mismatch(self):
        with self.assertRaises(UpdateError):
            download(self.base + "/pkg.zip", self.root / "b.zip", "0" * 64, len(PAYLOAD))
        self.assertFalse((self.root / "b.zip").exists())
        self.assertFalse((self.root / "b.zip.part").exists())

    def test_download_size_mismatch(self):
        with self.assertRaises(UpdateError):
            download(self.base + "/pkg.zip", self.root / "c.zip", DIGEST, len(PAYLOAD) + 10)


def build_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_unpack_and_root(self):
        archive = build_zip(self.root / "p.zip", {
            "EveTech/EveTech.exe": b"exe",
            "EveTech/_internal/lib.dll": b"dll",
        })
        target = unpack(archive, self.root / "out")
        self.assertTrue((target / "EveTech" / "_internal" / "lib.dll").is_file())
        self.assertEqual(package_root(target), target / "EveTech")

    def test_unpack_rejects_traversal(self):
        archive = build_zip(self.root / "bad.zip", {"../evil.txt": b"x"})
        with self.assertRaises(UpdateError):
            unpack(archive, self.root / "out")

    def test_unpack_rejects_absolute(self):
        archive = build_zip(self.root / "abs.zip", {"/etc/evil": b"x"})
        with self.assertRaises(UpdateError):
            unpack(archive, self.root / "out")

    def test_unpack_rejects_symlink(self):
        archive = self.root / "link.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            info = zipfile.ZipInfo("EveTech/link")
            info.external_attr = (0o120777 << 16)
            handle.writestr(info, "target")
        with self.assertRaises(UpdateError):
            unpack(archive, self.root / "out")

    def test_package_root_missing(self):
        target = self.root / "empty"
        target.mkdir()
        self.assertIsNone(package_root(target))

    def test_local_package_info(self):
        archive = build_zip(self.root / ("EveTech-%s-Windows-x64.zip" % NEW), {
            PACKAGE_META: json.dumps({"version": NEW, "notes": "离线包"}, ensure_ascii=False).encode(),
            "EveTech/EveTech.exe": b"exe",
        })
        info = local_package_info(archive)
        self.assertEqual(info["version"], NEW)
        self.assertTrue(info["available"])
        self.assertEqual(info["sha256"], sha256_of(archive))

    def test_local_package_version_from_name(self):
        archive = build_zip(self.root / "EveTech-1.3.1-Windows-x64.zip", {"EveTech/EveTech.exe": b"exe"})
        self.assertEqual(local_package_info(archive)["version"], "1.3.1")

    def test_stage_writes_script(self):
        archive = build_zip(self.root / "EveTech-1.2.0.zip", {"EveTech/EveTech.exe": b"exe"})
        install = self.root / "EveTech"
        install.mkdir()
        inner, script = stage(archive, NEW, root=install)
        self.assertTrue((inner / "EveTech.exe").is_file())
        self.assertTrue(script.is_file())
        self.assertEqual(script.parent, self.root)

    def test_updater_script_content(self):
        script = updater_script(r"C:\Games\EveTech.update", r"C:\Games\EveTech",
                                self.root / "up.bat", log=self.root / "up.log")
        text = script.read_text(encoding="utf-8")
        self.assertIn("robocopy", text)
        self.assertIn("/E", text)
        self.assertIn("tasklist", text)
        self.assertIn('start "" /D "%DST%" "%DST%\\%EXE%"', text)
        self.assertIn('set "DST=C:\\Games\\EveTech"', text)
        self.assertIn("(goto) 2>nul & del", text)
        self.assertIn(b"\r\n", script.read_bytes())   # 批处理必须是 CRLF（按字节判断，read_text 会规范化换行）
        self.assertIn("ping -n 2 127.0.0.1", text)
        self.assertNotIn("@@", text)          # 占位符必须全部替换

    def test_updater_script_never_uses_timeout(self):
        """timeout 在无控制台（stdin 已重定向）时立即失败，会让替换逻辑形同虚设。

        2026-11-18 的真实故障：1.3→1.4 自动更新一律中止，用户得手动双击 bat。
        """
        script = updater_script(r"C:\Games\EveTech.update", r"C:\Games\EveTech",
                                self.root / "up.bat", log=self.root / "up.log")
        text = script.read_text(encoding="utf-8")
        self.assertNotIn("timeout /t", text)
        self.assertIn("ping -n", text)

    def test_updater_script_checks_exe_before_restart(self):
        script = updater_script(r"C:\Games\EveTech.update", r"C:\Games\EveTech",
                                self.root / "up.bat", log=self.root / "up.log")
        text = script.read_text(encoding="utf-8")
        self.assertIn('if not exist "%DST%\\%EXE%"', text)
        self.assertLess(text.index("robocopy"), text.index('start "" /D'))

    def test_last_update_failure_reads_log_tail(self):
        root = self.root / "EveTech"
        root.mkdir(exist_ok=True)
        log = self.root / "EveTech-update.log"
        log.write_text("[x] update start\n[y] old process still running, update aborted\n", encoding="utf-8")
        message = last_update_failure(root, within_hours=48)
        self.assertIsNotNone(message)
        self.assertIn("没有完成", message)
        log.write_text("[x] update start\n[y] update done\n", encoding="utf-8")
        self.assertIsNone(last_update_failure(root, within_hours=48))

    def test_launch_updater_hides_console(self):
        """必须用 CREATE_NO_WINDOW，不能用 DETACHED_PROCESS。

        2026-11-18 用户报的「find… 弹窗一直显示」就是 DETACHED_PROCESS 让 cmd.exe
        自己 AllocConsole 出来的可见窗口（标题是 tasklist | find 那条命令）。
        """
        source = Path(inspect.getsourcefile(launch_updater)).read_text(encoding="utf-8")
        body = source[source.index("def launch_updater"):]
        body = body[:body.index("\ndef ", 1)]
        self.assertIn("CREATE_NO_WINDOW", body)
        self.assertNotIn("flags |= getattr(subprocess, \"DETACHED_PROCESS\"", body)

    def test_stale_scripts_are_cleaned(self):
        """残留替换脚本要能被找出来并删掉（否则用户会当成安装包双击）。"""
        root = self.root / "EveTech"
        root.mkdir(exist_ok=True)
        first = self.root / "EveTech-update-1.4.0.bat"
        second = self.root / "EveTech-update-1.4.1.bat"
        for path in (first, second):
            path.write_text("@echo off\n", encoding="utf-8")
        self.assertEqual(len(stale_scripts(root)), 2)
        removed = cleanup_scripts(root)
        self.assertEqual(len(removed), 2)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_install_root_in_source_mode(self):
        self.assertTrue((install_root() / "evetech").is_dir())


if __name__ == "__main__":
    unittest.main()
