<p align="center">
  <img src="assets/brand/logo.svg" width="120" alt="MAVO BOX"/>
</p>

<h1 align="center">MAVO BOX</h1>
<p align="center">本地运行的 TikTok 短视频工具箱：爆款钩子克隆 · 智能混剪 · 多账号定时发布</p>

---

MAVO BOX 是一个 **Windows / macOS 桌面应用**（Tauri 外壳 + 本机 Python 后端）。所有数据、素材和 API Key 都留在你自己的电脑上，没有任何云端账号体系，也不上传遥测。AI 能力来自**你自己配置的** API（大模型、语音、图片、视频生成），费用由你的账号承担，软件在花钱前会先给出估算并等你确认。

## 它能做什么

| 模块 | 你给它 | 它做 |
|---|---|---|
| **下载** | 单个 TikTok 视频链接 | 用 yt-dlp 下载，并截取前 N 秒登记到素材库 |
| **爆款克隆** | 参考视频（链接或本地）+ 你的产品图 / 人物图 + 要求 | 解读钩子（词级转写、镜头节奏、为什么有效）→ 为你的产品改写脚本与分镜 → 调用你配置的图片 / 配音 / 视频生成 API → 按词级时间合成带逐词高亮字幕的竖屏成片，可一次展开最多 20 个变体 |
| **智能混剪** | 一批素材（文件或文件夹）+ 剪辑要求 + 大模型 | 自动切镜头并抽关键帧 → 大模型理解镜头、只输出剪辑时间线 JSON → 校验后由 ffmpeg 渲染成 9:16 成片；时间线可手改 |
| **账号与发布** | 本地视频 + 文案 / 话题 + 预设时间 + 多个 TikTok 账号 | 每个账号独立浏览器环境和代理，到点自动发布；带随机错峰、并发 / 同账号 / 同代理互斥、重试、重启恢复，遇到验证码转人工 |

另外：首次使用向导（环境自检、一键安装浏览器组件、AI 服务预设）、授权码激活、应用内自动更新。

## 工作方式

```
        ┌──────────────────────── MAVO BOX（桌面窗口，Tauri）────────────────────────┐
        │  React 界面  ──HTTP(127.0.0.1)──►  Python 后端 (FastAPI)                    │
        │                                       ├─ 任务队列 / 调度器（SQLite 持久化）  │
        │                                       ├─ ffmpeg（内置） · yt-dlp · Playwright│
        │                                       └─ 系统凭据管理器（存 API Key）        │
        └──────────────────────────────────────────────────────────────────────────┘
                                   │ 仅在你确认后调用
                                   ▼
                你配置的 LLM / ASR / TTS / 图片 / 视频生成 API（OpenAI 兼容、fal.ai、Replicate、自定义 HTTP）
```

## 给学员：下载与使用

1. 安装：运行 `MAVO BOX_x.x.x_x64-setup.exe`（Windows）或打开 `.dmg`（macOS）。
2. 激活：首次启动会显示**机器码**，发给老师换取授权码，粘贴后激活（授权码绑定这台电脑，可能带有效期）。
3. 向导：确认 ffmpeg ✅（已内置）→ 如要用发布功能，点"一键安装浏览器组件" → 选择服务类型、填你自己的 API Key。
4. 开始用：下载 / 素材库 / 混剪 / 爆款克隆 / 账号 / 发布 / 设置。

详细步骤、费用与合规提醒、常见问题见 **[学员手册](docs/学员手册.md)**。

## 费用、安全与隐私

- **只监听本机**：后端绑定 `127.0.0.1`，并校验 Host 与 Origin，网页里的恶意脚本无法调用你的本地接口；同一数据目录只允许一个实例。
- **Key 不落盘明文**：API Key 与代理密码只存系统凭据管理器（Windows Credential Manager / macOS Keychain），接口只返回"已设置"，日志和错误信息里不会出现。
- **花钱前先确认**：分析 / 规划 / 生成默认 dry run，只返回估算；真正调用需要带着最新报价金额确认，价格变了旧确认会被拒绝（409）。付费请求不会自动重发，"结果未知"的请求必须你核对账单后手动重试。
- **防重复发布**：点击发布后结果不明的任务转人工确认，重试前要先确认视频确实没发出去。
- **文件与网络**：服务商返回的结果地址不得指向本机 / 内网，下载有大小上限；文件接口限定在数据目录内。
- **授权码**：离线 Ed25519 签名，机器码绑定，可设有效期。它的目的是挡住随手转发，**不是防破解方案**。

