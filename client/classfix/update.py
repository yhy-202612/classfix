"""软件更新：读取更新清单、下载校验、安全解压、替换重启。

两条通道并行存在：
* 在线：读取 update.json → 下载 zip → SHA256 校验 → 解压到同级 .update 目录 → 退出后由 bat 覆盖 → 重启。
* 离线：教室机没有外网时，把发布包 zip 拷进安装目录（或其中的 update\\ 子目录），
  由界面「从文件更新」直接走本地的校验与替换流程。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import VERSION
from . import delta

MANIFEST_URL = "https://aiclass.openplays.cn/classfix/update.json"
MANIFEST_TIMEOUT = 6
DOWNLOAD_TIMEOUT = 30
CHUNK = 1 << 16
MAX_PACKAGE_BYTES = 400 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
EXE_NAME = "ClassFix.exe"
PACKAGE_META = "classfix-package.json"
USER_AGENT = "ClassFix/%s (+classroom-tool)" % VERSION


class UpdateError(Exception):
    """更新流程中可以直接展示给使用者的错误。"""


# ---------------------------------------------------------------- 版本与清单

def version_tuple(text):
    """把 1.2.0 / v1.2.0-beta 这类版本串拆成可比较的整数元组。"""
    numbers = re.findall(r"\d+", str(text or ""))
    if not numbers:
        raise UpdateError("版本号无法识别：%s" % text)
    return tuple(int(item) for item in numbers[:4])


def is_newer(candidate, current):
    left, right = version_tuple(candidate), version_tuple(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def check_url(url):
    """只接受 https；本地回环地址允许 http，方便离线自测。"""
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    if not parsed.hostname:
        raise UpdateError("下载地址不正确。")
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost"):
        return url
    raise UpdateError("下载地址必须是 https，已拒绝非加密链接。")


def parse_manifest(raw, current_version=None):
    """把清单 JSON 规整为固定结构，字段缺失或格式非法都抛 UpdateError。"""
    current_version = current_version or VERSION
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else dict(raw)
    except (TypeError, ValueError):
        raise UpdateError("更新清单不是有效的 JSON。")
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式不正确。")
    version = str(data.get("version") or "").strip()
    url = check_url(data.get("url"))
    digest = str(data.get("sha256") or "").strip().lower()
    try:
        size = int(data.get("size") or 0)
    except (TypeError, ValueError):
        raise UpdateError("更新清单里的文件大小不正确。")
    if not version:
        raise UpdateError("更新清单缺少版本号。")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise UpdateError("更新清单里的 SHA256 格式不正确。")
    if size <= 0 or size > MAX_PACKAGE_BYTES:
        raise UpdateError("更新包大小超出允许范围。")
    notes = data.get("notes") or data.get("changelog") or ""
    if isinstance(notes, (list, tuple)):
        notes = "\n".join(str(line).strip() for line in notes if str(line).strip())
    patches = []
    raw_patches = data.get("patches") or []
    if isinstance(raw_patches, list):
        for item in raw_patches[:8]:
            if not isinstance(item, dict):
                continue
            source = str(item.get("from") or "").strip()
            patch_digest = str(item.get("sha256") or "").strip().lower()
            if not source or not re.fullmatch(r"[0-9a-fA-F]{64}", patch_digest or ""):
                continue
            try:
                patch_size = int(item.get("size") or 0)
                patch_url = check_url(item.get("url"))
            except (UpdateError, TypeError, ValueError):
                continue
            if patch_size <= 0 or patch_size > MAX_PACKAGE_BYTES:
                continue
            patches.append({
                "from": source,
                "url": patch_url,
                "sha256": patch_digest,
                "size": patch_size,
                "runtime": item.get("runtime") if isinstance(item.get("runtime"), dict) else {},
            })
    return {
        "version": version,
        "url": url,
        "sha256": digest,
        "size": size,
        "notes": str(notes).strip()[:4000],
        "mandatory": bool(data.get("mandatory")),
        "available": is_newer(version, current_version),
        "patches": patches,
    }


def fetch_manifest(url=None, timeout=MANIFEST_TIMEOUT, opener=None):
    """读取线上清单。任何网络异常都抛 UpdateError，由调用方决定是否提示。"""
    url = check_url(url or MANIFEST_URL)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    open_method = (opener or urllib.request.urlopen)
    try:
        with open_method(request, timeout=timeout) as response:
            payload = response.read(MAX_MANIFEST_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise UpdateError("更新服务器返回 HTTP %s。" % error.code)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise UpdateError("连接更新服务器失败：%s" % (getattr(error, "reason", None) or error))
    if len(payload) > MAX_MANIFEST_BYTES:
        raise UpdateError("更新清单异常偏大，已中止。")
    return parse_manifest(payload)


# ---------------------------------------------------------------- 下载与解压

def sha256_of(path, chunk=CHUNK):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, target, digest, size=None, on_progress=None, timeout=DOWNLOAD_TIMEOUT, opener=None):
    """流式下载到 target，边下边算 SHA256；校验不过就删掉临时文件。"""
    url = check_url(url)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    if partial.exists():
        partial.unlink()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    open_method = (opener or urllib.request.urlopen)
    hasher = hashlib.sha256()
    done = 0
    total = int(size or 0)
    try:
        with open_method(request, timeout=timeout) as response:
            header = getattr(response, "headers", None)
            declared = header.get("Content-Length") if header is not None else None
            if declared:
                try:
                    total = max(total, int(declared))
                except (TypeError, ValueError):
                    pass
            with partial.open("wb") as handle:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    done += len(block)
                    if done > MAX_PACKAGE_BYTES:
                        raise UpdateError("更新包超过允许大小，已中止。")
                    hasher.update(block)
                    handle.write(block)
                    if on_progress:
                        on_progress(done, total)
    except urllib.error.HTTPError as error:
        raise UpdateError("下载失败：HTTP %s。" % error.code)
    except (urllib.error.URLError, OSError) as error:
        partial.unlink(missing_ok=True)
        raise UpdateError("下载中断：%s" % (getattr(error, "reason", None) or error))
    except Exception:
        # 包体超限（UpdateError）、进度回调抛错、磁盘写失败等都不在上面的分支里；
        # 不在这里清掉会留下半截 .part 文件。
        partial.unlink(missing_ok=True)
        raise
    if size and done != int(size):
        partial.unlink(missing_ok=True)
        raise UpdateError("下载大小与清单不一致（%d / %d 字节）。" % (done, int(size)))
    if hasher.hexdigest().lower() != str(digest).lower():
        partial.unlink(missing_ok=True)
        raise UpdateError("SHA256 校验未通过，已丢弃下载文件。")
    partial.replace(target)
    return target


def unpack(zip_path, target):
    """解压发布包：拒绝绝对路径、上级目录穿越与符号链接。"""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    base = target.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise UpdateError("压缩包包含绝对路径，已拒绝解压。")
            parts = [part for part in name.split("/") if part not in ("", ".")]
            if any(part == ".." for part in parts):
                raise UpdateError("压缩包包含上级目录，已拒绝解压。")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise UpdateError("压缩包包含符号链接，已拒绝解压。")
            destination = base.joinpath(*parts)
            if base not in destination.parents and destination != base:
                raise UpdateError("压缩包条目越界，已拒绝解压。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as sink:
                shutil.copyfileobj(source, sink, CHUNK)
    return target


def package_root(directory):
    """发布包可能是 ClassFix/ 单根目录，也可能直接是 exe 同级。"""
    directory = Path(directory)
    if (directory / EXE_NAME).is_file():
        return directory
    children = [item for item in directory.iterdir() if item.is_dir()]
    if len(children) == 1 and (children[0] / EXE_NAME).is_file():
        return children[0]
    return None


def read_package_meta(zip_path):
    """读取发布包内嵌的元数据（离线更新时用来判断版本）。"""
    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if name.replace("\\", "/").endswith(PACKAGE_META)]
        if not names:
            return {}
        with archive.open(names[0]) as handle:
            try:
                return json.loads(handle.read().decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return {}


# ---------------------------------------------------------------- 安装位置与替换

def install_root():
    """返回正在运行的安装目录（打包后是 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def staging_directory(root=None):
    root = Path(root or install_root())
    return root.parent / (root.name + ".update")


