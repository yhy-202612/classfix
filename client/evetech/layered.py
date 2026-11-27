"""Coordinate playback, Windows events, short ETW samples and local history."""

import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import Event, Thread
from time import monotonic

from PySide6.QtCore import QObject, Qt, Signal, Slot

from .media_trace import PowerPointTarget, TraceResult, collect_trace
from .model import Check, Finding, Report
from .ui.jobs import run_job
from .windows_events import matching_wer, subscribe


@dataclass(frozen=True)
class PlaybackFailure:
    at: float
    slide: int
    detail: str
    target: PowerPointTarget | None = None
    powerpoint_error: str = ""
    api_error: str = ""
    file_checks: tuple[Check, ...] = ()
    volume: int | None = None
    muted: bool | None = None
    presentation: str = ""


def playback_report(failure, events, log_state, trace):
    checks = list(failure.file_checks) or [
        Check("media_file", "PPTX 与媒体", "unknown", "尚未取得能对应本次放映页的文件检测证据。")
    ]
    if failure.volume is not None and failure.muted is not None:
        audio = f"默认媒体输出音量 {failure.volume}% · {'静音' if failure.muted else '未静音'}。"
        checks.append(Check("media_audio", "Core Audio", "warning" if failure.muted or failure.volume == 0 else "ok", audio))
    else:
        checks.append(Check("media_audio", "Core Audio", "unknown", "尚未检测 · 未读到故障时的默认输出音量。"))
    page = f"第 {failure.slide} 页 · " if failure.slide else ""
    deck = f"{failure.presentation} · " if failure.presentation else ""
    checks.append(Check("media_ppt", "放映状态", "problem", deck + page + failure.detail))
    checks.append(Check("media_dialog", "PowerPoint 原始报错", "problem" if failure.powerpoint_error else "unknown",
                        failure.powerpoint_error or "未读取到可用的原始媒体报错。"))
    if failure.api_error:
        checks.append(Check("media_com", "PowerPoint 放映接口", "warning",
                            f"读取播放器状态时，COM 接口返回 {failure.api_error}。"))
    related = []
    if failure.target:
        related = [event.detail for event in events if event.pid == failure.target.pid
                   and event.at >= failure.target.started and failure.at - 10 <= event.at <= failure.at + 10]
    checks.append(Check("media_events", "Windows Application", "warning" if related else "unknown",
                        " · ".join(dict.fromkeys(related)) if related else
                        f"{log_state} 这次播放附近未取得同进程的崩溃或无响应事件。"))
    checks.append(Check("media_etw", "ETW 短时追踪", "warning" if trace.events else "unknown", trace.detail,
                        "\n".join(trace.events)))
    finding = Finding("PowerPoint 播放出现异常", failure.detail,
                      "先用已知可播放的视频试播，再结合上面的文件、音量和系统记录逐项核对。",
                      "现象已确认 · 根因待核对", 1)
    return Report("playback", "放映故障诊断" + (f" · 第 {failure.slide} 页" if failure.slide else ""),
                  checks, [finding], time=datetime.fromtimestamp(failure.at).astimezone().isoformat(timespec="seconds"))


