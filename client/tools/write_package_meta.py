"""把 evetech-package.json 写进打包目录，供「从文件更新」识别版本。"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evetech import VERSION  # noqa: E402
from evetech.update import PACKAGE_META  # noqa: E402


def runtime_fingerprint():
    """运行时指纹：决定差异包能不能用。

    Python 或 PySide6 换过版本时，解压目录里 Qt 的 dll 与 pyd 会整体变化，
    差异包对不上就会让新旧运行库混在一起、一启动就崩，所以客户端会直接走全量包。
    """
    pyside = ""
    try:
        import PySide6
        pyside = str(getattr(PySide6, "__version__", "") or "")
    except ImportError:
        pass
    return {
        "python": "%d.%d" % sys.version_info[:2],
        "pyside6": pyside,
        "arch": "x64" if sys.maxsize > 2 ** 32 else "x86",
        "onedir": True,
    }


def write_meta(directory, notes=""):
    directory = Path(directory)
    payload = {
        "version": VERSION,
        "built": datetime.now().isoformat(timespec="seconds"),
        "ffprobe": (directory / "tools" / "ffprobe.exe").is_file(),
        "runtime": runtime_fingerprint(),
        "notes": notes,
    }
    target = directory / PACKAGE_META
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main(argv):
    if len(argv) < 2:
        print("用法：python tools/write_package_meta.py <打包目录>")
        return 2
    print(write_meta(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
