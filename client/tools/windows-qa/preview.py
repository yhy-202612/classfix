"""Exercise the packaged application on an interactive Windows desktop."""
import ctypes
import hashlib
from ctypes import wintypes
from pathlib import Path
import os
from shutil import copytree
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from PIL import ImageChops, ImageGrab
import psutil
import win32api
import win32con
import win32gui
from pywinauto import Desktop, keyboard, mouse


user = ctypes.WinDLL("user32", use_last_error=True)
callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user.EnumWindows.argtypes = (callback, wintypes.LPARAM)
user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user.IsWindowVisible.argtypes = (wintypes.HWND,)
user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user.SetProcessDPIAware()
dwm = ctypes.WinDLL("dwmapi")
dwm.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.RECT), wintypes.DWORD)
report = Path(os.environ["QA_REPORT"])
report.mkdir(parents=True, exist_ok=True)
stock_exe_sha = "943fd1ea44266c5d7fa02f2b292db095a4e6ba8027a1f6c73fd60d1165e63aff"
notepad_title = 'Goose "Not-epad"'


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


def is_above(window, other):
    while other:
        other = win32gui.GetWindow(other, win32con.GW_HWNDPREV)
        if other == window:
            return True
    return False


def orange_over_note(screenshot, note, from_right):
    left, top, right, bottom = note
    edge = left if from_right else right
    middle = (top + bottom) // 2
    strip = screenshot.crop((edge - (0 if from_right else 9), middle - 10,
                             edge + (9 if from_right else 0), middle + 10)).convert("RGB")
    return sum(r >= 240 and 135 <= g <= 190 and b <= 50 for r, g, b in strip.getdata())


def visible_note_frame(note):
    frame = wintypes.RECT()
    # Extended frame bounds exclude the invisible DWM margins in GetWindowRect.
    if dwm.DwmGetWindowAttribute(note, 9, ctypes.byref(frame), ctypes.sizeof(frame)) == 0:
        return frame.left, frame.top, frame.right, frame.bottom
    return win32gui.GetWindowRect(note)


