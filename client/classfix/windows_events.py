"""Read future PowerPoint Application events and matching existing WER reports."""

import ntpath
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .opencheck import parse_wer


EVENT_QUERY = ("*[System[(Provider[@Name='Application Error'] and EventID=1000) or "
               "(Provider[@Name='Windows Error Reporting'] and EventID=1001) or "
               "(Provider[@Name='Application Hang'] and EventID=1002)]]")
SOURCES = {1000: "Application Error", 1001: "Windows Error Reporting", 1002: "Application Hang"}
NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
_GUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)


def identifier(value):
    value = (value or "").strip().strip("{}").lower()
    return value if _GUID.fullmatch(value) else ""


def error_hex(value):
    value = str(value or "").strip().lower()
    if re.fullmatch(r"(?:0x)?[0-9a-f]{8}", value):
        return "0x" + value.removeprefix("0x").upper()
    return ""


def module_name(value):
    name = ntpath.basename((value or "").strip())
    return name if re.fullmatch(r"[\w .-]{1,80}\.(?:exe|dll|ocx|ax)", name, re.I) else ""


@dataclass(frozen=True)
class OfficeEvent:
    event_id: int
    at: float
    pid: int | None = None
    report_id: str = ""
    module: str = ""
    code: str = ""

    @property
    def detail(self):
        label = {1000: "应用崩溃", 1001: "Windows 错误报告", 1002: "应用无响应"}[self.event_id]
        return " · ".join(item for item in (f"{label}（事件 {self.event_id}）", self.module, self.code) if item)


def parse_application_event(xml):
    if not isinstance(xml, str) or len(xml) > 65536:
        return None
    try:
        root = ET.fromstring(xml)
        system = root.find("e:System", NS)
        event_id = int(system.findtext("e:EventID", namespaces=NS))
        if SOURCES.get(event_id) != system.find("e:Provider", NS).get("Name"):
            return None
        at = datetime.fromisoformat(system.find("e:TimeCreated", NS).get("SystemTime")).timestamp()
    except (AttributeError, ET.ParseError, TypeError, ValueError, OverflowError):
        return None
    data = {element.get("Name"): (element.text or "") for element in root.findall("e:EventData/e:Data", NS)}
    app = data.get("AppName") or data.get("P1")
    if module_name(app).casefold() != "powerpnt.exe":
        return None
    # System/Execution is the event writer, which may be WER rather than Office.
    raw_pid = data.get("ProcessId", "")
    try:
        pid = int(raw_pid, 16 if raw_pid.lower().startswith("0x") else 10)
    except ValueError:
        pid = None
    crash_report = event_id == 1001 and data.get("EventName", "").upper() == "APPCRASH"
    return OfficeEvent(event_id, at, pid if pid and pid > 0 else None,
                       identifier(data.get("IntegratorReportId") or data.get("ReportId")),
                       module_name(data.get("ModuleName") or (data.get("P4") if crash_report else "")),
                       error_hex(data.get("ExceptionCode") or (data.get("P7") if crash_report else "")))


def subscribe(stop, on_event, on_status):
    """Pull only new Application events. Own and close all Windows handles in this thread."""
    try:
        import pywintypes
        import win32event
        import win32evtlog
    except ImportError:
        on_status("Windows 事件订阅不可用（缺少 Windows 接口）。")
        return
    signal = subscription = None
    try:
        signal = win32event.CreateEvent(None, False, False, None)
        subscription = win32evtlog.EvtSubscribe(
            "Application", win32evtlog.EvtSubscribeToFutureEvents, SignalEvent=signal, Query=EVENT_QUERY)
        on_status("已订阅 Windows Application 新事件。")
        while not stop.is_set():
            if win32event.WaitForSingleObject(signal, 250) != win32event.WAIT_OBJECT_0:
                continue
            while not stop.is_set():
                try:
                    batch = win32evtlog.EvtNext(subscription, 8, 0)
                except pywintypes.error as error:
                    if getattr(error, "winerror", None) in (259, 1460):
                        break
                    raise
                if not batch:
                    break
                try:
                    for handle in batch:
                        item = parse_application_event(win32evtlog.EvtRender(handle, win32evtlog.EvtRenderEventXml))
                        if item and not stop.is_set():
                            on_event(item)
                finally:
                    for handle in batch:
                        handle.Close()
    except (OSError, pywintypes.error) as error:
        if not stop.is_set():
            code = getattr(error, "winerror", None)
            on_status(f"Windows 事件订阅不可用（{code if code is not None else type(error).__name__}）。")
    finally:
        if subscription is not None:
            subscription.Close()
        if signal is not None:
            signal.Close()


def matching_wer(event, roots=None):
    """Only exact report-ID matches can be attributed to this crash."""
    if not event.report_id:
        return None
    if roots is None:
        roots = (Path(base) / "Microsoft/Windows/WER" for key in ("PROGRAMDATA", "LOCALAPPDATA")
                 if (base := os.environ.get(key)))
    for root in roots:
        for area in ("ReportArchive", "ReportQueue"):
            try:
                folders = [item for item in (root / area).iterdir()
                           if item.name.casefold().startswith(("appcrash_powerpnt", "apphang_powerpnt"))
                           and abs(item.stat().st_mtime - event.at) <= 300]
            except OSError:
                continue
            for folder in folders[:24]:
                try:
                    with (folder / "Report.wer").open("rb") as stream:
                        raw = stream.read(131073)
                except OSError:
                    continue
                if len(raw) > 131072:
                    continue
                encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
                text = raw.decode(encoding, errors="replace")
                parsed = parse_wer(text)
                extra = {name: value.strip() for line in text.splitlines() if "=" in line
                         for name, value in (line.split("=", 1),)
                         if name in ("IntegratorReportIdentifier", "NsAppName", "AppPath", "Sig[0].Value")}
                if event.report_id not in (identifier(parsed["report"]),
                                           identifier(extra.get("IntegratorReportIdentifier"))):
                    continue
                if all(module_name(value).casefold() != "powerpnt.exe" for value in
                       (parsed["app"], extra.get("NsAppName"), extra.get("AppPath"), extra.get("Sig[0].Value"))):
                    continue
                module = module_name(parsed["module"]) if parsed["event"].upper() == "APPCRASH" else ""
                code = error_hex(parsed["code"]) if parsed["event"].upper() == "APPCRASH" else ""
                return " · ".join(item for item in ("找到同一报告 ID 的本机 WER 记录", module, code) if item)
    return None
