import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from classfix import cloud


class Handler(BaseHTTPRequestHandler):
    """最小的云端替身：只实现客户端会用到的几个端点。"""

    routes = {}
    seen = []

    def log_message(self, *_args):
        pass

    def _answer(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Handler.seen.append(("GET", self.path, self.headers.get("If-None-Match", ""),
                             self.headers.get("Authorization", "")))
        if self.path == "/api/me":
            return self._answer(200, {"ok": True, "user": {"phone": "138****8000", "points": 12}})
        if self.path == "/api/rules":
            if self.headers.get("If-None-Match") == 'W/"cf-rules-3"':
                self.send_response(304)
                self.end_headers()
                return None
            return self._answer(200, {"ok": True, "version": 3, "items": [
                {"id": "s1", "match": {"kind": "audio", "checkKeys": ["audio_devices"]},
                 "title": "确认蓝牙耳机已断开", "steps": ["关掉耳机蓝牙"], "verify": "声音回到音箱"},
                {"id": "s2", "match": {"kind": "", "checkKeys": []},
                 "title": "任何诊断都提示的通用检查", "steps": ["看一眼投影线缆"], "verify": "画面稳定"},
                {"id": "s3", "match": {"kind": "video", "checkKey": "display"},
                 "title": "旧格式单键规则", "steps": ["按 Win+P"], "verify": "第二屏出现"}]},
                {"ETag": 'W/"cf-rules-3"'})
        return self._answer(404, {"ok": False, "error": "没有这个接口。"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        Handler.seen.append(("POST", self.path, payload, self.headers.get("Authorization", "")))
        if self.path == "/api/auth/code":
            return self._answer(200, {"ok": True, "masked": "138****8000", "retryAfter": 60})
        if self.path == "/api/auth/login":
            if payload.get("code") != "123456":
                return self._answer(401, {"ok": False, "error": "验证码不正确或已过期"})
            return self._answer(200, {"ok": True, "created": True, "token": "c1.test.token",
                                      "user": {"phone": "138****8000", "points": 10}})
        if self.path == "/api/diagnose":
            if payload.get("kind") == "poor":
                return self._answer(402, {"ok": False, "error": "积分不足", "points": 0, "cost": 2})
            return self._answer(200, {"ok": True, "advice": "先把 Win+P 切到复制。",
                                      "steps": ["按 Win+P", "选复制"], "verify": "教学屏出现同样画面",
                                      "issues": [{"title": "投影线缆松动"}], "points": 8, "cost": 2})
        if self.path == "/api/report":
            return self._answer(200, {"ok": True, "stored": [{"id": "i1", "title": "投影线缆松动"}]})
        return self._answer(404, {"ok": False, "error": "没有这个接口。"})


class CloudTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.seen = []
        self.account = cloud.Account(base=self.base)

    def test_plain_http_cloud_address_is_rejected(self):
        with self.assertRaises(cloud.CloudError):
            cloud.check_base("http://example.com/classfix-api")
        self.assertEqual(cloud.check_base(self.base + "/"), self.base)

    def test_login_stores_token_and_points(self):
        result = cloud.login(self.account, "13800138000", "123456")
        self.assertTrue(result["created"])
        self.assertEqual(self.account.token, "c1.test.token")
        self.assertEqual(self.account.points, 10)
        self.assertEqual(self.account.phone, "138****8000")

    def test_wrong_code_raises_cloud_error_with_server_message(self):
        with self.assertRaises(cloud.CloudError) as caught:
            cloud.login(self.account, "13800138000", "000000")
        self.assertIn("验证码", str(caught.exception))

    def test_profile_refreshes_points(self):
        self.account.token = "c1.test.token"
        cloud.profile(self.account)
        self.assertEqual(self.account.points, 12)

    def test_diagnose_returns_advice_steps_and_issues(self):
        self.account.token = "c1.test.token"
        result = cloud.diagnose(self.account, "video", {"checks": []})
        self.assertEqual(self.account.points, 8)
        text = cloud.advice_text(result)
        self.assertIn("Win+P", text)
        self.assertIn("1. 按 Win+P", text)
        self.assertIn("验证：教学屏出现同样画面", text)
        self.assertIn("投影线缆松动", text)

    def test_insufficient_points_is_reported_as_cloud_error(self):
        self.account.token = "c1.test.token"
        with self.assertRaises(cloud.CloudError) as caught:
            cloud.diagnose(self.account, "poor", {})
        self.assertIn("积分不足", str(caught.exception))

    def test_report_posts_items(self):
        self.account.token = "c1.test.token"
        result = cloud.report(self.account, "audio", [{"title": "投影线缆松动"}])
        self.assertEqual(result["stored"][0]["title"], "投影线缆松动")

    def test_rules_etag_round_trip(self):
        payload, etag = cloud.rules(self.account)
        self.assertEqual(payload["version"], 3)
        self.assertEqual(etag, 'W/"cf-rules-3"')
        again, same = cloud.rules(self.account, etag)
        self.assertIsNone(again)
        self.assertEqual(same, etag)

    def test_rules_are_filtered_by_kind_and_check_key(self):
        payload, _etag = cloud.rules(self.account)
        titles = [item["title"] for item in cloud.rules_for(payload, "audio", ["audio_devices"])]
        self.assertEqual(titles, ["确认蓝牙耳机已断开", "任何诊断都提示的通用检查"])
        # kind 或检测项对不上都不应出现；旧格式的单个 checkKey 仍然生效。
        self.assertEqual([item["title"] for item in cloud.rules_for(payload, "video", ["audio_devices"])],
                         ["任何诊断都提示的通用检查"])
        self.assertEqual([item["title"] for item in cloud.rules_for(payload, "audio", ["display"])],
                         ["任何诊断都提示的通用检查"])
        self.assertEqual([item["title"] for item in cloud.rules_for(payload, "video", ["display"])],
                         ["任何诊断都提示的通用检查", "旧格式单键规则"])

    def test_extras_flatten_steps_and_verify(self):
        payload, _etag = cloud.rules(self.account)
        extras = cloud.extras_for(payload, "audio", ["audio_devices"])
        self.assertEqual(extras[0][0], "确认蓝牙耳机已断开")
        self.assertEqual(extras[0][1], ["关掉耳机蓝牙", "验证：声音回到音箱"])
        self.assertEqual(extras[1][1], ["看一眼投影线缆", "验证：画面稳定"])

    def test_rules_cache_round_trip(self):
        import tempfile
        directory = tempfile.mkdtemp()
        self.assertIsNone(cloud.load_cache(directory))
        self.assertFalse(cloud.cache_fresh(None))
        payload, etag = cloud.rules(self.account)
        self.assertTrue(cloud.save_cache(directory, payload, etag))
        cache = cloud.load_cache(directory)
        self.assertEqual(cache["payload"]["version"], 3)
        self.assertEqual(cache["etag"], etag)
        self.assertTrue(cloud.cache_fresh(cache))
        self.assertFalse(cloud.cache_fresh(cache, ttl=-1))
        self.assertTrue(cloud.touch_cache(directory, cache))

    def test_account_round_trip_through_settings(self):
        values = cloud.Account(self.base, "c1.test.token", "138****8000", 9).as_settings()
        restored = cloud.Account.from_settings(values)
        self.assertEqual(restored.token, "c1.test.token")
        self.assertEqual(restored.base, self.base)
        self.assertTrue(restored.signed_in)
        self.assertFalse(cloud.Account().signed_in)


if __name__ == "__main__":
    unittest.main()
