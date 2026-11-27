"""On-demand Windows memory maintenance using a short-lived elevated process."""

import ctypes
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import psutil

ELEVATED_CLEANUP_ARG = "--evetech-elevated-memory-cleanup"

# Operation order follows the former PCL Community Edition MemSwapService.
CLEANUP_STEPS = (
    "全局工作集", "文件缓存", "已修改页", "待机页",
    "低优先级待机页", "注册表整理", "物理页合并",
)
SYSTEM_MEMORY_LIST_INFORMATION = 80
SYSTEM_FILE_CACHE_INFORMATION_EX = 81
SYSTEM_COMBINE_PHYSICAL_MEMORY_INFORMATION = 130
SYSTEM_REGISTRY_RECONCILIATION_INFORMATION = 155
SE_INCREASE_QUOTA_PRIVILEGE = 5
SE_PROFILE_SINGLE_PROCESS_PRIVILEGE = 13
TH32CS_SNAPPROCESS = 0x00000002
ERROR_NO_MORE_FILES = 18
STATUS_SKIPPED = {
    0xC0000002,  # STATUS_NOT_IMPLEMENTED
    0xC0000003,  # STATUS_INVALID_INFO_CLASS
    0xC0000022,  # STATUS_ACCESS_DENIED
    0xC0000061,  # STATUS_PRIVILEGE_NOT_HELD
    0xC00000BB,  # STATUS_NOT_SUPPORTED
}


class CleanupStatus(IntEnum):
    SUCCEEDED = 0
    SKIPPED = 1
    FAILED = 2


class _CleanupBlock(IntEnum):
    NONE = 0
    POWERPOINT = 1
    PROCESS_CHECK_FAILED = 2
    ELEVATION_REQUIRED = 3


_BLOCK_MESSAGES = {
    _CleanupBlock.POWERPOINT: "PowerPoint 正在运行，已跳过剩余操作。",
    _CleanupBlock.PROCESS_CHECK_FAILED: "无法确认 PowerPoint 状态，已跳过剩余操作。",
    _CleanupBlock.ELEVATION_REQUIRED: "未获得 Windows 管理员权限。",
}
_SKIPPED = (CleanupStatus.SKIPPED,) * len(CLEANUP_STEPS)
_EXIT_MARKER = 1 << 24
_BLOCK_SHIFT = len(CLEANUP_STEPS) * 2


@dataclass(frozen=True)
class MemoryReading:
    available_mib: int
    percent: int


@dataclass(frozen=True)
class MemoryCleanup:
    before: MemoryReading | None
    after: MemoryReading | None
    steps: tuple[CleanupStatus, ...]
    message: str = ""


