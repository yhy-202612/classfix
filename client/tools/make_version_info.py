"""生成 PyInstaller 的 Windows 版本资源文件（build/version_info.txt）。

用法：python tools/make_version_info.py build/version_info.txt
版本号取自 evetech.VERSION，避免打包产物与代码里的版本号不一致。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evetech import VERSION  # noqa: E402
from evetech.update import version_tuple  # noqa: E402

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={quad},
    prodvers={quad},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [
          StringStruct('CompanyName', 'EveTech'),
          StringStruct('FileDescription', 'EveTech 教室电脑放映急救箱'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'EveTech'),
          StringStruct('LegalCopyright', 'EveTech'),
          StringStruct('OriginalFilename', 'EveTech.exe'),
          StringStruct('ProductName', 'EveTech'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def main(argv):
    target = Path(argv[1] if len(argv) > 1 else ROOT / "build" / "version_info.txt")
    parts = (version_tuple(VERSION) + (0, 0, 0))[:4]
    quad = "(%d, %d, %d, %d)" % parts
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.format(quad=quad, version=VERSION), encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