class LayeredDiagnostics(QObject):
    _log_event = Signal(object, object)
    _log_status = Signal(object, str)
    log_status_changed = Signal(str)

    def __init__(self, persist, etw_enabled, parent=None):
        super().__init__(parent)
        self.persist = persist
        self.etw_enabled = etw_enabled
        self.events = deque(maxlen=64)
        self._seen_crashes = deque(maxlen=64)
        self.log_state = "Windows 事件订阅尚未就绪。"
        self._log_stop = None
        self._trace_stop = None
        self._complete_trace = None
        self._last_trace = float("-inf")
        self._log_event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self._log_status.connect(self._on_status, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def record_media_problem(self, problem):
        title = problem.category_title + (f" · 第 {problem.slide} 页" if problem.slide else "")
        prefix = f"{problem.presentation} · " if problem.presentation else ""
        checks = [Check("media_alert", "检测依据", "warning", prefix + problem.detail),
                  Check("media_dialog", "PowerPoint 原始报错", "problem" if problem.powerpoint_error else "unknown",
                        problem.powerpoint_error or "未读取到原始报错。")]
        report = Report("media_alert", title, checks)
        self.persist(report, report, "pending", None)

    @Slot(bool)
    def set_monitoring(self, enabled):
        if not enabled:
            if self._log_stop is not None:
                self._log_stop.set()
                self._log_stop = None
            self.cancel_trace()
            self.events.clear()
            self._set_log_state("Windows 事件订阅已暂停。")
            return
        if self._log_stop is not None or sys.platform != "win32":
            return
        stop = self._log_stop = Event()
        self._set_log_state("正在连接 Windows 事件日志。")
        Thread(target=subscribe, args=(stop, lambda event: self._log_event.emit(stop, event),
                                      lambda status: self._log_status.emit(stop, status)),
               name="EveTech-EventLog", daemon=True).start()

    def cancel_trace(self):
        if self._trace_stop is not None:
            self._trace_stop.set()
        if self._complete_trace is not None:
            self._complete_trace(TraceResult("unavailable", "追踪已取消。"))

    def _set_log_state(self, status):
        self.log_state = status
        self.log_status_changed.emit(status)

    @Slot(object, str)
    def _on_status(self, stop, status):
        if self._log_stop is stop:
            self._set_log_state(status)

    @Slot(object, object)
    def _on_event(self, stop, event):
        if self._log_stop is not stop:
            return
        self.events.append(event)
        key = event.report_id or (event.event_id, event.pid, event.at)
        if key in self._seen_crashes:
            return
        self._seen_crashes.append(key)
        checks = [Check("office_event", "Windows Application", "problem", event.detail),
                  Check("office_wer", "已有 WER 报告", "unknown", "正在按报告 ID 查找本机记录。")]
        initial = Report("office_event", "PowerPoint 崩溃或无响应记录", checks,
                         time=datetime.fromtimestamp(event.at).astimezone().isoformat(timespec="seconds"))
        record_id = self.persist(initial, initial, "pending", None)

        def lookup():
            result = matching_wer(event)
            if not result and event.report_id and not stop.wait(3):
                result = matching_wer(event)  # WER may archive after the Application event.
            return result

        def ready(detail):
            wer = Check("office_wer", "已有 WER 报告", "warning" if detail else "unknown",
                        detail or "未找到可读取、且报告 ID 相同的本机 WER 记录。")
            latest = Report(initial.kind, initial.title, [checks[0], wer], time=initial.time)
            if record_id is not None:
                self.persist(initial, latest, "pending", record_id)

        run_job(lookup, ready, lambda _error: ready(None))

    @Slot(object)
    def handle_playback_failure(self, failure):
        def report(trace):
            return playback_report(failure, self.events, self.log_state, trace)

        if not self.etw_enabled():
            reason = "短时 ETW 已在设置中关闭。"
        elif failure.target is None:
            reason = "尚未检测 · 无法确认当前放映的 PowerPoint 进程。"
        elif self._trace_stop is not None or monotonic() - self._last_trace < 30:
            reason = "本次未开启 ETW · 有追踪进行中或仍在 30 秒冷却期。"
        else:
            reason = ""
        if reason:
            initial = report(TraceResult("unavailable", reason))
            self.persist(initial, initial, "pending", None)
            return

        stop = self._trace_stop = Event()
        self._last_trace = monotonic()
        initial = report(TraceResult("pending", "正在佐证此次播放故障，随后会更新本条记录。"))
        record_id = self.persist(initial, initial, "pending", None)

        def finish(trace):
            if self._trace_stop is not stop:
                return
            self._trace_stop = None
            self._complete_trace = None
            latest = report(trace)
            self.persist(initial, latest, "pending", record_id)

        self._complete_trace = finish
        run_job(lambda: collect_trace(failure.target, stop), finish,
                lambda name: finish(TraceResult("unavailable", f"ETW 采集失败（{name}）。")))
