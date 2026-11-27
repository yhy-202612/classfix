"""Single-instance handoff so opening the EXE reveals the background instance."""

from pathlib import Path

from PySide6.QtCore import QObject, QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    def __init__(self, name, lock_path, on_message, parent=None):
        super().__init__(parent)
        self.name = name
        path = Path(lock_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = path
        self.lock = self._new_lock()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._receive)
        self.on_message = on_message

    def listen(self):
        """返回 'primary'（本进程接手）、'handoff'（已叫醒既有实例）或 'blocked'。

        'blocked' 的典型场景：机器上还挂着旧版本（1.1.x）的托盘实例——它占着
        锁文件，却没有同名本地服务可以通信。以前这里静默返回、进程直接退出，
        用户看到的现象就是「新版装上去了但双击没反应，得手动点更新脚本才起来」。
        """
        if self.lock.tryLock(0):
            return "primary" if self._start_server() else "blocked"
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(1500):
            probe.write(b"show")
            probe.waitForBytesWritten(1000)
            probe.disconnectFromServer()
            return "handoff"
        # A crash or forced shutdown can leave the lock file behind even
        # though no local server exists. Recover it instead of silently
        # exiting forever on every later launch.
        recovered = self.lock.removeStaleLockFile()
        if not recovered and self._lock_owner_is_gone():
            # A failed QLockFile attempt can keep an open Windows handle on
            # the stale file. Destroy it before deleting and recreating it.
            old_lock = self.lock
            self.lock = None
            del old_lock
            try:
                self.lock_path.unlink(missing_ok=True)
                recovered = True
            except OSError:
                recovered = False
            self.lock = self._new_lock()
        if recovered and self.lock.tryLock(0):
            return "primary" if self._start_server() else "blocked"
        return "blocked"

    def lock_owner(self):
        """占着锁的进程号与版本线索，用于给用户一句可操作的提示。"""
        try:
            info = self.lock.getLockInfo()
            return int(info[0]) if info else None
        except (OSError, TypeError, ValueError):
            return None

    def _new_lock(self):
        lock = QLockFile(str(self.lock_path))
        lock.setStaleLockTime(5000)
        return lock

    def _lock_owner_is_gone(self):
        try:
            import psutil
            info = self.lock.getLockInfo()
            return bool(info) and not psutil.pid_exists(int(info[0]))
        except (ImportError, OSError, TypeError, ValueError):
            return False

    def _start_server(self):
        QLocalServer.removeServer(self.name)
        if self.server.listen(self.name):
            return True
        self.lock.unlock()
        return False

    def _receive(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket.waitForReadyRead(300):
                self.on_message(bytes(socket.readAll()).decode("utf-8", "replace"))
            socket.disconnectFromServer()
