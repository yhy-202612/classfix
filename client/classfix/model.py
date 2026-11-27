from dataclasses import asdict, dataclass, field
from datetime import datetime


STATUS = {"ok": "正常", "warning": "需要注意", "problem": "存在问题", "unknown": "无法检测"}
SYMPTOMS = {
    "open": "课件打不开或打开就报错",
    "click": "点击视频没有反应",
    "format": "提示无法播放或格式不支持",
    "picture": "黑屏、卡顿或画面异常",
    "silent": "有画面但没有声音",
}
RECOVERY = {"pending": "尚未验证", "restored": "已恢复", "failed": "仍有故障"}


@dataclass
class Check:
    key: str
    title: str
    status: str
    detail: str
    # 默认折叠的次要明细（例如当前不存在的历史音频输出）。界面点「查看全部」才展开，
    # 不影响 detail 用于比对与导出的口径。
    extra: str = ""


@dataclass
class Finding:
    title: str
    evidence: str
    action: str
    confidence: str
    priority: int


@dataclass
class Report:
    kind: str
    title: str
    checks: list[Check]
    findings: list[Finding] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    time: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    revision: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data["kind"], data["title"], [Check(**x) for x in data["checks"]],
                   [Finding(**x) for x in data["findings"]], data.get("facts", {}), data["time"],
                   data.get("revision", 0))

    def summary(self):
        counts = {key: 0 for key in STATUS}
        for check in self.checks:
            counts[check.status] += 1
        return "  ·  ".join(f"{count} 项{STATUS[key]}" for key, count in counts.items())


def sort_findings(findings):
    return sorted(findings, key=lambda finding: finding.priority)
