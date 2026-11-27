import os

from PySide6.QtWidgets import QMessageBox

from ..model import RECOVERY
from .jobs import run_job
from .report import display_comparison, display_report
from .widgets import button, clear, horizontal, label, panel
from .window import Page


SETTINGS = {
    "sound": "ms-settings:sound",
    "mixer": "ms-settings:apps-volume",
    "display": "ms-settings:display",
    "network": "ms-settings:network-status",
}


def open_settings(parent, target):
    try:
        os.startfile(SETTINGS[target])
    except (AttributeError, OSError):
        QMessageBox.information(parent, "无法打开设置", "请按 Win+I 打开 Windows 设置，并手动进入相应页面。")


class DevicePage(Page):
    def __init__(self, title, description, operation, links, persist=None, ai=None, verification=False,
                 actions=()):
        super().__init__(title, description)
        self.operation = operation
        self.links = links
        self.persist = persist
        self.ai = ai
        self.verification = verification
        self.actions = actions
        self.busy = False
        self.initial = self.latest = None
        self.record_id = None
        self.recovery = "pending"
        self.error = ""
        self.expanded = set()
        self.render()

    def start(self, repeat=False):
        if self.busy:
            return
        previous_feedback = self.recovery if repeat else "pending"
        self.busy = True
        self.error = ""
        self.render()
        run_job(lambda: self.operation(previous_feedback), lambda report: self.completed(report, repeat), self.failed)

    def completed(self, report, repeat):
        self.busy = False
        if repeat and self.latest:
            report.revision = self.latest.revision + 1
        self.latest = report
        if not repeat or not self.initial:
            self.initial = report
            self.record_id = None
        self.recovery = "pending"
        self.save()
        self.render()

    def save(self):
        if self.persist:
            self.record_id = self.persist(self.initial, self.latest, self.recovery, self.record_id)

    def failed(self, name):
        self.busy = False
        self.error = f"本次检测失败（{name}）。可重试。"
        self.render()

    def indicate(self, recovery):
        self.recovery = recovery
        self.save()
        self.render()

    def toggle_extra(self, key):
        """折叠/展开一条检测项的次要明细，只重绘本页。"""
        if key in self.expanded:
            self.expanded.discard(key)
        else:
            self.expanded.add(key)
        self.render()

    def render(self):
        clear(self.body)
        card, content = panel()
        if self.latest:
            content.addWidget(label(self.latest.summary(), "tag"))
        elif not self.busy:
            content.addWidget(label("尚未检测", "tag"))
        row = horizontal()
        start = button("正在检查…" if self.busy else "重新检测" if self.latest else "开始检查",
                       lambda: self.start(self.latest is not None), "primary", "check")
        start.setEnabled(not self.busy)
        row.addWidget(start)
        for target, caption in self.links:
            row.addWidget(button(caption, lambda _checked=False, name=target: open_settings(self, name)))
        for caption, action in self.actions:
            entry = button(caption, lambda _checked=False, run=action: run())
            if self.busy:
                entry.setEnabled(False)
            row.addWidget(entry)
        row.addStretch()
        content.addLayout(row)
        if self.error:
            content.addWidget(label(self.error, "muted"))
        if self.latest and self.verification and not self.busy:
            content.addWidget(label(f"实际播放反馈 · {RECOVERY[self.recovery]}", "strong"))
            content.addWidget(label("请播放已知有声的内容，确认实际输出。", "muted"))
            checks = horizontal()
            checks.addWidget(button("已恢复", lambda: self.indicate("restored")))
            checks.addWidget(button("仍有故障", lambda: self.indicate("failed")))
            if self.ai:
                checks.addWidget(button("分析复杂情况", lambda: self.ai(self.latest, self.recovery)))
            checks.addStretch()
            content.addLayout(checks)
        self.body.addWidget(card)
        if self.latest:
            display_report(self.body, self.latest, expanded=self.expanded, on_toggle=self.toggle_extra)
            if self.initial is not self.latest:
                display_comparison(self.body, self.initial, self.latest)
