import ctypes
import sys
import unittest
from unittest.mock import patch

from classfix.powerpoint_errors import read_powerpoint_media_errors


class PowerPointMediaErrorTest(unittest.TestCase):
    def test_keeps_literal_localized_media_error_text(self):
        message = "PowerPoint 无法播放媒体。\n可能缺少所需的编解码器。"
        english = "Cannot play media."
        with patch("classfix.powerpoint_errors._native_dialogs",
                   return_value=[("Microsoft PowerPoint", message, "确定"),
                                 ("Microsoft PowerPoint", english, "OK"), ("", message)]) as read:
            self.assertEqual(read_powerpoint_media_errors(110), (message, english))
        read.assert_called_once_with(110)

    def test_never_uses_unrelated_open_errors_or_merely_media_labels(self):
        with patch("classfix.powerpoint_errors._native_dialogs",
                   return_value=[("插入视频", "无法打开此演示文稿。", "视频文件", "取消"),
                                 ("Microsoft PowerPoint", "正在播放媒体。"),
                                 ("Microsoft PowerPoint", "You are in Protected View.")]):
            self.assertEqual(read_powerpoint_media_errors(), ())

    def test_inaccessible_native_windows_have_no_evidence(self):
        with patch("classfix.powerpoint_errors._native_dialogs", side_effect=OSError(5, "Access denied")):
            self.assertEqual(read_powerpoint_media_errors(19), ())

    @unittest.skipUnless(sys.platform == "win32", "Win32 callbacks are only available on Windows")
    def test_native_scan_only_reads_visible_labels_in_the_expected_powerpoint_process(self):
        labels_read = []
        # HWND 10 is the slide show, 20 its process's modal dialog, 30 a
        # second PowerPoint process and 40 another application altogether.
        windows = {
            10: (1, "screenClass", True),
            20: (1, "#32770", True),
            21: (1, "Static", True),
            22: (1, "Edit", True),
            23: (1, "Static", False),
            24: (3, "Static", True),
            30: (2, "#32770", True),
            40: (3, "#32770", True),
        }
        message = "无法播放视频。"

        class FakeUser32:
            def EnumWindows(self, callback, data):
                for handle in (20, 30, 40):
                    if not callback(handle, data):
                        break

            def EnumChildWindows(self, handle, callback, data):
                if handle == 20:
                    for child in (21, 22, 23, 24):
                        if not callback(child, data):
                            break

            def IsWindowVisible(self, handle):
                return windows[handle][2]

            def GetClassNameW(self, handle, text, _size):
                text.value = windows[handle][1]

            def GetWindowTextW(self, handle, text, _size):
                text.value = {20: "Microsoft PowerPoint", 30: "Cannot play audio.",
                              40: "Cannot play video."}[handle]

            def GetWindowThreadProcessId(self, handle, pid):
                pid._obj.value = windows[handle][0]

            def SendMessageTimeoutW(self, handle, command, size, address, _flags, timeout, copied):
                labels_read.append((handle, command, timeout))
                (ctypes.c_wchar * size).from_address(address).value = message
                copied._obj.value = len(message)
                return 1

        with patch("classfix.powerpoint_errors._user32", return_value=FakeUser32()), \
                patch("classfix.powerpoint_errors.monotonic", return_value=1.0), \
                patch("classfix.powerpoint_errors._process_basename", side_effect={
                    1: "POWERPNT.EXE", 2: "POWERPNT.EXE", 3: "EXPLORER.EXE",
                }.__getitem__):
            self.assertEqual(read_powerpoint_media_errors(10), (message,))
        self.assertEqual(len(labels_read), 1)
        handle, command, timeout = labels_read[0]
        self.assertEqual((handle, command), (21, 0x000D))
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 15)
