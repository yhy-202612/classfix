"""把打包目录做成可发布的更新包：zip + SHA256 + update.json。

用法（在 Windows 打完 dist\\EveTech 之后，任何平台都能跑这一步）：

    python tools/release.py --source dist/EveTech --output release \
        --notes release-notes.md --base-url https://aiclass.openplays.cn/classfix/

产出：
    release/EveTech-<版本>-Windows-x64.zip     发给用户 / 上传到站点
    release/update.json                        站点根目录的更新清单
    release/sha256.txt                         便于人工核对
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evetech import VERSION  # noqa: E402
from evetech.update import PACKAGE_META  # noqa: E402
from tools.write_package_meta import write_meta  # noqa: E402

EXCLUDED_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDED_FILES = {".DS_Store", "Thumbs.db"}


def iter_files(source):
    for path in sorted(source.rglob("*")):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.is_file() and path.name not in EXCLUDED_FILES:
            yield path


def sha256_of(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def read_notes(path):
    if not path:
        return []
    text = Path(path).read_text(encoding="utf-8")
    return [line.strip(" -*\t") for line in text.splitlines() if line.strip(" -*\t")]


def build_zip(source, archive):
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in iter_files(source):
            bundle.write(path, Path("EveTech") / path.relative_to(source))
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 EveTech 更新包与清单")
    parser.add_argument("--source", required=True, help="PyInstaller 输出的 dist/EveTech 目录")
    parser.add_argument("--output", default=str(ROOT / "release"), help="产物目录")
    parser.add_argument("--notes", default="", help="更新说明文件（markdown / 纯文本，每行一条）")
    parser.add_argument("--base-url", default="https://aiclass.openplays.cn/classfix/",
                        help="包上传后的公开目录（决定 update.json 里的下载地址）")
    parser.add_argument("--version", default=VERSION, help="覆盖清单版本号（默认取 evetech.VERSION）")
    args = parser.parse_args(argv)

    source = Path(args.source).resolve()
    if not (source / "EveTech.exe").is_file():
        print("找不到 %s，请先运行 build-exe.ps1 打包。" % (source / "EveTech.exe"))
        return 2
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    if args.version != VERSION:
        print("提示：清单版本 %s 与源码 VERSION %s 不一致，包内元数据仍写 %s。" % (args.version, VERSION, VERSION))
    notes = read_notes(args.notes)
    write_meta(source, "\n".join(notes))
    archive = output / ("EveTech-%s-Windows-x64.zip" % args.version)
    if archive.exists():
        archive.unlink()
    build_zip(source, archive)

    digest = sha256_of(archive)
    size = archive.stat().st_size
    url = args.base_url.rstrip("/") + "/" + archive.name
    manifest = {
        "version": args.version,
        "url": url,
        "sha256": digest,
        "size": size,
        "published": datetime.now().isoformat(timespec="seconds"),
        "notes": notes or ["常规更新。"],
    }
    (output / "update.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "sha256.txt").write_text("%s  %s\n" % (digest, archive.name), encoding="utf-8")

    print("包：%s（%.1f MB）" % (archive, size / 1048576.0))
    print("SHA256：%s" % digest)
    print("清单：%s" % (output / "update.json"))
    print("下一步：把 zip 与 update.json 一起放到 %s" % args.base_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
