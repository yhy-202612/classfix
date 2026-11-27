import json
import os
import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QInputDialog, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from .. import VERSION
from .. import cloud
from .. import delta
from ..ai import AIConfig, AIError, FEEDBACK, analyze, evidence, validate_config
from ..desktop_goose import DEEPSEEK_MODEL, DEEPSEEK_URL, original_goose_available, resolve_goose_folder
from ..goose_install import GOOSE_PAGE, GooseInstallError, install_goose
from ..pptx import find_ffprobe
from ..startup import set_startup, startup_enabled
from ..update import (UpdateError, download, fetch_manifest, install_root, last_update_failure,
                      launch_updater, local_package_info, local_packages, stage, stage_patch,
                      update_cache_directory)
from .jobs import run_job
from .theme import apply_theme
from .widgets import button, horizontal, label, panel
from .window import Page


def valid_phone(text):
    text = str(text or "").strip()
    return len(text) == 11 and text.isdigit() and text[0] == "1" and text[1] in "3456789"


class SettingsPage(Page):
    def __init__(self, app, settings, save, location, on_runtime_changed=None, account=None,
                 on_account_changed=None):
        super().__init__("设置", "配置本机媒体检测、账号积分、主题和可选 AI 服务。")
        self.app = app
        self.values = settings
        self.save_values = save
        self.on_runtime_changed = on_runtime_changed
        self.account = account if account is not None else cloud.Account.from_settings(settings)
        # 登录态变化时通知外部（右上角账号栏要跟着刷新）
        self.on_account_changed = on_account_changed or (lambda: None)
        background, content = panel()
        content.addWidget(label("后台监视", "heading"))
        content.addWidget(label("EveTech 常驻系统托盘。课件检测在后台完成，只有发现问题时才显示右下角提示。", "muted"))
        self.monitor = QCheckBox("监视 PowerPoint 课件和放映中的视频")
        self.monitor.setChecked(settings.get("monitor_enabled", "1") != "0")
        self.media_etw = QCheckBox("播放出现异常时短时追踪（ETW，最长 8 秒）")
        self.media_etw.setChecked(settings.get("media_etw", "1") != "0")
        self.media_etw.setToolTip("仅追踪确认的 PowerPoint 进程，证据保存在本机诊断记录。Windows 权限不足时会跳过。")
        self.memory_alerts = QCheckBox("系统内存持续偏高时提醒")
        self.memory_alerts.setChecked(settings.get("memory_alerts", "1") != "0")
        self.startup = QCheckBox("登录 Windows 后自动启动 EveTech")
        self.startup.setChecked(startup_enabled())
        threshold_row = horizontal()
        threshold_row.addWidget(label("声音过低提示阈值", "strong"))
        self.threshold = QSpinBox()
        self.threshold.setRange(5, 80)
        self.threshold.setSuffix("%")
        self.threshold.setValue(int(settings.get("low_volume_threshold", "25")))
        threshold_row.addWidget(self.threshold)
        threshold_row.addStretch()
        content.addWidget(self.monitor)
        self.event_log_status = label("Windows 事件订阅尚未就绪。", "muted")
        content.addWidget(self.event_log_status)
        content.addWidget(self.media_etw)
        content.addWidget(self.memory_alerts)
        content.addWidget(self.startup)
        content.addLayout(threshold_row)
        self.body.addWidget(background)
        media, content = panel()
        content.addWidget(label("媒体检测", "heading"))
        row = horizontal()
        self.path = QLineEdit(settings.get("ffprobe", ""))
        self.path.setPlaceholderText("自动查找程序目录、tools 目录及 PATH")
        row.addWidget(self.path, 1)
        row.addWidget(button("选择 FFprobe…", self.choose))
        content.addLayout(row)
        self.detected = label("", "muted")
        content.addWidget(self.detected)
        self.body.addWidget(media)

        online, content = panel()
        content.addWidget(label("AI 辅助分析（可选）", "heading"))
        content.addWidget(label("每次调用前可预览并确认发送内容。截图问 AI 需要支持图片输入的模型，例如 deepseek-flash。AI 密钥仅保留在本次运行内，也可由 EVETECH_AI_KEY 环境变量提供。", "muted"))
        self.url = self.field(content, "Chat Completions 请求地址", settings.get("ai_url", ""),
                              "https://服务地址/v1/chat/completions")
        self.model = self.field(content, "模型名称", settings.get("ai_model", ""), "服务提供的模型标识")
        self.secret = self.field(content, "API 密钥", "", "本次运行使用，可留空")
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.token = ""
        self.body.addWidget(online)

        self.goose_panel, content = panel()
        content.addWidget(label("Desktop Pet", "heading"))
        self.goose_enabled = QCheckBox("Enable Desktop Goose")
        self.goose_enabled.setChecked(settings.get("desktop_goose_enabled") == "1")
        self.goose_weekends = QCheckBox("Use the school schedule on weekends")
        self.goose_weekends.setChecked(settings.get("desktop_goose_weekends") == "1")
        self.goose_debug = QCheckBox("Debug mode: ignore the school schedule")
        self.goose_debug.setChecked(settings.get("desktop_goose_debug_always_show") == "1")
        self.goose_debug.setToolTip("Lets the goose roam at any time. During PowerPoint slideshows it rests in its nest.")
        self.touch_keyboard = QCheckBox("Show the touch keyboard when editing text")
        self.touch_keyboard.setChecked(settings.get("touch_keyboard_enabled") == "1")
        content.addWidget(self.goose_enabled)
        content.addWidget(self.goose_weekends)
        content.addWidget(self.goose_debug)
        content.addWidget(self.touch_keyboard)
        row = horizontal()
        self.goose_folder = QLineEdit(settings.get("desktop_goose_folder", ""))
        self.goose_folder.setReadOnly(True)
        self.goose_folder.setPlaceholderText("Extracted Desktop Goose v0.31 folder")
        row.addWidget(self.goose_folder, 1)
        row.addWidget(button("Choose folder…", self.choose_goose))
        content.addLayout(row)
        actions = horizontal()
        self.goose_download = button("Download && Set Up", self.download_goose, "primary")
        self.goose_import = button("Import Goose ZIP…", self.import_goose, "link")
        actions.addWidget(self.goose_download)
        actions.addWidget(self.goose_import)
        content.addLayout(actions)
        actions = horizontal()
        actions.addWidget(button("Official itch.io page", lambda: QDesktopServices.openUrl(QUrl(GOOSE_PAGE)), "link"))
        actions.addWidget(button("Configure DeepSeek…", self.use_deepseek, "link"))
        actions.addStretch()
        actions.addWidget(button("Save Settings", self.save_goose))
        content.addLayout(actions)
        self.goose_preview = QPushButton("Preview Goose")
        self.goose_preview.setToolTip("Show the goose on this display for 30 seconds, even outside school hours.")
        content.addWidget(self.goose_preview, alignment=Qt.AlignmentFlag.AlignLeft)
        self.goose_status = label("DeepSeek is optional. You can open Ask Goose from the system tray.", "muted")
        self.goose_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.addWidget(self.goose_status)
        self.body.addWidget(self.goose_panel)

        appearance, content = panel()
        content.addWidget(label("外观与数据", "heading"))
        self.theme = QComboBox()
        self.theme.addItem("浅色", "light")
        self.theme.addItem("深色", "dark")
        self.theme.setCurrentIndex(1 if settings.get("theme") == "dark" else 0)
        content.addWidget(self.theme)
        content.addWidget(label(f"诊断记录位置 · {location}", "small"))
        self.body.addWidget(appearance)

        self.body.addWidget(self.account_panel(self.account))

        self.pending = None
        self.progress = {"done": 0, "total": 0}
        self.ticker = QTimer(self)
        self.ticker.setInterval(400)
        self.ticker.timeout.connect(self.paint_progress)
        self.body.addWidget(self.update_panel(settings))

        row = horizontal()
        row.addWidget(button("保存设置", self.save, "primary"))
        self.message = label("", "muted")
        row.addWidget(self.message, 1)
        self.body.addLayout(row)
        self.refresh_on_open()

    # ---------------------------------------------------------------- 账号与积分

    def account_panel(self, account):
        block, content = panel()
        content.addWidget(label("账号与积分（云端 AI 诊断）", "heading"))
        content.addWidget(label(
            "登录后用积分调用云端 AI 诊断，每次 1~2 积分（视频诊断 2 分），每天赠送 %d 分。"
            "不登录也可以填自己的 API 密钥直连分析，不消耗积分。"
            % max(1, account.daily_grant or 5), "muted"))
        self.account_state = label(self.account_text(), "strong")
        content.addWidget(self.account_state)
        row = horizontal()
        self.phone = QLineEdit(account.phone if account.signed_in else "")
        self.phone.setPlaceholderText("11 位手机号")
        self.phone.setMaximumWidth(170)
        row.addWidget(self.phone)
        self.code_button = button("发送验证码", self.send_code)
        row.addWidget(self.code_button)
        self.code = QLineEdit()
        self.code.setPlaceholderText("验证码")
        self.code.setMaximumWidth(110)
        row.addWidget(self.code)
        row.addWidget(button("登录", self.do_login, "primary"))
        row.addStretch()
        content.addLayout(row)
        actions = horizontal()
        actions.addWidget(button("刷新积分", self.refresh_points))
        actions.addWidget(button("退出登录", self.sign_out))
        actions.addStretch()
        content.addLayout(actions)
        self.account_message = label("", "muted")
        content.addWidget(self.account_message)
        self.cooldown = QTimer(self)
        self.cooldown.setInterval(1000)
        self.cooldown.timeout.connect(self.tick_cooldown)
        self.cooldown_left = 0
        return block

    def account_text(self):
        account = self.account
        if not account.signed_in:
            return "当前未登录 · 云端诊断不可用（仍可使用自己的 API 密钥）"
        return "已登录 %s · 剩余 %d 积分" % (account.phone or "本机账号", account.points)

    def paint_account(self):
        self.account_state.setText(self.account_text())

    def tick_cooldown(self):
        self.cooldown_left -= 1
        if self.cooldown_left <= 0:
            self.cooldown.stop()
            self.code_button.setEnabled(True)
            self.code_button.setText("发送验证码")
        else:
            self.code_button.setText("%d 秒后可重发" % self.cooldown_left)

    def send_code(self):
        phone = self.phone.text().strip()
        if not valid_phone(phone):
            self.account_message.setText("请输入 11 位手机号。")
            return
        self.account_message.setText("正在发送验证码…")
        self.code_button.setEnabled(False)

        def done(result):
            self.account_message.setText("验证码已发送至 %s，5 分钟内有效。" % result.get("masked", phone))
            self.cooldown_left = int(result.get("retryAfter") or 60)
            self.code_button.setText("%d 秒后可重发" % self.cooldown_left)
            self.cooldown.start()

        def failed(name):
            self.code_button.setEnabled(True)
            self.code_button.setText("发送验证码")
            self.account_message.setText("发送失败（%s）。请检查网络后重试。" % name)

        run_job(lambda: self.guard_cloud(lambda: cloud.request_code(self.account, phone)), done, failed)

    def guard_cloud(self, operation):
        """把云端异常折叠成 {error: 中文原因}，避免线程里只剩异常类名。"""
        try:
            return operation()
        except cloud.CloudError as error:
            return {"error": str(error)}

    def do_login(self):
        phone = self.phone.text().strip()
        code = self.code.text().strip()
        if not valid_phone(phone):
            self.account_message.setText("请输入 11 位手机号。")
            return
        if not code:
            self.account_message.setText("请输入收到的验证码。")
            return
        self.account_message.setText("正在登录…")
        account = self.account

        def operation():
            try:
                result = cloud.login(account, phone, code)
            except cloud.CloudError as error:
                return {"error": str(error)}
            cloud.profile(account)
            return result

        def done(result):
            if "error" in result:
                self.account_message.setText(result["error"])
                return
            values = account.as_settings()
            self.values.update(values)
            self.save_values(values)
            self.paint_account()
            self.on_account_changed()
            self.account_message.setText(
                ("已创建账号，赠送 %d 积分。" % account.points) if result.get("created")
                else "登录成功，剩余 %d 积分。" % account.points)

        run_job(operation, done, lambda name: self.account_message.setText("登录失败（%s）。" % name))

    def refresh_points(self):
        if not self.account.signed_in:
            self.account_message.setText("请先登录。")
            return
        self.account_message.setText("正在刷新积分…")

        def done(result):
            if isinstance(result, dict) and "error" in result:
                self.account_message.setText(result["error"])
                return
            self.paint_account()
            self.on_account_changed()
            granted = int(result.get("grantedToday") or 0)
            self.account_message.setText(
                "今日已赠送 %d 积分，当前 %d 积分。" % (granted, self.account.points)
                if granted else "当前 %d 积分。" % self.account.points)

        run_job(lambda: self.guard_cloud(lambda: cloud.profile(self.account)
                                         if self.account.signed_in else {"error": "请先登录。"}),
                done, lambda name: self.account_message.setText("刷新失败（%s）。" % name))

    def sign_out(self):
        self.account.token = ""
        self.account.points = 0
        self.values.update({"cloud_token": ""})
        self.save_values({"cloud_token": ""})
        self.paint_account()
        self.on_account_changed()
        self.account_message.setText("已退出登录。云端诊断不可用，仍可使用自己的 API 密钥。")

    # ---------------------------------------------------------------- 软件更新

    def update_panel(self, settings):
        block, content = panel()
        content.addWidget(label("软件更新", "heading"))
        self.update_current = label("", "muted")
        content.addWidget(self.update_current)
        row = horizontal()
        self.update_button = button("检查更新", self.check_update)
        self.local_button = button("从文件更新…", self.update_from_file)
        row.addWidget(self.update_button)
        row.addWidget(self.local_button)
        row.addStretch()
        content.addLayout(row)
        self.update_status = label("", "muted")
        content.addWidget(self.update_status)
        self.auto_check = QCheckBox("启动时自动检查更新（每天一次）")
        self.auto_check.setChecked(settings.get("update_check", "1") != "0")
        content.addWidget(self.auto_check)
        self.refresh_update_label()
        return block

    def refresh_update_label(self):
        root = install_root()
        if getattr(sys, "frozen", False):
            text = "当前版本 %s · 安装目录 %s" % (VERSION, root)
        else:
            text = "当前版本 %s · 源码模式运行，自动替换仅打包版可用" % VERSION
        self.update_current.setText(text)
        problem = last_update_failure()
        if problem:
            self.update_status.setText(
                "⚠ %s\n也可以直接点「从文件更新…」选刚下载的完整包，绕过自动替换。" % problem)
            return
        packages = local_packages(root)[:3]
        if packages:
            self.update_status.setText("发现本地发布包：%s" % "、".join(item.name for item in packages))

    def refresh_update_button(self):
        self.update_button.setText("立即重启并更新" if self.pending else "检查更新")

    def busy(self, active, text=""):
        for widget in (self.update_button, self.local_button):
            widget.setEnabled(not active)
        if text:
            self.update_status.setText(text)
        if not active:
            self.refresh_update_button()

    def guarded(self, operation):
        """把更新过程中的异常折叠成 {error: 文案}，并返回闭包交给后台线程执行。

        这里必须返回可调用对象，不能当场执行：三个调用点都把返回值直接传给
        run_job()，若写成 return operation()，run_job 收到的就是 dict，
        后台线程执行 self.operation() 时会抛 TypeError: 'dict' object is not callable，
        界面只显示「更新过程中出错（TypeError）」，检查更新/下载/解压全废。
        """
        def run():
            try:
                return operation()
            except UpdateError as error:
                return {"error": str(error)}
            except OSError as error:
                return {"error": "文件操作失败：%s" % error}
        return run

    def check_update(self):
        if self.pending:
            self.ask_restart()
            return
        self.busy(True, "正在检查更新…")
        run_job(self.guarded(fetch_manifest), self.manifest_ready, self.failed)

    def manifest_ready(self, manifest):
        self.busy(False)
        if "error" in manifest:
            self.update_status.setText(manifest["error"])
            return
        if not manifest["available"]:
            self.update_status.setText("已是最新版本（清单版本 %s）。" % manifest["version"])
            return
        plan = delta.choose(manifest, install_root())
        notes = manifest["notes"] or "本次更新没有提供说明。"
        if plan["mode"] == "patch":
            notes = "%s\n\n（%s）" % (notes, plan["reason"])
        question = "发现新版本 %s（当前 %s）\n\n%s\n\n现在下载吗？" % (manifest["version"], VERSION, notes)
        if QMessageBox.question(self, "发现新版本", question,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.start_download(manifest, plan=plan)
        else:
            self.update_status.setText("已跳过 %s。" % manifest["version"])

    def start_download(self, manifest, force_full=False, plan=None):
        if plan is None:
            if force_full:
                plan = {"mode": "full", "reason": "", "full_size": int(manifest["size"]),
                        "entry": {"url": manifest["url"], "sha256": manifest["sha256"],
                                  "size": manifest["size"]}}
            else:
                plan = delta.choose(manifest, install_root())
        entry = plan["entry"]
        self.pending_plan = plan
        self.pending_manifest = manifest
        if plan["mode"] == "full":
            self.used_fallback = bool(force_full)
        if plan["mode"] == "patch":
            name = "EveTech-%s-to-%s.patch.zip" % (plan["entry"].get("from") or "old", manifest["version"])
            self.progress.update(done=0, total=entry["size"])
            self.ticker.start()
            self.busy(True, "正在下载增量更新…")
        else:
            name = "EveTech-%s-Windows-x64.zip" % manifest["version"]
            self.progress.update(done=0, total=entry["size"])
            self.ticker.start()
            self.busy(True, "正在下载…")
        target = update_cache_directory() / name

        def operation():
            return download(entry["url"], target, entry["sha256"], entry["size"],
                            on_progress=lambda done, total: self.progress.update(
                                done=done, total=total or entry["size"]))

        run_job(self.guarded(operation),
                lambda result: self.download_ready(manifest, plan, result), self.failed)

    def paint_progress(self):
        done = self.progress["done"]
        total = self.progress["total"] or 0
        if total:
            self.update_status.setText("正在下载… %d%% · %.1f / %.1f MB"
                                       % (min(100, done * 100 // total), done / 1048576.0, total / 1048576.0))
        else:
            self.update_status.setText("正在下载… %.1f MB" % (done / 1048576.0))

    def download_ready(self, manifest, plan, result):
        self.ticker.stop()
        self.busy(False)
        if isinstance(result, dict) and "error" in result:
            if plan.get("mode") == "patch":
                self.used_fallback = True
                self.update_status.setText("%s 已改为下载完整包，请稍候…" % result["error"])
                self.start_download(manifest, force_full=True)
                return
            self.update_status.setText(result["error"])
            return
        if plan.get("mode") == "patch":
            self.staged(lambda path, version: stage_patch(path, version, current_version=VERSION),
                        (result, manifest["version"]), manifest["version"])
            return
        self.staged(stage, (result, manifest["version"]), manifest["version"])

    def staged(self, operation, arguments, version):
        self.busy(True, "正在准备安装文件…")

        def run_stage():
            return operation(*arguments)

        run_job(self.guarded(run_stage), lambda result: self.stage_ready(result, version), self.failed)

    def stage_ready(self, result, version):
        self.busy(False)
        if isinstance(result, dict) and "error" in result:
            plan = getattr(self, "pending_plan", None) or {}
            manifest = getattr(self, "pending_manifest", None)
            if plan.get("mode") == "patch" and manifest and not getattr(self, "used_fallback", False):
                self.used_fallback = True
                self.update_status.setText("%s 已改为下载完整包，请稍候…" % result["error"])
                self.start_download(manifest, force_full=True)
                return
            self.update_status.setText(result["error"])
            return
        _inner, script = result
        self.pending = {"version": version, "script": script}
        self.update_button.setText("立即重启并更新")
        self.update_status.setText("%s 已准备好，重启后完成安装。" % version)
        self.ask_restart()

    def ask_restart(self):
        if not self.pending:
            return
        text = ("EveTech 会先退出，然后自动替换文件并重新启动，大约十几秒。\n"
                "过程中不会再弹出任何命令行窗口，请不要手动干预。\n"
                "当前版本 %s → %s\n\n"
                "如果半分钟后没有自动打开，说明替换被系统拦下了："
                "请到下载页重新下载完整安装包，解压后覆盖安装目录即可。"
                % (VERSION, self.pending["version"]))
        if QMessageBox.question(self, "安装更新", text,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            if not launch_updater(self.pending["script"]):
                self.update_status.setText("当前环境不支持自动替换，请手动解压覆盖安装目录。")
                return
            # 必须走主窗口的完整退出流程（停监视器、隐藏托盘），
            # 只调 QApplication.quit() 时进程可能还活着，替换脚本等不到退出就会中止。
            quit_all = getattr(self.window(), "quit_application", None)
            if callable(quit_all):
                quit_all()
            else:
                QApplication.instance().quit()

    def update_from_file(self):
        file, _ = QFileDialog.getOpenFileName(self, "选择 EveTech 发布包", str(install_root()),
                                              "EveTech 发布包 (*.zip)")
        if not file:
            return
        try:
            info = local_package_info(file)
        except UpdateError as error:
            self.update_status.setText(str(error))
            return
        if not info["available"]:
            question = "这个包是 %s，不比当前 %s 新，仍要安装吗？" % (info["version"], VERSION)
            if QMessageBox.question(self, "版本确认", question,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        if info["patch"]:
            if info["from"] and info["from"] != VERSION:
                self.update_status.setText("这个增量包是 %s → %s，本机是 %s，请改用完整包。"
                                           % (info["from"], info["version"], VERSION))
                return
            # 离线包不自动回落（没网可回落），失败就把原因直接说清楚
            self.pending_plan = {"mode": "patch"}
            self.pending_manifest = None
            self.used_fallback = True
            self.staged(lambda path, version: stage_patch(path, version, current_version=VERSION),
                        (file, info["version"]), info["version"])
            return
        self.pending_plan = {"mode": "full"}
        self.pending_manifest = None
        self.used_fallback = True
        self.staged(stage, (file, info["version"]), info["version"])

    def failed(self, name):
        self.ticker.stop()
        self.busy(False)
        self.update_status.setText("更新过程中出错（%s），请稍后再试。" % name)

    def field(self, panel_layout, title, initial, placeholder):
        panel_layout.addWidget(label(title, "strong"))
        line = QLineEdit(initial)
        line.setPlaceholderText(placeholder)
        panel_layout.addWidget(line)
        return line

    def choose(self):
        file, _ = QFileDialog.getOpenFileName(self, "选择 FFprobe", "", "FFprobe (ffprobe.exe)")
        if file:
            self.path.setText(file)

    def choose_goose(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose the extracted Desktop Goose v0.31 folder",
                                                   self.goose_folder.text(),
                                                   QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog)
        if not folder:
            return
        folder = resolve_goose_folder(folder)
        if not folder:
            self.goose_status.setText("Choose the fully extracted, official Windows Desktop Goose v0.31 folder.")
            return
        self.goose_folder.setText(folder)
        self.goose_enabled.setChecked(True)
        self.goose_status.setText("Desktop Goose v0.31 was found. Click Save Settings to enable it.")

    def download_goose(self):
        self.setup_goose()

    def import_goose(self):
        archive, _ = QFileDialog.getOpenFileName(self, "Choose the official Desktop Goose Windows ZIP",
                                                  "", "ZIP archives (*.zip)")
        if archive:
            self.setup_goose(archive)

    def setup_goose(self, archive=None):
        self.goose_download.setEnabled(False)
        self.goose_import.setEnabled(False)
        self.goose_status.setText("Checking the original ZIP…" if archive else "Downloading Desktop Goose from itch.io…")

        def operation():
            try:
                return str(install_goose(archive)), ""
            except GooseInstallError as error:
                return "", str(error)

        def finished(result):
            folder, error = result
            self.goose_download.setEnabled(True)
            self.goose_import.setEnabled(True)
            if error:
                self.goose_status.setText(error)
                return
            values = {"desktop_goose_folder": folder, "desktop_goose_enabled": "1"}
            if not self.save_values(values):
                self.goose_status.setText("Desktop Goose was downloaded, but EveTech could not save its settings.")
                return
            self.values.update(values)
            self.goose_folder.setText(folder)
            self.goose_enabled.setChecked(True)
            self.goose_status.setText("Desktop Goose is installed. Click Preview Goose to try it.")
            if self.on_runtime_changed:
                self.on_runtime_changed(values)

        run_job(operation, finished, lambda name: finished(("", "Desktop Goose setup failed (" + name + ").")))

    def use_deepseek(self):
        key, accepted = QInputDialog.getText(self, "DeepSeek for Desktop Goose",
                                             "DeepSeek API key (this session only)",
                                             QLineEdit.EchoMode.Password, self.secret.text())
        if not accepted:
            return
        self.url.setText(DEEPSEEK_URL)
        self.model.setText(DEEPSEEK_MODEL)
        self.secret.setText(key)
        self.goose_status.setText("Click Save Settings to apply your DeepSeek configuration.")

    def save_goose(self):
        self.save(from_desktop_pet=True)

    def config(self):
        return AIConfig(self.values.get("ai_url", ""), self.values.get("ai_model", ""),
                        self.token or os.environ.get("EVETECH_AI_KEY") or os.environ.get("CLASSFIX_AI_KEY", ""))

    def refresh_on_open(self):
        detected = find_ffprobe(self.values.get("ffprobe", ""))
        self.detected.setText(f"已找到 · {detected}" if detected else "尚未检测 · 当前找不到 FFprobe。")

    def save(self, *, from_desktop_pet=False):
        message = self.goose_status if from_desktop_pet else self.message
        values = {"ffprobe": self.path.text().strip(), "ai_url": self.url.text().strip(),
                  "ai_model": self.model.text().strip(), "theme": self.theme.currentData(),
                  "monitor_enabled": "1" if self.monitor.isChecked() else "0",
                  "media_etw": "1" if self.media_etw.isChecked() else "0",
                  "memory_alerts": "1" if self.memory_alerts.isChecked() else "0",
                  "desktop_goose_enabled": "1" if self.goose_enabled.isChecked() else "0",
                  "desktop_goose_weekends": "1" if self.goose_weekends.isChecked() else "0",
                  "desktop_goose_debug_always_show": "1" if self.goose_debug.isChecked() else "0",
                  "desktop_goose_folder": self.goose_folder.text(),
                  "touch_keyboard_enabled": "1" if self.touch_keyboard.isChecked() else "0",
                  "low_volume_threshold": str(self.threshold.value()),
                  "update_check": "1" if self.auto_check.isChecked() else "0"}
        if values["ffprobe"] and not find_ffprobe(values["ffprobe"]):
            message.setText("The selected ffprobe.exe could not be found. Check the path."
                            if from_desktop_pet else "找不到指定的 ffprobe.exe，请检查路径。")
            return
        if self.goose_enabled.isChecked() and not original_goose_available(values["desktop_goose_folder"]):
            text = "Choose the extracted Desktop Goose v0.31 folder first."
            self.goose_status.setText(text)
            message.setText(text)
            return
        try:
            if values["ai_url"] or values["ai_model"]:
                validate_config(AIConfig(values["ai_url"], values["ai_model"]))
        except AIError as error:
            message.setText("Check the AI request address and model name in Settings."
                            if from_desktop_pet else str(error))
            return
        if not self.save_values(values):
            message.setText("Could not save settings. Check write access to the data folder."
                            if from_desktop_pet else "本地设置保存失败，请检查数据目录写入权限。")
            return
        self.values.update(values)
        self.token = self.secret.text().strip()
        apply_theme(self.app, values["theme"] == "dark")
        self.refresh_on_open()
        self.goose_status.setText("Desktop pet settings saved.")
        if self.on_runtime_changed:
            self.on_runtime_changed(values)
        startup_saved = set_startup(self.startup.isChecked())
        if from_desktop_pet:
            if not startup_saved:
                self.message.setText("Settings saved. The Windows startup shortcut could not be updated.")
        else:
            self.message.setText("设置已保存。" if startup_saved else "设置已保存，但开机自启快捷方式更新失败。")
        return True


class AnalysisDialog(QDialog):
    """AI 辅助分析：可选云端（扣积分）或自己的 API 密钥（免费）。"""

    def __init__(self, parent, config, report, recovery, account=None, on_points=None):
        super().__init__(parent)
        self.setWindowTitle("EveTech · AI 辅助分析")
        self.resize(660, 680)
        self.config = config
        self.report = report
        self.recovery = recovery
        self.account = account if account is not None else cloud.Account()
        self.on_points = on_points
        self.busy = False
        self.kind = getattr(report, "kind", "check")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)

        layout.addWidget(label("分析通道", "strong"))
        self.channel = QComboBox()
        if self.account.signed_in:
            cost = cloud.cost_of(self.account, self.kind)
            self.channel.addItem("EveTech 云端 · 本次 %d 积分（现有 %d 积分）"
                                 % (cost, self.account.points), "cloud")
        if config.url and config.model:
            self.channel.addItem("自己的 API · %s（不扣积分）" % config.model, "key")
        if self.channel.count() == 0:
            self.channel.addItem("尚未登录，也没有填写自己的 API 密钥", "")
            self.channel.setEnabled(False)
        self.channel.currentIndexChanged.connect(self.paint_endpoint)
        layout.addWidget(self.channel)
        self.endpoint = label("", "muted")
        layout.addWidget(self.endpoint)

        layout.addWidget(label("补充已执行的排障操作", "heading"))
        self.action = QComboBox()
        for key, title in FEEDBACK.items():
            self.action.addItem(title, key)
        self.action.currentIndexChanged.connect(self.update_preview)
        layout.addWidget(self.action)
        layout.addWidget(label("即将发送的检测摘要", "strong"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(190)
        layout.addWidget(self.preview)
        self.paint_endpoint()
        self.update_preview()
        row = horizontal()
        self.send = button("确认发送并分析", self.request, "primary")
        row.addWidget(self.send)
        row.addWidget(button("关闭", self.close))
        row.addStretch()
        layout.addLayout(row)
        self.message = label("AI 输出是排障建议，请结合本机复测判断。", "muted")
        layout.addWidget(self.message)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)

    def paint_endpoint(self, _index=None):
        kind = self.channel.currentData()
        if kind == "cloud":
            self.endpoint.setText("发送至 %s · 由云端统一调用模型，本机不需要 API 密钥"
                                  % self.account.base)
        elif kind == "key":
            self.endpoint.setText("发送至 %s" % self.config.url)
        else:
            self.endpoint.setText("请先到「设置 · 账号与积分」登录，或填写自己的 API 密钥。")

    def update_preview(self, _index=None):
        self.data = evidence(self.report, self.recovery, self.action.currentData())
        self.preview.setPlainText(json.dumps(self.data, ensure_ascii=False, indent=2))

    def set_busy(self, busy):
        self.busy = busy
        self.send.setEnabled(not busy)
        self.action.setEnabled(not busy)
        self.channel.setEnabled(not busy and self.channel.count() > 1)

    def request(self):
        channel = self.channel.currentData()
        if channel == "cloud":
            self.request_cloud()
        elif channel == "key":
            self.request_key()
        else:
            self.message.setText("请先到「设置 · 账号与积分」登录，或填写自己的 API 密钥。")

    def request_cloud(self):
        self.set_busy(True)
        self.message.setText("正在请求云端分析（会扣除积分）…")
        self.output.clear()
        account, data, kind = self.account, self.data, self.kind

        def operation():
            try:
                return True, cloud.diagnose(account, kind, data)
            except cloud.CloudError as error:
                return False, str(error)

        run_job(operation, self.cloud_finished, self.failed)

    def request_key(self):
        self.set_busy(True)
        self.message.setText("正在请求辅助分析…")
        self.output.clear()
        config, data = self.config, self.data

        def operation():
            try:
                return True, analyze(config, data)
            except AIError as error:
                return False, str(error)

        run_job(operation, self.key_finished, self.failed)

    def cloud_finished(self, response):
        self.set_busy(False)
        good, result = response
        if not good:
            self.message.setText(result)
            return
        if self.on_points:
            self.on_points(self.account.points)
        cost = int(result.get("cost") or 0)
        self.message.setText("已扣除 %d 积分，剩余 %d 积分。请返回诊断页面复测并记录恢复结果。"
                             % (cost, self.account.points))
        self.output.setPlainText(cloud.advice_text(result))
        self.channel.setItemText(0, "EveTech 云端 · 本次 %d 积分（现有 %d 积分）"
                                % (cloud.cost_of(self.account, self.kind), self.account.points))

    def key_finished(self, response):
        self.set_busy(False)
        good, message = response
        self.message.setText("请返回诊断页面复测并记录恢复结果。" if good else message)
        if good:
            self.output.setPlainText(message)

    def failed(self, _error):
        self.set_busy(False)
        self.message.setText("AI 请求失败，请稍后重试。")

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)
