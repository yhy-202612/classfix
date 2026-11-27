"""Collect a bounded, process-filtered ETW diagnostic sample on playback failure."""

import ctypes as C
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import psutil

from .windows_events import NS, error_hex, identifier


PROVIDERS = {
    "bc97b970-d001-482f-8745-b8d7d5759f99": "Media Foundation Platform",
    "b65471e1-019d-436f-bc38-e15fa8e87f53": "Media Foundation PlayAPI",
    "4b7eac67-fc53-448c-a49d-7cc6db524da7": "Media Foundation Read/Write",
    "db6f6ddb-ac77-4e88-8253-819df9bbf140": "Direct3D 11",
    "5d8087dd-3a9b-4f56-90df-49196cdc4f11": "Direct3D 12",
}
U32 = C.c_uint32
U64 = C.c_uint64
GUID = C.c_ubyte * 16
TRACE_SECONDS = 8
MAX_XML_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class PowerPointTarget:
    pid: int
    started: float

    def available(self):
        return powerpoint_target(self.pid) == self


def powerpoint_target(pid):
    if not isinstance(pid, int) or not 0 < pid < 2**32:
        return None
    try:
        proc = psutil.Process(pid)
        if proc.name().casefold() == "powerpnt.exe":
            return PowerPointTarget(pid, proc.create_time())
    except (psutil.Error, OSError):
        pass
    return None


@dataclass(frozen=True)
class TraceResult:
    state: str
    detail: str
    events: tuple[str, ...] = ()


class Wnode(C.Structure):
    _fields_ = [("BufferSize", U32), ("ProviderId", U32), ("HistoricalContext", U64),
                ("TimeStamp", C.c_int64), ("Guid", GUID), ("ClientContext", U32), ("Flags", U32)]


class TraceProperties(C.Structure):
    _fields_ = [("Wnode", Wnode)] + [
        (name, U32) for name in (
            "BufferSize", "MinimumBuffers", "MaximumBuffers", "MaximumFileSize", "LogFileMode",
            "FlushTimer", "EnableFlags", "AgeLimit", "NumberOfBuffers", "FreeBuffers", "EventsLost",
            "BuffersWritten", "LogBuffersLost", "RealTimeBuffersLost")
    ] + [("LoggerThreadId", C.c_void_p), ("LogFileNameOffset", U32), ("LoggerNameOffset", U32)]


class TraceBuffer(C.Structure):
    _fields_ = [("properties", TraceProperties), ("name", C.c_wchar * 128), ("path", C.c_wchar * 1024)]


class EventFilter(C.Structure):
    _fields_ = [("Ptr", U64), ("Size", U32), ("Type", U32)]


class EnableParameters(C.Structure):
    _fields_ = [("Version", U32), ("EnableProperty", U32), ("ControlFlags", U32), ("SourceId", GUID),
                ("EnableFilterDesc", C.POINTER(EventFilter)), ("FilterDescCount", U32)]


def _trace_api():
    api = C.WinDLL("advapi32")
    api.StartTraceW.argtypes = (C.POINTER(U64), C.c_wchar_p, C.POINTER(TraceProperties))
    api.EnableTraceEx2.argtypes = (U64, C.POINTER(GUID), U32, C.c_ubyte, U64, U64, U32,
                                   C.POINTER(EnableParameters))
    api.ControlTraceW.argtypes = (U64, C.c_wchar_p, C.POINTER(TraceProperties), U32)
    for name in ("StartTraceW", "EnableTraceEx2", "ControlTraceW"):
        getattr(api, name).restype = U32
    return api


def _properties(session_id, file):
    buffer = TraceBuffer()
    props = buffer.properties
    props.Wnode.BufferSize = C.sizeof(buffer)
    props.Wnode.Guid = GUID.from_buffer_copy(session_id.bytes_le)
    props.Wnode.ClientContext = 1  # QPC timestamps
    props.Wnode.Flags = 0x20000  # WNODE_FLAG_TRACED_GUID
    props.BufferSize = 16  # KB per ETW buffer
    props.MaximumFileSize = 4  # MB
    props.LogFileMode = 2  # EVENT_TRACE_FILE_MODE_CIRCULAR
    props.LoggerNameOffset = TraceBuffer.name.offset
    props.LogFileNameOffset = TraceBuffer.path.offset
    buffer.path = str(file)
    return buffer


