"""Read classroom readiness signals using local Windows APIs."""

import ctypes as ct
import os
import sys
import time
from ctypes import wintypes as w
from dataclasses import dataclass
from pathlib import Path

import psutil

from .audio import inspect_audio
from .model import Check, Finding, Report, sort_findings
from .pnp import present_peripherals


class Luid(ct.Structure):
    _fields_ = [("low", w.DWORD), ("high", w.LONG)]


class Rational(ct.Structure):
    _fields_ = [("numerator", w.UINT), ("denominator", w.UINT)]


class Source(ct.Structure):
    _fields_ = [("adapter", Luid), ("id", w.UINT), ("mode", w.UINT), ("status", w.UINT)]


class Target(ct.Structure):
    _fields_ = [("adapter", Luid), ("id", w.UINT), ("mode", w.UINT), ("technology", w.LONG),
                ("rotation", w.LONG), ("scaling", w.LONG), ("refresh", Rational),
                ("scan", w.LONG), ("available", w.BOOL), ("status", w.UINT)]


class PathInfo(ct.Structure):
    _fields_ = [("source", Source), ("target", Target), ("flags", w.UINT)]


class ModeInfo(ct.Structure):
    # DISPLAYCONFIG_MODE_INFO header followed by its 48-byte mode union.
    _fields_ = [("kind", w.LONG), ("id", w.UINT), ("adapter", Luid), ("mode", ct.c_uint64 * 6)]


class DeviceHeader(ct.Structure):
    _fields_ = [("type", w.LONG), ("size", w.UINT), ("adapter", Luid), ("id", w.UINT)]


class TargetName(ct.Structure):
    _fields_ = [("header", DeviceHeader), ("flags", w.UINT), ("technology", w.LONG),
                ("manufacturer", w.WORD), ("product", w.WORD), ("connector", w.UINT),
                ("friendly", w.WCHAR * 64), ("path", w.WCHAR * 128)]


CONNECTORS = {0: "VGA", 4: "DVI", 5: "HDMI", 6: "LVDS", 10: "DisplayPort", 11: "内置 DisplayPort",
              15: "Miracast", 16: "间接有线", 17: "间接虚拟", 18: "DisplayPort USB", -2147483648: "内置"}

QDC_ALL_PATHS = 1
QDC_ONLY_ACTIVE_PATHS = 2
PATH_ACTIVE = 0x00000001
# DISPLAYCONFIG_OUTPUT_TECHNOLOGY_INTERNAL (-2147483648) 与 _LVDS (6)：判定「本机自带屏」。
INTERNAL_TECHNOLOGIES = frozenset((-2147483648, 6))


@dataclass(frozen=True)
class DisplayPath:
    """一个显示目标（一块屏 / 一个投出口）的当前状态。"""

    name: str
    connector: str
    active: bool
    internal: bool
    source: tuple
    available: bool


def _target_identity(user32, target):
    target_name = TargetName()
    target_name.header = DeviceHeader(2, ct.sizeof(target_name), target.adapter, target.id)
    result = user32.DisplayConfigGetDeviceInfo(ct.byref(target_name))
    if result == 0 and target_name.friendly:
        return target_name.friendly
    return "显示目标 %d" % target.id


def read_display_paths():
    """读取全部显示路径（包含未激活的）。

    只查「活动路径」时无法区分「教室里根本没有投影」和「投影接好了但 Win+P 停在
    『仅电脑屏幕』」，而这两种情况的处置完全不同，所以这里同时拿未激活路径。
    """
    user32 = ct.windll.user32
    code = 0
    for _ in range(3):
        paths, modes = w.UINT(), w.UINT()
        code = user32.GetDisplayConfigBufferSizes(QDC_ALL_PATHS, ct.byref(paths), ct.byref(modes))
        if code:
            return None
        path_info, mode_info = (PathInfo * paths.value)(), (ModeInfo * modes.value)()
        code = user32.QueryDisplayConfig(QDC_ALL_PATHS, ct.byref(paths), path_info,
                                         ct.byref(modes), mode_info, None)
        if code != 122:  # 期间插拔了屏幕，重试一次。
            break
    if code:
        return None
    entries = {}
    for path in path_info[:paths.value]:
        target = path.target
        key = (target.adapter.low, target.adapter.high, target.id)
        active = bool(path.flags & PATH_ACTIVE)
        source = (path.source.adapter.low, path.source.adapter.high, path.source.id)
        record = entries.get(key)
        if record is None:
            entries[key] = DisplayPath(_target_identity(user32, target),
                                       CONNECTORS.get(target.technology, "其他接口"),
                                       active, target.technology in INTERNAL_TECHNOLOGIES,
                                       source, bool(target.available))
        elif active and not record.active:
            # 同一块屏可能有多条路径，只要有一条激活就算已投出。
            entries[key] = DisplayPath(record.name, record.connector, True, record.internal,
                                       source, record.available)
    return list(entries.values())


