"""Read Windows render endpoints and PowerPoint sessions through Core Audio."""

import sys
from dataclasses import dataclass, field

from .model import Check, Finding, Report, sort_findings


@dataclass
class Endpoint:
    id: str
    name: str
    state: str


@dataclass
class Session:
    endpoint: str
    active: bool
    muted: bool
    volume: int


@dataclass
class AudioState:
    endpoints: list[Endpoint] | None = None
    default: Endpoint | None = None
    default_missing: bool = False
    volume: int | None = None
    muted: bool | None = None
    sessions: list[Session] = field(default_factory=list)
    session_incomplete: bool = False
    errors: dict[str, str] = field(default_factory=dict)


def error_code(error):
    code = getattr(error, "hresult", None)
    return f"0x{code & 0xFFFFFFFF:08X}" if isinstance(code, int) else type(error).__name__


def read_audio():
    state = AudioState()
    if sys.platform != "win32":
        state.errors["audio"] = "需要 Windows Core Audio"
        return state
    try:
        import comtypes
        comtypes.CoInitialize()
    except (ImportError, OSError, comtypes_error()):
        state.errors["audio"] = "Core Audio 初始化失败"
        return state
    try:
        collect_audio(state)
    except (OSError, comtypes.COMError) as error:
        state.errors["audio"] = error_code(error)
    finally:
        comtypes.CoUninitialize()
    return state


def collect_audio(state):
    import comtypes
    import psutil
    from pycaw.api.audiopolicy import IAudioSessionControl2
    from pycaw.api.mmdeviceapi.depend.structures import PROPERTYKEY
    from pycaw.constants import AudioDeviceState, DEVICE_STATE, EDataFlow, ERole
    from pycaw.utils import AudioDevice, AudioSession, AudioUtilities

    enumerator = AudioUtilities.GetDeviceEnumerator()
    key = PROPERTYKEY(comtypes.GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)

    def endpoint(native):
        properties = {}
        try:
            value = native.OpenPropertyStore(0).GetValue(key)
            try:
                properties[str(key).upper()] = value.GetValue()
            finally:
                value.clear()
        except (OSError, comtypes.COMError):
            pass
        device = AudioDevice(native.GetId(), AudioDeviceState(native.GetState()), properties, native)
        labels = {1: "可用", 2: "已禁用", 4: "不存在", 8: "未插入"}
        return device, Endpoint(device.id, device.FriendlyName or "未命名输出", labels[device.state.value])

    try:
        native = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eMultimedia.value)
        default, state.default = endpoint(native)
        volume = default.EndpointVolume
        state.muted = bool(volume.GetMute())
        state.volume = round(volume.GetMasterVolumeLevelScalar() * 100)
    except (OSError, comtypes.COMError) as error:
        if error_code(error) == "0x80070490" and state.default is None:
            state.default_missing = True
        else:
            state.errors["default"] = error_code(error)
    try:
        collection = enumerator.EnumAudioEndpoints(EDataFlow.eRender.value, DEVICE_STATE.MASK_ALL.value)
        devices = [endpoint(collection.Item(index)) for index in range(collection.GetCount())]
        state.endpoints = [item for _, item in devices]
    except (OSError, comtypes.COMError) as error:
        state.errors["endpoints"] = error_code(error)
        devices = []
        state.session_incomplete = True
    for device, observed in devices:
        if observed.state != "可用":
            continue
        try:
            sessions = device.AudioSessionManager.GetSessionEnumerator()
            for i in range(sessions.GetCount()):
                try:
                    control = sessions.GetSession(i)
                    session = AudioSession(control.QueryInterface(IAudioSessionControl2))
                    session_state = session.State
                    process = session.Process
                    if session_state == 2 or not process or process.name().casefold() != "powerpnt.exe":
                        continue
                    simple = session.SimpleAudioVolume
                    state.sessions.append(Session(observed.id, session_state == 1, bool(simple.GetMute()),
                                                  round(simple.GetMasterVolume() * 100)))
                except psutil.NoSuchProcess:
                    continue
                except (OSError, comtypes.COMError, psutil.AccessDenied):
                    state.session_incomplete = True
        except (OSError, comtypes.COMError):
            state.session_incomplete = True


