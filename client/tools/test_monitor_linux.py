"""在 Linux / macOS 上跑 monitor 判定逻辑的回归测试（不需要 Windows Core Audio）。

用法：
    pip install PySide6-Essentials
    QT_QPA_PLATFORM=offscreen python3 tools/test_monitor_linux.py

说明：只替换 Windows 专有的 `read_system_volume`（默认音量正常）与开机自启用例，
     monitor / overlay 的判定逻辑仍然走真实源码，便于在非 Windows 机器上做回归。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "tests"))

try:
    from PySide6.QtWidgets import QApplication  # noqa: F401
except ImportError:  # pragma: no cover - 仅在缺依赖时触发
    sys.exit("需要先安装 PySide6-Essentials：pip install PySide6-Essentials")

import classfix.monitor as monitor  # noqa: E402

monitor.read_system_volume = lambda: (60, False)

import test_monitor  # noqa: E402

WINDOWS_ONLY = {"test_per_user_startup_shortcut_can_be_added_and_removed"}


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    skipped = []
    for name in loader.getTestCaseNames(test_monitor.MonitorTest):
        if name in WINDOWS_ONLY:
            skipped.append(name)
            continue
        suite.addTest(test_monitor.MonitorTest(name))
    if skipped:
        print("跳过 Windows 专有用例：", ", ".join(skipped))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