def describe_layout(paths):
    """把显示路径归纳成 (Win+P 模式, 已投出的外接输出, 已连接但未投出的外接输出)。

    纯函数，便于在非 Windows 平台单测。
    """
    active = [item for item in paths if item.active]
    internal = [item for item in active if item.internal]
    external = [item for item in active if not item.internal]
    # available 为假的是注册表里留下的历史 target（换过屏、换过投影的机器上很常见），
    # 它们没有信号、也不会被 Win+P 切换，不能当成「已连接但没投出」，否则单屏机器
    # 会被误报成「8 个外接输出已连接」，把老师引去按 Win+P 找一块不存在的投影。
    idle = [item for item in paths if not item.active and not item.internal and item.available]
    if external and internal:
        mode = "复制" if len({item.source for item in active}) == 1 else "扩展"
    elif external:
        mode = "仅第二屏幕"
    elif internal:
        mode = "仅电脑屏幕"
    else:
        mode = "未输出"
    return mode, external, idle


def display_check():
    title = "显示器与投影连接"
    if sys.platform != "win32":
        return Check("display", title, "unknown", "尚未检测 · 需要 Windows 显示接口。")
    paths = read_display_paths()
    if paths is None:
        return Check("display", title, "unknown", "尚未检测 · Windows 显示配置接口没有返回可读结果。")
    mode, external, idle = describe_layout(paths)
    active = [item for item in paths if item.active]
    # 这块屏「是不是教室投影」软件判断不了，但「有没有第二个活动输出」可以直接确定。
    if len(active) >= 2 and external:
        status = "ok"
        detail = f"已投出到 {len(active)} 个屏幕（{mode}模式）。"
    elif idle:
        status = "problem"
        detail = ("检测到 %d 个外接输出已连接，但 Windows 当前只在「%s」模式下工作，教学屏不会亮。"
                  "按 Win+P 选择「复制」或「扩展」即可投出。" % (len(idle), mode))
    elif len(active) == 1:
        status = "warning"
        detail = ("Windows 只报告 1 个活动输出（%s 模式）。若教室应有投影，请先打开投影电源与输入源、"
                  "再接好线缆，然后按 Win+P 选择「复制」。如果本机就是只投一个屏，可忽略此项。"
                  % mode)
    elif active:
        status = "warning"
        detail = f"Windows 报告 {len(active)} 个活动输出（{mode}模式），未发现外接输出。"
    else:
        status = "problem"
        detail = "Windows 没有返回任何活动显示输出。请核对显示线缆与屏幕供电。"
    # 只列「在用的」和「确实插着的」；历史 target 折成一句话，避免十几行噪音。
    shown = [item for item in paths if item.active or item.available]
    lines = ["• %s · %s · %s" % (item.name, item.connector, "已投出" if item.active else "已连接，未投出")
             for item in sorted(shown, key=lambda item: (not item.active, item.internal))]
    hidden = len(paths) - len(shown)
    if hidden > 0:
        lines.append("• 另有 %d 个历史显示目标已登记但当前未连接，不影响投出。" % hidden)
    if lines:
        detail += "\n" + "\n".join(lines)
    return Check("display", title, status, detail)


