"""Manage the local ClassFix desktop pet and touch keyboard helper."""

import hashlib
import json
import sys
import ctypes
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .update import install_root


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-flash"
_STOCK_FILES = {
    "GooseDesktop.exe": "943fd1ea44266c5d7fa02f2b292db095a4e6ba8027a1f6c73fd60d1165e63aff",
    "GooseModdingAPI.dll": "efa31df7ab1394092395365805f913dd023cdcd21796603f133641524fb9ad79",
}


def original_goose_available(folder):
    if not folder:
        return False
    root = Path(folder)
    try:
        for name, expected in _STOCK_FILES.items():
            with (root / name).open("rb") as stream:
                content = stream.read(256 * 1024 + 1)
            if hashlib.sha256(content).hexdigest() != expected:
                return False
        return (root / "Assets" / "Sound" / "NotEmbedded" / "Honk1.mp3").is_file()
    except (OSError, ValueError):
        return False


def helper_path():
    if getattr(sys, "frozen", False):
        return install_root() / "tools" / "ClassroomGoose.exe"
    return install_root() / "desktop_goose" / "dist" / "ClassroomGoose.exe"


def deepseek_token(config):
    try:
        url = urlsplit(config.url)
        if (url.scheme == "https" and url.netloc.lower() in ("api.deepseek.com", "api.deepseek.com:443")
                and url.path.rstrip("/") in ("/chat/completions", "/v1/chat/completions")
                and not url.query and not url.fragment):
            return config.token
    except ValueError:
        pass
    return ""


class DesktopGoose(QObject):
    status = Signal(str)

    def __init__(self, settings, ai_config, parent=None, executable=None):
        super().__init__(parent)
        self.settings = settings
        self.ai_config = ai_config
        self.executable = Path(executable) if executable is not None else helper_path()
        self.paused = False
        self.slideshow = False
        self._ready = False
        self._stopping = False
        self._closed = False
        self._ask_pending = False
        self._folder = None
        self._last_config = None
        self._output = bytearray()

        self.process = QProcess(self)
        self.process.setProgram(str(self.executable))
        self.process.setArguments(["--classfix"])
        environment = QProcessEnvironment.systemEnvironment()
        for name in ("CLASSFIX_AI_KEY", "DEEPSEEK_API_KEY"):
            environment.remove(name)
        self.process.setProcessEnvironment(environment)
        self.process.started.connect(self._send_configuration)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self.process.readAllStandardError)
        self.process.errorOccurred.connect(self._handle_error)
        self.process.finished.connect(self._finished)

    def _enabled(self, key):
        return self.settings.get(key) == "1"

    def active(self):
        return self._enabled("desktop_goose_enabled") or self._enabled("touch_keyboard_enabled")

    def _configuration(self):
        return {
            "Action": "configure",
            "Folder": self.settings.get("desktop_goose_folder", ""),
            "Goose": self._enabled("desktop_goose_enabled"),
            "Keyboard": self._enabled("touch_keyboard_enabled"),
            "Weekends": self._enabled("desktop_goose_weekends"),
            "Token": deepseek_token(self.ai_config()),
            "Paused": self.paused,
            "Slideshow": self.slideshow,
        }

    def refresh(self):
        if self._closed or self._stopping:
            return
        if not self.active():
            self._stop()
            self.status.emit("桌鹅和触摸键盘助手已关闭。")
            return
        if not self.executable.is_file():
            self.status.emit("当前安装目录缺少桌鹅助手，请完整解压 ClassFix Windows 包。")
            return
        folder = self.settings.get("desktop_goose_folder", "")
        if self.process.state() != QProcess.ProcessState.NotRunning and folder != self._folder:
            pending = self._ask_pending
            self._stop()
            self._ask_pending = pending
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._folder = folder
            self._output.clear()
            self.status.emit("正在启动桌鹅助手…")
            self.process.start()
        elif self.process.state() == QProcess.ProcessState.Running:
            self._send_configuration()

    def ask(self):
        if not self.active() or self._closed:
            return False
        if self._ready:
            self._open_chat()
        else:
            self._ask_pending = True
            self.refresh()
        return True

    def _open_chat(self):
        if sys.platform == "win32":
            ctypes.windll.user32.AllowSetForegroundWindow(int(self.process.processId()))
        self._send({"Action": "ask"})

    def set_paused(self, value):
        self.paused = bool(value)
        self._send_configuration()

    def set_slideshow(self, value):
        self.slideshow = bool(value)
        self._send_configuration()

    def _send(self, value):
        self.process.write(json.dumps(value, ensure_ascii=True).encode("ascii") + b"\n")

    def _send_configuration(self):
        if self._closed or self._stopping or self.process.state() != QProcess.ProcessState.Running:
            return
        config = self._configuration()
        if config != self._last_config:
            self._send(config)
            self._last_config = config

    def _read_output(self):
        self._output.extend(bytes(self.process.readAllStandardOutput()))
        while b"\n" in self._output:
            line, rest = self._output.split(b"\n", 1)
            self._output[:] = rest
            try:
                result = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(result, dict) or self._stopping:
                continue
            if result.get("kind") == "ready":
                self._ready = True
                self.status.emit(result.get("message") or "桌鹅助手已就绪。")
                if self._ask_pending:
                    self._ask_pending = False
                    self._open_chat()
            elif result.get("kind") == "error":
                self.status.emit(result.get("message") or "桌鹅助手启动失败。")
        if len(self._output) > 16384:
            self._output.clear()

    def _handle_error(self, error):
        if not self._stopping and error == QProcess.ProcessError.FailedToStart:
            self._ready = False
            self.status.emit("桌鹅助手未能启动。请检查 Windows 的 .NET Framework。")

    def _finished(self, *_result):
        was_ready = self._ready
        self._ready = False
        self._last_config = None
        if was_ready and not self._stopping:
            self.status.emit("桌鹅助手已退出，可从托盘重新打开问鹅。")

    def _stop(self):
        self._ask_pending = False
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self._stopping = True
        try:
            if self.process.state() == QProcess.ProcessState.Running:
                self._send({"Action": "stop"})
                self.process.closeWriteChannel()
            if not self.process.waitForFinished(900) and self.process.state() != QProcess.ProcessState.NotRunning:
                self.process.kill()
                self.process.waitForFinished(500)
        finally:
            self._ready = False
            self._last_config = None
            self._stopping = False

    def shutdown(self):
        self._closed = True
        self._stop()
