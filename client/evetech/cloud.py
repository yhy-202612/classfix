"""EveTech 云端账号：手机号登录、积分、AI 诊断代理、规则包。

未登录时客户端仍然可以填自己的 API 密钥直连（见 ai.py），这条路径不经过本模块，
也不消耗积分。
"""

import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE = "https://aiclass.openplays.cn/classfix-api"
USER_AGENT = "EveTech/1.2 (Windows)"
TEXT_LIMIT = 512 * 1024
CACHE_FILE = "cloud-rules.json"
CACHE_TTL = 24 * 3600


class CloudError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_base(base):
    """只接受 https；本机回环允许 http，方便自测。"""
    try:
        parsed = urllib.parse.urlsplit(base or "")
        local = parsed.hostname in ("127.0.0.1", "localhost", "::1")
        if not parsed.hostname or (parsed.scheme != "https" and not (local and parsed.scheme == "http")):
            raise ValueError
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError
    except ValueError as error:
        raise CloudError("云端地址必须是 https 开头，当前值无法使用。") from error
    return base.rstrip("/")


def call(base, path, payload=None, token="", timeout=30):
    url = check_base(base) + path
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(TEXT_LIMIT + 1)
    except urllib.error.HTTPError as error:
        body = error.read(64 * 1024).decode("utf-8", "replace")
        message = "云端返回 HTTP %d。" % error.code
        try:
            parsed = json.loads(body)
            message = parsed.get("error") or message
        except ValueError:
            pass
        raise CloudError(message) from error
    except (urllib.error.URLError, OSError) as error:
        raise CloudError("连不上 EveTech 云端：%s" % error) from error
    if len(raw) > TEXT_LIMIT:
        raise CloudError("云端响应过大。")
    try:
        result = json.loads(raw.decode("utf-8"))
    except ValueError as error:
        raise CloudError("云端响应不是有效 JSON。") from error
    return result


@dataclass
class Account:
    """本机保存的登录态。token 失效时调用方应清空。"""

    base: str = DEFAULT_BASE
    token: str = ""
    phone: str = ""
    points: int = 0
    # 各诊断类型的积分单价（由 /api/me 下发，未登录时用下面的兜底值）
    costs: dict = field(default_factory=lambda: {"audio": 1, "check": 1, "video": 2})
    daily_grant: int = 0

    @property
    def signed_in(self):
        return bool(self.token)

    def as_settings(self):
        return {"cloud_base": self.base, "cloud_token": self.token, "cloud_phone": self.phone}

    @classmethod
    def from_settings(cls, values):
        return cls(values.get("cloud_base") or DEFAULT_BASE, values.get("cloud_token", ""),
                   values.get("cloud_phone", ""), 0)


def request_code(account, phone):
    return call(account.base, "/api/auth/code", {"phone": phone}, timeout=25)


def login(account, phone, code):
    result = call(account.base, "/api/auth/login", {"phone": phone, "code": code}, timeout=25)
    account.token = result["token"]
    account.phone = (result.get("user") or {}).get("phone", phone)
    account.points = int((result.get("user") or {}).get("points", 0))
    return result


def profile(account):
    result = call(account.base, "/api/me", token=account.token, timeout=20)
    account.points = int((result.get("user") or {}).get("points", 0))
    if isinstance(result.get("cost"), dict):
        account.costs = {key: int(value) for key, value in result["cost"].items()}
    account.daily_grant = int(result.get("dailyGrant") or 0)
    return result


def cost_of(account, kind):
    """本次诊断要扣多少积分；未知类型按 1 分算。"""
    try:
        return int((account.costs or {}).get(kind, 1))
    except (TypeError, ValueError):
        return 1


def diagnose(account, kind, evidence, timeout=90):
    result = call(account.base, "/api/diagnose", {"kind": kind, "evidence": evidence},
                  token=account.token, timeout=timeout)
    account.points = int(result.get("points", account.points))
    return result


