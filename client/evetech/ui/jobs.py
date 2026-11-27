from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication


class TaskSignals(QObject):
    outcome = Signal(bool, object)

    def __init__(self, on_result, on_error):
        super().__init__(QApplication.instance())
        self.on_result = on_result
        self.on_error = on_error
        self.outcome.connect(self.deliver, Qt.ConnectionType.QueuedConnection)

    @Slot(bool, object)
    def deliver(self, success, value):
        try:
            callback = self.on_result if success else self.on_error
            callback(value)
        finally:
            self.deleteLater()


class Task(QRunnable):
    def __init__(self, operation, on_result, on_error):
        super().__init__()
        self.operation = operation
        self.signals = TaskSignals(on_result, on_error)

    @Slot()
    def run(self):
        try:
            value = self.operation()
        except Exception as error:
            self.signals.outcome.emit(False, type(error).__name__)
        else:
            self.signals.outcome.emit(True, value)


def run_job(operation, on_result, on_error):
    QThreadPool.globalInstance().start(Task(operation, on_result, on_error))