def log_path(root=None):
    root = Path(root or install_root())
    return root.parent / "ClassFix-update.log"


def last_update_failure(root=None, within_hours=48):
    """上一次替换脚本失败了就返回一句能照着做的话，否则 None。

    以前更新失败是「静默」的：程序退出了、文件没换、也没人告诉用户，
    用户只会觉得「新版装上去了但打不开」。
    """
    path = log_path(root)
    try:
        if not path.is_file() or time.time() - path.stat().st_mtime > within_hours * 3600:
            return None
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return None
    if not lines:
        return None
    last = lines[-1]
    if "update done" in last:
        return None
    if "aborted" in last:
        return ("上次更新没有完成：旧版本进程一直没有退出，文件没有被替换。"
                "请先彻底退出 ClassFix（托盘图标右键 → 退出），再重新安装。")
    if "failed" in last or "cannot restart" in last or "missing" in last:
        return "上次更新没有完成，请手动解压完整包覆盖安装目录。"
    return None


_UPDATER_TEMPLATE = """@echo off
chcp 65001 >nul
setlocal
set "SRC=@@SRC@@"
set "DST=@@DST@@"
set "LOG=@@LOG@@"
set "EXE=@@EXE@@"
echo [%date% %time%] update start src="%SRC%" dst="%DST%" >> "%LOG%"
rem 等旧进程退出。这里不能用 timeout：脚本由主程序以无控制台方式启动
rem （stdin 已重定向），timeout 会立刻报 "Input redirection is not supported"
rem 并退出，导致等待逻辑形同虚设、更新必定中止。ping 不依赖 stdin，稳。
set /a WAITED=0
:waitloop
tasklist /FI "IMAGENAME eq %EXE%" 2>nul | find /I "%EXE%" >nul || goto stopped
if %WAITED% GEQ @@WAIT@@ goto timeout
set /a WAITED+=1
ping -n 2 127.0.0.1 >nul
goto waitloop
:timeout
echo [%date% %time%] old process still running, update aborted >> "%LOG%"
exit /b 1
:stopped
ping -n 2 127.0.0.1 >nul
robocopy "%SRC%" "%DST%" /E /NFL /NDL /NJH /NJS /NP /R:2 /W:1 >> "%LOG%" 2>&1
if errorlevel 8 (
  echo [%date% %time%] robocopy failed with %errorlevel% >> "%LOG%"
  exit /b 2
)
@@DELETES@@
if not exist "%DST%\\%EXE%" (
  echo [%date% %time%] new exe missing, cannot restart >> "%LOG%"
  exit /b 3
)
cd /d "%DST%"
start "" /D "%DST%" "%DST%\\%EXE%"
if errorlevel 1 echo [%date% %time%] start failed with %errorlevel% >> "%LOG%"
echo [%date% %time%] update done >> "%LOG%"
rmdir /s /q "%SRC%" 2>nul
(goto) 2>nul & del "%~f0"
"""


