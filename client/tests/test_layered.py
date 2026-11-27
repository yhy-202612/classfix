import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from unittest.mock import patch

from evetech.layered import LayeredDiagnostics, PlaybackFailure, playback_report
from evetech.media_trace import PowerPointTarget, TraceResult, collect_trace, parse_trace
from evetech.model import Check
from evetech.monitor import MediaProblem
from evetech.storage import Store
from evetech.windows_events import OfficeEvent, matching_wer, parse_application_event


STAMP = datetime(2026, 11, 22, tzinfo=timezone.utc)
REPORT_ID = "0a29f411-0c42-42cd-835e-99b7e6b40610"
XML_NS = "http://schemas.microsoft.com/win/2004/08/events/event"
SOURCE_GUID = "bc97b970-d001-482f-8745-b8d7d5759f99"


def application_event(app="POWERPNT.EXE", source="Application Error", extra=""):
    return f"""<Event xmlns="{XML_NS}">
    <System><Provider Name="{source}"/><EventID>1000</EventID>
    <TimeCreated SystemTime="{STAMP.isoformat()}"/><Execution ProcessID="12345"/></System>
    <EventData><Data Name="AppName">{app}</Data>
    <Data Name="ModuleName">mf.dll</Data><Data Name="ExceptionCode">c0000005</Data>
    <Data Name="IntegratorReportId">{REPORT_ID}</Data>{extra}</EventData></Event>"""


def trace_event(pid, level=2, provider=SOURCE_GUID):
    return f"""<Event xmlns="{XML_NS}"><System>
    <Provider Guid="{{{provider}}}"/><EventID>81</EventID><Level>{level}</Level>
    <Execution ProcessID="{pid}"/></System><EventData>
    <Data Name="HRESULT">0xC00D5212</Data><Data Name="Path">C:\\Users\\someone\\private.mp4</Data>
    </EventData></Event>"""


class EventAndTraceTests(unittest.TestCase):
    def test_event_is_powerpoint_and_uses_faulting_pid_not_event_writer(self):
        xml = application_event(extra='<Data Name="ProcessId">0x378</Data>')
        event = parse_application_event(xml)
        self.assertEqual((event.pid, event.event_id, event.report_id), (888, 1000, REPORT_ID))
        self.assertEqual((event.module, event.code), ("mf.dll", "0xC0000005"))
        self.assertIsNone(parse_application_event(application_event()).pid)
        self.assertIsNone(parse_application_event(application_event(app="excel.exe")))
        self.assertIsNone(parse_application_event(application_event(source="Other provider")))
        self.assertIsNone(parse_application_event("<broken "))

    def test_wer_only_links_matching_report_for_powerpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "ReportArchive" / "AppCrash_POWERPNT.EXE_test"
            folder.mkdir(parents=True)
            file = folder / "Report.wer"
            file.write_text(f"AppName=Microsoft PowerPoint\nNsAppName=POWERPNT.EXE\nEventType=APPCRASH\n"
                            f"IntegratorReportIdentifier={REPORT_ID}\nSig[3].Value=mf.dll\n"
                            f"Sig[6].Value=c0000005\n", encoding="utf-16")
            os.utime(folder, (STAMP.timestamp(), STAMP.timestamp()))
            hit = matching_wer(OfficeEvent(1000, STAMP.timestamp(), report_id=REPORT_ID), (Path(directory),))
            self.assertIn("mf.dll", hit)
            self.assertIn("0xC0000005", hit)
            self.assertIsNone(matching_wer(OfficeEvent(1000, STAMP.timestamp(), report_id="wrong"),
                                           (Path(directory),)))

    def test_trace_only_retains_selected_pid_provider_and_warning_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            xml = Path(directory) / "trace.xml"
            xml.write_text("<Events>" + trace_event(999) + trace_event(888, level=4)
                           + trace_event(888, provider=REPORT_ID) + trace_event(888) * 2 + "</Events>", encoding="utf-8")
            self.assertEqual(parse_trace(xml, 888), ("Media Foundation Platform 事件 81 · 0xC00D5212",))
            self.assertEqual(parse_trace(xml, 777), ())

    def test_trace_does_not_start_with_unverified_process(self):
        with patch("evetech.media_trace._capture") as capture:
            result = collect_trace(None, Event())
        capture.assert_not_called()
        self.assertEqual(result.state, "unavailable")


