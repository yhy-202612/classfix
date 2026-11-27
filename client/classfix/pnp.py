"""Read the present camera and HID device setup classes with SetupAPI."""

import ctypes as ct
from ctypes import wintypes as w
from dataclasses import dataclass
from uuid import UUID


CLASSES = (
    ("Camera", "ca3e7ab9-b4c3-4ae6-8251-579ef933890f"),
    ("Image", "6bdd1fc6-810f-11d0-bec7-08002be2092f"),
    ("HID", "745a17a0-74d3-11d0-b6fe-00a0c90f57da"),
)
TOUCH = ("touch screen", "touchscreen", "触摸屏", "觸控螢幕", "触控屏")


class DeviceInfo(ct.Structure):
    _fields_ = [("size", w.DWORD), ("class_id", ct.c_byte * 16),
                ("instance", w.DWORD), ("reserved", ct.c_size_t)]


@dataclass
class Peripheral:
    name: str
    problem: int | None
    started: bool


def present_peripherals():
    setup = ct.WinDLL("setupapi", use_last_error=True)
    config = ct.WinDLL("cfgmgr32")
    setup.SetupDiGetClassDevsW.argtypes = (ct.c_void_p, w.LPCWSTR, w.HWND, w.DWORD)
    setup.SetupDiGetClassDevsW.restype = w.HANDLE
    setup.SetupDiEnumDeviceInfo.argtypes = (w.HANDLE, w.DWORD, ct.POINTER(DeviceInfo))
    setup.SetupDiGetDeviceRegistryPropertyW.argtypes = (
        w.HANDLE, ct.POINTER(DeviceInfo), w.DWORD, ct.c_void_p, ct.c_void_p, w.DWORD, ct.c_void_p)
    setup.SetupDiDestroyDeviceInfoList.argtypes = (w.HANDLE,)
    config.CM_Get_DevNode_Status.argtypes = (ct.POINTER(w.ULONG), ct.POINTER(w.ULONG), w.DWORD, w.ULONG)
    results = []
    for category, class_id in CLASSES:
        guid = (ct.c_byte * 16).from_buffer_copy(UUID(class_id).bytes_le)
        handle = setup.SetupDiGetClassDevsW(guid, None, None, 2)  # DIGCF_PRESENT
        if handle == ct.c_void_p(-1).value:
            raise ct.WinError(ct.get_last_error())
        try:
            index = 0
            while True:
                device = DeviceInfo(size=ct.sizeof(DeviceInfo))
                if not setup.SetupDiEnumDeviceInfo(handle, index, ct.byref(device)):
                    error = ct.get_last_error()
                    if error != 259:  # ERROR_NO_MORE_ITEMS
                        raise ct.WinError(error)
                    break
                index += 1
                name = ct.create_unicode_buffer(1024)
                for property_id in (12, 0):  # SPDRP_FRIENDLYNAME, SPDRP_DEVICEDESC
                    if setup.SetupDiGetDeviceRegistryPropertyW(handle, ct.byref(device), property_id,
                                                              None, name, ct.sizeof(name), None):
                        break
                if category == "HID" and not any(match in name.value.casefold() for match in TOUCH):
                    continue
                flags, problem = w.ULONG(), w.ULONG()
                result = config.CM_Get_DevNode_Status(ct.byref(flags), ct.byref(problem), device.instance, 0)
                results.append(Peripheral(name.value or category, None if result else problem.value,
                                          result == 0 and bool(flags.value & 8)))  # DN_STARTED
        finally:
            setup.SetupDiDestroyDeviceInfoList(handle)
    return results
