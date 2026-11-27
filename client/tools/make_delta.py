"""发布时生成差异包：对比两版解压目录，只打包真正变化的文件。

用法（两边的目录都是「含 ClassFix.exe 的那一层」）：
    python tools/make_delta.py --old <旧版目录> --new <新版目录> --out <输出目录>
    python tools/make_delta.py ... --full-size 67759033 --patch-url https://.../x.zip \
        --update-json <update.json 路径>

产出：
* <out>/<from>-to-<to>.zip       差异包（patch.json + ClassFix/<变更文件>）
* <out>/manifest-<to>.json       新版全量清单（留档核验用）
* 屏幕打印统计与可直接粘贴的 update.json patches 片段

「差异过大」由客户端按 MAX_DELTA_RATIO / MAX_DELTA_BYTES 判断并回落全量，
本工具只负责如实产出，同时在屏幕上给出提示。
"""

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from classfix import delta  # noqa: E402
from classfix.update import PACKAGE_META, sha256_of  # noqa: E402


def read_meta(directory):
    path = Path(directory) / PACKAGE_META
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build(old_dir, new_dir, out_dir, full_size=0, note=""):
    old_dir = Path(old_dir)
    new_dir = Path(new_dir)
    out_dir = Path(out_dir)
    old_meta, new_meta = read_meta(old_dir), read_meta(new_dir)
    old_version = str(old_meta.get("version") or "").strip()
    new_version = str(new_meta.get("version") or "").strip()
    if not old_version or not new_version:
        raise SystemExit("两个目录里都要有 %s 且带 version 字段。" % PACKAGE_META)
    runtime = new_meta.get("runtime") or {}
    if not runtime:
        print("警告：新版 %s 没有 runtime 指纹，客户端会因此拒绝差异包并走全量。" % PACKAGE_META)
    old_files = delta.file_manifest(old_dir)
    new_files = delta.file_manifest(new_dir)
    changed = sorted(name for name, info in new_files.items()
                     if name not in old_files or old_files[name]["sha256"] != info["sha256"])
    removed = sorted(name for name in old_files if name not in new_files)
    keep = {name: info["size"] for name, info in new_files.items() if name not in changed}
    payload_bytes = sum(new_files[name]["size"] for name in changed)

    out_dir.mkdir(parents=True, exist_ok=True)
    patch_meta = {
        "from": old_version,
        "to": new_version,
        "runtime": runtime,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "files": {name: {"size": new_files[name]["size"], "sha256": new_files[name]["sha256"]}
                  for name in changed},
        "keep": keep,
        "deletes": removed,
        "payload_bytes": payload_bytes,
        "note": note,
    }
    patch_path = out_dir / ("%s-to-%s.zip" % (old_version, new_version))
    if patch_path.exists():
        patch_path.unlink()
    with zipfile.ZipFile(patch_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(delta.PATCH_META, json.dumps(patch_meta, ensure_ascii=False, indent=1))
        for name in changed:
            archive.write(new_dir / name, arcname="ClassFix/" + name)

    manifest_path = out_dir / ("manifest-%s.json" % new_version)
    manifest_path.write_text(json.dumps(
        {"version": new_version, "runtime": runtime,
         "files": {name: {"size": info["size"], "sha256": info["sha256"]}
                   for name, info in new_files.items()}},
        ensure_ascii=False, indent=1), encoding="utf-8")

    digest = sha256_of(patch_path)
    size = patch_path.stat().st_size
    print("旧版 %s / 新版 %s" % (old_version, new_version))
    print("相同文件 %d 个 · 变更 %d 个（%.2f MB）· 删除 %d 个"
          % (len(keep), len(changed), payload_bytes / 1048576.0, len(removed)))
    print("差异包 %s  %.2f MB  sha256=%s" % (patch_path.name, size / 1048576.0, digest))
    if changed:
        print("变更文件（前 10）：%s" % ", ".join(changed[:10]))
    if delta.delta_too_big(size, full_size or size * 2):
        print("提示：这个差异包已超过阈值（全包的 %d%% 或 24 MB），客户端会自动改用全量包。"
              % int(delta.MAX_DELTA_RATIO * 100))
    entry = {"from": old_version, "url": "${PATCH_URL}", "sha256": digest, "size": size,
             "runtime": runtime}
    print("\nupdate.json 的 patches 片段：")
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return {"patch": patch_path, "manifest": manifest_path, "entry": entry, "size": size}


def merge_update_json(path, entry, keep_versions=5, url=None):
    """把差异包条目合并进 update.json 的 patches 数组（同 from 覆盖，最多保留 N 个）。"""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = dict(entry)
    if url:
        entry["url"] = url
    patches = [item for item in (data.get("patches") or []) if item.get("from") != entry["from"]]
    patches.append(entry)
    data["patches"] = patches[-keep_versions:]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("已写入 %s，当前 patches：%s" % (path, ", ".join(item["from"] for item in data["patches"])))
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 ClassFix 差异更新包")
    parser.add_argument("--old", required=True, help="旧版目录（含 ClassFix.exe）")
    parser.add_argument("--new", required=True, help="新版目录（含 ClassFix.exe）")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--full-size", type=int, default=0, help="新版完整包字节数，用于判断差异是否过大")
    parser.add_argument("--patch-url", default="", help="差异包上线后的下载地址")
    parser.add_argument("--update-json", default="", help="顺手把条目合并进这个 update.json")
    parser.add_argument("--note", default="", help="写进 patch.json 的备注")
    args = parser.parse_args(argv)
    result = build(args.old, args.new, args.out, args.full_size, args.note)
    if args.update_json:
        merge_update_json(args.update_json, result["entry"], url=args.patch_url or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
