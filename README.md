# EveTech（原 ClassFix）

教室电脑排障助手（Classroom Troubleshooting Assistant）· 面向教师与电教委员的 Windows 原生桌面工具。首版聚焦「课件里的视频放不出来」这一类课堂高频故障：自动检查 PPTX Open XML 与媒体引用、读出 PowerPoint 自己的报错原文、检查系统音量与静音、抓 Windows 事件日志，最后给一份**分层诊断**，每条证据明确标注「已确认 / 未取得」。

- 官网/下载页：https://aiclass.openplays.cn/classfix/
- EEO 风格测试页：https://aiclass.openplays.cn/classfix-eeo/
- 客户端源码版本：**1.7.0**
- 官网下载清单版本：**1.6.3**

## 目录结构

```
client/      客户端源码（Python 3.11–3.13 + PySide6，Windows 原生，PyInstaller 打包为免安装 exe）
site/        下载站源码（静态单文件页 index.html + og.png + update.json）
site-eeo/    同一份宣传内容的 EEO 风格测试页（单文件 index.html）
server/      云端服务 classfix-api（零依赖 Node：诊断代理 + 积分/额度 + 管理页）
```

## client/ —— 客户端

- 入口 `main.py`，主包 `evetech/`，桌宠助手 `desktop_goose/`，测试 `tests/`，辅助脚本 `tools/`。
- 构建：`build-exe.ps1`（PyInstaller，产物为 `EveTech.exe` + `_internal`，免安装、无需管理员权限）。
- 依赖：`requirements.txt`（运行）、`requirements-build.txt`（构建）。
- 使用说明、构建步骤和当前版本变更见 [客户端 README](client/README.md)。
- 许可与第三方组件名单见 [THIRD-PARTY.md](client/THIRD-PARTY.md)。当前客户端构建所用 FFprobe 的分发许可随所选构建确定。

## site/ 与 site-eeo/ —— 下载页

两个都是**单文件静态页**，由 site-deploy 平台托管（`POST /api/sites/classfix/deploy` 与 `/api/sites/classfix-eeo/deploy`）。

发布时注意：

1. `update.json` 里的下载地址必须指向本站 `/classfix/ClassFix-x.y.z-Windows-x64.zip`，不能填 drop 站的直链（客户端自动更新读就是这个文件）。
2. 安装包（约 72MB）**不进仓库**：发布时从投放站直接 `cp` 进 release 目录（同机拷贝，零带宽），并 `chmod 644`。
3. 旧 release 里的大 zip 要清掉，否则每个 release 都累积一份。

## server/ —— classfix-api 云端服务

零依赖 Node，数据落在站点自己的 `data/`（`db.json` + `config.json`，**不进仓库**）。提供诊断代理、积分与每日额度、离线方案基线（catalog）、管理页 `/admin`。

- 管理口令、OTP 密钥等在服务器 `data/config.json`，不入库。
- 改动后打包 zip 部署到 site-deploy（`/api/sites/classfix-api/deploy`），约 3 秒生效。

## 不入库的内容

| 内容 | 原因 |
|---|---|
| `*.zip` 安装包与分片 | 体积大、可再生；发布走投放站直传 |
| 签名凭据、管理口令、API key | 凭据安全 |
| `data/db.json`、`data/config.json` | 含用户与配置数据 |
| `__pycache__/`、`.venv/`、`build/`、`dist/` | 构建产物 |

## 版本记录

| 版本 | 要点 |
|---|---|
| 1.7.0 | 原版桌鹅进程加载 EveTech Mod，改版聊天气泡和可调整选区的圈屏 |
| 1.6.12 | 使用当前用户的本地命名管道发送桌鹅命令，修复助手闲置后预览无应答 |
| 1.6.11 | 桌鹅预览在设置窗口所在屏幕立即出现，按钮显示应答、超时和助手退出状态 |
| 1.6.10 | 显式解析已校验的桌鹅 DLL，预览独立于 DeepSeek，启动错误显示故障步骤和文件名 |
| 1.6.9 | 从 itch.io 下载并配置桌鹅，提供独立下载程序、ZIP 导入和启动错误提示 |
| 1.6.8 | 客户端更名为 EveTech，加入截图圈画问答、放映入窝、桌鹅预览和可见性诊断 |
| 1.6.3 | 播放失败分层诊断（PPTX/媒体、音量、放映状态、PowerPoint 报错原文与 COM、Windows 事件日志）、提示框显示报错原文、内存紧张提醒 + 一键优化、多提示不互相遮挡、打开验证只关自己的副本 |
| 1.5.0 | 见 `client/release-notes-1.5.0.md` |