## 架构与技术栈

| 层 | 技术 |
|---|---|
| 桌面外壳 | Tauri 2（Rust）：拉起后端、等待就绪、开窗口、退出时连同子进程一起结束（Windows 作业对象）；启动时检查签名更新 |
| 界面 | React 19 + TypeScript + Vite（打包后由后端托管静态文件） |
| 后端 | Python 3.11+ · FastAPI · SQLite · 自带任务队列与调度器 · PyInstaller 打包 |
| 媒体 | ffmpeg / ffprobe（打包内置）· yt-dlp · PySceneDetect + OpenCV |
| 自动化 | Playwright（每账号独立持久化 Profile、独立代理，以及各自的语言 / 时区 / 视口设置） |
| 安全 | keyring · cryptography（授权码 / 签名校验） |

## 目录结构

```
backend/           Python 后端
  api/             HTTP 路由（app、publisher、remix、clone、static）
  core/            配置、数据库、任务队列、凭据、授权、环境自检、ffmpeg 定位、单实例锁
  modules/         downloader · publisher（调度器 + Playwright 驱动）· remix · clone
  tools/           license_tool.py（老师发授权码）
  tests/           pytest 测试（全部离线）
  launcher.py      桌面入口（选端口、单实例、启动服务）
frontend/          React 界面（src/ 下按功能分文件）
src-tauri/         Tauri 外壳与安装包配置、图标
scripts/           build.cmd / build.sh（打包后端）· tauri-build.ps1（出安装包）· dev.cmd（开发）
assets/brand/      LOGO 与图标源文件
docs/              学员手册 · 教师手册 · 功能与接口参考手册
.github/workflows/ release.yml（Windows + macOS 构建，尚未实跑）
private/           ⚠ 授权与更新签名私钥（已被 .gitignore 排除，不会提交，务必另外备份）
```

## 开发

**环境**：Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js 18+、ffmpeg + ffprobe（在 PATH，或设置 `FFMPEG_PATH`）。构建桌面安装包另需 Rust 与（Windows）Visual Studio Build Tools 的 C++ 工作负载。Windows 上 `python` 命令可能是应用商店的占位程序，请一律用 `uv run`。

```powershell
# 后端依赖 + 发布功能所需的浏览器
cd backend
uv sync
uv run playwright install chromium

# 前端依赖
cd ..\frontend
npm install
```

**运行（开发模式）**：`scripts\dev.cmd` 会分别启动后端（8000）和 Vite（5173），访问 http://127.0.0.1:5173 。脚本开头的 PATH 指向作者本机的 uv / node 目录，换机器时改掉那一行。也可手动：

```powershell
cd backend
$env:TIKTOL_DEV = "1"      # 让后端接受 Vite 开发服务器的来源
uv run --no-sync uvicorn main:app --host 127.0.0.1 --port 8000
# 另开终端
cd frontend; npm run dev
```

开发模式默认不要求授权码；要在开发时测试授权流程，设置 `TIKTOL_REQUIRE_LICENSE=1`。务必保留 `--host 127.0.0.1`，真实发布时只启动一个后端进程，不要用 `--reload`。

**测试**：

```powershell
cd backend
uv run pytest -q          # 基线：129 passed, 1 skipped（跳过的是需要符号链接权限的用例）
cd ..\frontend
npm run build             # tsc 类型检查 + vite 构建
```

测试全部离线：外部 API 一律用 mock，ffmpeg 相关用例会现场生成测试视频并做真实渲染。

### 环境变量