def network_check():
    title = "网络连接"
    if sys.platform != "win32":
        return Check("network", title, "unknown", "尚未检测 · 需要 Windows 网络状态接口。")
    bits = network_bits()
    if bits is None:
        return Check("network", title, "unknown", "尚未检测 · 无法读取 Windows 网络连接分类。")
    if bits & (0x40 | 0x400):
        return Check("network", title, "ok", "Windows 报告有 Internet 连接。未主动访问网站。")
    if bits:
        return Check("network", title, "warning", "Windows 报告已连接网络，Internet 访问尚未确认。")
    return Check("network", title, "problem", "Windows 报告当前未连接网络。若要播放在线内容，请核对校园网连接。")


def network_bits():
    from _ctypes import COMError
    import comtypes
    from comtypes.client import CreateObject

    try:
        comtypes.CoInitialize()
        try:
            manager = CreateObject("{DCB00C01-570F-4A9B-8D69-199FDBA5723B}", dynamic=True)
            try:
                return int(manager.GetConnectivity())
            finally:
                del manager
        finally:
            comtypes.CoUninitialize()
    except (OSError, COMError):
        return None


OFFICE_EXECUTABLE = "powerpnt.exe"
POWERPOINT_PROGID = "PowerPoint.Application"


def _registry_open(root, path):
    import winreg

    return winreg.OpenKey(root, path)


def _server_executable(raw):
    """从 LocalServer32 的命令行里取出可执行文件（可能是 "C:\\...\\POWERPNT.EXE" /automation）。"""
    command = str(raw or "").strip()
    if command.startswith('"'):
        executable, _, _rest = command[1:].partition('"')
    else:
        executable = command.split(" ")[0]
    file = Path(os.path.expandvars(executable.strip()))
    return file if file.name else None


def com_server_path(prog_id=POWERPOINT_PROGID):
    """零副作用地读 COM 自动化服务器登记，返回本地服务器可执行文件。

    放映监视靠 PowerPoint 的 SlideShowWindows 接口（COM 自动化）。登记存在就说明这套
    接口可以用；只读注册表不会启动 PowerPoint，可以放心在检测里调用。
    """
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with _registry_open(winreg.HKEY_CLASSES_ROOT, "SOFTWARE\\Classes\\%s\\CLSID" % prog_id) as handle:
            clsid, _ = winreg.QueryValueEx(handle, "")
        with _registry_open(winreg.HKEY_CLASSES_ROOT,
                            "SOFTWARE\\Classes\\CLSID\\%s\\LocalServer32" % str(clsid).strip()) as handle:
            raw, _ = winreg.QueryValueEx(handle, "")
    except (OSError, AttributeError, ValueError):
        return None
    return _server_executable(raw)


def looks_like_wps(install_path):
    """这份路径指向的是不是 WPS（而不是 Microsoft Office）。

    WPS 会注册一个冒充 PowerPoint.Application 的 COM 空壳，连 Version 都照抄 PowerPoint；
    真正的辨识点是安装位置与可执行文件名。注意不能只找「路径里出现 wps 三个字母」——
    像 …\\WPSHARE_Backup\\Office16\\POWERPNT.EXE 这样的真 PowerPoint 会被误判成 WPS。
    """
    text = str(install_path or "").replace("\\", "/")
    name = text.rsplit("/", 1)[-1].casefold()
    if name in ("wpp.exe", "kwpp.exe", "wps.exe", "wpsoffice.exe"):
        return True
    parts = [part.strip().casefold() for part in text.split("/") if part.strip()]
    if "kingsoft" in parts or "wps office" in parts:
        return True
    return any(part in ("wps", "wpsoffice", "wpsoffice6") for part in parts)


_looks_like_wps = looks_like_wps      # 旧名保留：preflight 与 opencheck 共用同一套判定