class _FileCacheInformation(ctypes.Structure):
    _fields_ = [
        ("current_size", ctypes.c_size_t),
        ("peak_size", ctypes.c_size_t),
        ("page_fault_count", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("current_size_including_transition", ctypes.c_size_t),
        ("peak_size_including_transition", ctypes.c_size_t),
        ("transition_repurpose_count", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class _CombineInformation(ctypes.Structure):
    _fields_ = [
        ("handle", ctypes.c_void_p),
        ("pages_combined", ctypes.c_size_t),
        ("flags", ctypes.c_uint32),
    ]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("pid", wintypes.DWORD),
        ("default_heap_id", ctypes.c_size_t),
        ("module_id", wintypes.DWORD),
        ("thread_count", wintypes.DWORD),
        ("parent_pid", wintypes.DWORD),
        ("base_priority", wintypes.LONG),
        ("flags", wintypes.DWORD),
        ("exe_name", wintypes.WCHAR * wintypes.MAX_PATH),
    ]


def _memory_reading():
    try:
        reading = psutil.virtual_memory()
    except (OSError, psutil.Error):
        return None
    return MemoryReading(reading.available // (1024 * 1024), round(reading.percent))


def _classroom_block():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    snapshot.restype = wintypes.HANDLE
    first, next_process = kernel.Process32FirstW, kernel.Process32NextW
    for read in (first, next_process):
        read.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
        read.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    processes = snapshot(TH32CS_SNAPPROCESS, 0)
    if processes == ctypes.c_void_p(-1).value:
        return _CleanupBlock.PROCESS_CHECK_FAILED

    try:
        entry = _ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        found = first(processes, ctypes.byref(entry))
        while found:
            if entry.exe_name.casefold() == "powerpnt.exe":
                return _CleanupBlock.POWERPOINT
            found = next_process(processes, ctypes.byref(entry))
        if ctypes.get_last_error() == ERROR_NO_MORE_FILES:
            return _CleanupBlock.NONE
        return _CleanupBlock.PROCESS_CHECK_FAILED
    finally:
        close(processes)


def _encode_result(steps, block):
    # The fixed helper reports two bits per operation through its process handle.
    # The marker keeps unexpected process exits from looking like success.
    return _EXIT_MARKER | (block << _BLOCK_SHIFT) | sum(
        step << (2 * index) for index, step in enumerate(steps)
    )


def _decode_result(code):
    allowed_bits = _EXIT_MARKER | ((4 << _BLOCK_SHIFT) - 1)
    if not code & _EXIT_MARKER or code & ~allowed_bits:
        raise OSError("Unexpected memory cleanup process exit")
    block = _CleanupBlock((code >> _BLOCK_SHIFT) & 3)
    steps = tuple(CleanupStatus((code >> (2 * index)) & 3) for index in range(len(CLEANUP_STEPS)))
    return steps, _BLOCK_MESSAGES.get(block, "")


def _cleanup_requests():
    cache = _FileCacheInformation()
    cache.minimum_working_set = cache.maximum_working_set = ctypes.c_size_t(-1).value
    return (
        (SYSTEM_MEMORY_LIST_INFORMATION, ctypes.c_int32(2)),
        (SYSTEM_FILE_CACHE_INFORMATION_EX, cache),
        (SYSTEM_MEMORY_LIST_INFORMATION, ctypes.c_int32(3)),
        (SYSTEM_MEMORY_LIST_INFORMATION, ctypes.c_int32(4)),
        (SYSTEM_MEMORY_LIST_INFORMATION, ctypes.c_int32(5)),
        (SYSTEM_REGISTRY_RECONCILIATION_INFORMATION, None),
        (SYSTEM_COMBINE_PHYSICAL_MEMORY_INFORMATION, _CombineInformation()),
    )


def run_elevated_cleanup():
    """Execute only the fixed maintenance steps in an administrator process."""
    if sys.platform != "win32":
        return _encode_result(_SKIPPED, _CleanupBlock.ELEVATION_REQUIRED)
    if not ctypes.WinDLL("shell32").IsUserAnAdmin():
        return _encode_result(_SKIPPED, _CleanupBlock.ELEVATION_REQUIRED)
    block = _classroom_block()
    if block:
        return _encode_result(_SKIPPED, block)

    ntdll = ctypes.WinDLL("ntdll")
    adjust = ntdll.RtlAdjustPrivilege
    adjust.argtypes = (ctypes.c_uint32, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.POINTER(ctypes.c_ubyte))
    adjust.restype = ctypes.c_uint32
    for privilege in (SE_PROFILE_SINGLE_PROCESS_PRIVILEGE, SE_INCREASE_QUOTA_PRIVILEGE):
        adjust(privilege, 1, 0, ctypes.byref(ctypes.c_ubyte()))

    set_information = ntdll.NtSetSystemInformation
    set_information.argtypes = (ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32)
    set_information.restype = ctypes.c_uint32
    steps = list(_SKIPPED)
    for index, (info_class, payload) in enumerate(_cleanup_requests()):
        block = _classroom_block()
        if block:
            break
        buffer = ctypes.byref(payload) if payload is not None else None
        size = ctypes.sizeof(payload) if payload is not None else 0
        status = set_information(info_class, buffer, size)
        if status < 0x80000000:
            steps[index] = CleanupStatus.SUCCEEDED
        elif status not in STATUS_SKIPPED:
            steps[index] = CleanupStatus.FAILED
    return _encode_result(steps, block)


def _launch_elevated_cleanup():
    import pythoncom
    import pywintypes
    import win32event
    import win32process
    from win32com.shell import shell, shellcon

    args = [ELEVATED_CLEANUP_ARG]
    if not getattr(sys, "frozen", False):
        args.insert(0, str(Path(__file__).resolve().parents[1] / "main.py"))
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    try:
        try:
            process = shell.ShellExecuteEx(
                fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | shellcon.SEE_MASK_FLAG_DDEWAIT,
                lpVerb="runas", lpFile=sys.executable, lpParameters=subprocess.list2cmdline(args),
                lpDirectory=str(Path(sys.executable).parent), nShow=0,
            )["hProcess"]
        except pywintypes.error as error:
            if error.winerror == 1223:  # ERROR_CANCELLED
                return None
            raise
        try:
            win32event.WaitForSingleObject(process, win32event.INFINITE)
            return win32process.GetExitCodeProcess(process)
        finally:
            process.Close()
    finally:
        pythoncom.CoUninitialize()


def optimize_system_memory():
    if sys.platform != "win32":
        raise OSError("This action requires Windows")
    block = _classroom_block()
    before = _memory_reading()
    if block:
        return MemoryCleanup(before, None, _SKIPPED, _BLOCK_MESSAGES[block])
    code = _launch_elevated_cleanup()
    if code is None:
        return MemoryCleanup(before, None, _SKIPPED, "已取消 Windows 管理员授权。")
    after = _memory_reading()
    steps, message = _decode_result(code)
    return MemoryCleanup(before, after, steps, message)