| 变量 | 作用 |
|---|---|
| `TIKTOL_DATA_DIR` | 数据目录。默认 Windows `%LOCALAPPDATA%\TikTolTool`，macOS `~/Library/Application Support/TikTolTool`（沿用旧目录名以免已有数据成为孤儿） |
| `TIKTOL_PORT` | 首选端口（默认 8000，被占用时自动换空闲端口） |
| `TIKTOL_DEV` | `1` = 额外信任 Vite 开发服务器（5173）的来源 |
| `TIKTOL_REQUIRE_LICENSE` | `1` = 非打包环境也强制授权码（打包版总是强制） |
| `TIKTOL_SERVE_UI` | `0` = 后端不托管前端静态文件 |
| `TIKTOL_STATIC_DIR` | 指定前端构建目录（默认 `frontend/dist` 或打包内置） |
| `FFMPEG_PATH` | ffmpeg / ffprobe 所在目录或文件（打包内置的优先） |
| `FFMPEG_BIN_DIR` | **构建时**：含 `ffmpeg` 与 `ffprobe` 的目录，会被打进安装包 |
| `PLAYWRIGHT_BROWSERS_PATH` | 浏览器组件位置（打包版自动设为数据目录下的 `browsers`） |

## 构建与发布

```powershell
# 1) 打包后端（含前端静态文件与 ffmpeg）
$env:FFMPEG_BIN_DIR = "D:\path\to\ffmpeg-bin"      # 含 ffmpeg.exe 与 ffprobe.exe
scripts\build.cmd
# 2) 在普通终端（不要在 Claude 桌面应用的沙箱里）生成带更新签名的安装包
powershell -ExecutionPolicy Bypass -File scripts\tauri-build.ps1
# 产物：src-tauri\target\release\bundle\nsis\MAVO BOX_<版本>_x64-setup.exe 和 .sig
```

macOS 用 `scripts/build.sh` + `npx @tauri-apps/cli@2 build --bundles dmg,app`，需要在 Mac 上或 GitHub Actions 的 macOS runner 上执行。给学员发授权码、签名密钥保管、更新清单 `latest.json` 的格式和托管，见 **[教师手册](docs/教师手册.md)**。

## 已知限制

- **真实平台未实测**：TikTok 上传页的选择器参考开源项目编写，只做过本地夹具测试，没有用真实账号验证；页面改版会失效。登录保持、代理实际生效、真实发出视频都需要你用测试号验证。
- **AI 接口未实测**：fal.ai / Replicate / OpenAI 兼容接口的字段细节、真实视频生成质量与人物一致性都只在 mock 下测试，需配置真实 Key 后验证。
- **macOS 未验证**：已做数据目录、字体、单实例锁等适配并写了构建脚本和 CI，但从未在 Mac 上跑过。无苹果开发者证书时用户会遇到"无法验证开发者"提示。
- **未做代码签名**：Windows 会提示"未知发布者"，部分杀毒软件可能误报；购买代码签名证书后可显著改善。
- **更新地址是占位的**：`src-tauri/tauri.conf.json` 里的 `plugins.updater.endpoints` 需要换成你实际托管 `latest.json` 的公开地址；在此之前更新检查静默失败，不影响使用。
- **一键安装浏览器较慢**：下载并解压后占用数百 MB（Chromium + headless shell），网络差时需在向导里填下载镜像。
- **复刻他人内容有版权风险**：默认不把参考视频的画面与音乐放进成片，只学习结构；自动化发布有被平台限流或封号的风险。

## 文档

| 文档 | 面向 | 内容 |
|---|---|---|
| [学员手册](docs/学员手册.md) | 学员 | 安装、激活、向导、功能、费用与合规、常见问题 |
| [教师手册](docs/教师手册.md) | 老师 / 发行 | 发授权码、密钥保管、发版与自动更新、发放前自检 |
| [功能与接口参考手册](docs/功能与接口参考手册.md) | 开发 / 进阶 | 调度参数、Provider 配置格式与示例、预算与状态机、HTTP 接口 |
| [第三方许可](THIRD_PARTY_NOTICES.md) | 发行 | 依赖许可证、ffmpeg 来源与校验和 |
| [规划稿](PLAN.md) | 开发 | 最初的调研与规划（部分设计已演进） |

## 版权

© 2026 SHINEVISTA GLOBAL GROUP LIMITED(HK). 保留所有权利。本仓库未附带开源许可证，使用须经授权。第三方组件各自遵循其许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