def _run_slide_show_probe(application, settle=0.25):
    """在临时演示文稿上真起一次放映，再立刻退出，返回 (能否放映, 说明)。

    只读 Version / Presentations.Count 证明不了「放映接口能用」：WPS 会照抄这些属性，
    但放映相关的对象未必齐全。所以这里直接建一个空白文稿、Run 一次、读放映窗口，
    最后无论成败都退出放映并关掉这个临时文稿，不留残窗。

    「说明」为 "already" 时表示当时已经有放映窗口在跑，没有另建文稿。
    """
    created = None
    try:
        windows = getattr(application, "SlideShowWindows", None)
        if windows is not None:
            try:
                if int(windows.Count) > 0:
                    return True, "already"
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        presentations = getattr(application, "Presentations", None)
        if presentations is None:
            return False, "对象模型里没有 Presentations 集合，不是演示程序"
        created = presentations.Add()
        try:
            created.Slides.Add(1, 12)      # 12 = ppLayoutBlank；个别版本 Add 已自带一页
        except Exception:                   # noqa: BLE001 - 只要有一页就能放映，加页失败不算错
            pass
        created.SlideShowSettings.Run()
        if settle:
            time.sleep(settle)              # 让放映窗口真正建起来，再判定与退出
        return True, ""
    except Exception as error:              # noqa: BLE001 - COM 异常类型随版本/实现而变
        return False, error_code(error)
    finally:
        if created is not None:
            try:
                created.Saved = True        # 先标记已保存，避免 Close 弹「是否保存」
            except Exception:               # noqa: BLE001
                pass
            for exit_show in (lambda: created.SlideShowWindow.View.Exit(),
                              lambda: application.SlideShowWindows.Item(1).View.Exit()):
                try:
                    exit_show()
                    break
                except Exception:           # noqa: BLE001
                    continue
            try:
                created.Close()
            except Exception:               # noqa: BLE001
                pass


def probe_verdict(vendor, version, run_ok, note=""):
    """把实测结果折叠成给人看的一句话（纯函数，便于单测）。

    vendor: "powerpoint" / "wps" / "unknown"；note: 失败原因，或 "already"。
    """
    if run_ok:
        if vendor == "powerpoint":
            return True, ("放映接口可用（Microsoft PowerPoint %s）：刚刚实测启动并退出了一次放映，"
                          "自动放映监视同样可用。" % version)
        if vendor == "wps":
            return True, ("放映接口可用（WPS 演示 %s）：刚刚实测能正常启动放映，课件用 WPS 放映没问题。\n\n"
                          "WPS 没有提供 PowerPoint 的放映窗口接口，EveTech 读不到当前页码与音量提示，"
                          "放映时请用「人工确认画面」。" % version)
        return True, "放映接口可用（%s）：刚刚实测能正常启动放映。" % (version or "未登记的程序")
    if vendor == "powerpoint":
        return False, ("放映接口不可用（Microsoft PowerPoint %s，%s）：没能启动放映，自动放映监视也不可用。"
                       "请先手动放映一次确认，仍失败则修复 Office 安装。" % (version, note))
    if vendor == "wps":
        return False, ("本机的放映接口由 WPS 接管（版本 %s），但实测没能启动放映（%s）。\n\n"
                       "请打开 WPS 演示手动放映一次：手动能放就说明只是自动化调用受限，"
                       "课件照常放映、EveTech 按「人工确认画面」使用即可；手动也放不出来则请修复 WPS。" % (version, note))
    return False, ("自动化接口由未登记的程序提供（版本 %s），实测没能启动放映（%s）。"
                   "请安装 Microsoft PowerPoint 或 WPS 演示后重试。" % (version or "读不到", note))


