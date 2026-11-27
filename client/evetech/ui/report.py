from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..model import STATUS
from .widgets import button, horizontal, label, panel, rule

# 云端规则包注入点：provider(report) -> [(标题, [明细行])]。
# 没有登录或没有缓存时为 None，报告页行为与离线完全一致。
_RULE_PROVIDER = None


def set_rule_provider(provider):
    global _RULE_PROVIDER
    _RULE_PROVIDER = provider


def check_row(check, expanded=False, on_toggle=None):
    widget = QWidget()
    row = horizontal(14)
    row.setContentsMargins(0, 5, 0, 5)
    words = QVBoxLayout()
    words.setSpacing(3)
    words.addWidget(label(check.title, "strong"))
    words.addWidget(label(check.detail, "muted"))
    if check.extra:
        if on_toggle is None:
            # 没有交互入口的场景（历史记录、复测对比）直接展开，避免出现点了没反应的按钮。
            words.addWidget(label(check.extra, "small"))
        else:
            count = len(check.extra.splitlines())
            caption = "收起（%d 项）" % count if expanded else "查看全部（另有 %d 项）" % count
            toggle = (lambda: on_toggle(check.key))
            line = horizontal(8)
            line.addWidget(button(caption, toggle, "link"))
            line.addStretch()
            words.addLayout(line)
            if expanded:
                words.addWidget(label(check.extra, "small"))
    row.addLayout(words, 1)
    status = label(STATUS[check.status], "tag", False)
    status.setProperty("status", check.status)
    row.addWidget(status)
    widget.setLayout(row)
    return widget


def display_report(layout, report, include_findings=True, expanded=None, on_toggle=None):
    if include_findings and report.findings:
        box, content = panel()
        content.addWidget(label("原因与建议（按优先顺序）", "heading"))
        for index, finding in enumerate(report.findings):
            if index:
                content.addWidget(rule())
            line = horizontal()
            line.addWidget(label(finding.title, "strong"), 1)
            line.addWidget(label(finding.confidence, "tag", False))
            content.addLayout(line)
            content.addWidget(label(finding.evidence, "muted"))
            content.addWidget(label(finding.action))
        layout.addWidget(box)
    box, content = panel()
    content.addWidget(label("检测明细", "heading"))
    for index, check in enumerate(report.checks):
        if index:
            content.addWidget(rule())
        content.addWidget(check_row(check, check.key in (expanded or ()), on_toggle))
    layout.addWidget(box)
    if include_findings:
        display_extras(layout, report)


def display_extras(layout, report):
    """云端规则包命中的补充建议（离线流程没有覆盖到的情况）。"""
    if _RULE_PROVIDER is None:
        return
    try:
        extras = _RULE_PROVIDER(report) or []
    except Exception:
        return
    if not extras:
        return
    box, content = panel()
    content.addWidget(label("云端补充建议", "heading"))
    content.addWidget(label("以下条目来自云端规则包，不在本机离线流程内，请结合现场情况判断。", "muted"))
    for title, lines in extras:
        content.addWidget(rule())
        content.addWidget(label(title, "strong"))
        for line in lines:
            content.addWidget(label("· " + str(line), "muted"))
    layout.addWidget(box)


def display_comparison(layout, initial, latest):
    from ..model import Check

    box, content = panel()
    content.addWidget(label("修复前后对比", "heading"))
    content.addWidget(label(f"首次检测 · {initial.time[11:19]}    复测 · {latest.time[11:19]}", "muted"))
    before = {check.key: check for check in initial.checks}
    changed = [item for item in latest.checks
               if item.key not in before or (item.status, item.detail) !=
               (before[item.key].status, before[item.key].detail)]
    if not changed:
        content.addWidget(label("已复测，检测状态与首次一致。请结合实际播放结果判断是否恢复。"))
    for item in changed:
        old = before.get(item.key)
        previous = f"{STATUS[old.status]} · {old.detail}" if old else "尚未检测"
        content.addWidget(check_row(Check(item.key, item.title, item.status,
                                          f"修复前  {previous}\n复测后  {STATUS[item.status]} · {item.detail}")))
    layout.addWidget(box)