def check_stock_delivery():
    original = Path(os.environ["EVETECH_DATA_DIR"]) / "desktop-goose" / "v0.31"
    assert hashlib.sha256((original / "GooseDesktop.exe").read_bytes()).hexdigest() == stock_exe_sha
    with TemporaryDirectory() as tmp:
        folder = copytree(original, Path(tmp) / "stock")
        # Accelerate idle between pranks and keep the cursor and audio undisturbed.
        (folder / "config.ini").write_text(
            "Version_DoNotEdit=1\nEnableMods=False\nSilenceSounds=True\n"
            "Task_CanAttackMouse=False\nAttackRandomly=False\n"
            "FirstWanderTimeSeconds=0.5\nMinWanderingTimeSeconds=0.5\nMaxWanderingTimeSeconds=0.8\n",
            encoding="ascii",
        )
        process = subprocess.Popen([str(folder / "GooseDesktop.exe")], cwd=folder)
        try:
            goose = wait_for(lambda: next((hwnd for hwnd in stock_windows(process.pid)
                                          if win32gui.GetWindowRect(hwnd)[2] > 500), None), 8)
            assert goose, "Stock Desktop Goose did not start"
            assert win32gui.GetWindowLong(goose, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST
            display_left, _, display_right, _ = win32gui.GetWindowRect(goose)
            for _ in range(4):
                side = None
                blocked = None
                clear = None
                deadline = time.monotonic() + 25
                previous = None
                move_start = None
                while time.monotonic() < deadline:
                    note = win32gui.FindWindow(None, notepad_title)
                    for popup in stock_windows(process.pid):
                        if popup not in (goose, note):
                            user.PostMessageW(popup, win32con.WM_CLOSE, 0, 0)
                    if note and win32gui.IsWindowVisible(note):
                        rect = win32gui.GetWindowRect(note)
                        left, _, right, _ = rect
                        note_topmost = win32gui.GetWindowLong(note, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST
                        if note_topmost and previous != rect:
                            if left < display_left:
                                side = False
                            if right > display_right:
                                side = True
                            visible_width = min(right, display_right) - max(left, display_left)
                            if side is not None and visible_width >= 95:
                                above = is_above(goose, note)
                                desktop = ImageGrab.grab(include_layered_windows=True)
                                orange = orange_over_note(desktop, visible_note_frame(note), side)
                                frame = (desktop, rect, orange)
                                move_start = move_start or time.monotonic()
                                if above:
                                    clear = frame
                                elif blocked is None or orange < blocked[2]:
                                    blocked = frame
                                if blocked and time.monotonic() - move_start >= 0.35:
                                    break
                        previous = rect
                    time.sleep(0.025)
                chosen = blocked or clear
                if chosen:
                    chosen[0].save(report / "preview-stock-drag.png")
                    print(f"Stock v0.31, both windows topmost. Note above goose: {blocked is not None}. "
                          f"Note bounds: {chosen[1]}. Orange beak pixels crossing note edge: {chosen[2]}.",
                          flush=True)
                    return
            raise RuntimeError("Could not capture the unmodified goose dragging a stock Not-epad")
        finally:
            process.terminate()
            process.wait(timeout=10)


def stock_windows(pid):
    windows = []

    @callback
    def collect(hwnd, lparam):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    user.EnumWindows(collect, 0)
    return windows


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


def check_text_delivery(window, preview, child_pid):
    window.set_focus()
    preview.click_input()
    time.sleep(0.75)
    keyboard.send_keys("^%g")
    bubble = wait_for(lambda: next((item for item in Desktop(backend="uia").windows()
                                   if item.window_text() == "EveTech · Ask Goose" and item.is_visible()), None))
    assert bubble is not None, "Text chat did not open"
    control(bubble, "Type Question").click_input()
    time.sleep(0.4)
    editor = bubble.descendants(control_type="Edit")[0]
    editor.set_edit_text("Why does 2x + 6 = 14 become 2x = 8?")
    ImageGrab.grab(include_layered_windows=True).save(report / "preview-text-question.png")
    note_title = notepad_title + " · Answer"
    assert not win32gui.FindWindow(None, note_title), "A note appeared before sending a question"
    control(bubble, "Send").click_input()

    display = pet_bounds(child_pid)[0]
    goose = win32gui.FindWindow(None, "EveTech · Desktop Goose")
    assert goose, "The original goose window was missing"
    entering = None
    settled = None
    from_right = None
    best_beak = 0
    frames = []
    start = time.monotonic()
    while time.monotonic() - start < 30:
        note = win32gui.FindWindow(None, note_title)
        if note and win32gui.IsWindowVisible(note):
            left, top, right, bottom = win32gui.GetWindowRect(note)
            effective_width = min(display[2], right) - max(display[0], left)
            if effective_width > 0:
                screenshot = ImageGrab.grab(include_layered_windows=True)
                frames.append(screenshot.resize((768, 576)))
                if win32gui.GetWindowLong(note, win32con.GWL_EXSTYLE) & 0x08000000:
                    assert is_above(goose, note), "Not-epad covered the goose while dragging"
                    assert win32gui.GetForegroundWindow() != note, "Delivering the note took keyboard focus"
                    if from_right is not None and effective_width >= 105:
                        orange = orange_over_note(screenshot, visible_note_frame(note), from_right)
                        if orange > best_beak:
                            best_beak = orange
                            screenshot.save(report / "preview-notepad-drag.png")
                if entering is None and 45 < effective_width < right - left - 45:
                    entering = (left, top, right, bottom)
                    from_right = right > display[2]
                if not win32gui.GetWindowLong(note, win32con.GWL_EXSTYLE) & 0x08000000:
                    if effective_width == right - left:
                        settled = (left, top, right, bottom)
                        screenshot.save(report / "preview-notepad-delivered.png")
                        break
        time.sleep(0.12)

    assert entering is not None, "Did not see a Not-epad window crossing the display edge"
    assert settled is not None, "The original Not-epad did not finish delivery inside the display"
    assert abs(entering[0] - settled[0]) > 45, "The note did not move from its entry point"
    assert best_beak >= 4, "The orange beak was not visible across the edge of the note"
    reply = Desktop(backend="uia").window(handle=note).descendants(control_type="Edit")[0]
    assert "Subtract 6 from both sides" in reply.get_value(), "The note did not contain the text answer"
    frames[0].save(report / "preview-notepad-walk.gif", save_all=True, append_images=frames[1:],
                   duration=140, loop=0, optimize=True)
    print(f"Original Not-epad crossed the edge {entering} and landed {settled} with the text answer.", flush=True)
    print(f"Goose stayed above the note while dragging; {best_beak} orange beak pixels overlap its edge.",
          flush=True)
    win32gui.PostMessage(note, win32con.WM_CLOSE, 0, 0)
    assert wait_for(lambda: not win32gui.IsWindow(note), 5), "The delivered note could not be closed"


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
        assert hashlib.sha256(Path(original.exe()).read_bytes()).hexdigest() == stock_exe_sha
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
        check_stock_delivery()
        window.set_focus()
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
        control(window, "Debug mode: ignore the school schedule").click_input()
        control(window, "Save Settings").click_input()
        check_text_delivery(window, preview, child.pid)
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