def probe_presentation_api():
    """真调一次放映接口，给出「能不能放映」的确定结论。

    Microsoft PowerPoint 与 WPS 演示都会被实测（WPS 会注册同名的 PowerPoint.Application
    自动化入口），只有两者都测不出来才判不可用。只应在用户主动点击时调用：没有正在运行的
    放映程序时，这会启动一个后台进程，并真的起一次放映（约 0.3 秒）后立刻退出。
    """
    if sys.platform != "win32":
        return False, "只在 Windows 上可用。"
    try:
        import comtypes
        from comtypes.client import CreateObject, GetActiveObject
    except ImportError:
        return False, "缺少 comtypes，无法调用放映接口。"
    comtypes.CoInitialize()
    application = None
    created = False
    try:
        try:
            application = GetActiveObject(POWERPOINT_PROGID)
        except (OSError, comtypes.COMError):
            application = CreateObject(POWERPOINT_PROGID)
            created = True
        version = str(getattr(application, "Version", "") or "未知版本")
        install = str(getattr(application, "Path", "") or "")
        server = com_server_path()
        if _looks_like_wps(install) or (not install and _looks_like_wps(server)):
            vendor = "wps"
        elif install or server is not None:
            vendor = "powerpoint"
        else:
            return False, ("自动化接口由未登记的程序提供（读不到安装路径），无法确认是哪个演示程序。"
                           "请安装 Microsoft PowerPoint 或 WPS 演示后重试。")
        run_ok, note = _run_slide_show_probe(application)
        return probe_verdict(vendor, version, run_ok, note)
    except (OSError, comtypes.COMError) as error:
        return False, "放映接口不可用（%s）。" % error_code(error)
    finally:
        if created and application is not None:
            try:
                application.Quit()
            except (OSError, comtypes.COMError):
                pass
        comtypes.CoUninitialize()


def error_code(error):
    code = getattr(error, "hresult", None)
    return "0x%08X" % (code & 0xFFFFFFFF) if isinstance(code, int) else type(error).__name__


def _app_paths_value(exe_name):
    """读取 App Paths\\<exe_name> 登记的可执行文件，文件存在才返回。

    只在 Windows 上读注册表；其他平台返回 None，也便于测试里替换。
    """
    if sys.platform != "win32":
        return None
    import winreg

    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe_name
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as handle:
                    value, _ = winreg.QueryValueEx(handle, "")
                file = Path(os.path.expandvars(value.strip('"')))
                if file.is_file():
                    return file
            except (OSError, AttributeError, ValueError):
                continue
    return None


def powerpnt_path():
    """Microsoft PowerPoint 的可执行文件。

    WPS Office 会把 App Paths\\POWERPNT.EXE 指向自己的 wps.exe，所以必须核对文件名，
    否则只有 WPS 的机器会被误判成「已安装 PowerPoint」。
    """
    file = _app_paths_value("POWERPNT.EXE")
    if file is not None and file.name.casefold() == OFFICE_EXECUTABLE:
        return file
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(variable)
        if base:
            for folder in ("Microsoft Office/Root/Office16", "Microsoft Office/Office16",
                           "Microsoft Office/Office15", "Microsoft Office 15/Root/Office15"):
                file = Path(base) / folder / "POWERPNT.EXE"
                if file.is_file():
                    return file
    return None


def office_alternative():
    """没有真正的 PowerPoint 时，找出占用该入口的替代程序（通常是 WPS 演示）。"""
    file = _app_paths_value("POWERPNT.EXE")
    if file is not None and file.name.casefold() != OFFICE_EXECUTABLE:
        return file
    for exe in ("WPP.EXE", "KWPP.EXE", "WPS.EXE"):
        file = _app_paths_value(exe)
        if file is not None:
            return file
    for variable in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(variable)
        if not base:
            continue
        for pattern in ("Kingsoft/WPS Office/*/office6/wpp.exe",
                        "Kingsoft/WPS Office/*/office6/wps.exe",
                        "Kingsoft/WPS Office/office6/wpp.exe"):
            for match in sorted(Path(base).glob(pattern), reverse=True):
                if match.is_file():
                    return match
    return None


WPS_PROGIDS = ("KWPP.Application", "Kwpp.Application", "WPS.Application")
WPS_EXECUTABLES = ("wpp.exe", "kwpp.exe")      # WPS 演示本体
WPS_LAUNCHER = ("wps.exe", "wpsoffice.exe")    # WPS 主程序 / 启动器


