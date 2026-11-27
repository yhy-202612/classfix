"""差异更新端到端演练：真实安装目录 + 真实差异包 → 覆盖后与全量包逐文件比对。

用法：
    python tools/verify_delta.py --old <1.3.2 解压目录> --patch <差异包.zip> --new <1.4.0 解压目录>

说明：
* --old 会被**复制**到临时目录再操作，原始目录不动；
* 覆盖方式与客户端批处理一致（逐文件覆盖 + 按 delete 清单删除）；
* 最后与 --new 的全量解压目录做「路径 + 大小 + sha256」全量比对，
  一致才算差异更新真的可用。
"""

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from classfix import delta, update  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="验证 ClassFix 差异更新")
    parser.add_argument("--old", required=True, help="旧版解压目录（含 ClassFix.exe）")
    parser.add_argument("--patch", required=True, help="差异包 zip")
    parser.add_argument("--new", default="", help="新版全量解压目录，用于最终比对")
    args = parser.parse_args(argv)

    old_dir = Path(args.old)
    patch_path = Path(args.patch)
    new_dir = Path(args.new) if args.new else None

    started = time.time()
    meta = delta.read_patch_meta(patch_path)
    size = patch_path.stat().st_size
    print("差异包 %s → %s" % (meta.get("from"), meta.get("to")))
    print("差异包大小 %.2f MB · 变更文件 %d 个 · 沿用文件 %d 个 · 删除文件 %d 个"
          % (size / 1048576.0, len(meta.get("files") or {}), len(meta.get("keep") or {}),
             len(meta.get("deletes") or [])))
    runtime = meta.get("runtime") or {}
    print("差异包运行时指纹：%s" % (runtime or "（未声明）"))

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "ClassFix"
        shutil.copytree(old_dir, work)
        before = delta.file_manifest(work)
        print("旧目录 %d 个文件 / %.1f MB" % (len(before), sum(v["size"] for v in before.values()) / 1048576.0))

        inner, script = update.stage_patch(patch_path, meta["to"], root=work,
                                           current_version=meta.get("from"))
        print("暂存目录：%s（替换脚本 %s）" % (inner.name, script.name))

        copied = 0
        for path in inner.rglob("*"):
            if not path.is_file():
                continue
            target = work / path.relative_to(inner)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
        removed = 0
        for relative in delta.delete_paths(meta):
            target = work / relative
            if target.exists():
                target.unlink()
                removed += 1
        print("已覆盖 %d 个文件、删除 %d 个文件" % (copied, removed))

        after = delta.file_manifest(work)
        print("覆盖后 %d 个文件 / %.1f MB" % (len(after), sum(v["size"] for v in after.values()) / 1048576.0))

        ok = True
        if new_dir is not None:
            want = delta.file_manifest(new_dir)
            ok = after == want
            print("与全量目录逐文件比对：%s" % ("完全一致" if ok else "不一致"))
            if not ok:
                only_new = sorted(set(after) - set(want))
                only_want = sorted(set(want) - set(after))
                differ = sorted(name for name in after if name in want and after[name] != want[name])
                if only_new:
                    print("  多出：%s" % only_new[:5])
                if only_want:
                    print("  缺少：%s" % only_want[:5])
                if differ:
                    print("  内容不同：%s" % differ[:5])
        print("耗时 %.1f 秒 · 本次下载 %.2f MB（全量包约 64.6 MB）"
              % (time.time() - started, size / 1048576.0))
        print("结论：%s" % ("差异更新可用" if ok else "差异更新结果不正确，必须回落全量"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
