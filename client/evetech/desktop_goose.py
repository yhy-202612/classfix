"""Manage the local EveTech desktop pet and touch keyboard helper."""

import json
import sys
import ctypes
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtNetwork import QLocalServer

from .goose_install import GooseInstallError, original_goose_available, prepare_classroom_goose
from .update import install_root


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-flash"


def resolve_goose_folder(folder):
    if not folder:
        return ""
    root = Path(folder)
    for candidate in (root, root / "DesktopGoose v0.31",
                      root / "Desktop Goose v0.31" / "DesktopGoose v0.31"):
        if original_goose_available(candidate):
            return str(candidate)
    return ""


def helper_path():
    if getattr(sys, "frozen", False):
        return install_root() / "tools" / "EveTechGoose.exe"
    return install_root() / "desktop_goose" / "dist" / "EveTechGoose.exe"


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
    circle_requested = Signal()

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
        self._preview_window = None
        self._folder = None
        self._runtime = None
        self._last_config = None
        self._output = bytearray()
        self._reported_error = False
        self._pipe = QLocalServer(self)
        self._pipe.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._pipe.newConnection.connect(self._connect_helper)
        self._connection = None
        self._preview_wait = QTimer(self)
        self._preview_wait.setSingleShot(True)
        self._preview_wait.setInterval(8000)
        self._preview_wait.timeout.connect(self._preview_timeout)
        self._startup_wait = QTimer(self)
        self._startup_wait.setSingleShot(True)
        self._startup_wait.setInterval(60000)
        self._startup_wait.timeout.connect(self._preview_timeout)

        self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        for name in ("EVETECH_AI_KEY", "CLASSFIX_AI_KEY", "DEEPSEEK_API_KEY"):
            environment.remove(name)
        environment.remove("EVETECH_GOOSE_PIPE")
        self._environment = environment
        self.process.started.connect(self._send_configuration)
        self.process.readyReadStandardError.connect(self.process.readAllStandardError)
        self.process.errorOccurred.connect(self._handle_error)
        self.process.finished.connect(self._finished)

    def _enabled(self, key):
        return self.settings.get(key) == "1"

    def goose_enabled(self):
        return self._enabled("desktop_goose_enabled")

    def active(self):
        return self.goose_enabled() or self._enabled("touch_keyboard_enabled")

    def _configuration(self):
        goose = self.goose_enabled()
        return {
            "Action": "configure",
            "Folder": self.settings.get("desktop_goose_folder", ""),
            "Goose": goose,
            "Keyboard": self._enabled("touch_keyboard_enabled"),
            "Weekends": self._enabled("desktop_goose_weekends"),
            "DebugAlwaysShow": self._enabled("desktop_goose_debug_always_show"),
            "Token": deepseek_token(self.ai_config()) if goose else "",
            "Paused": self.paused,
            "Slideshow": self.slideshow,
        }

    def refresh(self):
        if self._closed or self._stopping:
            return
        if not self.goose_enabled():
            self._ask_pending = False
            self._clear_preview()
        if not self.active():
            self._stop()
            self.status.emit("Desktop pet and touch keyboard are off.")
            return
        if not self.executable.is_file():
            self._clear_preview()
            self.status.emit("The desktop pet helper is missing. Extract the complete EveTech Windows package.")
            return
        if self.goose_enabled() and not self.executable.with_name("EveTechGooseMod.dll").is_file():
            self._clear_preview()
            self.status.emit("The classroom Goose Mod is missing. Extract the complete EveTech package.")
            return
        folder = self.settings.get("desktop_goose_folder", "") if self.goose_enabled() else ""
        if self.process.state() != QProcess.ProcessState.NotRunning and folder != self._folder:
            pending = self._ask_pending, self._preview_window
            self._stop()
            self._ask_pending, self._preview_window = pending
            if self._preview_window is not None:
                self._preview_wait.start(60000)
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._close_pipe()
            if not self._pipe.listen("EveTechGoose-" + uuid4().hex):
                self._clear_preview()
                self.status.emit("EveTech could not open the desktop pet's local connection.")
                return
            self._folder = folder
            self._output.clear()
            self._reported_error = False
            environment = QProcessEnvironment(self._environment)
            if self.goose_enabled():
                try:
                    self._runtime = prepare_classroom_goose(folder, self.executable.with_name("EveTechGooseMod.dll"))
                except GooseInstallError as error:
                    self._close_pipe()
                    self._clear_preview()
                    self.status.emit(str(error))
                    return
                root = Path(self._runtime.name).resolve()
                environment.insert("EVETECH_GOOSE_PIPE", self._pipe.serverName())
                self.process.setProgram(str(root / "GooseDesktop.exe"))
                self.process.setWorkingDirectory(str(root))
                self.process.setArguments([])
                self.status.emit("Approve the original Desktop Goose Mod prompt to start the classroom goose.")
                self._startup_wait.start()
            else:
                self.process.setProgram(str(self.executable))
                self.process.setWorkingDirectory(str(self.executable.parent))
                self.process.setArguments(["--evetech", self._pipe.serverName()])
                self.status.emit("Starting the touch keyboard helper…")
            self.process.setProcessEnvironment(environment)
            self.process.start()
        elif self.process.state() == QProcess.ProcessState.Running:
            self._send_configuration()

    def ask(self):
        if not self.goose_enabled() or self._closed:
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

    def preview(self, window=0):
        if not self.goose_enabled() or self._closed:
            self.status.emit("Enable Desktop Goose, choose the extracted v0.31 folder, and save Settings first.")
            return
        self._preview_window = int(window)
        self._preview_wait.start(60000)
        if self._ready:
            self._send_preview()
        else:
            self.status.emit("Waiting for the desktop pet helper…")
            self.refresh()

    def _send_preview(self):
        self.status.emit("Requesting Desktop Goose preview…")
        self._preview_wait.start(8000)
        self._send({"Action": "preview", "Window": self._preview_window})

    def _clear_preview(self):
        self._preview_window = None
        self._preview_wait.stop()

    def _preview_timeout(self):
        message = ("The desktop pet helper did not answer the preview request. Try Preview Goose again."
                   if self._ready else
                   "Desktop Goose did not load the classroom Mod. Try again and approve its Mod prompt.")
        self._stop()
        self.status.emit(message)

    def set_paused(self, value):
        self.paused = bool(value)
        self._send_configuration()

    def set_slideshow(self, value):
        self.slideshow = bool(value)
        self._send_configuration()

    def _connect_helper(self):
        connection = self._pipe.nextPendingConnection()
        if connection is None:
            return
        if self._connection is not None:
            connection.abort()
            connection.deleteLater()
            return
        self._connection = connection
        self._pipe.close()
        connection.readyRead.connect(self._read_output)
        self._send_configuration()

    def _close_pipe(self):
        self._pipe.close()
        if self._connection is not None:
            connection, self._connection = self._connection, None
            connection.abort()
            connection.deleteLater()

    def _send(self, value):
        if self._connection is not None:
            self._connection.write(json.dumps(value, ensure_ascii=True).encode("ascii") + b"\n")
            self._connection.flush()

    def _send_configuration(self):
        if (self._closed or self._stopping or self._connection is None
                or self.process.state() != QProcess.ProcessState.Running):
            return
        config = self._configuration()
        if config != self._last_config:
            self._send(config)
            self._last_config = config

    def _read_output(self):
        if self._connection is None:
            return
        self._output.extend(bytes(self._connection.readAll()))
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
                self._startup_wait.stop()
                if self._preview_window is None:
                    self.status.emit(result.get("message") or "Desktop pet helper is ready.")
                if self._ask_pending:
                    self._ask_pending = False
                    self._open_chat()
                if self._preview_window is not None:
                    self._send_preview()
            elif result.get("kind") == "preview" and self._preview_window is not None:
                self._clear_preview()
                self.status.emit(result.get("message") or "Desktop Goose answered the preview request.")
            elif result.get("kind") == "error":
                self._reported_error = True
                self._clear_preview()
                self.status.emit(result.get("message") or "The desktop pet helper could not start.")
            elif (result.get("kind") == "visibility" and self._preview_window is None
                  and self.goose_enabled() and isinstance(result.get("message"), str)):
                self.status.emit(result["message"])
            elif result.get("kind") == "circle_screen" and self.goose_enabled():
                self.circle_requested.emit()
        if len(self._output) > 16384:
            self._output.clear()

    def _handle_error(self, error):
        if not self._stopping and error == QProcess.ProcessError.FailedToStart:
            self._close_pipe()
            self._remove_runtime()
            self._ready = False
            self._reported_error = True
            self._clear_preview()
            self.status.emit("The desktop pet helper could not start. Check the Windows .NET Framework installation.")

    def _finished(self, exit_code, _exit_status):
        self._read_output()
        self._close_pipe()
        self._remove_runtime()
        was_ready = self._ready
        self._ready = False
        self._last_config = None
        self._clear_preview()
        if not self._stopping and not self._reported_error:
            when = "during startup" if not was_ready else "while running"
            self.status.emit(f"The desktop pet helper exited {when} (0x{exit_code & 0xFFFFFFFF:08X}). "
                             "Try Preview Goose again.")

    def _remove_runtime(self):
        self._startup_wait.stop()
        if self._runtime is not None:
            runtime, self._runtime = self._runtime, None
            try:
                runtime.cleanup()
            except OSError:
                pass

    def _stop(self):
        self._ask_pending = False
        self._clear_preview()
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self._stopping = True
        try:
            if self.process.state() == QProcess.ProcessState.Running:
                self._send({"Action": "stop"})
                if self._connection is not None and self._connection.bytesToWrite():
                    self._connection.waitForBytesWritten(200)
            if not self.process.waitForFinished(900) and self.process.state() != QProcess.ProcessState.NotRunning:
                self.process.kill()
                self.process.waitForFinished(500)
        finally:
            self._close_pipe()
            self._ready = False
            self._last_config = None
            self._stopping = False

    def shutdown(self):
        self._closed = True
        self._stop()
