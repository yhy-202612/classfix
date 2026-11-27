"""Evidence for decks that fail to open: real COM open, environment traces, safe-mode A/B, dialogs.

Contract:
  * The original deck is never written to; probes run on a temporary copy.
  * The COM probe closes only its copy. Application alerts and lifetime belong to Office.
  * Every probe returns a result object instead of raising when the platform is absent.
  * Temporary copies survive a timeout (PowerPoint may still hold them) and are swept later
    by cleanup_stale_dirs(); they are removed immediately on every other path.
  * Pure helpers (classify_*, parse_wer, diff_resiliency, verdict) carry no Win32 calls.
"""

import hashlib
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .model import Check, Finding, Report, sort_findings

OPEN_STATES = {"ok": "正常", "opened": "已打开", "failed": "失败", "skipped": "未检测", "timeout": "超时"}
PROBE_PREFIX = "evetech-open-"
POWERPOINT_IMAGE = "POWERPNT.EXE"

# 抓到即视为「失败类提示」的文本特征；DialogWatcher 与 verdict 共用同一份，避免两套口径
FAILURE_TEXT = (r"无法打开|无法读取|不能打开|cannot open|修复|内容有问题|格式|损坏|密码|"
                r"正在使用|受保护的视图|找不到|缺失|内存不足|崩溃")


# ---------------------------------------------------------------- error families

# (匹配用正则, 病因标题, 处置动作, 把握, 优先级)；顺序即优先级，越靠前越具体
MESSAGE_FAMILIES = (
    (r"内容有问题|发现.*问题.*修复|可尝试修复|repair",
     "课件内部结构有问题", "在 PowerPoint 里打开、选择「修复」，再「另存为」一个新的 .pptx；原文件先留一份备份。", "有检测依据", 5),
    (r"格式不受支持|文件格式与扩展名|不是有效|损坏|corrupt",
     "文件损坏或扩展名与实际格式不符", "用 PowerPoint 打开后另存为 pptx；若是从微信/网盘拿到的文件，先确认下载完整。", "有检测依据", 5),
    (r"无法读取|无法打开文件|无法打开该文件|无法显示|cannot be read|cannot open",
     "PowerPoint 无法读取这个文件", "先确认文件完整（拷到桌面再试）；仍失败就按结构损坏处理：用「打开并修复」后另存。", "有检测依据", 6),
    (r"来自 Internet|受保护的视图|Protected View|信任中心|trust center",
     "文件被「受保护的视图」拦住", "右键文件 → 属性 → 勾选「解除锁定」；或在 PowerPoint 信任中心把该文件夹加入受信任位置。", "有检测依据", 8),
    (r"密码|已加密|加密保护|password|encrypted",
     "演示文稿有密码或加密保护", "向课件作者索取密码，或让对方另存一份不带密码的版本。", "有检测依据", 6),
    (r"正在使用|已被占用|只读|另一个程序|in use|locked",
     "文件正被其它程序占用", "关掉正在打开它的 PowerPoint / WPS / 同步盘，或在「文件资源管理器」里复制一份再打开。", "有检测依据", 6),
    (r"找不到文件|文件不存在|cannot find the file|no such file",
     "文件不在原来的位置", "确认课件没有被移动、改名或从 U 盘拔掉；网络共享断开时也会这样。", "有检测依据", 7),
    (r"内存不足|资源不足|out of memory",
     "本机内存或资源不足", "关掉其它大程序再试；课件图片/视频过多时先在别的电脑上另存为精简版。", "可能", 12),
    (r"远程过程调用|RPC|服务器运行失败|DCOM|类未注册|无法创建",
     "PowerPoint 的组件注册坏了", "在「设置 → 应用」里对 Office 执行「快速修复」，不行再「联机修复」。", "有检测依据", 10),
    (r"字体|font",
     "课件用到了本机没有的字体", "装好缺失字体，或在 PowerPoint 里「替换字体」；这只影响版式，不影响能否打开。", "可能", 20),
    (r"链接|缺少文件|missing file|无法找到",
     "课件里引用的外部文件找不到", "把缺失的视频/图片放回原路径，或用 EveTech 的「替身」功能重新指向文件。", "有检测依据", 14),
)

HRESULT_HINTS = {
    0x80004005: ("通用拒绝访问/读取失败", "文件可能被占用或内容损坏；先在 PowerPoint 里用「打开并修复」试一次。", 8),
    0x800A03EC: ("文件不存在或路径无效", "确认文件的完整路径仍然存在（U 盘、网络盘、微信临时目录经常失效）。", 8),
    0x80070002: ("文件找不到", "确认文件没有被移动或改名；网络共享断开时也会出现这个错误。", 8),
    0x80070005: ("没有读取权限", "把文件复制到「桌面」再打开；学校网络盘权限过期时也会这样。", 9),
    0x80010105: ("PowerPoint 服务端异常", "重启 PowerPoint；仍不行则对 Office 做一次「快速修复」。", 10),
    0x800706BE: ("进程意外终止", "PowerPoint 崩溃退出，多为加载项或显卡驱动引起；见「安全模式对比」一项。", 9),
}

# 顺序即判定优先级：具体失败族必须排在 link 之前，否则「无法打开…找不到…」会被归成链接问题
DIALOG_FAMILIES = (
    (r"修复|repair", "repair"),
    (r"密码|password", "password"),
    (r"受保护的视图|protected view", "protected"),
    (r"正在使用|in use|locked", "busy"),
    (r"无法打开|无法读取|不能打开|cannot open|can't open", "cannot_open"),
    (r"内存|memory", "memory"),
    (r"链接|找不到|missing", "link"),
    (r"字体|font", "font"),
)