def _delete_lines(deletes):
    """把「新版本已经不再包含的旧文件」转成批处理里的删除行。"""
    lines = []
    for name in deletes or ():
        relative = str(name or "").replace("\\", "/").strip()
        parts = [part for part in relative.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts) or re.match(r"^[A-Za-z]:", relative):
            continue
        target = "\\".join(parts)
        lines.append('if exist "%%DST%%\\%s" del /f /q "%%DST%%\\%s" >nul 2>&1' % (target, target))
    return "\r\n".join(lines)


def updater_script(source, destination, script_path, log=None, exe_name=EXE_NAME, wait_seconds=90,
                   deletes=None):
    """生成在旧进程退出后执行替换并重启的批处理。

    robocopy 用 /E 而不是 /MIR：只覆盖同名文件，不删除安装目录里其余内容，
    因此用户自己放进去的课件、更新包都不会被清掉。差异更新需要的「删掉新版已移除的
    文件」由 deletes 参数单独列出，只在这些明确路径上删。
    """
    log = Path(log or log_path())
    text = _UPDATER_TEMPLATE
    for placeholder, value in (("@@SRC@@", source), ("@@DST@@", destination), ("@@LOG@@", log),
                               ("@@EXE@@", exe_name), ("@@WAIT@@", int(wait_seconds)),
                               ("@@DELETES@@", _delete_lines(deletes))):
        text = text.replace(placeholder, str(value))
    script_path = Path(script_path)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(text.replace("\n", "\r\n"), encoding="utf-8")
    return script_path


def stage(zip_path, package_version, root=None, deletes=None):
    """把发布包解到同级 .update 目录并写好替换脚本，返回 (新目录, 脚本路径)。"""
    root = Path(root or install_root())
    staging = staging_directory(root)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    unpack(zip_path, staging)
    inner = package_root(staging)
    if inner is None:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("发布包里没有找到 %s，无法完成更新。" % EXE_NAME)
    script = script_directory(root) / ("ClassFix-update-%s.bat" % re.sub(r"[^0-9A-Za-z._-]", "_", str(package_version)))
    updater_script(inner, root, script, deletes=deletes)
    return inner, script


def stage_patch(patch_zip, package_version, root=None, current_version=None):
    """差异包：解包 → 校验版本/内容/本机完整性 → 生成带删除清单的替换脚本。

    任何一项不过都抛 UpdateError（文案里带「请改用完整包」），由调用方回落全量更新。
    """
    root = Path(root or install_root())
    try:
        patch = delta.read_patch_meta(patch_zip)
    except ValueError as error:
        raise UpdateError("%s，请改用完整包。" % error)
    ok, reason = delta.check_version(patch, current_version or VERSION, package_version)
    if not ok:
        raise UpdateError("%s，请改用完整包。" % reason)
    staging = staging_directory(root)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    unpack(patch_zip, staging)
    inner = package_root(staging)
    if inner is None:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("差异包里没有找到 %s，请改用完整包。" % EXE_NAME)
    ok, problems = delta.verify_payload(inner, patch)
    if not ok:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("差异包内容校验未通过（%s），请改用完整包。" % "；".join(problems[:3]))
    ok, missing = delta.check_keep(root, patch)
    if not ok:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("本机安装目录不完整（缺 %s），差异更新会留下坏环境，请改用完整包。"
                          % "、".join(missing[:3]))
    script = script_directory(root) / ("ClassFix-update-%s.bat" % re.sub(r"[^0-9A-Za-z._-]", "_", str(package_version)))
    updater_script(inner, root, script, deletes=delta.delete_paths(patch))
    return inner, script


