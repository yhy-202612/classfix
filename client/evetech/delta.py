"""差异更新：只下载真正变化的文件；差异过大或环境不匹配就回落全量更新。

实测依据（1.1.7 → 1.3.2，跨 6 个版本）：解压后 177 MB 的包里，
199 个文件 / 92.3 MB 字节级完全相同（全部 Qt6 dll、pyd、python312.dll、
sqlite3、libcrypto…），真正变化只有 EveTech.exe 与 base_library.zip，约 3.8 MB。

所以只要「运行时指纹」一致，跨多个版本也能用同一份差异包升过去；
指纹不一致（将来升 PySide6 或 Python）或差异包过大时，一律回落全量包，
避免 Qt 的 dll 与 pyd 版本混搭导致一启动就崩。

本模块只做纯判断与校验，不弹窗、不联网，便于在 Linux 上单测。
"""

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

from . import VERSION

PATCH_META = "patch.json"
PACKAGE_META = "evetech-package.json"

# 「差异过大就全量」的两条线：占全包一半以上，或绝对值超过 24 MB。
MAX_DELTA_RATIO = 0.5
MAX_DELTA_BYTES = 24 * 1024 * 1024

# keep 里都是「新版沿用、差异包不带」的运行时文件（Qt dll、python312.dll 等），
# 缺一个或大小对不上都会让程序一启动就崩，所以一律拦下、改走全量包。
MAX_KEEP_MISSING = 0

# 扫描本地文件时跳过的目录/后缀：更新缓存与日志不该参与比对。
IGNORE_DIRS = ("update", ".update")
IGNORE_SUFFIXES = (".log", ".part")


def _sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_manifest(root, hashes=True):
    """列出安装目录里的文件：{相对路径: {"size": n, "sha256": hex}}。

    hashes=False 时只取大小，用于「先比大小、再比哈希」的快速路径。
    """
    root = Path(root)
    out = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if any(part in IGNORE_DIRS for part in parts[:-1]):
            continue
        if relative.suffix.lower() in IGNORE_SUFFIXES:
            continue
        name = relative.as_posix()
        out[name] = {"size": path.stat().st_size,
                     "sha256": _sha256(path) if hashes else ""}
    return out


def runtime_from_process():
    """按当前进程推断运行时指纹——1.4.0 之前的包内没有 runtime 记录，靠这个兜底。

    PySide6 的版本优先取 __version__；冻结环境里取不到时退到 Qt 运行时版本
    （PySide6 与 Qt 的大版本号是一致的），仍取不到就留空，由调用方判「信息不足」。
    """
    pyside = ""
    try:
        import PySide6
        pyside = str(getattr(PySide6, "__version__", "") or "")
    except ImportError:
        pyside = ""
    if not pyside:
        try:
            from PySide6 import QtCore
            pyside = ".".join(str(QtCore.qVersion()).split(".")[:3])
        except (ImportError, AttributeError, ValueError):
            pyside = ""
    return {
        "python": "%d.%d" % (sys.version_info[0], sys.version_info[1]),
        "pyside6": pyside,
        "arch": "x64" if sys.maxsize > 2 ** 32 else "x86",
        "onedir": bool(getattr(sys, "frozen", False)),
    }


def _runtime_from_file(root):
    root = Path(root)
    meta = root / PACKAGE_META
    if not meta.is_file():
        # 允许传入包根目录的上一层
        children = [item for item in root.iterdir() if item.is_dir()] if root.is_dir() else []
        if len(children) == 1:
            meta = children[0] / PACKAGE_META
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    runtime = data.get("runtime")
    return runtime if isinstance(runtime, dict) else {}


def local_runtime(root=None):
    """本机运行时指纹：优先读包内记录，老版本没有记录就按当前进程推断。"""
    if root:
        declared = _runtime_from_file(Path(root))
        if declared:
            return declared
    return runtime_from_process()


def runtime_problem(local, declared):
    """比较运行时指纹，返回空串表示一致，否则返回给人看的原因。"""
    if not declared:
        return "差异包没有声明运行时指纹"
    if not local:
        return "本机包内没有 runtime 记录，无法确认运行时是否一致"
    labels = {"python": "Python", "pyside6": "PySide6", "arch": "架构"}
    for key, label in labels.items():
        want = str(declared.get(key) or "").strip()
        have = str(local.get(key) or "").strip()
        if not want:
            continue
        if not have:
            return "本机缺少 %s 版本信息" % label
        if want != have:
            return "%s 不一致（本机 %s，差异包要求 %s）" % (label, have, want)
    return ""


def delta_too_big(patch_size, full_size):
    """差异包是否「差异过大」——是就改用全量包。"""
    try:
        patch_size = int(patch_size or 0)
        full_size = int(full_size or 0)
    except (TypeError, ValueError):
        return True
    if patch_size <= 0:
        return True
    if patch_size > MAX_DELTA_BYTES:
        return True
    if full_size > 0 and patch_size > full_size * MAX_DELTA_RATIO:
        return True
    return False