FAILURE_DIALOG_FAMILIES = ("repair", "cannot_open", "password", "busy")


def classify_message(text):
    """Map one PowerPoint sentence to (title, action, confidence, priority) or None."""
    if not text:
        return None
    lowered = str(text)
    for pattern, title, action, confidence, priority in MESSAGE_FAMILIES:
        if re.search(pattern, lowered, re.IGNORECASE):
            return title, action, confidence, priority
    return None


def classify_hresult(hresult):
    """Map a COM HRESULT to (title, action, confidence, priority) or None."""
    try:
        code = int(hresult) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None
    hint = HRESULT_HINTS.get(code)
    if hint is None:
        return None
    title, action, priority = hint
    return title, action, "有检测依据", priority


def classify_dialog(text):
    """Collapse a dialog's text into a stable family key (empty string when unknown)."""
    if not text:
        return ""
    for pattern, family in DIALOG_FAMILIES:
        if re.search(pattern, str(text), re.IGNORECASE):
            return family
    return ""


def parse_wer(text):
    """Parse a Windows Error Reporting .wer file into the fields we use."""
    values = {}
    for line in str(text).splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        signature = re.match(r"Sig\[(\d+)\]\.Value$", key.strip())
        if signature:
            values["sig" + signature.group(1)] = value.strip()
        elif key.strip() in ("AppName", "EventType", "ReportIdentifier"):
            values[key.strip()] = value.strip()
    return {
        "app": values.get("AppName", ""),
        "event": values.get("EventType", ""),
        "module": values.get("sig3", ""),
        "code": values.get("sig6", ""),
        "report": values.get("ReportIdentifier", ""),
    }


def strings_from_binary(data, minimum=6):
    """Pull readable paths out of REG_BINARY blobs (Office keeps UTF-16 paths there).

    Registry UTF-16 blobs are often not aligned to even offsets, so both alignments are
    decoded before scanning; that is what makes CJK paths survive.
    """
    if not data:
        return ()
    raw = data if isinstance(data, bytes) else str(data).encode("utf-8", "ignore")
    texts = [raw[offset:].decode("utf-16-le", "ignore") for offset in (0, 1)]
    texts.append(raw.decode("latin-1", "ignore"))
    found = []
    for text in texts:
        for chunk in re.findall(r"[\x20-\x7e\u4e00-\u9fff]{%d,}" % minimum, text):
            chunk = chunk.strip()
            if any(mark in chunk for mark in ("\\", "/", ".", ":")) and chunk not in found:
                found.append(chunk)
    return tuple(found)


def diff_resiliency(before, after):
    """Items that newly appeared in a pair of resiliency snapshots."""
    fresh = []
    for key, values in (after or {}).items():
        old = set((before or {}).get(key, ()))
        for value in values:
            if value not in old:
                fresh.append((key, value))
    return fresh


def expected_slides(path):
    """Slide count from ppt/presentation.xml sldIdLst, falling back to slide parts."""
    try:
        from zipfile import ZipFile
        with ZipFile(path) as archive:
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(archive.read("ppt/presentation.xml"))
                listed = sum(1 for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "sldId")
            except Exception:
                listed = 0
            if listed:
                return listed
            return len([name for name in archive.namelist()
                        if re.match(r"ppt/slides/slide\d+\.xml$", name)])
    except Exception:
        return 0


