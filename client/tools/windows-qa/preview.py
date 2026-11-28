"""Exercise the packaged application on an interactive Windows desktop."""
import ctypes
import hashlib
from ctypes import wintypes
from pathlib import Path
import os
import subprocess
import sys
import time

from PIL import ImageChops, ImageGrab
import psutil
import win32api
import win32con
import win32gui
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
        if child.name().casefold() == "goosedesktop.exe"
    }
    positions = []

    @callback
    def collect(hwnd, lparam):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in children and user.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            if user.GetWindowRect(hwnd, ctypes.byref(rect)):
                if rect.right - rect.left > 500 and rect.bottom - rect.top > 400:
                    positions.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    user.EnumWindows(collect, 0)
    return positions


def visible_goose(pid, before):
    screen = ImageGrab.grab(include_layered_windows=True).convert("RGB")
    for box in pet_bounds(pid):
        # The preview starts in the bottom-right quadrant, above the status label.
        width, height = box[2] - box[0], box[3] - box[1]
        box = (box[0] + width // 2, box[1] + height // 3, box[2], box[1] + height * 9 // 10)
        crop = screen.crop(box)
        changed = ImageChops.difference(before.crop(box), crop).getbbox()
        if not changed:
            continue
        pixels = list(crop.crop(changed).getdata())
        beak = [(index % (changed[2] - changed[0]), index // (changed[2] - changed[0]))
                for index, (r, g, b) in enumerate(pixels) if r > 200 and 75 < g < 200 and b < 90]
        orange = len(beak)
        white = sum(r > 229 and g > 229 and b > 229 for r, g, b in pixels)
        if orange >= 8 and white >= 45 and changed[2] - changed[0] < 130 and changed[3] - changed[1] < 130:
            left, top = box[0] + changed[0], box[1] + changed[1]
            x, y = min(beak, key=lambda p: p[1])
            return screen, (left, top, box[0] + changed[2], box[1] + changed[3]), (left + x, top + y + 2)
    return None


def check_nest(window):
    name = "screenClass"
    instance = win32api.GetModuleHandle(None)
    klass = win32gui.WNDCLASS()
    klass.hInstance = instance
    klass.lpszClassName = name
    klass.lpfnWndProc = win32gui.DefWindowProc
    klass.hbrBackground = win32con.COLOR_WINDOW + 1
    atom = win32gui.RegisterClass(klass)
    hwnd = win32gui.CreateWindow(atom, "Test slideshow window", win32con.WS_POPUP, 0, 0, 1024, 768,
                                 0, 0, instance, None)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        if not wait_for(lambda: [text for text in desktop_pet_status(window) if "resting in its" in text], 12):
            raise RuntimeError(f"The native goose did not walk into the nest: {desktop_pet_status(window)!r}")
        ImageGrab.grab(include_layered_windows=True).save(report / "preview-nest.png")
        print("Native goose reached the nest for a simulated slideshow window.", flush=True)
    finally:
        win32gui.DestroyWindow(hwnd)
        win32gui.UnregisterClass(name, instance)


def main():
    exe = Path(sys.argv[1]).resolve()
    child = subprocess.Popen([str(exe)], cwd=exe.parent)
    try:
        window = wait_for(lambda: current_app(child.pid), 30)
        if not window:
            raise RuntimeError("EveTech did not open an interactive window")
        prompt = wait_for(lambda: next(
            (w for w in Desktop(backend="uia").windows() if w.window_text() == "Mod Enabler Warning"), None))
        if not prompt:
            raise RuntimeError("Original Desktop Goose did not ask to enable its Mod")
        control(prompt, "Yes").click_input()
        original = next(p for p in psutil.Process(child.pid).children() if p.name().lower() == "goosedesktop.exe")
        assert hashlib.sha256(Path(original.exe()).read_bytes()).hexdigest() == (
            "943fd1ea44266c5d7fa02f2b292db095a4e6ba8027a1f6c73fd60d1165e63aff"
        )
        window.set_focus()
        settings = wait_for(lambda: control(window, "\u8bbe\u7f6e"))
        if not settings:
            print("Window controls:", [w.window_text() for w in window.descendants()], flush=True)
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
            print("Window controls:", [w.window_text() for w in window.descendants()], flush=True)
            raise RuntimeError("Could not scroll to the Preview Goose button")
        ready = wait_for(lambda: [text for text in desktop_pet_status(window)
                                   if "outside the school schedule" in text.casefold()], 35)
        if not ready:
            raise RuntimeError(f"The helper did not reach idle before the test: {desktop_pet_status(window)!r}")
        time.sleep(2)
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
        screen, box, beak = result
        screen.save(report / "preview-windows.png")
        screen.crop(box).save(report / "preview-goose-detail.png")
        print("Visible native goose:", box, flush=True)
        if not any("preview is active" in message for message in status):
            raise RuntimeError("EveTech did not acknowledge the preview")
        def click_goose():
            frame = visible_goose(child.pid, before)
            if frame:
                mouse.move(coords=frame[2])
                time.sleep(.08)
                mouse.click(coords=frame[2])
            return next((w for w in Desktop(backend="uia").windows()
                         if w.window_text() == "EveTech · Ask Goose" and w.is_visible()), None)

        bubble = wait_for(click_goose, 8)
        if bubble is None:
            raise RuntimeError("Clicking the native goose did not open the chat bubble")
        time.sleep(.3)
        ImageGrab.grab(include_layered_windows=True).save(report / "preview-bubble.png")
        control(bubble, "Type Question").click_input()
        time.sleep(.35)
        ImageGrab.grab(include_layered_windows=True).save(report / "preview-question.png")
        control(bubble, "Circle Screen").click_input()
        picker = wait_for(lambda: next((w for w in Desktop(backend="uia").windows(process=child.pid)
                                       if control(w, "Use selection")), None), 8)
        if picker is None:
            raise RuntimeError("Circle Screen did not open the capture overlay")
        mouse.move(coords=(300, 200))
        mouse.press(coords=(300, 200))
        mouse.move(coords=(580, 390))
        mouse.release(coords=(580, 390))
        use = control(picker, "Use selection")
        if not use.is_enabled():
            raise RuntimeError("Drawing a region did not enable confirmation")
        ImageGrab.grab(include_layered_windows=True).save(report / "preview-selection.png")
        use.click_input()
        question = wait_for(lambda: next((w for w in Desktop(backend="uia").windows(process=child.pid)
                                         if w.window_text() == "EveTech · Circle Screen"), None), 8)
        if question is None:
            raise RuntimeError("The screenshot annotation window did not open")
        ImageGrab.grab(include_layered_windows=True).save(report / "preview-annotation.png")
        control(question, "Close").click_input() if control(question, "Close") else question.close()
        check_nest(window)
        window.set_focus()
        preview.click_input()
        frames = []
        for _ in range(32):
            frames.append(ImageGrab.grab(include_layered_windows=True).resize((640, 480)))
            time.sleep(.12)
        frames[0].save(report / "preview-walk.gif", save_all=True, append_images=frames[1:],
                       duration=140, loop=0, optimize=True)
        print("Native goose, chat and Circle Screen all passed.", flush=True)
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