def wps_path():
    """WPS 演示（WPS Presentation）的可执行文件。

    依次看：COM 登记（KWPP.Application 系列，只读注册表不会启动程序）→ App Paths\\WPP.EXE →
    常见安装目录（用户级 LocalAppData 与企业级 Program Files 都覆盖）。
    """
    for prog_id in WPS_PROGIDS:
        server = com_server_path(prog_id)
        if server is not None and server.is_file() and server.name.casefold() in WPS_EXECUTABLES + WPS_LAUNCHER:
            return server
    for exe in ("WPP.EXE", "KWPP.EXE", "WPS.EXE"):
        file = _app_paths_value(exe)
        if file is not None and ("kingsoft" in str(file).casefold() or "wps" in file.name.casefold()):
            return file
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if not base:
            continue
        for pattern in ("Kingsoft/WPS Office/*/office6/wpp.exe",
                        "Kingsoft/WPS Office/*/office6/kwpp.exe",
                        "Kingsoft/WPS Office/office6/wpp.exe"):
            for match in sorted(Path(base).glob(pattern), reverse=True):
                if match.is_file():
                    return match
    return None


def wps_version(path):
    """从安装路径里取 WPS 版本号（...\\WPS Office\\12.1.0.16929\\office6\\wpp.exe）。

    正反斜杠都按分隔符处理，避免在非 Windows 上（测试环境）解析不出来。
    """
    text = str(path or "").replace("\\", "/")
    for part in text.split("/")[::-1]:
        if part.count(".") >= 1 and part.replace(".", "").isdigit() and len(part) >= 5:
            return part
    return ""


def wps_running():
    """WPS 演示 / WPS 主程序是否正在运行。"""
    names = set(WPS_EXECUTABLES) | set(WPS_LAUNCHER)
    try:
        return any((p.info.get("name") or "").casefold() in names for p in psutil.process_iter(["name"]))
    except (psutil.Error, OSError):
        return None


def com_taken_by_wps():
    """PowerPoint.Application 这个放映接口是否被 WPS 接管（返回被指向的可执行文件名）。"""
    server = com_server_path()
    if server is not None and _looks_like_wps(server):
        return server
    return None


def powerpoint_check():
    """Microsoft PowerPoint 检测（key 保持 office，历史记录与规则包都沿用这个 key）。"""
    title = "Microsoft PowerPoint"
    try:
        running = any((p.info.get("name") or "").casefold() == OFFICE_EXECUTABLE
                      for p in psutil.process_iter(["name"]))
    except (psutil.Error, OSError):
        running = None
    if running:
        return Check("office", title, "ok", "检测到 PowerPoint 正在运行，放映监视可以直接附加。")
    server = com_server_path()
    if powerpnt_path():
        if server is not None and server.name.casefold() == OFFICE_EXECUTABLE:
            return Check("office", title, "ok",
                         "找到 PowerPoint 安装文件，放映接口（COM 自动化）已注册。")
        if server is None:
            return Check("office", title, "warning",
                         "找到 PowerPoint 安装文件，但没有读到放映接口的 COM 登记。"
                         "先手动启动一次 PowerPoint 再重新检测；仍不行则修复 Office 安装。")
        return Check("office", title, "problem",
                     "POWERPNT.EXE 存在，但放映接口登记指向 %s。放映监视可能不可用，"
                     "请修复 Office 安装后再检测。" % server.name)
    alternative = office_alternative()
    if alternative is not None:
        return Check("office", title, "warning",
                     "未安装 Microsoft PowerPoint（本机由 %s 占用了 POWERPNT.EXE 入口，通常是 WPS 演示）。"
                     "课件用 WPS 一样能放；但 EveTech 的放映监视依赖 PowerPoint 接口，"
                     "用 WPS 放映时请按「人工确认画面」处理。" % alternative.name)
    return Check("office", title, "problem",
                 "未找到 Microsoft PowerPoint，也没有读到放映接口登记。放映监视不可用。")