def file_signature(path):
    """(size, mtime_ns, sha1) so a probe can prove it did not touch the original."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    digest = hashlib.sha1()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return (stat.st_size, stat.st_mtime_ns, "")
    return (stat.st_size, stat.st_mtime_ns, digest.hexdigest())


# ---------------------------------------------------------------- result objects


@dataclass(frozen=True)
class ComResult:
    state: str = "skipped"
    seconds: float = 0.0
    hresult: int = 0
    message: str = ""
    slides: int = 0
    expected: int = 0
    bad_shapes: tuple = ()
    bad_slides: tuple = ()
    dialog: str = ""
    vendor: str = ""          # "wps" = 本次打开由 WPS 应答，结论不能算作 PowerPoint 的


@dataclass(frozen=True)
class EnvEvidence:
    state: str = "skipped"
    disabled: tuple = ()
    startup: tuple = ()
    recovery: tuple = ()
    crashes: tuple = ()
    events: tuple = ()
    note: str = ""

    @property
    def dirty(self):
        return bool(self.disabled or self.startup or self.recovery or self.crashes or self.events)


@dataclass(frozen=True)
class SafeResult:
    state: str = "skipped"
    seconds: float = 0.0
    dialog: str = ""


# ---------------------------------------------------------------- verdict

def quant_slides_lost(com):
    # 只有真的打开成功才谈"页数变少"；blocked/timeout/failed 时 slides 恒为 0，不能据此报丢页
    return com.state == "ok" and bool(com.expected and com.slides < com.expected)


def verdict(com, safe, env):
    """Combine the signals into (headline, explanation, confidence, priority)."""
    if com.state == "skipped":
        return None
    if com.state == "blocked":
        return None            # 工具自身缺组件，不能据此推断课件好坏
    if com.state == "aborted":
        return ("检测没能开始：课件副本都复制不出来",
                "EveTech 会先把课件复制到临时目录再交给 PowerPoint，这一步失败了，"
                "所以「PowerPoint 打不开」这个结论并不成立。",
                "有检测依据", 8)
    if com.state == "timeout":
        if safe.state == "failed":
            return ("安全模式下也打不开：问题在文件本身",
                    "常规打开超时，但安全模式明确给出了失败提示，按文件本身的问题处理。", "有检测依据", 3)
        if safe.state == "opened":
            return ("安全模式下能打开：问题出在本机的加载项或插件",
                    "常规打开迟迟不返回，但安全模式（/safe）能打开，说明文件本身可读，"
                    "是本机的加载项或插件在拖慢它。", "有检测依据", 4)
        return ("打开测试超时",
                "PowerPoint 在限定时间内没有给出结果，通常是文件过大或卡在修复提示上。", "现象", 12)
    if com.state == "failed":
        if com.vendor == "wps":
            # 本机没有 PowerPoint 时，PowerPoint.Application 由 WPS 应答：这是 WPS 的判断，
            # 不能据此下「课件坏了」的结论（真机实测 CF-002：本机只有 WPS）
            return ("本机的 WPS 打不开这个课件（PowerPoint 的结论未知）",
                    "本机没有 Microsoft PowerPoint，PowerPoint.Application 由 WPS 应答，"
                    "所以这次失败是 WPS 给出的。请到装了 PowerPoint 的电脑上复核一次再下结论。",
                    "有检测依据", 9)
        title = classify_message(com.message) or classify_hresult(com.hresult)
        if safe.state == "opened":
            return ("安全模式下能打开：问题出在本机的加载项或插件",
                    "同一个文件在 PowerPoint 安全模式（/safe）下打开成功，说明文件本身可读，"
                    "是加载项、COM 插件或显卡加速在拖后腿。", "有检测依据", 4)
        if safe.state == "failed":
            return ("同一个文件在安全模式下也打不开：问题在文件本身",
                    "PowerPoint 安全模式仍失败，基本可以排除加载项，按文件损坏或格式问题处理。", "有检测依据", 3)
        base = title[0] if title else "PowerPoint 无法打开这个课件"
        return (base, "本次实际调用了 PowerPoint 打开课件，它返回了失败。",
                (title[2] if title else "现象"), (title[3] if title else 10))
    if com.bad_shapes or com.bad_slides:
        return ("课件能打开，但有元素在渲染时出错",
                "PowerPoint 打开了课件，遍历时有个别页或形状无法导出，放映到那一页可能黑屏或卡住。",
                "有检测依据", 6)
    if quant_slides_lost(com):
        return ("课件能打开，但页数比文件里记录的少",
                "打开后读到的页数少于 presentation.xml 里登记的页数，说明 PowerPoint 丢弃了部分页面。",
                "有检测依据", 5)
    if re.search(FAILURE_TEXT, com.dialog, re.IGNORECASE):
        return ("能打开，但打开过程中弹过失败提示",
                "PowerPoint 最终打开了课件，但期间出现过失败类对话框，说明它可能跳过了部分内容：%s" % com.dialog,
                "有检测依据", 7)
    if env.dirty:
        return ("课件能打开，但这台电脑的 PowerPoint 有异常痕迹",
                "文件本身没问题，环境里有崩溃或被禁用的加载项记录，可对照「环境痕迹」一项处理。",
                "有检测依据", 9)
    if com.vendor == "wps":
        return ("本机的 WPS 能正常打开这个课件",
                "本机没有 Microsoft PowerPoint，PowerPoint.Application 由 WPS 应答；"
                "若老师那边用的是 PowerPoint，建议用 PowerPoint 再确认一次。",
                "有检测依据", 18)
    return ("本机 PowerPoint 能正常打开这个课件",
            "实测打开成功、逐页渲染无异常。若老师那边仍打不开，差异在对方电脑的 Office 版本或权限。",
            "有检测依据", 18)


# ---------------------------------------------------------------- infra helpers

def _short(error, limit=160):
    text = str(error).replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


def cleanup_stale_dirs(max_age=3600.0):
    """Sweep probe copies left behind by a timeout (PowerPoint may still hold them)."""
    try:
        root = Path(tempfile.gettempdir())
        for item in root.glob(PROBE_PREFIX + "*"):
            try:
                if time.time() - item.stat().st_mtime <= max_age:
                    continue
                shutil.rmtree(item, ignore_errors=True)
            except OSError:
                continue
    except Exception:
        pass


def _copy_for_probe(deck):
    """Copy to a temp dir so PowerPoint's repair/autosave never lands on the original."""
    folder = Path(tempfile.mkdtemp(prefix=PROBE_PREFIX))
    target = folder / ("probe-" + Path(deck).name)
    shutil.copy2(deck, target)
    return target, folder


def _com_error_parts(error):
    """pythoncom.com_error -> (hresult, human message)."""
    args = getattr(error, "args", ()) or ()
    hresult, message = 0, _short(error)
    if args:
        try:
            hresult = int(args[0]) & 0xFFFFFFFF
        except (TypeError, ValueError):
            hresult = 0
        if len(args) > 2 and isinstance(args[2], tuple) and len(args[2]) >= 3 and args[2][2]:
            message = _short(args[2][2])
        elif len(args) > 1 and args[1]:
            message = _short(args[1])
    return hresult, message


def _process_basename(pid):
    try:
        import psutil
        return (psutil.Process(pid).name() or "").upper()
    except Exception:
        return ""


