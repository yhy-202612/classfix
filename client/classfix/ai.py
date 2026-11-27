"""Optional OpenAI-compatible advisory endpoint using a fixed evidence allowlist."""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from .flow import QUESTIONS
from .model import RECOVERY, STATUS, SYMPTOMS


CHECK_KEYS = frozenset(("deck", "reference", "codec", "container", "video_codec", "audio_codec", "compat",
                        "original_codec", "original_container", "original_video_codec", "original_audio_codec", "original_compat",
                        "playback", "audio_default", "audio_volume", "audio_devices", "app_audio", "audio_playback",
                        "open_deck", "open_com", "open_env", "open_safe", "open_integrity"))
CODECS = frozenset(("h264", "hevc", "av1", "vp8", "vp9", "mpeg4", "mpeg2video", "mpeg1video", "wmv1", "wmv2",
                   "wmv3", "mjpeg", "aac", "mp3", "ac3", "eac3", "opus", "vorbis", "flac", "pcm_s16le"))
ERROR_CODE = re.compile(r"0x[0-9A-F]{8}\Z")
FEEDBACK = {
    "none": "尚未执行操作",
    "settings": "已核对播放设置或输出设备",
    "reinserted": "已重新插入或更换视频",
    "local": "已测试独立播放",
}


class AIError(Exception):
    pass


@dataclass(frozen=True)
class AIConfig:
    url: str
    model: str
    token: str = ""


def validate_config(config):
    try:
        url = urlsplit(config.url)
        local = url.hostname in ("127.0.0.1", "localhost", "::1")
        if (not url.hostname or (url.scheme != "https" and not (local and url.scheme == "http"))
                or url.username or url.password or url.query or url.fragment
                or not url.path.rstrip("/").endswith("/chat/completions")):
            raise ValueError
        _ = url.port
    except ValueError as error:
        raise AIError("请输入完整的 HTTPS /chat/completions 地址。本机服务可使用 HTTP。") from error
    if not config.model.strip() or len(config.model) > 150:
        raise AIError("请输入模型名称。")


def evidence(report, recovery, action):
    facts = report.facts
    answers = {}
    for key, source in QUESTIONS.items():
        value = facts.get("answers", {}).get(key)
        if value in dict(source.options):
            answers[source.prompt] = dict(source.options)[value]
    result = {
        "diagnosis": SYMPTOMS.get(facts.get("case"), "Windows 声音诊断"),
        "checks": [{"key": check.key, "status": STATUS[check.status]} for check in report.checks
                   if check.key in CHECK_KEYS and check.status in STATUS],
        "answers": answers,
        "verification": RECOVERY.get(recovery, "尚未验证"),
        "action": FEEDBACK.get(action, FEEDBACK["none"]),
    }
    for key in ("muted", "default_known"):
        if type(facts.get(key)) is bool:
            result[key] = facts[key]
    for key in ("volume", "active_outputs", "session_count"):
        value = facts.get(key)
        if type(value) is int and 0 <= value <= 1000:
            result[key] = value
    for key in ("video", "audio"):
        values = facts.get(key)
        if isinstance(values, list):
            result[key] = [value if value in CODECS else "other" for value in values[:64] if isinstance(value, str)]
    for key, allowed in (
        ("probe_state", ("ok", "partial", "failed", "unavailable", "unreadable", "missing")),
        ("media_status", ("present", "missing", "remote", "unreadable", "unselected")),
    ):
        if facts.get(key) in allowed:
            result[key] = facts[key]
    code = facts.get("audio_error")
    if isinstance(code, str) and ERROR_CODE.fullmatch(code):
        result["audio_error"] = code
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def analyze(config, data):
    validate_config(config)
    instructions = (
        "你是 ClassFix 的课堂急救顾问。只引用给定检测数据，未知或缺失信息说明尚未检测。"
        "可能原因按依据排序并注明推测。先给可逆、最快的恢复步骤，再给验证步骤。"
        "保留本地检测结果的优先级。不要求上传文件、不建议安装驱动或修改注册表。"
        "用简体中文，以简短纯文本回答。"
    )
    messages = [{"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]
    payload = json.dumps({"model": config.model, "messages": messages}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.token:
        headers["Authorization"] = f"Bearer {config.token}"
    request = urllib.request.Request(config.url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise AIError("AI 响应过大。")
        content = json.loads(raw)["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise AIError("AI 没有返回文本建议。")
        return content.strip()[:12000]
    except urllib.error.HTTPError as error:
        raise AIError(f"AI 服务返回 HTTP {error.code}。请核对地址、模型及密钥。") from error
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        raise AIError("AI 请求失败或响应格式不受支持。请核对服务连接和配置。") from error