def wps_check():
    """WPS 演示检测：只做识别与说明，不改动任何 WPS 设置。"""
    title = "WPS 演示"
    running = wps_running()
    path = wps_path()
    version = wps_version(path) if path is not None else ""
    label = ("WPS 演示 %s" % version) if version else "WPS 演示"
    hijack = com_taken_by_wps()
    if path is None:
        if hijack is not None:
            return Check("wps", title, "warning",
                         "读到 WPS 接管了放映接口（%s），但没在常见位置找到 WPS 演示主程序。"
                         "如果本机用 WPS 上课，建议重装一次 WPS 让登记完整。" % hijack.name)
        return Check("wps", title, "unknown",
                     "未检测到 WPS 演示。只用 Microsoft PowerPoint 的机器属正常情况，"
                     "需要时可在本机安装 WPS 后重新检测。")
    detail = ["已安装 %s" % label]
    detail.append("路径 %s" % path)
    detail.append("当前正在运行" if running else "当前未运行")
    if hijack is not None:
        detail.append("注意：PowerPoint 放映接口由 WPS 接管（%s），EveTech 的自动放映监视在 WPS 下不可用，"
                      "请用「人工确认画面」代替。" % hijack.name)
    else:
        detail.append("WPS 可以正常打开 .pptx；放映监视仍以 Microsoft PowerPoint 为准。")
    status = "warning" if (hijack is not None and running) else "ok"
    return Check("wps", title, status, "\n".join(detail))


def office_check():
    """兼容旧调用点：只关心 PowerPoint 的检测结果。"""
    return powerpoint_check()


def peripheral_check():
    title = "教室摄像头与触控"
    if sys.platform != "win32":
        return Check("peripherals", title, "unknown", "尚未检测 · 需要 Windows 即插即用接口。")
    try:
        items = present_peripherals()
    except OSError:
        return Check("peripherals", title, "unknown", "尚未检测 · 读取 Windows 即插即用设备失败。")
    if not items:
        return Check("peripherals", title, "warning", "未发现当前连接的摄像头或可识别的触控屏。有需要时请核对 USB 连接。")
    problems = any(item.problem for item in items)
    incomplete = any(item.problem is None or not item.started for item in items)
    status = "problem" if problems else "warning" if incomplete else "ok"
    lines = []
    for item in items:
        state = ("尚未检测" if item.problem is None else f"Windows 设备问题代码 {item.problem}" if item.problem
                 else "驱动已启动" if item.started else "驱动未启动")
        lines.append(f"• {item.name} · {state}")
    lines.append("实际成像与触控需在教室确认。")
    return Check("peripherals", title, status, "\n".join(lines))


def inspect_preflight(_feedback="pending"):
    audio = inspect_audio()
    display, network = display_check(), network_check()
    office, wps, peripherals = powerpoint_check(), wps_check(), peripheral_check()
    checks = [check for check in audio.checks if check.key in ("audio_default", "audio_volume", "audio_devices")]
    checks.extend((display, network, office, wps, peripherals))
    findings = [finding for finding in audio.findings if finding.priority <= 2]
    if display.status == "problem":
        findings.append(Finding("教学屏没有投出", display.detail,
                                "按 Win+P 选择「复制」或「扩展」；仍无画面时再核对线缆、投影电源与输入源。",
                                "已检测", 20))
    elif display.status == "warning":
        findings.append(Finding("显示输出数量需要确认", display.detail,
                                "若教室应有投影，请打开投影电源、插好线缆后按 Win+P 选择「复制」并重新检测。",
                                "已检测", 20))
    if network.status in ("warning", "problem"):
        findings.append(Finding("提前准备离线课件", network.detail,
                                "如课程需要在线媒体，先核对校园网并准备本地课件或视频副本。", "有检测依据", 30))
    if office.status == "problem":
        findings.append(Finding("本机不能监视 PowerPoint 放映", office.detail,
                                "安装 Microsoft PowerPoint 后重新检测；只能用 WPS 时改为人工检查放映画面。",
                                "已检测", 25))
    elif office.status == "warning":
        if wps.status != "unknown":
            findings.append(Finding("放映监视只支持 Microsoft PowerPoint", office.detail,
                                    "课件用 WPS 演示也能正常放映，只是 EveTech 读不到页码与音量提示；"
                                    "需要自动监视时装 Microsoft PowerPoint，或按人工确认放映画面。",
                                    "已检测", 25))
        else:
            findings.append(Finding("放映接口没有登记", office.detail,
                                    "手动启动一次 PowerPoint，确认能打开演示文稿后再重新检测。",
                                    "已检测", 25))
    return Report("check", "课前巡检", checks, sort_findings(findings))