def _windows(only_dialogs=True, processes=(POWERPOINT_IMAGE,)):
    """[(title, [child texts], pid)] for visible windows owned by the given processes."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    except Exception:
        return []
    found = []

    def children_of(handle):
        texts = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def each_child(child, _param):
            buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(child, buffer, 512)
            text = buffer.value.strip()
            if text:
                texts.append(text)
            return True

        try:
            user32.EnumChildWindows(handle, each_child, 0)
        except Exception:
            pass
        return texts

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def each_window(handle, _param):
        try:
            if not user32.IsWindowVisible(handle):
                return True
            title = ctypes.create_unicode_buffer(512)
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, 512)
            user32.GetClassNameW(handle, class_name, 256)
            dialog = class_name.value in ("#32770", "NetUIHWND", "NUIDialog")
            if only_dialogs and not dialog:
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            if processes and _process_basename(pid.value) not in processes:
                return True
            found.append((title.value.strip(), children_of(handle) if dialog else [], pid.value))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(each_window, 0)
    except Exception:
        pass
    return found


class DialogWatcher:
    """Poll PowerPoint's visible dialogs while a file is being read."""

    FAILURE = FAILURE_TEXT

    def __init__(self, seconds=0.0, interval=0.25):
        self.deadline = time.time() + seconds if seconds else 0.0
        self.interval = interval
        self.seen = []
        self._thread = None
        self._stop = False

    def start(self):
        if sys.platform != "win32":
            return self
        import threading
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        while not self._stop:
            if self.deadline and time.time() > self.deadline:
                break
            try:
                for title, children, _pid in _windows():
                    for text in (title,) + tuple(children):
                        if text and text not in self.seen:
                            self.seen.append(text)
            except Exception:
                pass
            time.sleep(self.interval)

    def stop(self):
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return self

    def text(self):
        """The first collected line that actually reads like a failure (else empty)."""
        for item in list(self.seen):
            if re.search(self.FAILURE, item, re.IGNORECASE):
                return item
        return ""

    def family(self):
        return classify_dialog(self.text())


# ---------------------------------------------------------------- P0 COM probe

def com_probe(deck, expected=0, timeout=150.0):
    """Open a temp copy through PowerPoint COM; return what it says and what renders."""
    if sys.platform != "win32":
        return ComResult(state="skipped", message="尚未检测 · 需要 Windows 与 PowerPoint。")
    try:
        copy, folder = _copy_for_probe(deck)
    except OSError as error:
        return ComResult(state="aborted", message="无法复制课件副本：%s" % _short(error))
    result = _com_open(str(copy), expected or 0, timeout)
    if result.state != "timeout":
        shutil.rmtree(folder, ignore_errors=True)      # 超时保留：PowerPoint 可能还持有它
    return result


def _com_open(path, expected, timeout):
    import threading
    watcher = DialogWatcher(seconds=timeout + 30).start()
    box = {}

    def work():
        try:
            box["value"] = _com_open_direct(path, expected, watcher)
        except Exception as error:                      # noqa: BLE001 - surface as evidence
            box["value"] = ComResult(state="failed", message=_short(error), dialog=watcher.text())

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout + 15)
    if worker.is_alive():
        watcher.stop()
        return ComResult(state="timeout", seconds=timeout, dialog=watcher.text(),
                         message="PowerPoint 在 %.0f 秒内没有返回，通常是卡在修复提示或文件过大。"
                                 "若任务管理器里出现多余的 POWERPNT.EXE，可手动结束。" % timeout)
    watcher.stop()
    return box.get("value") or ComResult(state="failed", message="打开测试没有返回任何结果。")


def _com_open_direct(path, expected, watcher):
    try:
        import pythoncom
        import win32com.client
    except ImportError as error:
        # 组件缺失 ≠ 课件打不开。真机实测（CF-002）：只按 requirements 装的环境曾把它报成
        # 「打开失败：No module named 'pythoncom'」，把能正常打开的课件判成打不开。
        return ComResult(state="blocked", expected=expected, dialog=watcher.text(),
                         message="检测组件缺失（%s）" % _short(error, 80))

    started = time.time()
    pythoncom.CoInitialize()
    app = None
    presentation = None
    folder = None
    try:
        folder = Path(tempfile.mkdtemp(prefix=PROBE_PREFIX + "render-"))
        # Office can return the user's existing instance even through DispatchEx.
        # Only the Presentation opened below (a hidden, read-only copy) is ours.
        app = win32com.client.DispatchEx("PowerPoint.Application")
        # WPS 也注册 PowerPoint.Application 这个入口；先问清楚应答的是谁，
        # 否则 WPS 打不开某个 PowerPoint 能打开的课件时会被报成「文件损坏」
        try:
            vendor = "wps" if _looks_like_wps(getattr(app, "Path", "")) else ""
        except Exception:
            vendor = ""
        try:
            presentation = app.Presentations.Open(str(path), True, False, False)
        except pythoncom.com_error as error:
            hresult, message = _com_error_parts(error)
            return ComResult(state="failed", seconds=time.time() - started, hresult=hresult,
                             message=message, expected=expected, dialog=watcher.text(), vendor=vendor)
        slides = int(presentation.Slides.Count)
        bad_slides, bad_shapes = [], []
        for index in range(1, slides + 1):
            slide = presentation.Slides(index)
            try:
                slide.Export(str(folder / ("slide%03d.png" % index)), "PNG", 640, 360)
            except Exception as error:                 # noqa: BLE001
                bad_slides.append((index, _short(error, 120)))
        for index, _detail in bad_slides:
            slide = presentation.Slides(index)
            try:
                limit = int(slide.Shapes.Count)
            except Exception:
                continue
            for position in range(1, limit + 1):
                shape = slide.Shapes(position)
                try:
                    name = str(shape.Name)
                except Exception:
                    name = "第 %d 个形状" % position
                try:
                    shape.Export(str(folder / ("s%03d_%03d.png" % (index, position))), "PNG", 320, 180)
                except Exception as error:             # noqa: BLE001
                    bad_shapes.append((index, name, _short(error, 120)))
        return ComResult(state="ok", seconds=time.time() - started, slides=slides, expected=expected,
                         bad_slides=tuple(bad_slides), bad_shapes=tuple(bad_shapes),
                         dialog=watcher.text(), vendor=vendor)
    finally:
        watcher.stop()
        try:
            if presentation is not None:
                presentation.Close()
        except Exception:
            pass
        shape = slide = presentation = app = None
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------- P1 environment