def comtypes_error():
    # Import can fail before the COMError name is available on non-Windows hosts.
    from _ctypes import COMError
    return COMError


def build_audio_report(state, feedback="unknown"):
    checks = []
    findings = []
    details = state.errors.get("audio") or state.errors.get("default")
    if state.default is not None:
        checks.append(Check("audio_default", "默认媒体输出", "ok" if state.default.state == "可用" else "problem",
                            f"{state.default.name} · {state.default.state}。请确认这就是教室实际使用的输出。"))
        if state.default.state != "可用":
            findings.append(Finding("默认输出不可用", "Windows 报告当前默认设备不可用。",
                                    "打开声音设置，选择已连接的扬声器或 HDMI 输出。", "已检测", 1))
    elif state.default_missing:
        checks.append(Check("audio_default", "默认媒体输出", "problem", "Windows 没有返回默认媒体输出。"))
        findings.append(Finding("没有默认输出设备", "Core Audio 找不到默认媒体输出。",
                                "确认教室音响已通电、音频或 HDMI 线已插好，再在声音设置里选择输出。", "已检测", 1))
    else:
        checks.append(Check("audio_default", "默认媒体输出", "unknown", f"尚未检测 · {details or '无法读取默认设备'}。"))
    if state.volume is None or state.muted is None:
        checks.append(Check("audio_volume", "系统音量", "unknown", "尚未检测 · 无法读取默认输出的静音与音量。"))
    else:
        issue = state.muted or state.volume == 0
        low = 0 < state.volume < 15
        checks.append(Check("audio_volume", "系统音量", "problem" if issue else "warning" if low else "ok",
                            f"{state.volume}% · {'静音' if state.muted else '未静音'}"))
        if issue or low:
            findings.append(Finding("系统静音或音量过低", f"默认输出音量 {state.volume}%，{'静音' if state.muted else '未静音'}。",
                                    "打开声音设置，确认选中教室设备后取消静音并逐步提高音量。", "已检测", 2))
    active = None
    if state.endpoints is None:
        checks.append(Check("audio_devices", "音频输出设备", "unknown",
                            f"尚未检测 · {state.errors.get('endpoints', '无法枚举 Windows 输出设备')}。"))
    else:
        active = sum(item.state == "可用" for item in state.endpoints)
        # Windows 会长期保留已拔掉的设备记录，十几个「不存在」的条目会把真正可用的输出淹没。
        # 默认只列可用 / 未插入 / 已禁用，历史残留折叠到 extra，由界面「查看全部」展开。
        visible = [item for item in state.endpoints if item.state != "不存在"]
        absent = [item for item in state.endpoints if item.state == "不存在"]
        detail = f"{active} 个可用，共 {len(state.endpoints)} 个已知输出。"
        if visible:
            detail += "\n" + "\n".join(f"• {item.name} · {item.state}" for item in visible)
        if absent:
            detail += f"\n另有 {len(absent)} 个当前不存在的历史输出已折叠。"
        elif not visible:
            detail += "\n未列出任何输出设备。"
        checks.append(Check("audio_devices", "音频输出设备", "ok" if active else "problem", detail,
                            "\n".join(f"• {item.name} · {item.state}" for item in absent)))
    if not state.sessions:
        reason = "部分输出的会话无法读取。" if state.session_incomplete else "未找到可读取的 PowerPoint 音频会话。"
        checks.append(Check("app_audio", "PowerPoint 应用音量", "unknown",
                            f"尚未检测 · {reason}请开始播放，然后打开音量混合器并复测。"))
        findings.append(Finding("核对 PowerPoint 独立音量和输出", reason,
                                "在 PowerPoint 放映一个确认有声音的视频，同时打开音量混合器检查应用音量与输出。", "待验证", 39))
    else:
        names = {item.id: item.name for item in state.endpoints or []}
        current = [session for session in state.sessions if session.active]
        bad = any(session.muted or session.volume == 0 for session in current)
        other = bool(state.default and any(session.endpoint != state.default.id for session in current))
        low = any(0 < session.volume < 15 for session in current)
        summary = ["PowerPoint 音频会话"]
        summary.extend(f"• {names.get(item.endpoint, '未知输出')} · {item.volume}% · "
                       f"{'静音' if item.muted else '未静音'} · {'活动' if item.active else '未播放'}"
                       for item in state.sessions)
        if state.session_incomplete:
            summary.append("部分输出的会话尚未检测。")
        checks.append(Check("app_audio", "PowerPoint 应用音量",
                            "problem" if bad else "warning" if other or low else "unknown" if not current else "ok", "\n".join(summary)))
        if bad or low:
            findings.append(Finding("PowerPoint 会话音量异常", "至少一个活动会话已静音或音量过低。",
                                    "在 Windows 音量混合器中查找 PowerPoint，逐步提高应用音量。", "已检测", 3))
        if other:
            findings.append(Finding("PowerPoint 正在使用另一个输出", "有活动的 PowerPoint 会话在默认输出之外的设备上。",
                                    "在音量混合器里确认 PowerPoint 输出已指向教室音响，再试播。", "有检测依据", 8))
    if active and active > 1:
        findings.append(Finding("确认当前选中教室音响", f"Windows 检测到 {active} 个可用输出。",
                                "在声音设置中核对默认设备名称，并播放短音频验证。", "待验证", 31))
    if feedback == "failed":
        findings.append(Finding("验证音响供电和输入", "你反馈复测后仍没有声音。",
                                "先播放已知有声的本地文件，再在一体机或功放上核对音量与输入源。", "用户反馈", 18))
    checks.append(Check("audio_playback", "实际出声", "unknown", "尚未检测 · 请播放已知有声的内容，在教室内确认。"))
    facts = {"muted": state.muted, "volume": state.volume, "active_outputs": active,
             "session_count": len(state.sessions) if not state.session_incomplete else None,
             "default_known": state.default is not None,
             "audio_error": next(iter(state.errors.values()), None)}
    return Report("audio", "声音诊断", checks, sort_findings(findings), facts)


