import sqlite3
import sys
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import VERSION
from . import cloud
from .ai import AIConfig, AIError, validate_config
from .audio import inspect_audio
from .desktop_goose import DesktopGoose, original_goose_available
from .instance import SingleInstance
from .layered import LayeredDiagnostics
from .monitor import PowerPointWatcher
from .performance import MemoryPressure
from .preflight import inspect_preflight, probe_presentation_api
from .startup import set_startup, startup_enabled
from .storage import Store, data_directory
from .update import cleanup_scripts, cleanup_staging, fetch_manifest, last_update_failure
from .ui.device import DevicePage, open_settings
from .ui.account import AccountBar, AccountDialog
from .ui.flash import flash_screens
from .ui.history import HistoryPage
from .ui.overlay import MediaAlerts, MemoryPopup, VolumePopup
from .ui.popup_stack import PopupStack
from .ui.preferences import AnalysisDialog, SettingsPage
from .ui.report import set_rule_provider
from .ui.jobs import run_job
from .ui.screenshot import ScreenshotTool
from .ui.tray import TrayIcon
from .ui.theme import apply_theme
from .ui.video import VideoPage
from .ui.widgets import button
from .ui.window import AidPage, HomePage, MainWindow


def create_window(directory=None):
    app = QApplication.instance()
    window = MainWindow()
    go = window.navigate
    root = Path(directory) if directory is not None else data_directory()
    settings = {}
    store = None
    try:
        store = Store(root)
        settings = store.settings()
    except (OSError, sqlite3.Error):
        store = None
        window.statusBar().showMessage("本地数据库暂不可用。本次仍可进行检测。")

    def records(limit=150, kind=None):
        if store:
            try:
                return store.records(limit, kind)
            except (ValueError, KeyError, sqlite3.Error):
                window.statusBar().showMessage("部分本地记录无法读取。")
        return []

    def save_settings(values):
        if not store:
            return False
        try:
            store.update_settings(values)
        except sqlite3.Error:
            return False
        return True

    def persist(initial, latest, recovery, record_id):
        if store:
            try:
                return store.save(initial, latest, recovery, record_id)
            except (OSError, sqlite3.Error):
                window.statusBar().showMessage("本次记录未能写入本地数据库。")
        return record_id

    def refresh_home():
        checks = records(1, "check")
        previous = records(3)
        if checks:
            report = checks[0].latest
            home.health.setText(f"{report.time[:16].replace('T', ' ')} 的巡检结果\n{report.summary()}")
        if previous:
            home.history.setText("\n".join(f"{item.latest.time[5:16].replace('T', ' ')}  ·  {item.latest.title}"
                                          for item in previous))

    def verify_projection():
        """在副屏上闪示，让老师亲眼确认教学屏真的投出了画面。"""
        count = flash_screens()
        if not count:
            QMessageBox.information(
                window, "没有可闪示的副屏",
                "Windows 当前只检测到一块屏幕，无法在教学屏上闪示验证。\n\n"
                "请先打开投影电源、插好线缆，按 Win+P 选择「复制」，再重新检测。")
            return
        answer = QMessageBox.question(
            window, "投影验证",
            "已在 %d 块副屏上闪示 4 秒蓝色画面，画面写着「EveTech 投影验证」。\n\n"
            "教学屏（投影）上是否出现了这个画面？" % count,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            stamp = datetime.now().isoformat(timespec="seconds")
            window.settings_values["projection_verified"] = stamp
            save_settings({"projection_verified": stamp})
            window.statusBar().showMessage("投影验证已确认 · %s"
                                           % stamp[5:16].replace("T", " "), 12000)
        else:
            window.statusBar().showMessage(
                "教学屏没有出现闪示画面。请核对投影输入源、分配器与 Win+P 模式。", 20000)

    def probe_office():
        """真调一次放映接口，给出「能不能放映」的确定结论。"""
        if QMessageBox.question(
                window, "实测放映接口",
                "这一步会真实调用放映接口（Microsoft PowerPoint 与 WPS 演示都会实测），\n"
                "并真的启动一次放映、约 0.3 秒后自动退出。\n"
                "若放映程序没有在运行，会短暂启动一个后台窗口（随后自动关闭），\n"
                "首次检测通常需要 10~40 秒，请稍候。\n\n继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return

        def ready(result):
            ok, text = result
            window.statusBar().showMessage(text, 20000)
            show = QMessageBox.information if ok else QMessageBox.warning
            show(window, "放映接口实测", text)

        def failed(name):
            window.statusBar().showMessage("实测放映接口失败（%s）。" % name, 15000)

        # 首次实测会拉起放映程序（WPS 冒充时尤其慢，实测 37 秒），必须给出等待提示。
        window.statusBar().showMessage("正在启动放映程序并检测放映接口，最长约 40 秒…", 0)
        run_job(probe_presentation_api, ready, failed)

    account = cloud.Account.from_settings(settings)

    def points_changed(points):
        preferences.account.points = points
        preferences.paint_account()
        bar = getattr(window, "account_bar", None)
        if bar is not None:
            bar.refresh()

    rule_state = {"payload": None}

    def rule_extras(report):
        payload = rule_state.get("payload")
        if not payload:
            return []
        return cloud.extras_for(payload, getattr(report, "kind", "check"),
                                [check.key for check in report.checks])

    set_rule_provider(rule_extras)

    def refresh_rules():
        """24 小时节流拉一次云端规则包；任何失败都静默跳过，不影响离线使用。"""
        cache = cloud.load_cache(root)
        if cloud.cache_fresh(cache):
            rule_state["payload"] = cache.get("payload")
            return
        etag = (cache or {}).get("etag", "")

        def operation():
            try:
                payload, fresh_etag = cloud.rules(account, etag)
            except cloud.CloudError:
                return (cache or {}).get("payload")
            if payload is None:
                cloud.touch_cache(root, cache)
                return (cache or {}).get("payload")
            cloud.save_cache(root, payload, fresh_etag)
            return payload

        run_job(operation, lambda payload: rule_state.update(payload=payload), lambda _name: None)

    def analyze_report(report, recovery):
        """AI 辅助分析：云端（扣积分）或自己的 API 密钥，两条通道都不可用时引导去设置。"""
        config = preferences.config()
        try:
            validate_config(config)
            key_ready = True
        except AIError:
            config, key_ready = AIConfig("", ""), False
        if not account.signed_in and not key_ready:
            answer = QMessageBox.question(
                window, "AI 尚未配置",
                "AI 辅助分析需要下面任意一条通道：\n\n"
                "· 登录 EveTech 账号，用积分调用云端诊断（每天赠送 5 积分，诊断每次 1~2 积分）\n"
                "· 或者填写自己的 API 密钥直连，不消耗积分\n\n"
                "现在去「设置」配置吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if answer == QMessageBox.StandardButton.Yes:
                go("settings")
            return
        AnalysisDialog(window, config, report, recovery, account=account,
                       on_points=points_changed).exec()
        points_changed(account.points)

    apply_theme(app, settings.get("theme") == "dark")
    watcher = PowerPointWatcher(lambda: settings.get("ffprobe", ""),
                                int(settings.get("low_volume_threshold", "25")), window)
    diagnostics = LayeredDiagnostics(persist, lambda: settings.get("media_etw", "1") != "0", window)
    watcher.monitoring_changed.connect(diagnostics.set_monitoring)
    watcher.playback_failure.connect(diagnostics.handle_playback_failure)
    watcher.media_record.connect(diagnostics.record_media_problem)

    def runtime_changed(values):
        if values.get("media_etw") == "0":
            diagnostics.cancel_trace()
        tray = getattr(window, "tray", None)
        if "low_volume_threshold" in values:
            watcher.set_threshold(values["low_volume_threshold"])
        if "monitor_enabled" in values:
            monitor_enabled = values["monitor_enabled"] != "0"
            watcher.set_enabled(monitor_enabled)
            if tray and tray.monitor_action.isChecked() != monitor_enabled:
                tray.monitor_action.blockSignals(True)
                tray.monitor_action.setChecked(monitor_enabled)
                tray.monitor_action.blockSignals(False)
        desktop_goose = getattr(window, "desktop_goose", None)
        if desktop_goose is not None:
            if tray is not None and tray.goose_enabled_action is not None:
                tray.goose_enabled_action.setChecked(desktop_goose.goose_enabled())
            desktop_goose.refresh()

    def account_changed():
        """登录 / 退出后把登录态写回设置，并同步设置页与右上角账号栏。"""
        values = account.as_settings()
        window.settings_values.update(values)
        save_settings(values)
        page = window.pages.get("settings", (None, None))[0]
        if hasattr(page, "paint_account"):
            page.account = account
            page.paint_account()
        bar = getattr(window, "account_bar", None)
        if bar is not None:
            bar.account = account
            bar.refresh()

    def open_account():
        AccountDialog(window, account, account_changed).exec()

    account_bar = AccountBar(account, open_account)
    window.set_account_bar(account_bar)

    preferences = SettingsPage(app, settings, save_settings, root, runtime_changed, account=account,
                               on_account_changed=account_changed)
    diagnostics.log_status_changed.connect(preferences.event_log_status.setText)

    home = HomePage(go, lambda: go("check").start(), refresh_home)
    window.add_page("home", home)
    window.add_page("video", VideoPage(lambda: settings.get("ffprobe", ""), inspect_audio, persist, analyze_report,
                                         open_setting=lambda key: open_settings(window, key)))
    window.add_page("audio", DevicePage("声音诊断", "检查 Windows 音频输出、系统音量与 PowerPoint 会话。",
                                         inspect_audio, (("sound", "声音设置"), ("mixer", "音量混合器")),
                                         persist, analyze_report, verification=True))
    window.add_page("check", DevicePage("课前巡检", "读取本机声音、显示器、网络和常见教学设备的状态。",
                                         inspect_preflight, (("display", "显示设置"), ("network", "网络设置")),
                                         persist, actions=(("验证投影投出", verify_projection),
                                                           ("实测放映接口", probe_office))))
    window.add_page("aid", AidPage(lambda case: go("video").begin(case), lambda: go("audio").start(), lambda: go("check").start()))
    window.add_page("history", HistoryPage(records))
    window.add_page("settings", preferences)
    window.navigate("home")
    window.watcher = watcher
    window.layered_diagnostics = diagnostics
    window.settings_values = settings
    window.save_settings = save_settings
    refresh_rules()
    return window


def wire_monitor_popups(watcher, media_alerts, volume_popup):
    watcher.media_problem.connect(media_alerts.notify_problem)
    watcher.low_volume.connect(volume_popup.notify)
    volume_popup.dismissed.connect(watcher.suppress_current_playback)


def wire_desktop_goose_tray(window, preferences, desktop_goose, open_goose_settings):
    tray = window.tray
    action = tray.goose_enabled_action

    def set_enabled(enabled):
        if enabled and not original_goose_available(window.settings_values.get("desktop_goose_folder", "")):
            action.setChecked(desktop_goose.goose_enabled())
            open_goose_settings()
            preferences.goose_status.setText("Choose the extracted Desktop Goose v0.31 folder and click Save Settings.")
            return
        value = "1" if enabled else "0"
        if not window.save_settings({"desktop_goose_enabled": value}):
            action.setChecked(desktop_goose.goose_enabled())
            tray.showMessage("Desktop Goose", "Could not save the Desktop Goose setting.",
                             QSystemTrayIcon.MessageIcon.Warning)
            return
        window.settings_values["desktop_goose_enabled"] = value
        preferences.goose_enabled.setChecked(enabled)
        preferences.goose_status.setText("Desktop Goose is enabled." if enabled else "Desktop Goose is off.")
        desktop_goose.refresh()

    action.setChecked(desktop_goose.goose_enabled())
    action.triggered.connect(set_enabled)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("EveTech")
    app.setOrganizationName("EveTech")
    app.setQuitOnLastWindowClosed(False)

    try:
        cleanup_staging()
        # 上次更新被打断时会在安装目录上一级留下 EveTech-update-*.bat，
        # 用户容易把它当成安装包去双击（又跑一遍旧流程）。启动即清掉。
        cleanup_scripts()
    except OSError:
        pass

    smoke_mode = "--smoke-test" in sys.argv
    instance = None
    if not smoke_mode:
        pending = {}

        def receive(message):
            window = pending.get("window")
            if window and message == "show":
                window.showNormal()
                window.raise_()
                window.activateWindow()

        # Share the guard with existing ClassFix versions that use the same database.
        instance = SingleInstance("ClassFix-PowerPoint-Monitor", data_directory() / "instance.lock", receive, app)
        state = instance.listen()
        if state == "handoff":
            return 0
        if state == "blocked":
            owner = instance.lock_owner()
            QMessageBox.warning(
                None, "EveTech 已在运行",
                "已经有一个 EveTech 在运行%s。\n\n"
                "如果那是旧版本，它会让新版「装上去了却打不开」：请到屏幕右下角托盘里"
                "右键 EveTech 图标选择「退出」，然后再启动新版。\n\n"
                "如果只是想打开窗口，直接在托盘图标上双击即可。"
                % (("（进程号 %s）" % owner) if owner else ""))
            return 0

    window = create_window()
    if instance:
        pending["window"] = window

    if smoke_mode:
        window.show()
        def smoke_test():
            try:
                if len(sys.argv) != 3 or sys.argv[1] != "--smoke-test":
                    app.exit(2)
                    return
                report = inspect_preflight()
                Path(sys.argv[2]).write_text(json.dumps(report.to_dict(), ensure_ascii=False), encoding="utf-8")
            except Exception:
                app.exit(1)
            else:
                app.quit()

        QTimer.singleShot(200, smoke_test)
        return app.exec()

    stack = PopupStack(app)
    media_alerts = MediaAlerts(app, stack)
    volume_popup = VolumePopup(app, stack)
    memory_popup = MemoryPopup(app, stack, window.settings_values.get("memory_snoozed_until"),
                               lambda until: window.save_settings({"memory_snoozed_until": str(until)}))
    memory = MemoryPressure(lambda: window.settings_values.get("memory_alerts", "1") != "0", window)
    memory.sampled.connect(memory_popup.update_if_open)
    memory.high.connect(lambda percent, available: memory_popup.notify(percent, available, window.watcher.show_location))
    memory.timer.start()
    wire_monitor_popups(window.watcher, media_alerts, volume_popup)
    preferences, settings_scroll = window.pages["settings"]
    desktop_goose = DesktopGoose(window.settings_values, preferences.config, window)
    window.desktop_goose = desktop_goose
    desktop_goose.status.connect(preferences.goose_status.setText)
    preferences.goose_preview.clicked.connect(lambda: desktop_goose.preview(window.winId()))
    window.watcher.slideshow_changed.connect(desktop_goose.set_slideshow)
    app.aboutToQuit.connect(desktop_goose.shutdown)
    app.aboutToQuit.connect(window.watcher.shutdown)

    def open_goose_settings():
        window.showNormal()
        window.raise_()
        window.activateWindow()
        window.navigate("settings")
        settings_scroll.ensureWidgetVisible(preferences.goose_panel)

    def open_screenshot_ai_settings():
        window.showNormal()
        window.raise_()
        window.activateWindow()
        window.navigate("settings")
        settings_scroll.ensureWidgetVisible(preferences.url, 20, 80)

    screenshot = ScreenshotTool(
        preferences.config, open_screenshot_ai_settings,
        lambda message: QMessageBox.warning(window, "截图问 AI", message),
    )
    desktop_goose.circle_requested.connect(screenshot.start)
    window.bar_extra.insertWidget(0, button("截图问 AI", screenshot.start))

    def quit_application():
        window.allow_exit = True
        window.watcher.shutdown()
        if window.tray is not None:
            window.tray.hide()
        app.quit()

    # 设置页的「立即重启并更新」也要走这里：只调 QApplication.quit() 时
    # 监视器还在轮询 PowerPoint、托盘图标还在，替换脚本等不到进程退出就会中止。
    window.quit_application = quit_application

    if QSystemTrayIcon.isSystemTrayAvailable():
        window.tray = TrayIcon(window, window.watcher, quit_application, desktop_goose, open_goose_settings,
                               screenshot.start)
        wire_desktop_goose_tray(window, preferences, desktop_goose, open_goose_settings)
        window.background_enabled = True
        window.tray.monitor_action.setChecked(window.settings_values.get("monitor_enabled", "1") != "0")

        def persist_tray_monitor(enabled):
            value = "1" if enabled else "0"
            window.settings_values["monitor_enabled"] = value
            window.save_settings({"monitor_enabled": value})
            # 托盘改了要立刻生效，不能等下次启动（设置页走的是 runtime_changed，这里得自己调）
            window.watcher.set_enabled(enabled)
            # 设置页的开关跟着托盘走。原来是直接引用 create_window 的局部变量 preferences，
            # 在 main() 里根本取不到（NameError），只能通过 window.pages 拿页面对象。
            page = window.pages.get("settings", (None, None))[0]
            if page is not None and hasattr(page, "monitor"):
                page.monitor.blockSignals(True)
                page.monitor.setChecked(enabled)
                page.monitor.blockSignals(False)

        window.tray.monitor_action.toggled.connect(persist_tray_monitor)
        window.tray.show()
    else:
        window.tray = None
        window.background_enabled = False
        app.setQuitOnLastWindowClosed(True)

    background = "--background" in sys.argv
    if not background:
        window.show()
    if window.settings_values.get("monitor_enabled", "1") != "0":
        window.watcher.start()
    if desktop_goose.active():
        desktop_goose.refresh()

    def configure_startup():
        if not getattr(sys, "frozen", False) or window.settings_values.get("startup_prompted") == "1":
            return
        answer = QMessageBox.question(
            window, "让 EveTech 随 Windows 启动？",
            "EveTech 需要常驻后台监视 PowerPoint。是否为当前用户启用开机自启？\n\n"
            "之后可以在“设置”中随时关闭。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            # 自启要先起一次 PowerShell 写快捷方式，慢机器上可能十几秒才回来。
            # 成功与否必须说出来：静默失败会让用户以为设好了，下次开机却发现没有。
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                created = set_startup(True)
            finally:
                QApplication.restoreOverrideCursor()
            if not created:
                QMessageBox.information(
                    window, "开机自启没有设置成功",
                    "创建自启快捷方式失败了（这台机器启动 PowerShell 较慢，或安全软件拦住了）。\n\n"
                    "可以稍后在“设置 → 常规”里再点一次「随 Windows 启动」；"
                    "仍然不行的话，手动把 EveTech.exe 的快捷方式放进"
                    "「开始菜单 → 启动」文件夹即可，效果一样。")
        window.save_settings({"startup_prompted": "1"})
        window.settings_values["startup_prompted"] = "1"

    def silent_update_check():
        if window.settings_values.get("update_check", "1") == "0":
            return
        last = window.settings_values.get("update_last_check", "")
        try:
            checked = datetime.fromisoformat(last)
        except ValueError:
            checked = None
        if checked is not None and (datetime.now() - checked).total_seconds() < 86400:
            return

        def remember():
            stamp = datetime.now().isoformat(timespec="seconds")
            window.settings_values["update_last_check"] = stamp
            window.save_settings({"update_last_check": stamp})

        def ready(manifest):
            remember()
            if not isinstance(manifest, dict) or not manifest.get("available"):
                return
            text = "EveTech %s 已发布（当前 %s），可在“设置 → 软件更新”安装。" % (manifest["version"], VERSION)
            if window.tray is not None:
                window.tray.showMessage("EveTech 有更新", text, QSystemTrayIcon.MessageIcon.Information, 8000)
            window.statusBar().showMessage(text, 20000)

        run_job(fetch_manifest, ready, lambda _name: remember())

    def report_update_problem():
        """上次更新失败过就说清楚，别让用户对着「装了但打不开」发呆。"""
        message = last_update_failure()
        if not message:
            return
        window.statusBar().showMessage(message, 30000)
        if window.tray is not None:
            window.tray.showMessage("EveTech 上次更新未完成", message,
                                    QSystemTrayIcon.MessageIcon.Warning, 15000)

    if not background:
        QTimer.singleShot(500, configure_startup)
    QTimer.singleShot(4000, silent_update_check)
    QTimer.singleShot(6000, report_update_problem)
    return app.exec()
