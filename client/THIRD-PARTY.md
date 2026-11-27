# 第三方组件

ClassFix 的目录式发布包使用未修改的 Python、PySide6 Essentials / Shiboken6、psutil、comtypes、pycaw 和 PyInstaller 运行时。Qt DLL 与 Qt for Python 二进制模块作为独立文件放在 `_internal` 内。可从同版本源码重新构建，或在与其兼容的 Qt for Python 环境运行随附的 ClassFix 源码。

- Python 3.13.15 · [源码与许可证](https://www.python.org/downloads/release/python-31315/)
- Qt 6.8.3 · [源码](https://download.qt.io/archive/qt/6.8/6.8.3/single/) · [许可证](https://doc.qt.io/qt-6.8/licensing.html)
- PySide6 / Shiboken6 6.8.3 · [源码](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/) · [许可与致谢](https://doc.qt.io/qtforpython-6/licenses.html)
- psutil 7.2.2 · [源码与许可证](https://github.com/giampaolo/psutil)
- comtypes 1.5.0 · [源码与许可证](https://github.com/enthought/comtypes)
- pycaw 20251023 · [源码与许可证](https://github.com/AndreMiras/pycaw)
- pywin32 · [源码与许可证](https://github.com/mhammond/pywin32) · Windows 的课件打开验证与事件日志读取，Python 3.13 打包使用 308。
- PyInstaller 6.22.3 · [源码与许可证](https://github.com/pyinstaller/pyinstaller)

- FFprobe / FFmpeg · [source and license](https://ffmpeg.org/legal.html) · 是否适用 LGPL 还是 GPL **以实际打包进去的 ffprobe 为准**，判断方式是运行 `tools\ffprobe.exe -L`：本机验证过的 gyan.dev essentials_build 为 `--enable-gpl --enable-version3`，即 GPLv3-or-later，对应发布包内必须附 GPLv3 全文（命名为 `tools\FFmpeg-LICENSE.txt`）。换成不带 `--enable-gpl` 的 LGPL 构建时才改附 LGPL-2.1 全文。

带 `-FFprobeBundle` 构建时，脚本会把 `tools\ffprobe.exe`（及同目录的 `*.dll`）复制进发布包，并在子目录里找到 `LICENSE.txt` 时一并放成 `tools\FFmpeg-LICENSE.txt`。若该目录没有许可文本，构建会打印警告：**对外分发前必须自行补上 FFmpeg 的许可文本**（参考 https://ffmpeg.org/legal.html），否则即违反 LGPL/GPL 的署名与许可要求。
