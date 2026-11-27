import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from evetech import memory_cleanup as cleanup
from evetech.memory_cleanup import CleanupStatus, MemoryReading


class MemoryCleanupTest(unittest.TestCase):
    def test_native_calls_follow_community_edition_order_and_report_partial_success(self):
        calls = []
        statuses = iter((0, 0, 0xC0000003, 0, 0, 0xC000000D, 0))

        def set_information(info_class, buffer, size):
            payload = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int32)).contents.value if info_class == 80 else None
            calls.append((info_class, payload, size))
            return next(statuses)

        ntdll = SimpleNamespace(RtlAdjustPrivilege=Mock(), NtSetSystemInformation=Mock(side_effect=set_information))
        libs = {"shell32": SimpleNamespace(IsUserAnAdmin=lambda: True), "ntdll": ntdll}
        with patch.object(cleanup.sys, "platform", "win32"), \
             patch.object(cleanup.ctypes, "WinDLL", side_effect=libs.__getitem__, create=True), \
             patch.object(cleanup, "_classroom_block", return_value=cleanup._CleanupBlock.NONE):
            steps, message = cleanup._decode_result(cleanup.run_elevated_cleanup())
        self.assertEqual(calls, [
            (80, 2, 4), (81, None, ctypes.sizeof(cleanup._FileCacheInformation)),
            (80, 3, 4), (80, 4, 4), (80, 5, 4), (155, None, 0),
            (130, None, ctypes.sizeof(cleanup._CombineInformation)),
        ])
        self.assertEqual([call.args[0] for call in ntdll.RtlAdjustPrivilege.call_args_list], [13, 5])
        self.assertEqual(steps, (
            CleanupStatus.SUCCEEDED, CleanupStatus.SUCCEEDED, CleanupStatus.SKIPPED,
            CleanupStatus.SUCCEEDED, CleanupStatus.SUCCEEDED, CleanupStatus.FAILED,
            CleanupStatus.SUCCEEDED,
        ))
        self.assertEqual(message, "")

    def test_powerpoint_opening_during_cleanup_skips_remaining_steps(self):
        ntdll = SimpleNamespace(RtlAdjustPrivilege=Mock(), NtSetSystemInformation=Mock(return_value=0))
        libs = {"shell32": SimpleNamespace(IsUserAnAdmin=lambda: True), "ntdll": ntdll}
        blocks = [cleanup._CleanupBlock.NONE, cleanup._CleanupBlock.NONE, cleanup._CleanupBlock.POWERPOINT]
        with patch.object(cleanup.sys, "platform", "win32"), \
             patch.object(cleanup.ctypes, "WinDLL", side_effect=libs.__getitem__, create=True), \
             patch.object(cleanup, "_classroom_block", side_effect=blocks):
            steps, message = cleanup._decode_result(cleanup.run_elevated_cleanup())
        ntdll.NtSetSystemInformation.assert_called_once()
        self.assertEqual(steps[0], CleanupStatus.SUCCEEDED)
        self.assertEqual(steps[1:], (CleanupStatus.SKIPPED,) * 6)
        self.assertIn("PowerPoint", message)

    def test_running_powerpoint_skips_elevation(self):
        reading = MemoryReading(725, 90)
        with patch.object(cleanup.sys, "platform", "win32"), \
             patch.object(cleanup, "_classroom_block", return_value=cleanup._CleanupBlock.POWERPOINT), \
             patch.object(cleanup, "_memory_reading", return_value=reading), \
             patch.object(cleanup, "_launch_elevated_cleanup") as launch:
            result = cleanup.optimize_system_memory()
        launch.assert_not_called()
        self.assertIs(result.before, reading)
        self.assertIsNone(result.after)
        self.assertEqual(result.steps, (CleanupStatus.SKIPPED,) * len(cleanup.CLEANUP_STEPS))

    def test_measurements_stay_signed_and_failed_helper_exit_does_not_report_success(self):
        before = MemoryReading(725, 90)
        after = MemoryReading(710, 91)
        status = cleanup._encode_result((CleanupStatus.SUCCEEDED,) * 7, cleanup._CleanupBlock.NONE)
        with patch.object(cleanup.sys, "platform", "win32"), \
             patch.object(cleanup, "_classroom_block", return_value=cleanup._CleanupBlock.NONE), \
             patch.object(cleanup, "_memory_reading", side_effect=[before, after]), \
             patch.object(cleanup, "_launch_elevated_cleanup", return_value=status):
            result = cleanup.optimize_system_memory()
        self.assertEqual((result.before, result.after), (before, after))
        with self.assertRaises(OSError):
            cleanup._decode_result(0)