def choose(manifest, root=None, current_version=None):
    """在清单里挑一个更新包：优先差异包，不合适就用全量包。

    返回 {"mode": "patch"|"full", "entry": {...}, "reason": 文案, "full_size": n}。
    entry 里一定有 url / sha256 / size。本函数不下载、不读盘上大文件，可放心在主线程调。
    """
    version = str(manifest.get("version") or "")
    full = {"url": manifest.get("url"), "sha256": manifest.get("sha256"), "size": manifest.get("size")}
    plan = {"mode": "full", "entry": full, "reason": "", "full_size": int(manifest.get("size") or 0),
            "version": version}
    patches = manifest.get("patches") or []
    if not patches:
        plan["reason"] = "清单里没有差异包"
        return plan
    current = str(current_version or VERSION)
    candidate = None
    for item in patches:
        if str(item.get("from") or "").strip() == current:
            candidate = item
            break
    if candidate is None:
        plan["reason"] = "没有 %s → %s 的差异包" % (current, version)
        return plan
    if delta_too_big(candidate.get("size"), manifest.get("size")):
        plan["reason"] = "差异包偏大（%.1f MB），改用全量包" % (int(candidate.get("size") or 0) / 1048576.0)
        return plan
    problem = runtime_problem(local_runtime(root), candidate.get("runtime"))
    if problem:
        plan["reason"] = problem + "，改用全量包"
        return plan
    plan["mode"] = "patch"
    plan["entry"] = {"url": candidate.get("url"), "sha256": candidate.get("sha256"),
                     "size": candidate.get("size"), "from": candidate.get("from"),
                     "runtime": candidate.get("runtime") or {}}
    plan["reason"] = "本次只需下载 %.1f MB（完整包 %.1f MB）" % (
        int(candidate.get("size") or 0) / 1048576.0, int(manifest.get("size") or 0) / 1048576.0)
    return plan


def read_patch_meta(zip_path):
    """读出差异包里的 patch.json；没有或格式不对就抛 ValueError。"""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [name for name in archive.namelist() if name.replace("\\", "/") == PATCH_META]
            if not names:
                raise ValueError("差异包里缺少 %s" % PATCH_META)
            with archive.open(names[0]) as handle:
                data = json.loads(handle.read().decode("utf-8"))
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, ValueError) as error:
        raise ValueError("差异包元数据无法读取：%s" % error)
    if not isinstance(data, dict) or not data.get("to"):
        raise ValueError("差异包元数据不完整")
    return data


def _safe_relative(name):
    """校验相对路径：拒绝绝对路径、盘符、上级目录穿越。"""
    text = str(name or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def verify_payload(payload_root, patch, max_report=5):
    """校验解出来的文件与 patch.json 记录一致，返回 (ok, [问题描述])。"""
    problems = []
    files = patch.get("files") or {}
    if not isinstance(files, dict):
        return False, ["patch.json 的 files 字段格式不对"]
    payload_root = Path(payload_root)
    for name, expected in files.items():
        relative = _safe_relative(name)
        if relative is None:
            problems.append("patch.json 里有非法路径：%s" % name)
            continue
        target = payload_root / relative
        if not target.is_file():
            problems.append("差异包缺少文件：%s" % relative)
        elif isinstance(expected, dict):
            size = expected.get("size")
            digest = str(expected.get("sha256") or "")
            if size is not None and target.stat().st_size != int(size):
                problems.append("%s 大小不符" % relative)
            elif digest and _sha256(target) != digest.lower():
                problems.append("%s 校验不通过" % relative)
        elif str(expected).strip() and _sha256(target) != str(expected).lower():
            problems.append("%s 校验不通过" % relative)
    return (not problems), problems[:max_report]


def check_keep(root, patch):
    """检查「新版本沿用、差异包不带」的文件是否还在本机，返回 (ok, 缺失清单)。

    防的是「老师手工覆盖过安装目录、某些运行库被删」这类残缺环境：
    差异包不会补这些文件，装上就会一启动就崩。
    """
    keep = patch.get("keep") or {}
    if not isinstance(keep, dict):
        return True, []
    root = Path(root)
    missing = []
    broken = []
    for name, expected_size in keep.items():
        relative = _safe_relative(name)
        if relative is None:
            continue
        target = root / relative
        if not target.is_file():
            missing.append(relative)
            continue
        try:
            if expected_size is not None and target.stat().st_size != int(expected_size):
                # 文件在、但大小对不上：说明安装目录被改过或版本混杂，比缺失更该拦。
                broken.append(relative)
        except (TypeError, ValueError):
            continue
    ok = not broken and len(missing) <= MAX_KEEP_MISSING
    return ok, (broken + missing)[:8]


def check_version(patch, current_version=None, target_version=None):
    """校验 from/to 与本次升级是否对得上。"""
    current = str(current_version or VERSION)
    source = str(patch.get("from") or "").strip()
    if source and source != current:
        return False, "差异包是 %s → %s，本机是 %s" % (source, patch.get("to"), current)
    if target_version and str(patch.get("to")).strip() != str(target_version).strip():
        return False, "差异包目标版本 %s 与清单版本 %s 不一致" % (patch.get("to"), target_version)
    return True, ""


def delete_paths(patch):
    """差异包里要删除的旧文件（相对安装目录），已剔除非法路径。"""
    out = []
    for name in patch.get("deletes") or []:
        relative = _safe_relative(name)
        if relative:
            out.append(relative)
    return out