def inspect_audio(feedback="unknown"):
    return build_audio_report(read_audio(), feedback)


def set_system_volume(percent):
    """Set the default multimedia endpoint volume after an explicit slider action."""
    value = max(0, min(100, int(percent))) / 100
    import comtypes
    from pycaw.utils import AudioUtilities

    comtypes.CoInitialize()
    try:
        device = AudioUtilities.GetSpeakers()
        if device is None:
            return False
        device.EndpointVolume.SetMasterVolumeLevelScalar(value, None)
        if value:
            device.EndpointVolume.SetMute(False, None)
        return True
    except (OSError, comtypes.COMError):
        return False
    finally:
        comtypes.CoUninitialize()


def set_system_mute(muted):
    """Change only the default multimedia endpoint mute state."""
    import comtypes
    from pycaw.utils import AudioUtilities

    comtypes.CoInitialize()
    try:
        device = AudioUtilities.GetSpeakers()
        if device is None:
            return False
        device.EndpointVolume.SetMute(bool(muted), None)
        return True
    except (OSError, comtypes.COMError):
        return False
    finally:
        comtypes.CoUninitialize()


def read_system_volume():
    """Read only the default endpoint's master volume for the slide-show hot path."""
    import comtypes
    from pycaw.utils import AudioUtilities

    comtypes.CoInitialize()
    try:
        device = AudioUtilities.GetSpeakers()
        if device is None:
            return None, None
        endpoint = device.EndpointVolume
        return round(endpoint.GetMasterVolumeLevelScalar() * 100), bool(endpoint.GetMute())
    except (OSError, comtypes.COMError):
        return None, None
    finally:
        comtypes.CoUninitialize()