def report(account, kind, items):
    """上报「AI 提到但离线流程没有覆盖」的问题，供后台归纳成方案。"""
    return call(account.base, "/api/report", {"kind": kind, "items": items},
                token=account.token, timeout=20)


def rules(account, etag=""):
    """拉规则包。返回 (payload 或 None, etag)，None 表示 304 没有变化。"""
    url = check_base(account.base) + "/api/rules"
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            return json.loads(response.read(TEXT_LIMIT).decode("utf-8")), response.headers.get("ETag", "")
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return None, etag
        raise CloudError("规则包拉取失败（HTTP %d）。" % error.code) from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise CloudError("规则包拉取失败：%s" % error) from error


def advice_text(result):
    """把云端返回的结构化诊断拼成对话框里可读的纯文本。"""
    lines = []
    advice = str(result.get("advice") or "").strip()
    if advice:
        lines.append(advice)
    steps = [str(item).strip() for item in (result.get("steps") or []) if str(item).strip()]
    if steps:
        lines.append("")
        lines.append("操作步骤：")
        lines.extend("%d. %s" % (index + 1, step) for index, step in enumerate(steps))
    verify = str(result.get("verify") or "").strip()
    if verify:
        lines.append("")
        lines.append("验证：" + verify)
    issues = result.get("issues") or []
    if issues:
        lines.append("")
        lines.append("云端已记录的新问题（会在后台归纳成方案）：")
        lines.extend("· " + str(item.get("title", "")) for item in issues)
    return "\n".join(lines).strip() or "云端没有返回可用建议。"


def rules_for(rules_payload, kind, check_keys):
    """从规则包里挑出与本次检测相关的补充建议。"""
    if not rules_payload:
        return []
    selected = []
    wanted = set(check_keys or ())
    for item in rules_payload.get("items") or []:
        match = item.get("match") or {}
        if match.get("kind") and match.get("kind") != kind:
            continue
        # 服务端下发 checkKeys（列表），早期手工规则用 checkKey（单个），两种都认。
        keys = [key for key in (match.get("checkKeys") or []) if key]
        if match.get("checkKey"):
            keys.append(match["checkKey"])
        if keys and not (set(keys) & wanted):
            continue
        selected.append(item)
    return selected[:6]


def load_cache(directory):
    """读取本地规则包缓存；不存在或损坏时返回 None。"""
    try:
        with open(os.path.join(str(directory), CACHE_FILE), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("payload"), dict) else None


def cache_fresh(cache, ttl=CACHE_TTL):
    try:
        return bool(cache) and (time.time() - float(cache.get("fetched") or 0)) < ttl
    except (TypeError, ValueError):
        return False


def save_cache(directory, payload, etag=""):
    """原子写缓存（同目录临时文件 + 替换），失败不抛出，也不留临时文件。"""
    record = {"fetched": time.time(), "etag": etag, "payload": payload}
    directory = str(directory)
    handle = None
    try:
        os.makedirs(directory, exist_ok=True)
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory,
                                             prefix=".rules-", suffix=".tmp", delete=False)
        with handle:
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, os.path.join(directory, CACHE_FILE))
        return True
    except (OSError, TypeError, ValueError):
        # 写失败时顺手清掉半截临时文件，否则调用方（含测试的目录清理）会撞上残留文件。
        leftover = getattr(handle, "name", "")
        if leftover:
            try:
                os.unlink(leftover)
            except OSError:
                pass
        return False


def touch_cache(directory, cache):
    """304 时只把拉取时间往后推，避免每次启动都重拉。"""
    if not cache:
        return False
    return save_cache(directory, cache.get("payload") or {}, cache.get("etag") or "")


def extras_for(payload, kind, check_keys):
    """把命中的规则整理成 [(标题, [明细行])]，供报告页直接渲染。"""
    extras = []
    for item in rules_for(payload, kind, check_keys):
        lines = [str(step) for step in (item.get("steps") or []) if str(step).strip()]
        verify = str(item.get("verify") or "").strip()
        if verify:
            lines.append("验证：" + verify)
        extras.append((str(item.get("title") or "补充建议"), lines))
    return extras