def resiliency_snapshot():
    """{'DisabledItems': (...), 'StartUpItems': (...), 'DocumentRecovery': (...)}.

    Office keeps these as sub-keys whose values ("0", "1", ...) are REG_BINARY blobs;
    some versions also write a value directly on the Resiliency key, so both layouts
    are read.
    """
    if sys.platform != "win32":
        return {}
    try:
        import winreg
    except Exception:
        return {}
    snapshot = {"DisabledItems": [], "StartUpItems": [], "DocumentRecovery": []}
    for version in ("16.0", "15.0", "14.0", "12.0"):
        base = r"Software\Microsoft\Office\%s\PowerPoint\Resiliency" % version
        for name, bucket in snapshot.items():
            for item in _subkey_strings(winreg, base + "\\" + name):
                if item not in bucket:
                    bucket.append(item)
            for item in _value_strings(winreg, base, name):
                if item not in bucket:
                    bucket.append(item)
    return {key: tuple(values[:64]) for key, values in snapshot.items()}


def _subkey_strings(winreg, path):
    found = []
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return found
    with key:
        limit = 0
        while limit < 64:
            try:
                _name, value, _kind = winreg.EnumValue(key, limit)
            except OSError:
                break
            limit += 1
            found.extend(strings_from_binary(value))
    return found


def _value_strings(winreg, base, name):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
    except OSError:
        return []
    with key:
        try:
            value, _kind = winreg.QueryValueEx(key, name)
        except OSError:
            return []
    return list(strings_from_binary(value))


def env_evidence(days=14, snapshot=None):
    """What Windows already recorded about PowerPoint in the last days."""
    if sys.platform != "win32":
        return EnvEvidence(state="skipped", note="尚未检测 · 需要 Windows。")
    snap = snapshot if snapshot is not None else resiliency_snapshot()
    note, crashes, events = "", [], []
    try:
        crashes = _wer_crashes()
    except Exception as error:                          # noqa: BLE001
        note = "读取崩溃报告失败：%s" % _short(error)
    try:
        events = _powerpoint_events(days)
    except Exception as error:                          # noqa: BLE001
        extra = "读取事件日志失败：%s" % _short(error)
        note = (note + "；" + extra) if note else extra
    return EnvEvidence(state="ok",
                       disabled=tuple(snap.get("DisabledItems", ())),
                       startup=tuple(snap.get("StartUpItems", ())),
                       recovery=tuple(snap.get("DocumentRecovery", ())),
                       crashes=tuple(crashes), events=tuple(events), note=note)


def _wer_crashes(limit=5):
    programdata = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    local = os.environ.get("LOCALAPPDATA") or ""
    roots = [Path(programdata) / "Microsoft" / "Windows" / "WER" / "ReportArchive"]
    if local:
        roots.append(Path(local) / "Microsoft" / "Windows" / "WER" / "ReportArchive")
    hits = []
    for root in roots:
        try:
            if not root.is_dir():
                continue
            folders = []
            for item in root.iterdir():
                try:
                    if item.is_dir() and item.name.lower().startswith("appcrash_powerpnt"):
                        folders.append((item.stat().st_mtime, item))
                except OSError:
                    continue
            folders.sort(reverse=True)
        except OSError:
            continue
        for stamp, folder in folders[:limit]:
            text = ""
            try:
                candidates = [item for item in folder.iterdir() if item.name.lower() == "report.wer"]
            except OSError:
                candidates = []
            for report in candidates:
                for encoding in ("utf-16", "utf-8", "latin-1"):
                    try:
                        text = report.read_text(encoding=encoding, errors="ignore")
                    except OSError:
                        text = ""
                    if "AppName" in text:
                        break
                if text:
                    break
            if not text:
                continue
            parsed = parse_wer(text)
            parsed["time"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))
            hits.append(parsed)
    return hits


def _powerpoint_events(days=14, limit=5):
    try:
        import win32evtlog
    except Exception:
        return []
    deadline = time.time() - days * 86400
    try:
        handle = win32evtlog.OpenEventLog(None, "Application")
    except Exception:
        return []
    flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
    hits, guard = [], 0
    try:
        while len(hits) < limit and guard < 40:
            guard += 1
            records = win32evtlog.ReadEventLog(handle, flags, 0)
            if not records:
                break
            for record in records:
                if int(record.EventID) & 0xFFFF not in (1000, 1001, 1002):
                    continue
                message = " ".join(str(item) for item in (record.StringInserts or ()))
                lowered = message.lower()
                if "powerpnt" not in lowered and "powerpoint" not in lowered:
                    continue
                stamp = record.TimeGenerated.timestamp()
                if stamp < deadline:
                    continue
                hits.append({"id": int(record.EventID) & 0xFFFF,
                             "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp)),
                             "message": _short(message, 240)})
                if len(hits) >= limit:
                    break
    except Exception:
        pass
    finally:
        try:
            win32evtlog.CloseEventLog(handle)
        except Exception:
            pass
    return hits


# ---------------------------------------------------------------- P2 safe mode

def _looks_like_wps(install_path):
    """WPS 判定统一放在 preflight，避免两处实现各自漂移。"""
    from .preflight import looks_like_wps
    return looks_like_wps(install_path)


def _wps_present():
    """本机是否有 WPS 演示（用于把「做不了安全模式对比」解释准）。"""
    try:
        from .preflight import office_alternative
        return office_alternative() is not None
    except Exception:
        return False


