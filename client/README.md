# ClassFix 1.6.3

**Classroom Troubleshooting Assistant** · 面向教师与电教委员的 Windows 原生桌面工具。首版支持 Microsoft PowerPoint。

## 教室电脑怎么启动

1. 解压免安装 ZIP，保留其中完整的 `ClassFix` 文件夹及 `_internal`。
2. 双击 `ClassFix.exe`。目标电脑无需安装 Python，也无需管理员权限。首次运行会询问是否为当前用户启用开机自启。
3. 关闭主窗口后 ClassFix 继续在系统托盘运行。双击托盘图标可重新打开，托盘菜单可以暂停监视或完全退出。
4. 打开 PowerPoint 后，ClassFix 会静默检查已保存的 Open XML 课件，并在右下角按故障类型提示。能读取 PowerPoint 媒体错误对话框时，会显示报错原文。
5. 进入有视频的放映页后，ClassFix 检查 Windows 音量和静音状态。系统静音或音量低于阈值时，右下角显示音量滑块。可点击滑条跳到所选音量，也可拖动。调音期间可以继续放映，提示保留至手动关闭。

诊断记录和设置默认保存在 `%LOCALAPPDATA%\ClassFix\classfix.sqlite3`。备份或迁移记录前，请先退出 ClassFix，再复制数据库。测试时可通过 `CLASSFIX_DATA_DIR` 指定单独的目录。

## 从源码运行

