"""Per-user startup shortcut management without administrator rights."""

import base64
import os
import subprocess
import sys
from pathlib import Path


def shortcut_path():
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    return startup / "ClassFix.lnk"


def launch_command():
    if getattr(sys, "frozen", False):
        return Path(sys.executable), "--background"
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    target = pythonw if pythonw.is_file() else python
    main = Path(__file__).resolve().parents[1] / "main.py"
    return target, f'"{main}" --background'


def startup_enabled():
    try:
        return shortcut_path().is_file()
    except (OSError, KeyError):
        return False


def set_startup(enabled):
    path = shortcut_path()
    if not enabled:
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError:
            return False
    try:
        target, arguments = launch_command()
        path.parent.mkdir(parents=True, exist_ok=True)

        def quoted(value):
            return str(value).replace("'", "''")

        # Isolate WScript.Shell in its own process. PowerPoint and Core Audio
        # use different COM apartment models in the main application process.
        script = "\n".join((
            "$shell = New-Object -ComObject WScript.Shell",
            f"$link = $shell.CreateShortcut('{quoted(path)}')",
            f"$link.TargetPath = '{quoted(target)}'",
            f"$link.Arguments = '{quoted(arguments)}'",
            f"$link.WorkingDirectory = '{quoted(target.parent)}'",
            f"$link.IconLocation = '{quoted(target)},0'",
            "$link.Description = 'ClassFix 课堂故障监视'",
            "$link.Save()",
        ))
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        # 超时留足：装机量最大的那种机器上，powershell.exe 冷启动要 16~23 秒
        # （每次都做 Preparing modules for first use，再叠加杀软扫描）。原来写 10 秒，
        # 超时异常又被下面的 except 吞掉，结果就是「勾了开机自启但其实没创建」。
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
            check=False, capture_output=True, creationflags=0x08000000, timeout=45)
        return completed.returncode == 0 and path.is_file()
    except (OSError, KeyError, subprocess.SubprocessError):
        return False
