from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QProgressBar

from ..find import repair, reveal, search
from ..flow import diagnose, questions
from ..model import RECOVERY, SYMPTOMS
from ..pptx import read_deck
from .jobs import run_job
from .report import display_comparison, display_report
from .widgets import button, clear, horizontal, label, panel
from .window import Page


def shorten(text, limit=72):
    text = str(text)
    if len(text) <= limit:
        return text
    head = limit // 2 - 3
    return text[:head] + " … " + text[-(limit - head - 3):]


class VideoPage(Page):
    def __init__(self, ffprobe, audio=None, persist=None, ai=None, open_setting=None):
        super().__init__("PPT 视频诊断", "选一个症状，定位到具体视频，再检查播放所需条件。")
        self.ffprobe = ffprobe
        self.audio = audio
        self.persist = persist
        self.ai = ai
        self.open_setting = open_setting
        self.case = ""
        self.stage = 0
        self.deck_path = self.original_path = self.media = self.deck = None
        self.answers = {}
        self.index = 0
        self.busy = False
        self.initial = self.latest = None
        self.record_id = None
        self.select_automatically = True
        self.recovery = "pending"
        self.error = ""
        self.finder = None
        self.find_token = None
        self.render()

    def begin(self, case):
        if self.busy:
            return
        self.reset_finder()
        self.case = case
        self.stage = 1
        self.answers = {}
        self.index = 0
        self.deck_path = self.original_path = self.media = self.deck = None
        self.initial = self.latest = None
        self.record_id = None
        self.select_automatically = True
        self.recovery = "pending"
        self.error = ""
        self.render()

    def reset_finder(self):
        if self.find_token:
            self.find_token["stop"] = True
        self.find_token = None
        self.finder = None

    def set_stage(self, stage):
        self.error = ""
        self.stage = stage
        self.render()

    def render(self):
        clear(self.body)
        if self.stage:
            progress = QProgressBar()
            progress.setTextVisible(False)
            progress.setRange(0, 4)
            progress.setValue(self.stage)
            self.body.addWidget(progress)
            phase = ("", "1  选择文件", "2  定位视频", "3  回答问题", "4  结果与复测")[self.stage]
            self.body.addWidget(label(f"{phase}    ·    {SYMPTOMS[self.case]}", "tag"))
            hints = {
                1: "待检查：PPTX 文件、视频引用和媒体编码",
                2: "已完成：读取文件结构    ·    待检查：视频引用及编码",
                3: "已完成：定位视频来源    ·    待检查：编码及实际播放",
                4: "已完成：本机检测    ·    实际恢复由放映验证",
            }
            self.body.addWidget(label(hints[self.stage], "small"))
        if self.error:
            self.body.addWidget(label(self.error, "muted"))
        if self.busy:
            self.body.addWidget(label("正在读取检测数据，请稍候…", "heading"))
            return
        render = (self.render_cases, self.render_files, self.render_media, self.render_question, self.render_result)
        render[self.stage]()

    def render_cases(self):
        for index, (key, symptom) in enumerate(SYMPTOMS.items()):
            box, content = panel()
            row = horizontal(14)
            row.addWidget(label(chr(65 + index), "tag", False))
            row.addWidget(label(symptom, "strong"), 1)
            row.addWidget(button("开始", lambda _checked=False, case=key: self.begin(case), "link", "arrow"))
            content.addLayout(row)
            self.body.addWidget(box)

    def render_files(self):
        box, content = panel()
        fields = [("PowerPoint 课件", self.deck_path, "deck_path", "PowerPoint (*.pptx *.pptm *.ppsx *.ppsm);;所有文件 (*)")]
        if self.case != "open":
            fields.append(("原始视频（可选）", self.original_path, "original_path",
                           "常见视频 (*.mp4 *.m4v *.mov *.avi *.wmv *.mkv *.webm *.mpg *.mpeg *.ts *.m2ts *.flv *.3gp);;所有文件 (*)"))
        for title, current, source, file_filter in fields:
            content.addWidget(label(title, "strong"))
            row = horizontal()
            row.addWidget(label(str(current) if current else "未选择", "muted"), 1)
            row.addWidget(button("浏览…", lambda _checked=False, name=source, pattern=file_filter:
                                 self.choose(name, pattern)))
            if current:
                row.addWidget(button("清除", lambda _checked=False, name=source: self.clear_file(name)))
            content.addLayout(row)
        self.body.addWidget(box)
        if self.case == "open":
            if self.deck_path:
                self.actions("返回症状", lambda: self.set_stage(0), "开始打开测试", self.scan)
            else:
                self.actions("返回症状", lambda: self.set_stage(0))
        else:
            self.actions("返回症状", lambda: self.set_stage(0),
                         "检查文件并继续" if self.deck_path or self.original_path else "无文件，继续问答", self.scan)

    def choose(self, attribute, file_filter):
        file, _ = QFileDialog.getOpenFileName(self, "选择本地文件", "", file_filter)
        if file:
            self.set_file(attribute, Path(file))

    def clear_file(self, attribute):
        self.set_file(attribute, None)

    def set_file(self, attribute, path):
        if attribute == "deck_path" and self.deck_path != path:
            self.media = None
            self.answers = {}
            self.select_automatically = True
            self.reset_finder()
        setattr(self, attribute, path)
        self.render()

    def scan(self):
        self.busy = True
        self.error = ""
        path = self.deck_path
        self.render()
        if self.case == "open":
            self.run_diagnosis()
            return
        run_job(lambda: read_deck(path), self.scan_finished, self.failed)

    def scan_finished(self, deck):
        self.deck = deck
        self.busy = False
        self.set_stage(2)

    def failed(self, name):
        self.busy = False
        self.error = f"无法完成本次检测（{name}）。请重试或返回上一步。"
        self.render()

    def render_media(self):
        box, content = panel()
        content.addWidget(label("定位发生故障的视频", "heading"))
        if self.deck:
            content.addWidget(label(self.deck.check.detail, "muted"))
        if self.deck and self.deck.media:
            lost = self.deck.missing
            if lost:
                notice = label("⚠ " + self.deck.missing_summary(), "strong")
                notice.setProperty("status", "problem")
                content.addWidget(notice)
            choices = QComboBox()
            choices.addItem("无法确定或仅诊断原视频", None)
            selected = 1 if len(self.deck.media) == 1 and self.select_automatically else 0
            if len(lost) == 1 and self.select_automatically and self.media is None:
                # One broken reference: jump straight to it instead of making the teacher guess.
                selected = self.deck.media.index(lost[0]) + 1
            for index, media in enumerate(self.deck.media, 1):
                choices.addItem(f"{media.description} · {media.state_text}", media)
                if self.media and media == self.media:
                    selected = index
            choices.setCurrentIndex(selected)
            content.addWidget(choices)
            self.media = choices.currentData()
            choices.currentIndexChanged.connect(lambda _: self.select_media(choices.currentData()))
            content.addWidget(label("按 PowerPoint 实际放映顺序显示页码，每一条后标注本机检测结果。", "small"))
        else:
            self.media = None
            content.addWidget(label("可继续回答问题，并用独立视频文件辅助诊断。"))
        self.render_finder(content)
        self.body.addWidget(box)
        self.actions("上一步", lambda: self.set_stage(1), "继续", self.start_questions)

    def select_media(self, media):
        if media != self.media:
            self.reset_finder()
        self.media = media
        self.select_automatically = False
        self.render()

    def render_finder(self, content):
        """Local recovery panel: only meaningful for a broken external link."""
        media = self.media
        finder = self.finder
        broken = media is not None and media.location == "linked" and media.availability == "missing"
        if not broken and not finder:
            return
        content.addWidget(label("本机找回", "heading"))
        if not finder or finder.get("state") == "idle":
            content.addWidget(button("在本机查找这个视频", self.start_find))
            content.addWidget(label("先在桌面、下载、文档和 U 盘找同名文件，再扫其它磁盘；只读取文件名，不会改动文件。",
                                    "small"))
            return
        if finder.get("state") == "running":
            content.addWidget(label("正在本机查找同名或相近的视频…", "strong"))
            content.addWidget(label("可以继续用其它功能，结果回来后会显示在这里。", "small"))
            content.addWidget(button("取消查找", self.cancel_find, "link"))
            return
        note = finder.get("note")
        error = finder.get("error")
        if note:
            fixed = label(note, "strong")
            fixed.setProperty("status", "ok")
            content.addWidget(fixed)
        if error:
            failed = label(error, "strong")
            failed.setProperty("status", "problem")
            content.addWidget(failed)
        result = finder.get("result")
        if result is not None and result.candidates:
            content.addWidget(label(f"找到 {len(result.candidates)} 个候选 · 扫描 {result.scanned} 个文件夹 · "
                                    f"{result.seconds} 秒", "small"))
            for candidate in result.candidates[:6]:
                row, lines = panel(12, 3)
                lines.addWidget(label(candidate.path.name, "strong"))
                lines.addWidget(label(shorten(str(candidate.path)), "small"))
                lines.addWidget(label(f"{candidate.size_text} · {candidate.time_text} · {candidate.reason}", "small"))
                actions = horizontal(8)
                actions.addWidget(button("在文件夹中显示",
                                         lambda _checked=False, path=candidate.path: reveal(path), "link"))
                actions.addWidget(button("复制路径",
                                         lambda _checked=False, path=candidate.path: self.copy_path(path), "link"))
                actions.addWidget(button("指回课件",
                                         lambda _checked=False, item=candidate: self.repoint(item)))
                actions.addStretch(1)
                lines.addLayout(actions)
                content.addWidget(row)
        elif result is not None and not error and not note:
            content.addWidget(label("没有找到同名或足够相近的视频。"
                                    "可以换个磁盘再试，或直接在 PowerPoint 中重新插入一份本地副本。", "muted"))
        if result is not None and not note:
            content.addWidget(button("重新查找", self.start_find, "link"))
        if finder.get("stopped"):
            content.addWidget(label(f"上次查找未扫完：{finder['stopped']}。", "small"))

    def start_find(self):
        media = self.media
        if media is None or (self.finder and self.finder.get("state") == "running"):
            return
        token = {"stop": False}
        self.find_token = token
        self.finder = {"state": "running"}
        self.render()
        run_job(lambda: search(media.target, stop=lambda: token["stop"]),
                self.find_finished, self.find_failed)

    def cancel_find(self):
        if self.find_token:
            self.find_token["stop"] = True
        if self.finder and self.finder.get("state") == "running":
            self.finder = {"state": "done", "result": None, "note": "", "error": "已取消本次查找。"}
        if self.stage == 2:
            self.render()

    def find_finished(self, result):
        if self.find_token and self.find_token.get("stop"):
            return
        self.finder = {"state": "done", "result": result, "note": "", "error": result.error,
                       "stopped": result.stopped}
        if self.stage == 2:
            self.render()

    def find_failed(self, name):
        self.finder = {"state": "done", "result": None, "note": "", "error": f"查找失败（{name}）。"}
        if self.stage == 2:
            self.render()

    def copy_path(self, path):
        QApplication.clipboard().setText(str(path))

    def repoint(self, candidate):
        media, deck = self.media, self.deck_path
        if media is None or deck is None:
            return
        self.finder = dict(self.finder or {}, note="", error="", state="running")
        self.render()

        def operation():
            try:
                return "ok", repair(Path(deck), media, candidate.path)
            except ValueError as error:
                return "error", str(error)

        run_job(operation, self.repointed, self.find_failed)

    def repointed(self, outcome):
        kind, value = outcome
        if kind != "ok":
            self.finder = {"state": "done", "result": None, "note": "", "error": value}
            if self.stage == 2:
                self.render()
            return
        backup, target = value
        old = self.media
        self.deck = read_deck(self.deck_path)
        self.media = next((item for item in self.deck.media
                           if item.slide == getattr(old, "slide", 0) and item.location == "linked"
                           and item.availability == "present"), None)
        self.select_automatically = False
        self.finder = {"state": "done", "result": None,
                       "note": f"已把第 {getattr(old, 'slide', '?')} 页的视频指向 {target}，"
                               f"原课件备份为 {backup.name}。现在可以点「继续」重新检测，或直接放映验证。",
                       "error": ""}
        if self.stage == 2:
            self.render()

    def start_questions(self):
        self.index = 0
        self.set_stage(3)

    def render_question(self):
        steps = questions(self.case, self.answers, self.media is not None)
        question = steps[self.index]
        box, content = panel()
        content.addWidget(label(f"问题 {self.index + 1} / {len(steps)}", "tag"))
        content.addWidget(label(question.prompt, "heading"))
        for key, text in question.options:
            chosen = self.answers.get(question.key) == key
            option = button(text + ("  ✓" if chosen else ""),
                            lambda _checked=False, answer=key: self.answer(question.key, answer),
                            "primary" if chosen else "")
            content.addWidget(option, alignment=Qt.AlignmentFlag.AlignLeft)
        self.body.addWidget(box)
        self.actions("上一步", self.back_question)

    def answer(self, key, value):
        self.answers[key] = value
        steps = questions(self.case, self.answers, self.media is not None)
        valid = {step.key for step in steps}
        self.answers = {question: answer for question, answer in self.answers.items() if question in valid}
        if self.index + 1 < len(steps):
            self.index += 1
            self.render()
        else:
            self.run_diagnosis()

    def back_question(self):
        if self.index:
            self.index -= 1
            self.render()
        else:
            self.set_stage(2)

    def run_diagnosis(self, retest=False):
        self.busy = True
        self.error = ""
        self.render()
        case, deck, media, original = self.case, self.deck_path, self.media, self.original_path
        setting, answers, inspect_audio = self.ffprobe(), dict(self.answers), self.audio
        previous_feedback = self.recovery if retest else "pending"

        def operation():
            sound = inspect_audio(previous_feedback) if case == "silent" and inspect_audio else None
            return diagnose(case, deck, media, original, setting, answers, sound, previous_feedback)

        run_job(operation, lambda report: self.completed(report, retest), self.failed)

    def completed(self, report, retest):
        self.busy = False
        if not retest:
            self.initial = report
            self.record_id = None
        else:
            report.revision = self.latest.revision + 1
        self.latest = report
        self.recovery = "pending"
        self.stage = 4
        self.save()
        self.render()

    def save(self):
        if self.persist:
            self.record_id = self.persist(self.initial, self.latest, self.recovery, self.record_id)

    def set_recovery(self, state):
        self.recovery = state
        self.save()
        self.render()

    def render_result(self):
        box, content = panel()
        content.addWidget(label(self.latest.summary(), "tag"))
        content.addWidget(label("验证恢复", "heading"))
        content.addWidget(label("执行建议后，先复测检测项，再在 PowerPoint 当前页放映并记录结果。", "muted"))
        content.addWidget(label(f"实际放映反馈 · {RECOVERY[self.recovery]}", "strong"))
        row = horizontal()
        for text, action, role in (
            ("重新检测", lambda: self.run_diagnosis(True), "primary"),
            ("已恢复", lambda: self.set_recovery("restored"), ""),
            ("仍有故障", lambda: self.set_recovery("failed"), ""),
        ):
            row.addWidget(button(text, action, role))
        row.addStretch()
        content.addLayout(row)
        tools = horizontal()
        if self.open_setting and self.case == "silent":
            tools.addWidget(button("声音设置", lambda: self.open_setting("sound")))
            tools.addWidget(button("音量混合器", lambda: self.open_setting("mixer")))
        if self.open_setting and self.case == "picture":
            tools.addWidget(button("显示设置", lambda: self.open_setting("display")))
        if self.ai:
            tools.addWidget(button("分析复杂情况", lambda: self.ai(self.latest, self.recovery)))
        tools.addStretch()
        content.addLayout(tools)
        self.body.addWidget(box)
        display_report(self.body, self.latest)
        if self.latest is not self.initial:
            display_comparison(self.body, self.initial, self.latest)
        if self.case == "open":
            self.actions("返回选择课件", lambda: self.set_stage(1), "诊断另一项", lambda: self.set_stage(0))
        else:
            self.actions("返回问题", lambda: self.set_stage(3), "诊断另一项", lambda: self.set_stage(0))

    def actions(self, back, go_back, forward="", go_forward=None):
        row = horizontal()
        row.addWidget(button(back, go_back))
        row.addStretch()
        if go_forward:
            row.addWidget(button(forward, go_forward, "primary"))
        self.body.addLayout(row)