在 Windows 10 22H2 或 Windows 11 使用 x64 Python 3.11–3.13。当前版本以 Windows 11 x64 / Python 3.13 验证。打开 PowerShell，切换到包含 `main.py` 的源码目录：

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe main.py
```

如果装的是 Python 3.11 或 3.12，把首行的 `-3.13` 换成对应版本。若目标电脑不能联网，可在同架构、同 Python 版本的联网 Windows 机器下载 wheels，复制后离线安装：

```powershell
py -3.13 -m pip download --only-binary=:all: -r requirements.txt -d wheels
& .\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheels -r requirements.txt
```

## 课堂排障

### 后台自动监视

- 支持 `.pptx`、`.pptm`、`.ppsx`、`.ppsm`、`.potx` 和 `.potm`。旧版二进制 `.ppt` 需先在 PowerPoint 中另存为上述格式，后台才能读取其媒体结构。
- 打开课件时，后台读取每页视频引用并用 FFprobe 检查真实容器和音视频编码。外部链接丢失、媒体无法读取、或媒体不符合 PowerPoint 推荐的 MP4 / H.264 / AAC 组合时才提示。扫描过程不显示进度窗口，也不会修改课件。
- 放映监视通过当前页媒体形状或课件扫描结果确认视频页。播放器从播放或暂停转为未就绪时，ClassFix 会单独提示播放状态异常。能读取 PowerPoint 原生媒体错误对话框时会显示原文，未确认关联页码时省略页码。
- 只在当前放映页确实含视频时检查音量。同一次放映中，同一页只提醒一次。音量滑条调整 Windows 默认媒体输出的系统总音量，默认提醒阈值为 25%，可在「设置 → 后台监视」修改。
- 黑色提示从屏幕右侧滑入右下角。连续出现时，现有提示先平滑上移让位，新提示进入最下方。屏幕放不下的提示会排队，课堂提示不会切走 PowerPoint 的键盘焦点。
- 媒体故障会写入本地「诊断记录」，包括已知课件名、页码、检测依据，以及读到的 PowerPoint 原始报错或接口错误码。报错提示停留约 12 秒，底边细线平滑收短，走完后提示从右侧滑出。
- 监视期间订阅 Windows Application 中的 PowerPoint 崩溃或无响应事件，按报告 ID 查找可访问的已有 Windows 错误报告。出现实际播放异常且能确认关联的 PowerPoint 进程时，可采集最多 8 秒 ETW。是否可采集取决于本机 Windows 权限。可在设置中关闭短时追踪。
- 开机自启使用当前用户的 Windows“启动”文件夹，不需要管理员权限。移动免安装文件夹后，请启动一次新的 `ClassFix.exe`，再到设置中重新保存开机自启选项。

### 低配置教室电脑

- 自动课件检测一次只处理一份课件，课件内重复使用的视频只探测一次。后台 FFprobe 以较低的进程优先级运行，给正在使用的程序留出 CPU 时间。
- 每 5 秒用开源 `psutil` 读取一次系统内存。连续两次达到 85% 或可用内存低于 800 MiB 时提醒，弹窗用百分比和进度条显示占用。关闭后冷却两分钟，持续高占用才会再次提醒。也可以点击「1 小时内不再提醒」，此选择在重启 ClassFix 后仍有效。
- 课前关闭 PowerPoint，再点「优化内存」和「确认优化」，并允许 Windows 管理员授权。短时运行的辅助进程依次处理全局工作集、文件缓存、已修改页、待机页、低优先级待机页、注册表整理和物理页合并。这些操作会影响前台程序，重新加载内存页可能使程序短时变慢。检测到 PowerPoint 后跳过剩余操作。 [旧 PCL 社区版内存优化源码](https://github.com/PCL-Community/PCL-CE/blob/7b5c912ffa29ef5554d069e7605d3faa99238c4a/PCL.Core/App/Tools/MemSwapService.cs)
- 优化结果保留到手动关闭。弹窗显示整机可用内存和占用率的前后实测值，以及每项操作是否成功、跳过或失败。可以从同一弹窗打开 Windows 任务管理器。

### PPT 视频

选择点击无反应、报错、画面异常或有画面无声音，选中课件和出问题的幻灯片视频。原视频可作为补充，也可以在没有课件时单独检测。向导会根据已识别的引用和回答调整问题。

- PPTX 通过幻灯片与媒体的关系表区分嵌入和外部链接；页码按课件中保存的幻灯片顺序显示。
- 外部本地视频不存在时，先在桌面、下载、文档与 U 盘按同名/相近文件名找回，可一键把课件里的链接指回找到的文件（自动备份原课件为 `.bak`）；嵌入媒体丢失或关系损坏则需用原视频重新插入。
- 兼容性以实际容器与码流为依据。PowerPoint 推荐 MP4 + H.264 + AAC。检测兼容组合后，仍需在教室电脑试播。 [Microsoft 支持](https://support.microsoft.com/en-us/powerpoint/video-and-audio-file-formats-supported-in-powerpoint)
- 执行建议并保存课件后，点「重新检测」查看变化。放映后选「已恢复」或「仍有故障」。复测记录保留首次证据和最近一次结果。
- 支持本机 PPTX 结构检查。旧版 PPT 或加密课件可先在 PowerPoint 打开，再另存为可读取的 PPTX。网页、UNC 及可识别的映射共享链接显示为尚未检测。嵌入媒体大于 512 MB 时，可导出到本地后用「原始视频」探测。

### FFprobe

完整免安装包已经包含 LGPL 共享版 FFprobe 及其运行库。自行构建时也可以从 FFmpeg 下载页获取与电脑架构匹配的构建。将 `ffprobe.exe` 及共享构建所需 DLL 放在 ClassFix 的 `tools` 子目录中，或在「设置 → 媒体检测」选择它。 [FFmpeg 下载](https://ffmpeg.org/download.html)

### 声音与教室设备

- 「声音诊断」通过 Core Audio 读取默认媒体输出、系统静音和音量、输出设备及 PowerPoint 音频会话。在 PowerPoint 播放有声内容时复测，更容易识别应用音量与实际会话的输出端点。声音设置和混音器按钮直接打开对应 Windows 页面。 [Core Audio 接口](https://learn.microsoft.com/en-us/windows/win32/coreaudio/core-audio-interfaces)
- 「课前巡检」读取活动显示目标、Windows 网络连接分类、PowerPoint 安装或进程状态，以及 Windows 识别到的摄像头及触控设备。屏幕信号、实际出声、成像和触控效果需要在教室确认。Windows 报告的 Internet 状态属于操作系统检测结果。

### 配置 AI 辅助分析

1. 在「设置」中输入服务商的完整 `https://…/v1/chat/completions` 地址与模型名并保存。仅回环地址支持明文 HTTP，可接入本机兼容服务。
2. API 密钥可在设置里输入，存于本次运行的内存；也可在启动环境中设置 `CLASSFIX_AI_KEY`。密钥不落盘。
3. 在诊断结果页点「分析复杂情况」。选择已执行的操作，检查预览，再点「确认发送并分析」。请求数据只包含列入允许清单的检测状态、编码枚举、数值、选择题回答和少数错误码。课件、视频、路径、设备名和原始日志留在本机。
4. AI 回复以纯文本建议显示。按建议处理后可回到结果页复测。未配置服务时仍可完成本地诊断。

