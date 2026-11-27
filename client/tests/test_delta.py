"""差异更新的判定与落地测试。

回归背景（1.3.2 及更早）：更新只有「下 67 MB 全包」一条路。实测跨 6 个版本，
解压后 177 MB 里 199 个文件 / 92.3 MB 完全没变（Qt 与 Python 运行时），
真正变化的只有 ClassFix.exe 与 base_library.zip，约 3.8 MB。

本文件守住三条红线：
1. 运行时指纹不一致（换了 Python / PySide6）绝不能走差异包——否则 dll 与 pyd 混搭一启动就崩；
2. 差异包偏大时按阈值回落全量；
3. 差异包内容被篡改、或本机目录残缺时，拒绝安装并清掉暂存目录。
"""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from classfix import delta
from classfix import update as update_module

RUNTIME = {"python": "3.12", "pyside6": "6.8.3", "arch": "x64", "onedir": True}


def make_patch_zip(path, files, keep=None, deletes=None, from_version="1.3.2", to_version="1.4.0",
                   runtime=None):
    """造一个结构真实的差异包：patch.json + ClassFix/<变更文件>。"""
    meta = {
        "from": from_version,
        "to": to_version,
        "runtime": RUNTIME if runtime is None else runtime,
        "files": {name: {"size": len(data), "sha256": delta._sha256_bytes(data)}
                  for name, data in files.items()},
        "keep": keep or {},
        "deletes": deletes or [],
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("patch.json", json.dumps(meta, ensure_ascii=False))
        for name, data in files.items():
            archive.writestr("ClassFix/" + name, data)
    return meta


def make_install(root, version="1.3.2", runtime=None, files=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    payload = {"version": version, "runtime": RUNTIME if runtime is None else runtime}
    (root / "classfix-package.json").write_text(json.dumps(payload), encoding="utf-8")
    for name, data in (files or {}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


class RuntimeFingerprintTest(unittest.TestCase):
    def test_identical_fingerprint_passes(self):
        self.assertEqual(delta.runtime_problem(dict(RUNTIME), dict(RUNTIME)), "")

    def test_pyside_upgrade_blocks_delta(self):
        local = dict(RUNTIME, pyside6="6.8.0")
        self.assertIn("PySide6", delta.runtime_problem(local, dict(RUNTIME)))

    def test_python_upgrade_blocks_delta(self):
        self.assertIn("Python", delta.runtime_problem(dict(RUNTIME, python="3.11"), dict(RUNTIME)))

    def test_missing_local_runtime_blocks_delta(self):
        self.assertTrue(delta.runtime_problem({}, dict(RUNTIME)))

    def test_missing_declared_runtime_blocks_delta(self):
        self.assertTrue(delta.runtime_problem(dict(RUNTIME), {}))


class ProcessRuntimeTest(unittest.TestCase):
    def test_infers_python_and_arch(self):
        runtime = delta.runtime_from_process()
        self.assertIn(".", runtime["python"])
        self.assertIn(runtime["arch"], ("x64", "x86"))
        self.assertIn("pyside6", runtime)

    def test_local_runtime_falls_back_to_process_when_package_has_no_record(self):
        # 1.4.0 之前的安装包没有 runtime 字段，必须靠进程推断，否则差异更新永远走全量
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            (root / "classfix-package.json").write_text(json.dumps({"version": "1.3.2"}), encoding="utf-8")
            runtime = delta.local_runtime(root)
        self.assertEqual(runtime["python"], delta.runtime_from_process()["python"])

    def test_package_record_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install", runtime=dict(RUNTIME))
            self.assertEqual(delta.local_runtime(root), RUNTIME)


class SizeGateTest(unittest.TestCase):
    def test_small_patch_is_fine(self):
        self.assertFalse(delta.delta_too_big(3_800_000, 67_759_033))

    def test_half_of_full_package_is_too_big(self):
        self.assertTrue(delta.delta_too_big(40_000_000, 67_759_033))

    def test_absolute_cap(self):
        self.assertTrue(delta.delta_too_big(delta.MAX_DELTA_BYTES + 1, 400 * 1024 * 1024))

    def test_zero_size_is_rejected(self):
        self.assertTrue(delta.delta_too_big(0, 67_759_033))


class ChooseTest(unittest.TestCase):
    def manifest(self, **patch):
        entry = {"from": "1.3.2", "url": "https://x/p.zip", "sha256": "a" * 64,
                 "size": 3_800_000, "runtime": dict(RUNTIME)}
        entry.update(patch)
        return {"version": "1.4.0", "url": "https://x/full.zip", "sha256": "b" * 64,
                "size": 67_759_033, "patches": [entry]}

    def test_picks_patch_when_everything_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install")
            plan = delta.choose(self.manifest(), root, current_version="1.3.2")
        self.assertEqual(plan["mode"], "patch")
        self.assertIn("MB", plan["reason"])

    def test_falls_back_without_patches(self):
        manifest = self.manifest()
        manifest["patches"] = []
        with tempfile.TemporaryDirectory() as tmp:
            plan = delta.choose(manifest, make_install(Path(tmp) / "install"), current_version="1.3.2")
        self.assertEqual(plan["mode"], "full")
        self.assertIn("没有差异包", plan["reason"])

    def test_falls_back_when_from_version_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = delta.choose(self.manifest(), make_install(Path(tmp) / "install"),
                                current_version="1.1.7")
        self.assertEqual(plan["mode"], "full")
        self.assertIn("1.1.7", plan["reason"])

    def test_falls_back_when_runtime_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install", runtime=dict(RUNTIME, pyside6="6.9.0"))
            plan = delta.choose(self.manifest(), root, current_version="1.3.2")
        self.assertEqual(plan["mode"], "full")
        self.assertIn("PySide6", plan["reason"])

    def test_falls_back_when_patch_too_big(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install")
            plan = delta.choose(self.manifest(size=50_000_000), root, current_version="1.3.2")
        self.assertEqual(plan["mode"], "full")
        self.assertIn("偏大", plan["reason"])


class PatchMetaTest(unittest.TestCase):
    def test_reads_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.zip"
            make_patch_zip(path, {"ClassFix.exe": b"new"})
            meta = delta.read_patch_meta(path)
            self.assertEqual(meta["to"], "1.4.0")

    def test_missing_meta_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("ClassFix/ClassFix.exe", b"x")
            with self.assertRaises(ValueError):
                delta.read_patch_meta(path)


class VerifyPayloadTest(unittest.TestCase):
    def test_payload_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.zip"
            meta = make_patch_zip(path, {"ClassFix.exe": b"new-bytes"})
            payload = Path(tmp) / "payload"
            (payload / "ClassFix").mkdir(parents=True)
            (payload / "ClassFix" / "ClassFix.exe").write_bytes(b"new-bytes")
            ok, problems = delta.verify_payload(payload / "ClassFix", meta)
        self.assertTrue(ok, problems)

    def test_tampered_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = make_patch_zip(Path(tmp) / "p.zip", {"ClassFix.exe": b"new-bytes"})
            payload = Path(tmp) / "payload" / "ClassFix"
            payload.mkdir(parents=True)
            (payload / "ClassFix.exe").write_bytes(b"tampered")
            ok, problems = delta.verify_payload(payload, meta)
        self.assertFalse(ok)
        self.assertTrue(any("ClassFix.exe" in item for item in problems))


class KeepTest(unittest.TestCase):
    def test_missing_files_within_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install", files={"a.dll": b"1" * 10})
            ok, missing = delta.check_keep(root, {"keep": {"a.dll": 10}})
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_too_many_missing_files_blocks_install(self):
        keep = {"f%d.dll" % i: 10 for i in range(delta.MAX_KEEP_MISSING + 2)}
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install")
            ok, missing = delta.check_keep(root, {"keep": keep})
        self.assertFalse(ok)
        self.assertTrue(missing)

    def test_size_mismatch_counts_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install", files={"a.dll": b"1234567890"})
            ok, missing = delta.check_keep(root, {"keep": {"a.dll": 1}})
        self.assertFalse(ok)


class DeletePathTest(unittest.TestCase):
    def test_filters_unsafe_paths(self):
        paths = delta.delete_paths({"deletes": ["old/a.dll", "../evil.dll", "/abs.dll",
                                                "C:/x.dll", "old\\b.dll", ""]})
        self.assertEqual(paths, ["old/a.dll", "old/b.dll"])


class ManifestScanTest(unittest.TestCase):
    def test_skips_update_cache_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.dll").write_bytes(b"1")
            (root / "update").mkdir()
            (root / "update" / "ClassFix-1.4.0-Windows-x64.zip").write_bytes(b"2")
            (root / "ClassFix-update.log").write_bytes(b"3")
            names = set(delta.file_manifest(root))
        self.assertEqual(names, {"a.dll"})


class UpdaterScriptTest(unittest.TestCase):
    def test_delete_lines_are_written_for_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "up.bat"
            update_module.updater_script(Path(tmp) / "src", Path(tmp) / "dst", script,
                                          log=Path(tmp) / "log.txt",
                                          deletes=["old/a.dll", "../evil.dll"])
            text = script.read_text(encoding="utf-8")
        self.assertIn("del /f /q", text)
        self.assertIn("old\\a.dll", text)
        self.assertNotIn("evil.dll", text)

    def test_full_package_script_has_no_delete_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "up.bat"
            update_module.updater_script(Path(tmp) / "src", Path(tmp) / "dst", script,
                                          log=Path(tmp) / "log.txt")
            text = script.read_text(encoding="utf-8")
        self.assertNotIn("del /f /q", text)


class StagePatchTest(unittest.TestCase):
    def stage(self, tmp, files=None, **patch_kwargs):
        root = make_install(Path(tmp) / "install",
                            files={"_internal/Qt6Core.dll": b"q" * 32})
        patch = Path(tmp) / "p.zip"
        meta = make_patch_zip(patch, files or {"ClassFix.exe": b"new-exe"},
                              keep={"_internal/Qt6Core.dll": 32}, deletes=["old/gone.dll"],
                              **patch_kwargs)
        inner, script = update_module.stage_patch(patch, "1.4.0", root=root, current_version="1.3.2")
        return root, inner, script, meta

    def test_stages_patch_and_writes_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, inner, script, meta = self.stage(tmp)
            self.assertTrue((inner / "ClassFix.exe").is_file())
            text = script.read_text(encoding="utf-8")
            self.assertIn("old\\gone.dll", text)
            self.assertIn("ClassFix.exe", text)

    def test_wrong_from_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install")
            patch = Path(tmp) / "p.zip"
            make_patch_zip(patch, {"ClassFix.exe": b"x"}, from_version="1.0.0")
            with self.assertRaises(update_module.UpdateError):
                update_module.stage_patch(patch, "1.4.0", root=root, current_version="1.3.2")

    def test_tampered_patch_is_rejected_and_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install",
                               files={"_internal/Qt6Core.dll": b"q" * 32})
            patch = Path(tmp) / "p.zip"
            make_patch_zip(patch, {"ClassFix.exe": b"right"}, keep={"_internal/Qt6Core.dll": 32})
            # 重新打包：patch.json 里记的哈希还是 right，实际内容被换掉
            with zipfile.ZipFile(patch) as archive:
                items = {name: archive.read(name) for name in archive.namelist()}
            items["ClassFix/ClassFix.exe"] = b"tampered"
            with zipfile.ZipFile(patch, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in items.items():
                    archive.writestr(name, data)
            with self.assertRaises(update_module.UpdateError):
                update_module.stage_patch(patch, "1.4.0", root=root, current_version="1.3.2")
            self.assertFalse(update_module.staging_directory(root).exists())

    def test_missing_keep_files_blocks_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_install(Path(tmp) / "install")  # 故意不放 Qt6Core.dll
            patch = Path(tmp) / "p.zip"
            make_patch_zip(patch, {"ClassFix.exe": b"x"}, keep={"_internal/Qt6Core.dll": 32})
            with self.assertRaises(update_module.UpdateError) as ctx:
                update_module.stage_patch(patch, "1.4.0", root=root, current_version="1.3.2")
            self.assertIn("不完整", str(ctx.exception))


class LocalPackageInfoTest(unittest.TestCase):
    """离线「从文件更新」也要能认增量包，否则老师从 U 盘拷了增量包却装不上。"""

    def test_patch_package_is_recognized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ClassFix-1.3.2-to-1.4.0.patch.zip"
            make_patch_zip(path, {"ClassFix.exe": b"new"}, from_version="1.3.2", to_version="1.4.0")
            info = update_module.local_package_info(path, current_version="1.3.2")
        self.assertTrue(info["patch"])
        self.assertEqual(info["version"], "1.4.0")
        self.assertEqual(info["from"], "1.3.2")
        self.assertTrue(info["available"])

    def test_full_package_is_not_marked_as_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ClassFix-1.4.0-Windows-x64.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("ClassFix/ClassFix.exe", b"exe")
                archive.writestr("ClassFix/classfix-package.json", json.dumps({"version": "1.4.0"}))
            info = update_module.local_package_info(path, current_version="1.3.2")
        self.assertFalse(info["patch"])
        self.assertEqual(info["version"], "1.4.0")


class ParseManifestPatchTest(unittest.TestCase):
    def base(self):
        return {"version": "1.4.0", "url": "https://x/full.zip", "sha256": "b" * 64,
                "size": 67_759_033}

    def test_patches_are_parsed(self):
        data = self.base()
        data["patches"] = [{"from": "1.3.2", "url": "https://x/p.zip", "sha256": "a" * 64,
                            "size": 3_800_000, "runtime": RUNTIME}]
        result = update_module.parse_manifest(json.dumps(data), current_version="1.3.2")
        self.assertEqual(len(result["patches"]), 1)
        self.assertEqual(result["patches"][0]["from"], "1.3.2")

    def test_broken_patch_entry_is_skipped_not_fatal(self):
        data = self.base()
        data["patches"] = [{"from": "1.3.2", "url": "http://evil/p.zip", "sha256": "a" * 64,
                            "size": 3_800_000},
                           {"from": "1.3.1", "url": "https://x/p2.zip", "sha256": "short",
                            "size": 1},
                           {"from": "1.3.0", "url": "https://x/p3.zip", "sha256": "c" * 64,
                            "size": 2_000_000}]
        result = update_module.parse_manifest(json.dumps(data), current_version="1.3.2")
        self.assertEqual([item["from"] for item in result["patches"]], ["1.3.0"])


if __name__ == "__main__":
    unittest.main()
