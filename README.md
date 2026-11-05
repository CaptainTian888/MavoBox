# TikTol M1

FastAPI + SQLite 后端和 React / TypeScript / Vite 前端，仅用于本机。保留 M0 下载截取、素材注册、任务队列和 Provider 配置，新增 M1 多账号、代理、Playwright 发布及本地持久化调度。PLAN.md 保持原样；Tauri、混剪和视频生成调用留到后续。

## 安装（宿主机联网执行）

Python 3.11+，Node.js 18+，uv，PATH 中的 ffmpeg/ffprobe。不要使用本机的商店占位 `python`。

```powershell
$env:PATH = "C:\Users\Captain\AppData\Local\hermes\bin;C:\Users\Captain\AppData\Local\hermes\node;$env:PATH"
cd D:\下载\tiktol\backend
uv sync
uv run playwright install chromium
cd ..\frontend
npm.cmd i
```

依赖分别保存在 `backend/.venv` 与 `frontend/node_modules`。M1 固定 `playwright==1.56.0`；现有 uv.lock 尚未联网更新，宿主运行 `uv sync` 更新并保留锁文件。不需要重新安装前端依赖。

## 启动

运行 `scripts\dev.cmd`，访问 http://127.0.0.1:5173 。脚本最小化启动两个终端，关闭对应服务终端即停止。也可分别运行：

```powershell
cd D:\下载\tiktol\backend
uv run --no-sync uvicorn main:app --host 127.0.0.1 --port 8000
# 另一终端
cd D:\下载\tiktol\frontend
npm.cmd run dev
```

必须保留 `--host 127.0.0.1`，不要开放到局域网。Vite 将 `/api` 代理到 8000 端口。前端生产 build 用于后续桌面封装；M0 用 Vite 开发服务器运行，非静态文件双击打开。

数据默认在 `%LOCALAPPDATA%\TikTolTool`，设置 `TIKTOL_DATA_DIR` 可覆盖。SQLite 保存配置、素材元数据和任务；媒体下载到该目录的 `media`，原下载临时文件处理后清理。注册本地素材仅记录路径，不复制或修改原文件。`FFMPEG_PATH` 可指向 ffmpeg.exe 或包含 ffmpeg/ffprobe 的目录。

Key 只进入 keyring（Windows Credential Manager），无明文回退；界面/API 仅显示是否设置，Key 字段保存后清空。Provider Base URL 不可嵌入认证信息；不要把 Key 填入名称、模型或 URL 等普通字段。凭据服务失败返回固定错误。测试使用内存凭据，不接触真实系统凭据。删除 Provider 同时删除对应凭据。

任务默认并发 2、失败自动重试 1 次；可在 `Settings` 中配置。手动重试只支持失败任务并重置尝试次数。取消是协作式的：待执行任务立即标记取消，下载在下一进度回调检测，ffmpeg 截取每 0.2 秒检测。下载网络请求有 20 秒 socket 超时；关闭服务等待正在执行的线程退出。启动时 running 变为 failed，pending 自动恢复。第三方错误只显示固定失败提示，不保存原始异常。下载成功资产是截取后的 MP4。

## 测试与验收

```powershell
cd D:\下载\tiktol\backend
uv run pytest -q
cd ..\frontend
npm.cmd run build
```

测试覆盖凭据不回显（包括无效请求）、CRUD、输入校验、队列并发/取消/重试/重启恢复，以及 mock yt-dlp 的下载全链路。无网络调用；ffmpeg 测试现场生成 2 秒 lavfi testsrc 视频，ffmpeg/ffprobe 不存在则 skip。

后端启动后，在另一 PowerShell 终端真实验收（测试媒体放在工作区）：

```powershell
cd D:\下载\tiktol
ffmpeg -nostdin -y -f lavfi -i testsrc=size=160x120:rate=15 -t 2 -c:v libx264 -pix_fmt yuv420p .\sample.mp4
curl.exe --fail http://127.0.0.1:8000/api/health
$body = @{path=(Join-Path (Get-Location) 'sample.mp4')} | ConvertTo-Json -Compress
# stdin 避免 Windows curl 对 JSON 引号和中文路径的转义问题
$body | curl.exe --fail -H 'Content-Type: application/json' --data-binary '@-' http://127.0.0.1:8000/api/assets
curl.exe --fail http://127.0.0.1:8000/api/assets
```

预期 health 为 `{"status":"ok"}`；assets 注册返回 id、绝对路径、video、约 2 秒、160×120。

仓库秘密检查（只扫源文件，排除依赖与构建产物）：