ClassFix 的检测为只读。修复由教师通过 PowerPoint 或 Windows 设置执行。

### ClassFix 桌宠与触摸键盘

- 在「设置 → ClassFix 桌宠」选择已解压的 Windows Desktop Goose v0.31，再保存设置。ClassFix 使用原版的步态与绘制，桌鹅随 ClassFix 启停。托盘菜单提供「问鹅」和「暂停鹅出场」。
- 鹅在课间、眼保健操、课间操和午休静音游荡，上课时走回屏幕外。放映和手动暂停优先。课表默认工作日，可在设置中启用周末。 [Desktop Goose 官方下载](https://samperson.itch.io/desktop-goose)
- 在同一设置页点「使用 DeepSeek」，填入 API 密钥并保存。桌鹅复用 ClassFix 当前运行的官方 DeepSeek 密钥，密钥不落盘。点鹅打开聊天气泡，也可从 ClassFix 托盘或按 `Ctrl+Alt+G` 提问。安全检查通过的完整回答由鹅拖入屏幕并轻叫一声。桌鹅不能出场时可在提问框查看。 [DeepSeek 内容安全说明](https://api-docs.deepseek.com/guides/safety/)
- 「全局触摸键盘」可以独立启用。它用 Windows 的输入焦点和可编辑状态控制系统键盘，离开编辑区时收起。不同应用使用同一套判断，第三方自绘控件可能需要 Windows 任务栏的键盘按钮。 [Windows 触摸键盘说明](https://support.microsoft.com/zh-CN/Windows/Hardware/Input-Devices/enable-and-disable-the-touch-keyboard-in-windows)

## 构建 EXE

在 Windows 的源码目录运行以下命令。脚本生成 PyInstaller **目录式**应用，`dist\ClassFix\ClassFix.exe` 与同级 `_internal` 需一起使用。

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build-exe.ps1 `
  -FFprobeBundle C:\path\to\ffmpeg-lgpl-shared
```

这里的执行策略参数仅用于该次构建进程。`-FFprobeBundle` 接受已解压的 FFmpeg 目录，脚本会复制 `ffprobe.exe`、同目录 DLL 和许可证。可使用 `-Python`、`-Destination` 和 `-BuildDirectory` 覆盖路径。再次构建时可选择新的目标目录。将整个 `dist\ClassFix` 压缩后分发。面向校内批量部署时，可由学校按自身流程为构建签名。 [Qt 的 PyInstaller 打包文档](https://doc.qt.io/qtforpython-6/deployment/deployment-pyinstaller.html)

构建桌宠完整包时加上 `-DesktopGooseFolder C:\path\to\DesktopGoose-v0.31`。脚本使用 .NET Framework C# 编译器参考原版 Mod SDK，把生成的助手放进 `tools`。原版程序和素材由使用者从官网取得。

打包脚本还会做两件事：用 `tools\make_version_info.py` 依据 `classfix.VERSION` 生成版本资源（右键 exe → 属性 可看到版本号，不会与代码里的版本号走偏），并写入 `classfix-package.json`，供「从文件更新」识别包版本。

## 发布与更新

程序自带更新能力，发布只需要产出一个压缩包和一份清单：

```powershell
& .\.venv\Scripts\python.exe tools\release.py `
  --source dist\ClassFix --output release --notes release-notes.md
```

`tools\release.py` 会把目录打成 `ClassFix-<版本>-Windows-x64.zip`、计算 SHA256、生成 `update.json`（含版本、下载地址、SHA256、大小、更新说明）。把 zip 与 `update.json` 放到同一个公开目录即可；`update.json` 里的下载地址由 `--base-url` 决定，默认指向班级网站的站点目录。

用户侧的三种升级方式：

| 方式 | 用法 | 适用场景 |
|---|---|---|
| 自动检查 | 启动时静默检查（每天一次），有新版时托盘与状态栏提示；或在「设置 → 软件更新」点「检查更新」 | 能联网的机器 |
| 从文件更新 | 把 zip 拷到机器上，设置页点「从文件更新…」选中它 | 教室机器不能联网 |
| 手动替换 | 解压后覆盖原程序目录 | 程序入口被占用、替换失败时兜底 |

安全边界：只接受 HTTPS 下载地址（本机回环地址例外，方便自测）、强制核对 SHA256、压缩包内只允许单层目录、所有路径都做穿越检查；升级只替换程序目录，诊断记录与设置留在 `%LOCALAPPDATA%\ClassFix`，不会被覆盖。

## 测试

```powershell
# 路径仅在 FFmpeg 和 FFprobe 未加入 PATH 时需要
$env:CLASSFIX_TEST_FFMPEG = 'C:\ffmpeg\bin\ffmpeg.exe'
$env:CLASSFIX_TEST_FFPROBE = 'C:\ffmpeg\bin\ffprobe.exe'
& .\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

测试涵盖原生导航、诊断向导、后台课件扫描、PowerPoint 播放状态分支、右下角提示、系统音量调整、开机启动快捷方式、外链修复复测、重开后记录、真实 FFprobe、Windows Core Audio、课前巡检和本机模拟 AI 接口。编码集成测试在无法找到两个媒体工具时跳过。AI 测试需要允许本机回环连接。

没有 Windows 机器时，可以用影子测试跑「后台监视 + 右下角提示」的判定逻辑（会替换 Windows 专有的音量读取与开机自启，其余走真实源码）：

```bash
pip install PySide6-Essentials
QT_QPA_PLATFORM=offscreen python3 tools/test_monitor_linux.py
```

可对目录式发布包做冒烟测试。以下命令启动窗口、执行实机巡检并输出 JSON 后退出：

```powershell
$report = Join-Path (Get-Location) 'classfix-smoke.json'
$run = Start-Process .\dist\ClassFix\ClassFix.exe -ArgumentList "--smoke-test `"$report`"" -PassThru -Wait
$run.ExitCode
```

## 代码结构

| 模块 | 职责 |
|---|---|
| `classfix/ui/` | Qt Widgets 页面、向导、后台任务与结果展示 |
| `classfix/pptx.py` | 只读 PPTX 引用解析和本地 FFprobe 探测 |
| `classfix/find.py` | 缺失视频的本机查找与课件链接改指（改前备份、改后校验） |
| `classfix/flow.py` | 症状分支、依据、排序、建议与反馈后的复测路径 |
| `classfix/audio.py` | Windows 音频输出、音量和 PowerPoint 会话 |
| `classfix/preflight.py`、`classfix/pnp.py` | 显示、网络、PowerPoint 与即插即用设备 |
| `classfix/monitor.py`、`classfix/ui/overlay.py` | PowerPoint 后台监视与右下角放映提示 |
| `classfix/startup.py`、`classfix/ui/tray.py` | 当前用户开机自启与系统托盘常驻 |
| `classfix/ai.py` | 证据摘要、可配置接口与响应处理 |
| `classfix/update.py` | 版本比较、更新清单、下载校验、安全解压与替换脚本 |
| `classfix/storage.py` | SQLite 历史与设置 |
| `tests/` | 单元、Windows 集成与 Qt 操作测试 |

## 更新记录

### 1.5.2

- 打开验证结束时只关闭本次打开的临时课件副本，保留 PowerPoint 中的其他课件及提示设置。

### 1.5.1

- 修复扫描告警盖掉放影音量弹窗，以及同一页的视频识别来源切换时意外收起音量弹窗。用户拖完滑块后才允许自动收起。
- 右下角按外部链接失效、嵌入媒体缺失、媒体读取、编码兼容性风险和 PowerPoint 播放异常分别提示。能捕获时显示 PowerPoint 原始报错。

### 1.5.0

#### 新增：真的打开一次

新增「打开验证」：不再只看文件结构，而是让本机 PowerPoint 真把这个课件打开一次，给出页数、耗时与失败原因。
打不开时按原因分类：文件损坏或格式不符、受保护的视图拦住、文件被别的程序占用、PowerPoint 组件注册坏了、进程意外终止，每条都附一种做法。
打开成功但页数比文件里登记的少，会明确提示「PowerPoint 丢弃了部分页面」，避免课堂上翻到某一页才发现缺页。
打开成功但个别页或形状渲染出错，也会单独列出，提前知道哪一页可能黑屏。

#### 新增：安全模式对比

同一个课件再用 PowerPoint 安全模式（/safe）打开一次，用来分清「是加载项拖累」还是「文件本身有问题」。
安全模式能开、正常模式打不开，基本可判定为加载项；两种都打不开，按文件损坏处理。

#### 新增：把 PowerPoint 自己的报错读出来

读取 Windows 事件日志里 PowerPoint 的崩溃记录，以及 Windows 错误报告（WER）里保存的崩溃模块与异常码。
检测前后对比 Office 的「禁用加载项 / 启动项 / 文档恢复」记录，新增即说明这次打开被 PowerPoint 拉黑或反复进入恢复。

#### 修复

只装了 WPS 的电脑不再白等 25 秒：安全模式对比仅 Microsoft PowerPoint 支持，检测到 WPS 时直接跳过并写明原因。
本机没有 PowerPoint、由 WPS 应答打开请求时，结论会明确标注「这是 WPS 的判断」，不会冒充 PowerPoint 的结论。
修复托盘「监视」开关拨动时报错、开关状态不同步的问题。
补齐 pywin32 依赖：缺少它会让所有课件都被判成「打不开」，属于发布级阻断。

#### 兼容性

本地记录、设置、登录态仍保存在 %LOCALAPPDATA%\ClassFix，覆盖升级不丢数据。
1.4.x 可以在线升级到 1.5.0；不能联网的机器把发布包拷过去用「从文件更新」。

### 1.4.1

- 修复：1.3.x 升到 1.4 时自动替换会中止、必须手动双击 `ClassFix-update-*.bat` 才装得上。根因是替换脚本用 `timeout /t 1` 等待旧进程退出，而脚本本身以无控制台方式启动（stdin 已重定向），`timeout` 会立刻报 “Input redirection is not supported” 并退出，等待逻辑形同虚设；改用 `ping -n 2 127.0.0.1` 计时，不依赖 stdin。
- 修复：「立即重启并更新」原来只调 `QApplication.quit()`，监视器与托盘还在，进程迟迟不退，替换脚本等到超时就中止。现在走主窗口的完整退出流程（停监视器、隐藏托盘），并在确认框里告知「自动替换没开始时可手动双击更新脚本」。
- 修复：机器上还挂着旧版本（如 1.1.x）托盘实例时，新版启动会因单实例守卫**静默退出**，用户看到的是「装好了但双击没反应」。现在会明确提示「已在运行，请先在托盘右键退出旧版」。
- 新增：上次自动更新失败时，启动后主动告知（状态栏 + 托盘气泡），不再无声失败。
- 新增：替换脚本在重启前校验新 exe 是否存在，启动失败会写入 `ClassFix-update.log`。

- 修复：只装 WPS 的教室电脑点「实测放映接口」不再被判「不是 Microsoft PowerPoint」而拒测，改为真建临时文稿放映一次，按「能否放映」给出结论；WPS 下提示改读页码/音量需人工确认。
- 新增：差异（增量）更新。发布包可附带补丁清单，程序按本地文件指纹挑选补丁，只下载变化的部分；差异超过全量包 50% 或 24 MB、运行库指纹不一致、补丁校验失败时自动回落全量下载。
- 新增：课件里视频引用失效时给出整篇汇总（不选视频也会提示），下拉每一条标注「本机找不到源文件 / 媒体已在课件内 / 网络或共享路径」，唯一缺失时自动选中。
- 新增：本机找回。对失效的外部链接按文件名在桌面、下载、文档、U 盘与其它磁盘搜索同名或相近视频，可一键把课件链接指回找到的文件；改前备份为 `.bak`、改后逐项校验，课件正被 PowerPoint 打开时拒绝写入。
- 测试：新增差异更新 36 项、本机找回 10 项、诊断页 3 项单测。

### 1.2.0

- 新增：程序内自动更新。设置页新增「软件更新」，可检查更新、下载并自动替换重启；启动时静默检查每天一次，有新版时用托盘气泡与状态栏提示，不打断上课。
- 新增：「从文件更新」，离线机器把发布包交给程序即可升级；支持选择任意版本的包，比当前旧时先确认。
- 新增：打包产物带版本资源（`--version-file`），右键 exe → 属性 可见版本号；包内写入 `classfix-package.json`。
- 新增：`tools/release.py` 一键产出发布包与 `update.json`，`tools/make_version_info.py` 由源码版本号生成版本资源。
- 安全：更新清单与下载地址必须是 HTTPS（仅本机回环允许 http）、强制校验 SHA256、压缩包只允许单层目录且逐项检查路径穿越、替换脚本用固定模板生成不留注入面。
- 兼容：升级只替换程序目录，诊断记录与设置保存在 `%LOCALAPPDATA%\ClassFix`，不会被覆盖；离线/无网机器不受影响。
- 测试：新增 24 项更新模块单测（版本比较、清单校验、下载与校验、安全解压、脚本生成、离线包识别）。

### 1.1.7

- 修复（重要）：只有 WPS Office 的电脑会被课前巡检误判成「已装了 PowerPoint」。WPS 会把注册表里 `App Paths\POWERPNT.EXE` 指向自己的 `wps.exe`，旧逻辑只检查这个文件在不在，于是报「找到 PowerPoint 安装文件」；本课的放映监视依赖的是 PowerPoint 的放映接口，在 WPS 上语义并不相同。现在会核对可执行文件名，检测到替代程序时明确提示「请确认使用 Microsoft PowerPoint，而不是 WPS 演示」。
- 修复：PowerPoint 退出后，「本次放映已提示过本页」的记录没有重置，重新打开同一课件的同一页不再提醒音量。
- 构建：`build-exe.ps1` 带 `-FFprobeBundle` 却在目录里找不到 `LICENSE.txt` 时会打印警告；`THIRD-PARTY.md` 补充分发前必须附 FFmpeg 许可文本的说明。
- 测试：新增 4 条 PowerPoint 判定用例（注册表读取与进程枚举打桩，不依赖 Windows，可在任何平台回归）。
- 文档：FFmpeg 许可口径改为「以包内 ffprobe 的实际构建为准」。`ffprobe -L` 才是权威，`-version` 不含许可行；已验证的 gyan.dev 构建是 GPLv3-or-later，因此分发包内附 GPLv3 全文而不是 LGPL。

### 1.1.6

- 修复：翻到同一课件的纯文字页、或放映另一份课件时，仍按「扫描到的最早视频页」弹出音量提示，且页码是错的。现在只有在当前放映页确实含视频时才提示。
- 修复：换页后系统音量已恢复正常，上一页的音量提示却不消失（换页时提前清零了内部状态，导致「收起提示」信号没有发出）。
- 改善：同一次放映中同一页只提醒一次，来回翻页不再反复弹窗；退出放映后重置。
- 改善：多屏放映时，提示位置与课件页码只取第一个可读的放映窗口，避免弹到另一块屏或引用另一份课件的页码。
- 改善：音量提示与格式提示互相隐藏，不再同时叠在右下角。
- 测试：新增 5 条用例覆盖上述行为，并新增 `tools/test_monitor_linux.py`，可在非 Windows 机器上跑后台监视逻辑回归（13 项）。
- 测试：修正视频页下拉框用例的时序竞态（stage 变化后要等控件真正可见），Windows 上不再偶发 `StopIteration`。

### 1.1.5

- 后台可在 PowerPoint 未提供播放器对象（视频尚未点击）时，仅凭媒体形状判定视频页并做预防性音量检查。
- 崩溃或强制退出残留的实例锁可以自动恢复，不再永久无法启动。
- 右下角提示改为自绘深色面板并加入上滑动画；音量提示与格式提示不再重叠。
