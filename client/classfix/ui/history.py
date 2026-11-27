from PySide6.QtWidgets import QDialog, QListWidget, QScrollArea, QVBoxLayout, QWidget

from ..model import RECOVERY
from .report import display_comparison, display_report
from .widgets import button, label, panel
from .window import Page


class HistoryPage(Page):
    def __init__(self, records):
        super().__init__("诊断记录", "查看本机保存的故障证据、建议及复测结果。")
        self.records = records
        self.items = []
        card, content = panel()
        self.message = label("", "muted")
        content.addWidget(self.message)
        self.list = QListWidget()
        self.list.setMinimumHeight(310)
        self.list.currentRowChanged.connect(self.selection_changed)
        self.list.itemActivated.connect(self.open_record)
        content.addWidget(self.list)
        self.open = button("查看记录", self.open_record, "primary", "arrow")
        self.open.setEnabled(False)
        content.addWidget(self.open)
        self.body.addWidget(card)

    def refresh_on_open(self):
        self.items = self.records()
        self.list.clear()
        for item in self.items:
            label_text = f"{item.latest.time[:16].replace('T', ' ')}  ·  {item.latest.title}"
            if item.latest.kind != "check":
                label_text += f"  ·  {RECOVERY[item.recovery]}"
            self.list.addItem(label_text)
        self.message.setText(f"本机最近 {len(self.items)} 条记录" if self.items else "还没有诊断记录。")
        if self.items:
            self.list.setCurrentRow(0)

    def selection_changed(self, row):
        self.open.setEnabled(0 <= row < len(self.items))

    def open_record(self, _activated=None):
        row = self.list.currentRow()
        if not 0 <= row < len(self.items):
            return
        item = self.items[row]
        dialog = QDialog(self)
        dialog.setWindowTitle(item.latest.title)
        dialog.resize(770, 660)
        outer = QVBoxLayout(dialog)
        viewport = QScrollArea()
        viewport.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("Page")
        body = QVBoxLayout(content)
        body.setContentsMargins(17, 15, 17, 15)
        body.addWidget(label(item.latest.title, "heading"))
        body.addWidget(label(item.latest.summary(), "tag"))
        if item.latest.kind != "check":
            body.addWidget(label(f"实际播放反馈 · {RECOVERY[item.recovery]}", "muted"))
        display_report(body, item.latest)
        if item.latest.to_dict() != item.initial.to_dict():
            display_comparison(body, item.initial, item.latest)
        body.addStretch()
        viewport.setWidget(content)
        outer.addWidget(viewport, 1)
        outer.addWidget(button("关闭", dialog.close))
        dialog.exec()