class DiagnosticFusionTests(unittest.TestCase):
    def test_popup_error_keeps_full_text_in_history_after_reopening_database(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            controller = LayeredDiagnostics(store.save, lambda: False)
            original = "PowerPoint 无法播放媒体 0xC00D5212。" * 10
            controller.record_media_problem(MediaProblem(9, "PowerPoint 媒体错误", "检测详情",
                                                         original, presentation="课堂.pptx"))
            recent = Store(Path(directory)).records(kind="media_alert")[0].latest
        self.assertIn("第 9 页", recent.title)
        self.assertIn("课堂.pptx", recent.checks[0].detail)
        self.assertEqual(recent.checks[1].detail, original)

    def failure(self):
        at = STAMP.timestamp()
        return PlaybackFailure(at, 2, "播放器在播放后转为未就绪。", PowerPointTarget(888, at - 90),
                               api_error="0xC00D5212", volume=18, muted=True,
                               file_checks=(Check("media_encoding", "编码", "ok", "H.264 + AAC"),))

    def test_fusion_keeps_unmatched_windows_events_out_of_playback_report(self):
        failure = self.failure()
        evidence = [OfficeEvent(1000, failure.at, 666, module="unrelated.dll"),
                    OfficeEvent(1000, failure.at - 40, 888, module="old.dll"),
                    OfficeEvent(1002, failure.at, 888),
                    OfficeEvent(1000, failure.at)]
        report = playback_report(failure, evidence, "已订阅", TraceResult("empty", "未捕获公开事件"))
        checks = {check.key: check for check in report.checks}
        self.assertIn("1002", checks["media_events"].detail)
        self.assertNotIn("1000", checks["media_events"].detail)
        self.assertIn("18%", checks["media_audio"].detail)
        self.assertEqual(checks["media_dialog"].status, "unknown")
        self.assertEqual(checks["media_etw"].status, "unknown")
        self.assertIn("根因待核对", report.findings[0].confidence)

    def test_trigger_records_baseline_then_updates_same_row_and_throttles(self):
        writes = []

        def persist(initial, latest, recovery, row):
            writes.append((initial, latest, recovery, row))
            return len(writes)

        controller = LayeredDiagnostics(persist, lambda: True)
        with patch("evetech.layered.run_job") as job:
            controller.handle_playback_failure(self.failure())
            self.assertEqual(job.call_count, 1)
            finish = job.call_args.args[1]
            finish(TraceResult("observed", "采集完成", ("示例事件",)))
            controller.handle_playback_failure(self.failure())
            self.assertEqual(job.call_count, 1)
        self.assertIsNone(writes[0][3])
        self.assertEqual(writes[1][3], 1)
        self.assertIn("示例事件", writes[1][1].checks[-1].extra)
        self.assertIn("冷却", writes[2][1].checks[-1].detail)

    def test_disabled_trace_still_saves_report(self):
        stack = []
        controller = LayeredDiagnostics(lambda *args: stack.append(args), lambda: False)
        with patch("evetech.layered.run_job") as job:
            controller.handle_playback_failure(self.failure())
        job.assert_not_called()
        self.assertIn("已在设置中关闭", stack[0][1].checks[-1].detail)

    def test_crash_record_only_updates_with_matching_wer_work(self):
        saved = []

        def persist(*args):
            saved.append(args)
            return 26

        controller = LayeredDiagnostics(persist, lambda: False)
        controller._log_stop = Event()
        event = OfficeEvent(1000, STAMP.timestamp(), 888, REPORT_ID, "mf.dll", "0xC0000005")
        with patch("evetech.layered.run_job") as job, patch("evetech.layered.matching_wer", return_value="同一报告"):
            controller._on_event(Event(), event)  # stale subscription
            controller._on_event(controller._log_stop, event)
            controller._on_event(controller._log_stop, event)
            self.assertEqual(job.call_count, 1)
            operation, finish, _error = job.call_args.args
            finish(operation())
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[1][3], 26)
        self.assertEqual(saved[1][1].checks[-1].detail, "同一报告")
