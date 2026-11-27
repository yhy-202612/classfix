"""Low-overhead memory pressure checks."""

import os
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

MIB = 1024 * 1024


class MemoryPressure(QObject):
    sampled = Signal(int, int)
    high = Signal(int, int)

    def __init__(self, enabled, parent=None):
        super().__init__(parent)
        self.enabled = enabled
        self._high_samples = 0
        self.timer = QTimer(self, interval=5000)
        self.timer.timeout.connect(self.poll)

    def poll(self):
        if not self.enabled():
            self._high_samples = 0
            return
        try:
            memory = psutil.virtual_memory()
        except (OSError, psutil.Error):
            self._high_samples = 0
            return
        reading = (round(memory.percent), memory.available // MIB)
        self.sampled.emit(*reading)
        if memory.percent < 85 and memory.available >= 800 * MIB:
            self._high_samples = 0
            return
        self._high_samples = min(self._high_samples + 1, 2)
        if self._high_samples == 2:
            self.high.emit(*reading)


def open_task_manager():
    system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    os.startfile(str(system / "System32" / "Taskmgr.exe"))