def powerpoint_exe():
    """真正的 POWERPNT.EXE；只有 WPS 的机器返回空串。

    WPS 会把 App Paths\\POWERPNT.EXE 指向自己的 wps.exe，也会接管
    PowerPoint.Application 这个 COM 入口，所以两条路都要核对安装路径：
    只读登记会把 WPS 当成 PowerPoint（真机实测 CF-002：WPS 的 /safe 会立刻退出）。
    """
    if sys.platform != "win32":
        return ""
    try:
        from .preflight import com_server_path, powerpnt_path
    except Exception:
        return ""
    try:
        file = powerpnt_path()
    except Exception:
        file = None
    if file is not None and Path(file).is_file():
        return str(file)
    try:
        server = com_server_path()
    except Exception:
        server = None
    # 第二条路也必须核对文件名：LocalServer32 里注册什么 exe 都可能，不能拿它去跑 /safe
    if (server is not None and Path(server).is_file()
            and Path(server).name.casefold() == POWERPOINT_IMAGE.casefold()
            and not _looks_like_wps(server)):
        return str(server)
    return ""


def _powerpnt_pids():
    try:
        import psutil
        return {proc.pid for proc in psutil.process_iter(["name"])
                if (proc.info.get("name") or "").upper() == POWERPOINT_IMAGE}
    except Exception:
        return set()


def safe_mode_probe(deck, seconds=25.0):
    """Open a copy with `POWERPNT.EXE /safe` and watch for a rejection dialog."""
    if sys.platform != "win32":
        return SafeResult(state="skipped")
    exe = powerpoint_exe()
    if not exe:
        if _wps_present():
            return SafeResult(state="skipped",
                              dialog="本机只有 WPS：安全模式（/safe）对比仅 Microsoft PowerPoint 支持，已跳过。")
        return SafeResult(state="skipped", dialog="本机没有安装 Microsoft PowerPoint，无法做安全模式对比。")
    import subprocess
    try:
        copy, folder = _copy_for_probe(deck)
    except OSError as error:
        return SafeResult(state="skipped", dialog="无法复制课件副本：%s" % _short(error))
    before = _powerpnt_pids()
    started = time.time()
    watcher = DialogWatcher(seconds=seconds).start()
    try:
        process = subprocess.Popen([exe, "/safe", str(copy)])
    except OSError as error:
        watcher.stop()
        shutil.rmtree(folder, ignore_errors=True)
        return SafeResult(state="skipped", dialog="启动安全模式失败：%s" % _short(error))
    try:
        opened = False
        while time.time() - started < seconds:
            time.sleep(0.5)
            if watcher.family() in FAILURE_DIALOG_FAMILIES:
                return SafeResult(state="failed", seconds=time.time() - started, dialog=watcher.text())
            if _shows_presentation(copy.name, before):
                opened = True
                break
            # 进程已经退出又没出现窗口：再等下去只会白等满 25 秒（WPS 的 /safe 就是这样）
            if process.poll() is not None:
                return SafeResult(state="skipped", seconds=time.time() - started,
                                  dialog="安全模式启动后立即退出（返回码 %s），本次没有可对比的结果。"
                                          % process.returncode)
        return SafeResult(state="opened" if opened else "timeout",
                          seconds=time.time() - started, dialog=watcher.text())
    finally:
        watcher.stop()
        try:
            process.terminate()
        except Exception:
            pass
        shutil.rmtree(folder, ignore_errors=True)


def _shows_presentation(name, exclude_pids=()):
    """True when a new PowerPoint window's title contains the file stem."""
    if sys.platform != "win32":
        return False
    stem = Path(name).stem
    if not stem:
        return False
    for title, _children, pid in _windows(only_dialogs=False):
        if pid in exclude_pids:
            continue
        if stem.lower() in str(title).lower():
            return True
    return False


# ---------------------------------------------------------------- assembly

def inspect_open(deck_path, feedback="pending", safe=True, env=True, timeout=150.0):
    """Full evidence pass for a deck that will not open."""
    cleanup_stale_dirs()
    path = Path(deck_path)
    title = "课件能否打开"
    if path.suffix.lower() not in (".pptx", ".pptm", ".ppsx", ".ppsm", ".potx", ".potm"):
        return Report("open", title,
                      [Check("open_deck", "课件文件", "unknown", "尚未检测 · 请选择 .pptx/.pptm 课件。")],
                      facts={"case": "open"})
    if not path.is_file():
        return Report("open", title,
                      [Check("open_deck", "课件文件", "problem", "文件不存在：%s" % path)],
                      facts={"case": "open"})

    before = file_signature(path)
    expected = expected_slides(path)
    snapshot_before = resiliency_snapshot() if env else {}
    com = com_probe(path, expected=expected, timeout=timeout)
    environment = (env_evidence(snapshot=resiliency_snapshot()) if env
                   else EnvEvidence(state="skipped", note="本次未做环境检查。"))
    fresh = diff_resiliency(snapshot_before, {
        "DisabledItems": environment.disabled,
        "StartUpItems": environment.startup,
        "DocumentRecovery": environment.recovery,
    }) if env else []
    if safe and com.state not in ("ok", "blocked"):
        safe_result = safe_mode_probe(path)
    else:
        safe_result = SafeResult(state="skipped", dialog="本次未做安全模式对比。")
    after = file_signature(path)

    # 四项检查始终齐全：未做的项显式写「未做」，比整行消失更容易判断报告是否完整
    checks = [_com_check(com), _env_check(environment), _safe_check(safe_result, com),
              _integrity_check(before, after)]

    findings = _findings(com, safe_result, environment, fresh)
    facts = {
        "case": "open",
        "com_state": com.state, "com_seconds": round(com.seconds, 1), "hresult": com.hresult,
        "slides": com.slides, "expected_slides": expected,
        "bad_slides": len(com.bad_slides), "bad_shapes": len(com.bad_shapes),
        "safe_state": safe_result.state,
        "com_vendor": com.vendor or ("office" if com.state == "ok" else ""),
        "dialog": com.dialog or (safe_result.dialog if safe_result.state != "skipped" else ""),
        "crashes": len(environment.crashes), "disabled": len(environment.disabled),
        "fresh_resiliency": len(fresh), "feedback": feedback,
    }
    return Report("open", title, checks, sort_findings(findings), facts)


