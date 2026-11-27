"""Exercise the packaged application on an interactive Windows desktop."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import os
import subprocess
import sys
import time

from PIL import ImageChops, ImageGrab
import psutil
from pywinauto import Desktop, mouse


user = ctypes.WinDLL("user32", use_last_error=True)
callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user.EnumWindows.argtypes = (callback, wintypes.LPARAM)
user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user.IsWindowVisible.argtypes = (wintypes.HWND,)
user.SetProcessDPIAware()
report = Path(os.environ["QA_REPORT"])
report.mkdir(parents=True, exist_ok=True)


def wait_for(read, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if value:
            return value
        time.sleep(0.25)
    return None


def current_app(pid):
    return next((w for w in Desktop(backend="uia").windows(process=pid)
                 if w.window_text().startswith("EveTech") and w.is_visible()), None)


def control(window, label):
    return next((w for w in window.descendants() if w.window_text() == label), None)


def desktop_pet_status(window):
    return [w.window_text() for w in window.descendants(control_type="Text") if any(
        text in w.window_text().lower() for text in ("preview", "goose", "pet helper"))]


def pet_bounds(pid):
    children = {
        child.pid for child in psutil.Process(pid).children()
        if child.name().casefold() == "evetechgoose.exe"
    }
    positions = []

    @callback
    def collect(hwnd, lparam):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in children and user.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            if user.GetWindowRect(hwnd, ctypes.byref(rect)):
                if 125 <= rect.right - rect.left <= 250 and 110 <= rect.bottom - rect.top <= 225:
                    positions.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    user.EnumWindows(collect, 0)
    return positions


def visible_goose(pid, before):
    screen = ImageGrab.grab(include_layered_windows=True).convert("RGB")
    for box in pet_bounds(pid):
        crop = screen.crop(box)
        changed = ImageChops.difference(before.crop(box), crop).getbbox()
        if not changed:
            continue
        pixels = list(crop.crop(changed).getdata())
        orange = sum(r > 200 and 75 < g < 200 and b < 90 for r, g, b in pixels)
        white = sum(r > 229 and g > 229 and b > 229 for r, g, b in pixels)
        if orange >= 8 and white >= 45:
            return screen, box, orange, white
    return None


def main():
    exe = Path(sys.argv[1]).resolve()
    child = subprocess.Popen([str(exe)], cwd=exe.parent)
    try:
        window = wait_for(lambda: current_app(child.pid), 30)
        if not window:
            raise RuntimeError("EveTech did not open an interactive window")
        window.set_focus()
        settings = wait_for(lambda: control(window, "\u8bbe\u7f6e"))
        if not settings:
            window.print_control_identifiers()
            raise RuntimeError("Settings navigation was missing from Windows UI Automation")
        settings.click_input()
        preview = None
        for _ in range(20):
            bounds = window.rectangle()
            preview = control(window, "Preview Goose")
            if preview and preview.is_visible() and bounds.top + 50 < preview.rectangle().mid_point().y < bounds.bottom - 15:
                break
            mouse.scroll(coords=(bounds.right - 95, max(bounds.top + 140, bounds.bottom - 195)), wheel_dist=-2)
            time.sleep(0.2)
        else:
            window.print_control_identifiers()
            raise RuntimeError("Could not scroll to the Preview Goose button")
        before = ImageGrab.grab(include_layered_windows=True).convert("RGB")
        before.save(report / "before-preview.png")
        print("Before clicking:", desktop_pet_status(window), flush=True)
        preview.click_input()
        result = wait_for(lambda: visible_goose(child.pid, before), 12)
        status = wait_for(lambda: [text for text in desktop_pet_status(window) if "preview is active" in text],
                          2) or desktop_pet_status(window)
        print("After clicking:", status, "Goose window bounds:", pet_bounds(child.pid), flush=True)
        if not result:
            raise RuntimeError("No rendered goose was visible in the captured Windows desktop pixels")
        screen, box, orange, white = result
        screen.save(report / "preview-windows.png")
        screen.crop(box).save(report / "preview-goose-detail.png")
        print("Visible Goose pixels:", {"orange": orange, "white": white}, flush=True)
        if not any("preview is active" in message for message in status):
            raise RuntimeError("EveTech did not acknowledge the preview")
    except Exception:
        ImageGrab.grab(include_layered_windows=True).save(report / "preview-failure.png")
        raise
    finally:
        process = psutil.Process(child.pid) if child.poll() is None else None
        if process:
            for helper in process.children(recursive=True):
                try:
                    helper.terminate()
                except psutil.NoSuchProcess:
                    pass
            child.terminate()


if __name__ == "__main__":
    main()