```powershell
rg -n --hidden -g '!PLAN.md' -g '!**/node_modules/**' -g '!**/.venv/**' -g '!**/dist/**' '(sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|BEGIN .*[P]RIVATE KEY)' .
```

预期没有匹配（rg 退出码 1）。此扫描只能找常见格式；测试使用运行时随机值验证 API、日志和 SQLite 无 Key/代理密码泄漏。

## M1 使用与调度

1. 打开「账号」，创建分组、代理及账号；代理地址只填 `http://host:port`、`https://host:port` 或 `socks5://host:port`，认证信息单独填写。密码进入系统凭据，API 只返回 `password_status`。密码留空保留原值，勾选清除可删除凭据。
2. 每个账号点击「登录 / 人工接管」，在有头 Chromium 中手动登录。检测到 Studio 上传入口后自动关闭并保存 Profile；手动关闭也保留 Profile，但状态需要再点「检查登录态」确认。登录任务通过原 `/api/tasks` 查询，可在下载页查看进度。
3. 打开「发布」，多选账号、选素材或填写视频绝对路径，设置文案、话题、可见性、AI 标注及互动权限。默认仅自己可见；文案支持 `{account_name}` 和 `{index}`，批量按选中的顺序展开。输入时间使用本机时区，服务端统一持久化为 UTC；裸时间（没有时区）被拒绝。
4. 任务到点后上传并立即发布，使用 TikTok 默认封面。**本地调度不操作 TikTok 服务器端定时日历**；电脑、应用及网络需要保持运行。计划时间是开始执行时间，实际发布还需上传和页面处理时间。
5. `needs_attention` 表示验证码、页面改版、登录失败或提交结果未知。检查数据目录 `screenshots/<任务ID>-*.png`，登录 / 人工接管后先在 TikTok 确认是否已发布，再选择「人工确认已完成」或重试。截图会遮盖输入框及可编辑内容，不保存 Cookie 导出文件或网络抓包。

只启动**一个后端进程 / 一个 Uvicorn worker**，不要同时启动多个实例或使用 `--reload` 处理真实发布。SQLite 任务跨重启保留，账号和代理互斥由单进程调度器统一管理；登录与检查也使用同一浏览器租约。账号有发布历史时不可删除，可停用以保留关联；删除无历史账号不递归删除其 Profile。

数据目录新增 `profiles/<account_id>` 和 `screenshots`。Profile 含浏览器会话，只在数据目录内创建，不通过 API 返回 Cookie；不要分享该目录。代理密码使用现有 KeyringSecrets 的 `proxy:<id>` 槽位，与 Provider 凭据隔离。发布错误和日志只记录固定事件文案，不持久化第三方异常原文。截图失败不影响任务转入人工处理。

默认设置位于 `backend/core/config.py` 的 `Settings`，可通过 `create_app(Settings(...))` 配置：

| 参数 | 默认值 | 行为 |
|---|---|---|
| `publish_concurrency` | 2 | 发布和账号浏览器操作的总并发上限 |
| `publish_poll_seconds` | 15 | 使用 Event.wait 的轮询间隔，无忙等 |
| `publish_min_interval_seconds` | 1800 | 同账号上次成功完成到下次开始的最小间隔 |
| `publish_missed_grace_seconds` | 600 | 重启时超过有效执行时间的宽限则标记 missed |
| `publish_headless` | True | 发布/检查默认无头；手动登录始终有头 |
| `browser_locale` / `browser_timezone` | en-US / UTC | 每个独立 context 的语言和时区 |
| `browser_width` / `browser_height` | 1280 / 900 | 每个独立 context 的视口 |
| `browser_timeout_ms` / `upload_timeout_ms` | 30000 / 180000 | 页面操作 / 上传的显式超时 |
| `login_timeout_seconds` | 600 | 等待手动登录的上限 |

创建任务时，在 `[0, jitter_window_seconds]` 中抽取一次随机延后秒数并落库；重新启动、重试或改期不重新抽取。有效首次执行时间为 `scheduled_at + jitter_seconds`。暂时故障仅在确定未点击发布时自动重试，退避依次为 60、120、240 秒，上限 3600 秒，`max_retries` 表示首次之外的重试次数（默认 1）。退避截止写入 `next_attempt_at`，重启不会丢失。未知异常及点击发布后超时均转人工确认，防止自动重复提交。