def _com_check(com):
    if com.state == "skipped":
        return Check("open_com", "PowerPoint 实际打开测试", "unknown", com.message or "尚未检测。")
    if com.state == "blocked":
        return Check("open_com", "PowerPoint 实际打开测试", "unknown",
                     "%s。这不是课件的问题，请重装或更新 EveTech 后再检测。" % com.message)
    if com.state == "aborted":
        return Check("open_com", "PowerPoint 实际打开测试", "unknown",
                     "检测没能开始：%s（这不是 PowerPoint 的判断，请先解决复制失败的原因）" % com.message)
    if com.state == "failed":
        suffix = ("（错误码 0x%08X）" % com.hresult) if com.hresult else ""
        detail = "打开失败%s：%s" % (suffix, com.message or "PowerPoint 没有给出原因")
        if com.vendor == "wps":
            detail += "\n（本次由本机的 WPS 应答，不是 Microsoft PowerPoint 的结论。）"
        return Check("open_com", "PowerPoint 实际打开测试",
                     "warning" if com.vendor == "wps" else "problem", detail)
    if com.state == "timeout":
        return Check("open_com", "PowerPoint 实际打开测试", "unknown", com.message)
    lines = ["PowerPoint 打开成功，读到 %d 页（用时 %.1f 秒）。" % (com.slides, com.seconds)]
    if quant_slides_lost(com):
        lines.append("文件里登记了 %d 页，打开后少了 %d 页。" % (com.expected, com.expected - com.slides))
    if com.bad_slides:
        lines.append("有 %d 页无法导出画面：第 %s 页。" %
                     (len(com.bad_slides), "、".join(str(i) for i, _d in com.bad_slides[:6])))
    if com.bad_shapes:
        lines.append("定位到 %d 个形状渲染失败。" % len(com.bad_shapes))
    if com.dialog:
        lines.append("打开过程中出现过提示：%s" % com.dialog)
    if com.vendor == "wps":
        lines.append("本次由本机的 WPS 应答打开（PowerPoint 入口被 WPS 接管），结论按 WPS 的表现理解。")
    status = "problem" if (com.bad_slides or com.bad_shapes or quant_slides_lost(com)) else "ok"
    return Check("open_com", "PowerPoint 实际打开测试", status, "\n".join(lines))


def _env_check(environment):
    if environment.state == "skipped":
        return Check("open_env", "PowerPoint 环境痕迹", "unknown", environment.note or "尚未检测。")
    lines, status = [], "ok"
    if environment.disabled or environment.startup:
        status = "warning"
        lines.append("有 %d 条被 PowerPoint 禁用的加载项/启动项记录。" %
                     (len(environment.disabled) + len(environment.startup)))
        for item in (environment.disabled + environment.startup)[:4]:
            lines.append("• %s" % item)
    if environment.recovery:
        status = "warning"
        lines.append("有 %d 条文档恢复记录（上次异常退出时留下的）。" % len(environment.recovery))
    if environment.crashes:
        status = "warning"
        for crash in environment.crashes[:3]:
            lines.append("• %s 崩溃：模块 %s，异常码 %s" %
                         (crash.get("time", "?"), crash.get("module") or "未知", crash.get("code") or "未知"))
    if environment.events:
        status = "warning"
        for event in environment.events[:3]:
            lines.append("• %s 事件 %s：%s" % (event.get("time"), event.get("id"), event.get("message")))
    if not lines:
        if environment.note:
            status = "unknown"
            lines.append("本次未能完整读取 Office 记录：%s" % environment.note)
        else:
            lines.append("没有发现崩溃、禁用加载项或恢复记录。")
    elif environment.note:
        lines.append(environment.note)
    return Check("open_env", "PowerPoint 环境痕迹", status, "\n".join(lines))


def _safe_check(safe_result, com):
    if safe_result.state == "skipped":
        if com.state == "blocked":
            return Check("open_safe", "安全模式对比", "unknown",
                         "检测组件缺失，本次未做安全模式对比（先修好 EveTech 的组件再检测）。")
        note = safe_result.dialog or "本次未做安全模式对比。"
        if com.state == "ok":
            if note == "本次未做安全模式对比。":
                return Check("open_safe", "安全模式对比", "unknown", "课件已能正常打开，" + note)
            return Check("open_safe", "安全模式对比", "unknown",
                         "课件已能正常打开，未做安全模式对比：%s" % note)
        return Check("open_safe", "安全模式对比", "unknown", note)
    if safe_result.state == "opened":
        return Check("open_safe", "安全模式对比", "warning",
                     "用 PowerPoint 安全模式（/safe）打开同一个文件成功（%.1f 秒），"
                     "说明文件本身可读，问题在本机的加载项或环境。" % safe_result.seconds)
    if safe_result.state == "failed":
        return Check("open_safe", "安全模式对比", "problem",
                     "安全模式下也打不开（%.1f 秒），可排除加载项，按文件本身的问题处理。%s"
                     % (safe_result.seconds, ("提示：" + safe_result.dialog) if safe_result.dialog else ""))
    return Check("open_safe", "安全模式对比", "unknown",
                 "安全模式在限定时间内没有给出明确结果%s。"
                 % (("，出现提示：" + safe_result.dialog) if safe_result.dialog else ""))