def _capture(target, file, stop):
    session_id = uuid4()
    session_name = f"EveTech-Media-{session_id.hex}"
    buffer = _properties(session_id, file)
    props = C.byref(buffer.properties)
    handle = U64()
    api = _trace_api()
    status = api.StartTraceW(C.byref(handle), session_name, props)
    if status:
        reason = "当前用户没有 ETW 会话权限" if status in (5, 1314) else "Windows 无法启动 ETW"
        return TraceResult("unavailable", f"{reason}（Windows 错误 {status}）。")

    pid = U32(target.pid)
    selector = EventFilter(C.addressof(pid), C.sizeof(pid), 0x80000004)  # EVENT_FILTER_TYPE_PID
    parameters = EnableParameters(2, 0, 0, GUID(), C.pointer(selector), 1)
    errors = []
    enabled = 0
    try:
        for provider in PROVIDERS:
            if stop.is_set() or not target.available():
                break
            # Warnings and errors only. Do not retry with wider scope if the filter fails.
            provider_id = GUID.from_buffer_copy(UUID(provider).bytes_le)
            status = api.EnableTraceEx2(handle.value, C.byref(provider_id), 1, 3, 0, 0, 0,
                                        C.byref(parameters))
            if status:
                errors.append(status)
            else:
                enabled += 1
        deadline = monotonic() + TRACE_SECONDS
        while enabled and monotonic() < deadline and target.available():
            if stop.wait(min(0.25, max(0, deadline - monotonic()))):
                break
    finally:
        # ControlTrace uses its own output buffer. Reusing the original with
        # a populated logfile field can cause the request to be rejected.
        end = _properties(session_id, "")
        stopped = api.ControlTraceW(handle.value, session_name, C.byref(end.properties), 1)
        if stopped not in (0, 4201):
            stopped = api.ControlTraceW(0, session_name, C.byref(end.properties), 1)
    if stopped not in (0, 4201):
        return TraceResult("unavailable", f"Windows 未确认停止本次 ETW 会话（错误 {stopped}）。")
    if not enabled:
        detail = f"错误 {errors[0]}" if errors else "目标已退出或诊断已取消"
        return TraceResult("unavailable", f"没有启用进程过滤的 ETW 提供程序（{detail}）。")
    return TraceResult("sample", f"捕获范围为故障后最多 {TRACE_SECONDS} 秒，启用了 {enabled} 个提供程序。"
                                 + (" 部分提供程序不可用。" if errors else ""))


def parse_trace(path, pid):
    if path.stat().st_size > MAX_XML_BYTES:
        raise ValueError("trace output limit")
    events = []
    for _, node in ET.iterparse(path, events=("end",)):
        if node.tag.rsplit("}", 1)[-1] != "Event":
            continue
        system = node.find("e:System", NS)
        try:
            execution = system.find("e:Execution", NS)
            raw_pid = execution.get("ProcessID", "")
            event_pid = int(raw_pid, 16 if raw_pid.lower().startswith("0x") else 10)
            source = identifier(system.find("e:Provider", NS).get("Guid"))
            level = int(system.findtext("e:Level", namespaces=NS))
            event_id = int(system.findtext("e:EventID", namespaces=NS))
        except (AttributeError, ValueError, TypeError):
            node.clear()
            continue
        if event_pid == pid and source in PROVIDERS and 1 <= level <= 3:
            codes = dict.fromkeys(
                code for data in node.findall("e:EventData/e:Data", NS)
                if (data.get("Name") or "").casefold() in {"hr", "hresult", "errorcode", "status", "result"}
                if (code := error_hex(data.text)) and code not in ("0x00000000", "0x00000001"))
            entry = " · ".join((f"{PROVIDERS[source]} 事件 {event_id}", *codes))
            if entry not in events:
                events.append(entry)
                if len(events) == 8:
                    break
        node.clear()
    return tuple(events)


def collect_trace(target, stop):
    if sys.platform != "win32" or target is None or not target.available():
        return TraceResult("unavailable", "尚未检测 · 无法确认当前放映的 PowerPoint 进程。")
    if stop.is_set():
        return TraceResult("unavailable", "追踪已取消。")
    try:
        with tempfile.TemporaryDirectory(prefix="EveTech-etw-") as directory:
            etl = Path(directory) / "playback.etl"
            sample = _capture(target, etl, stop)
            if sample.state != "sample":
                return sample
            if stop.is_set():
                return TraceResult("unavailable", "追踪已取消。")
            if not etl.is_file():
                return TraceResult("unavailable", "Windows 未写出可解析的 ETW 样本。")
            xml = etl.with_suffix(".xml")
            decoder = Path(os.environ["SystemRoot"]) / "System32" / "tracerpt.exe"
            result = subprocess.run([str(decoder), str(etl), "-o", str(xml), "-of", "XML", "-gmt", "-y"],
                                    cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=7,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or not xml.is_file():
                return TraceResult("unavailable", f"Windows ETW 解码器未生成可用输出（{result.returncode}）。")
            events = parse_trace(xml, target.pid)
            suffix = " 下列事件与同一进程相关。" if events else " 未捕获该进程的公开警告或错误事件。"
            return TraceResult("observed" if events else "empty", sample.detail + suffix, events)
    except (ET.ParseError, OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        return TraceResult("unavailable", f"ETW 无法完成（{type(error).__name__}）。")