def script_directory(root=None):
    """替换脚本放在安装目录的上级（安装目录本身马上会被覆盖）。"""
    root = Path(root or install_root())
    return root.parent


def launch_updater(script):
    """脱离当前进程启动替换脚本，调用方随后应立即退出。

    这里必须用 CREATE_NO_WINDOW，不能用 DETACHED_PROCESS：DETACHED_PROCESS 完全不建
    控制台，cmd.exe 发现自己没有控制台就会自己 AllocConsole 出一个**可见**窗口——1.3
    升级到 1.4 时用户看到、并且一关就把更新打断的那个黑窗口（标题是
    `find /I "ClassFix.exe"`，即 tasklist 管道那条命令）就是它。
    CREATE_NO_WINDOW 分配的是隐藏控制台，bat 里的 tasklist / robocopy 照常执行，
    只是全程不出现窗口；bat 末尾用 start 启动主程序，那是独立 GUI 进程，窗口正常显示。
    """
    script = Path(script)
    if os.name != "nt":
        return False
    flags = 0
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd", "/c", str(script)], cwd=str(script.parent),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
    return True


def cleanup_staging(root=None):
    """启动时清理上次未完成的替换残留。"""
    staging = staging_directory(root)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)


def stale_scripts(root=None):
    """安装目录上一级残留的替换脚本。

    替换成功时脚本会自删（结尾的 del "%~f0"），只有失败、或者用户把那个黑窗口
    关掉打断流程时才会留下来。留在磁盘上会被误当成「安装包」，双击一下又跑一遍
    旧流程——所以主程序启动时要清掉。
    """
    try:
        return sorted(script_directory(root).glob("ClassFix-update-*.bat"))
    except OSError:
        return []


def cleanup_scripts(root=None):
    """删除残留的替换脚本，返回被删掉的路径。

    只在主程序启动时调用：替换流程要求主程序先退出，所以此刻不可能有替换正在
    执行，删脚本是安全的（也正因为这样才不会被「正在执行的 bat 被删」干扰）。
    """
    removed = []
    for path in stale_scripts(root):
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------- 离线包

def local_packages(directory=None):
    """在安装目录及其 update\\ 子目录里找可用的离线发布包。"""
    directory = Path(directory or install_root())
    found = []
    for folder in (directory, directory / "update"):
        if folder.is_dir():
            found.extend(sorted(path for path in folder.glob("ClassFix-*.zip") if path.is_file()))
    return found


def is_patch_package(zip_path):
    """离线包是「差异包」还是「完整包」：里面有 patch.json 就是差异包。"""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            return any(name.replace("\\", "/") == delta.PATCH_META for name in archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def local_package_info(zip_path, current_version=None):
    """整理离线包信息，供界面显示「本地包 version」。

    差异包（含 patch.json）会额外带上 patch=True 与 from 版本，界面据此走增量流程；
    1.4.0 之前的程序不认识差异包，所以差异包只对 1.4.0 及以后有意义。
    """
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise UpdateError("找不到指定的发布包。")
    patch_meta = {}
    if is_patch_package(zip_path):
        try:
            patch_meta = delta.read_patch_meta(zip_path)
        except ValueError as error:
            raise UpdateError("%s。" % error)
        version = str(patch_meta.get("to") or "").strip()
        notes = str(patch_meta.get("note") or "").strip()
    else:
        meta = read_package_meta(zip_path)
        version = str(meta.get("version") or "").strip()
        notes = str(meta.get("notes") or "").strip()
        if not version:
            match = re.search(r"(\d+\.\d+(?:\.\d+)?)", zip_path.name)
            version = match.group(1) if match else ""
    if not version:
        raise UpdateError("无法从文件名或包内元数据判断版本。")
    return {
        "version": version,
        "path": zip_path,
        "size": zip_path.stat().st_size,
        "sha256": sha256_of(zip_path),
        "notes": notes[:4000],
        "patch": bool(patch_meta),
        "from": str(patch_meta.get("from") or ""),
        "available": is_newer(version, current_version or VERSION),
    }


def update_cache_directory(root=None):
    root = Path(root or install_root())
    return root / "update"


def default_download_path(version, root=None):
    return update_cache_directory(root) / ("ClassFix-%s-Windows-x64.zip" % version)


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")
