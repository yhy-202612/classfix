"""Read media failure text exposed by native PowerPoint dialogs.

Inline messages and custom controls may not expose their text here. A dialog
is attributed to a PowerPoint process, not to a particular presentation or page.
"""

import ctypes
import re
import sys
from ctypes import wintypes
from functools import lru_cache
from time import monotonic

from .opencheck import POWERPOINT_IMAGE, _process_basename

_DIALOG_CLASSES = {"#32770", "NetUIHWND", "NUIDialog"}
_MEDIA_WORD = re.compile(
    r"视频|視訊|影片|音频|音訊|媒[体體]|影音|编解码|編解碼|解[码碼]器|"
    r"\b(?:audio|video|media|codec|decoder)s?\b", re.IGNORECASE
)
_FAILURE_WORD = re.compile(
    r"无法|無法|不能|未能|找不到|缺少|缺失|丢失|遺失|不支持|不支援|"
    r"不受支持|不受支援|失[败敗]|[损損][坏壞]|錯誤|错误|不兼容|不相容|"
    r"\b(?:cannot|can['’]t|could not|couldn['’]t|unable|failed|failure|error|"
    r"unsupported|missing|corrupt(?:ed)?|not (?:supported|found|available|installed)|"
    r"does not support|incompatible)\b", re.IGNORECASE
)
_WM_GETTEXT = 0x000D
_READ_FLAGS = 0x0001 | 0x0002 | 0x0020  # BLOCK | ABORTIFHUNG | ERRORONEXIT
_TEXT_CAPACITY = 1024
_SCAN_BUDGET_SECONDS = 0.08
_WINDOW_CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM) if sys.platform == "win32" else None


@lru_cache(maxsize=1)
def _user32():
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.EnumWindows.argtypes = (_WINDOW_CALLBACK, wintypes.LPARAM)
    api.EnumChildWindows.argtypes = (wintypes.HWND, _WINDOW_CALLBACK, wintypes.LPARAM)
    api.IsWindowVisible.argtypes = (wintypes.HWND,)
    api.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    api.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    api.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    api.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                        wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                        ctypes.POINTER(ctypes.c_size_t))
    api.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    return api


def _native_dialogs(window_handle):
    if sys.platform != "win32":
        return ()

    api = _user32()
    deadline = monotonic() + _SCAN_BUDGET_SECONDS
    names = {}
    dialogs = []

    def pid_for(handle):
        pid = wintypes.DWORD()
        api.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        return pid.value

    def is_powerpoint(pid):
        if pid not in names:
            names[pid] = _process_basename(pid) if pid else ""
        return names[pid] == POWERPOINT_IMAGE

    def class_for(handle):
        buffer = ctypes.create_unicode_buffer(64)
        api.GetClassNameW(handle, buffer, len(buffer))
        return buffer.value

    expected_pid = None
    if isinstance(window_handle, int) and window_handle:
        expected_pid = pid_for(window_handle)
        if not is_powerpoint(expected_pid):
            return ()

    @_WINDOW_CALLBACK
    def visit_dialog(handle, _unused):
        if monotonic() >= deadline:
            return False
        if not api.IsWindowVisible(handle) or class_for(handle) not in _DIALOG_CLASSES:
            return True
        pid = pid_for(handle)
        if (expected_pid is not None and pid != expected_pid) or not is_powerpoint(pid):
            return True

        title = ctypes.create_unicode_buffer(_TEXT_CAPACITY)
        api.GetWindowTextW(handle, title, len(title))
        texts = [title.value]

        @_WINDOW_CALLBACK
        def visit_control(child, _unused_control):
            remaining_ms = int((deadline - monotonic()) * 1000)
            if remaining_ms <= 0:
                return False
            if (api.IsWindowVisible(child) and pid_for(child) == pid
                    and class_for(child) == "Static"):
                # GetWindowTextW only gets captions across processes. Ask the
                # standard label for its text, with a deadline for hung Office.
                buffer = ctypes.create_unicode_buffer(_TEXT_CAPACITY)
                copied = ctypes.c_size_t()
                if api.SendMessageTimeoutW(child, _WM_GETTEXT, len(buffer),
                                           ctypes.addressof(buffer), _READ_FLAGS,
                                           min(15, remaining_ms), ctypes.byref(copied)) and copied.value:
                    texts.append(buffer.value)
            return True

        api.EnumChildWindows(handle, visit_control, 0)
        dialogs.append(texts)
        return True

    api.EnumWindows(visit_dialog, 0)
    return dialogs


def read_powerpoint_media_errors(window_handle=0):
    """Return literal visible media failure strings, or an empty tuple.

    With a known native HWND, require that the dialog belong to its PowerPoint
    process. Without one, read only dialogs owned by POWERPNT.EXE processes.
    """
    try:
        dialogs = _native_dialogs(window_handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return ()
    messages = dict.fromkeys(text.strip() for dialog in dialogs for text in dialog
                             if _MEDIA_WORD.search(text) and _FAILURE_WORD.search(text))
    return tuple(messages)
