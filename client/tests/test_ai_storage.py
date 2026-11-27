import json
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from evetech.ai import AIConfig, AIError, analyze, evidence, validate_config
from evetech.model import Check, Report
from evetech.storage import Store


class LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.server.payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.startswith("/redirect"):
            self.send_response(307)
            self.send_header("Location", "/v1/chat/completions")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": {"content": "先检查音量。"}}]}).encode())


class PersistenceTest(unittest.TestCase):
    def test_sqlite_retains_original_and_recovery_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = Report("audio", "声音诊断", [Check("audio_volume", "声音", "problem", "静音")])
            last = Report("audio", "声音诊断", [Check("audio_volume", "声音", "ok", "音量 50%")])
            store = Store(base)
            key = store.save(first, first, "pending")
            store.update_settings({"theme": "dark"})
            self.assertEqual(store.save(first, last, "restored", key), key)
            reopened = Store(base)
            item, = reopened.records()
            self.assertEqual((item.initial.checks[0].status, item.latest.checks[0].status, item.recovery),
                             ("problem", "ok", "restored"))
            self.assertEqual(reopened.settings(), {"theme": "dark"})


class AITest(unittest.TestCase):
    def test_only_allowlisted_evidence_crosses_network(self):
        report = Report("video", "SECRET lesson", [Check("reference", "Name", "unknown", "C:/SECRET/teacher.pptx")],
                        facts={"case": "silent", "volume": 45, "secret": "SECRET",
                               "video": ["h264", "SECRET"], "answers": {"original": "yes", "error": "SECRET"}})
        data = evidence(report, "failed", "local")
        self.assertNotIn("SECRET", json.dumps(data))
        self.assertEqual(data["video"], ["h264", "other"])
        self.assertEqual(data["volume"], 45)
        self.assertEqual(data["verification"], "仍有故障")
        self.assertEqual(data["checks"][0]["status"], "无法检测")

    def test_api_against_loopback_and_redirect_is_rejected(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                self.assertEqual(analyze(AIConfig(base + "/v1/chat/completions", "test"), {"volume": 40}), "先检查音量。")
                sent = json.loads(server.payload["messages"][1]["content"])
                self.assertEqual(sent, {"volume": 40})
                with self.assertRaisesRegex(AIError, "307"):
                    analyze(AIConfig(base + "/redirect/chat/completions", "test"), {})
            finally:
                server.shutdown()
                thread.join(timeout=2)

    def test_remote_http_is_rejected(self):
        with self.assertRaises(AIError):
            validate_config(AIConfig("http://example.test/v1/chat/completions", "test"))