重启时 `running → needs_attention`，因为远端可能已经发布；超出宽限的 `scheduled → missed`，其余继续执行。已有成功发布可能因账号最小间隔而延后任务。运行中的任务不提供取消、改期或强行标记完成，API 返回 409；可取消 scheduled / needs_attention，failed / cancelled / missed / needs_attention 可手动重试；成功任务不能改期。任务的删除不在 M1 范围。

## M1 公开 API

均为 `/api` 前缀，沿用本机 Host/Origin 限制、参数校验和中文错误；API 本身不改变绑定地址，启动仍使用 `127.0.0.1`。

| 路径 | 方法 | 用途 |
|---|---|---|
| `/account-groups` | GET / POST | 分组列表 / 新增 |
| `/account-groups/{id}` | PUT / DELETE | 更新 / 删除（解除关联） |
| `/proxies` | GET / POST | 代理列表 / 新增 |
| `/proxies/{id}` | GET / PUT / DELETE | 读取 / 更新 / 删除；已绑定的代理不可删除 |
| `/accounts` | GET / POST | 列表（可按 group_id 筛选）/ 新增 |
| `/accounts/{id}` | GET / PUT / DELETE | 读取 / 更新 / 删除无发布历史账号 |
| `/accounts/{id}/login` | POST | 返回 202 登录任务，可通过 M0 tasks API 查询 |
| `/accounts/{id}/check` | POST | 有限超时的登录态检查并更新账号状态 |
| `/publish-jobs` | POST | account_id 单个创建返回对象；account_ids 批量创建返回数组 |
| `/publish-jobs/batch` | POST | 显式批量创建（返回数组） |
| `/publish-jobs` | GET | status、account_id、date_from、date_to 筛选；日期是含时区 ISO 时间，按计划时间筛选 |
| `/publish-jobs/{id}` | GET | 详情及固定文案日志 |
| `/publish-jobs/{id}/cancel`、`/retry` | POST | 取消 / 手动重试 |
| `/publish-jobs/{id}/reschedule` | POST | 请求体 `{ "scheduled_at": "2026-12-01T12:00:00Z" }` |
| `/publish-jobs/{id}/complete` | POST | 人工确认；请求体 `{}` 或可选不含查询参数的 TikTok 视频 result_url |

批量创建字段：`account_ids`、`asset_id` 或 `video_path`（二选一）、`caption`、`hashtags` 数组、`scheduled_at`、`stagger_minutes`、`jitter_window_seconds`、`privacy`（public/friends/private）、`ai_generated`、`allow_comment`、`allow_duet`、`allow_stitch`、`max_retries`。创建前检查全部账号与媒体，再以事务展开，不会因后一个账号无效留下半个批次。不存在的视频被拒绝，非 9:16 / 建议时长范围外 / 超过 4GB / 无法探测规格会返回 warnings 并记入日志；执行前再次检查文件。

## M1 验证与已知未验证点

宿主机安装依赖后运行：

```powershell
cd D:\下载\tiktol\backend
uv sync
uv run playwright install chromium
uv run pytest -q
cd ..\frontend
npm.cmd run build
```

本轮离线使用现有 `.venv` 跑全部测试：22 passed、1 skipped（Playwright 未安装，本地 HTML 夹具自动跳过），包含 M0 的 9 个用例。沙箱中 Python 3.14 的私有目录 ACL 导致普通 pytest 临时目录不可访问；仅测试命令内临时替换 os.mkdir 的模式为普通目录并指定工作区 basetemp 后完成运行，未改测试框架或系统权限。宿主仍需以标准 `uv run pytest -q` 复跑。Starlette 有现存 httpx 弃用警告。

`npm run build` 的 TypeScript 检查已通过，Vite 因沙箱禁止启动 esbuild 子进程（spawn EPERM）未完成，**不声称生产构建通过**。未联网安装依赖，未更新 uv.lock，未调用真实 TikTok。**真实 TikTok 页面选择器、代理连通性、Cookie/Profile 登录保持和实际发布均无法在沙箱中验证**；宿主应先用测试号、仅自己可见验证单条任务。选择器统一在 `modules/publisher/selectors.py`，参考了两个 MIT 项目的 Studio 上传、权限和确认流程，未整段复制实现。

若成功确认页面没有明确的新视频链接，`result_url` 留空，不根据账号最新视频猜测链接。封面使用 TikTok 默认值，服务器端日历不参与调度；其参考选择器已集中保留。账号页可分别覆盖 browser_locale、browser_timezone、browser_width、browser_height，留空继承 Settings 默认值；已有数据库以增量字段迁移兼容。独立审查由宿主 Claude 执行，本轮 GPT 只完成实现与离线验证。
