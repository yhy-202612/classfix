import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from classfix.performance import MIB, MemoryPressure


class MemoryPressureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Match UI tests when the suite already has a running Qt application.
        from PySide6.QtWidgets import QApplication
        cls.app = QCoreApplication.instance() or QApplication([])

    def test_single_high_sample_is_ignored_and_both_criteria_are_checked(self):
        watcher = MemoryPressure(lambda: True)
        seen = []
        watcher.high.connect(lambda *values: seen.append(values))
        usual = SimpleNamespace(percent=78, available=1024 * MIB)
        high_percent = SimpleNamespace(percent=86, available=1000 * MIB)
        low_available = SimpleNamespace(percent=82, available=690 * MIB)
        with patch("classfix.performance.psutil.virtual_memory",
                   side_effect=[high_percent, usual, low_available, high_percent]):
            for _ in range(3):
                watcher.poll()
            self.assertFalse(seen)
            watcher.poll()
        self.assertEqual(seen, [(86, 1000)])

    def test_disabled_setting_avoids_sampling(self):
        watcher = MemoryPressure(lambda: False)
        with patch("classfix.performance.psutil.virtual_memory") as sample:
            watcher.poll()
        sample.assert_not_called()