def _integrity_check(before, after):
    if before is None or after is None:
        return Check("open_integrity", "原文件完整性", "unknown", "文件在检测前后不可读。")
    if before == after:
        return Check("open_integrity", "原文件完整性", "ok", "检测前后文件签名一致，原课件没有被改动。")
    return Check("open_integrity", "原文件完整性", "problem",
                 "检测前后文件签名发生变化，可能是检测期间有程序改写了课件。")


def _findings(com, safe_result, environment, fresh=()):
    items = []
    blocked = com.state == "blocked"
    if blocked:
        # 真机实测（CF-002）：缺 pywin32 时若报成「打开失败」，会把好课件判成坏课件
        items.append(Finding("检测组件缺失，本次没能验证课件", com.message,
                             "这不是课件的问题：重装或更新 EveTech（缺少 pywin32 组件）后再检测。",
                             "有检测依据", 3))
    combined = None if blocked else verdict(com, safe_result, environment)
    headline = combined[0] if combined else ""
    if combined is not None:
        _headline, explanation, confidence, priority = combined
        evidence = []
        if com.state == "failed":
            evidence.append("COM 打开失败：%s%s" % (com.message,
                                                  ("（0x%08X）" % com.hresult) if com.hresult else ""))
        elif com.state == "ok":
            evidence.append("COM 打开成功，%d 页，渲染异常 %d 页" % (com.slides, len(com.bad_slides)))
        if com.state == "aborted":
            evidence.append(com.message)
        if safe_result.state != "skipped":
            evidence.append("安全模式结果：%s" % OPEN_STATES.get(safe_result.state, safe_result.state))
        if com.state == "aborted":
            action = "先把课件复制到桌面或 D 盘再试，并确认临时目录所在磁盘还有剩余空间。"
        elif safe_result.state == "opened" and com.state in ("failed", "timeout"):
            action = "在「文件 → 选项 → 加载项」里逐个禁用后重试；也可先用 PowerPoint 安全模式应急上课。"
        else:
            classified = classify_message(com.message) or classify_hresult(com.hresult)
            action = classified[1] if classified else "按下方各项检查逐条处理。"
        items.append(Finding(headline, "；".join(evidence) or explanation, action, confidence, priority))
    if com.dialog and not blocked:
        extra = classify_message(com.dialog)
        if extra is not None and extra[0] != headline:
            items.append(Finding("PowerPoint 提示：%s" % extra[0],
                                 "打开过程中读到的提示文本：%s" % com.dialog,
                                 extra[1], extra[2], extra[3]))
    for index, name, detail in com.bad_shapes[:5]:
        items.append(Finding("第 %d 页的「%s」无法渲染" % (index, name),
                             "把该形状单独导出成图片时失败：%s" % detail,
                             "删除或重新插入这个形状；若是视频，改用 MP4/H.264 后重新插入。",
                             "有检测依据", 7))
    for index, detail in com.bad_slides[:3]:
        if any(index == bad[0] for bad in com.bad_shapes):
            continue
        items.append(Finding("第 %d 页导出画面失败" % index,
                             "整页导出 PNG 时报错：%s" % detail,
                             "把这一页里的视频/图片换成本机可播放的版本，或把这一页另存为图片再插入。",
                             "有检测依据", 9))
    if quant_slides_lost(com):
        items.append(Finding("打开后丢失了 %d 页" % (com.expected - com.slides),
                             "presentation.xml 登记 %d 页，PowerPoint 只读到 %d 页。" % (com.expected, com.slides),
                             "用 PowerPoint 的「打开并修复」，随后立即另存为新文件。", "有检测依据", 5))
    if fresh and com.state != "blocked":
        key, value = fresh[0]
        label = {"DisabledItems": "禁用加载项", "StartUpItems": "启动项", "DocumentRecovery": "文档恢复"}.get(key, key)
        items.append(Finding("检测期间 PowerPoint 多了一条%s记录" % label,
                             "检测前后对比，Resiliency 新增：%s" % value,
                             "这说明问题与本次打开直接相关；先用安全模式应急，再逐一禁用加载项排查。",
                             "有检测依据", 4))
    for crash in environment.crashes[:1]:
        items.append(Finding("最近一次 PowerPoint 崩溃发生在 %s" % crash.get("time", ""),
                             "崩溃模块 %s，异常码 %s（历史记录，与本次检测无关）。"
                             % (crash.get("module") or "未知", crash.get("code") or "未知"),
                             ("先修好 EveTech 的组件，再看这一条：" if blocked else "")
                             + "对照崩溃模块判断是加载项还是显卡驱动；先做一次 Office「快速修复」。",
                             "有检测依据", 11))
    if environment.disabled and not fresh:
        items.append(Finding("PowerPoint 曾禁用加载项",
                             "禁用记录：%s" % "；".join(environment.disabled[:3]),
                             ("先修好 EveTech 的组件，再看这一条：" if blocked else "")
                             + "在「文件 → 选项 → 加载项」里逐个启用排查，找出会让课件打不开的那个。",
                             "有检测依据", 12))
    if not items and com.state == "ok":
        items.append(Finding("本机能打开，问题可能在对方电脑",
                             "PowerPoint 实际打开并逐页导出均正常。",
                             "把课件连同字体一起打包发给对方，或让对方用同一版本 Office 打开。",
                             "有检测依据", 20))
    return items
