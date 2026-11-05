# TikTol M2

FastAPI + SQLite 后端和 React / TypeScript / Vite 前端，仅用于本机。保留 M0 下载、M1 多账号发布及 M2 智能混剪，新增 M3 爆款克隆：参考解读、Brief / Treatment / Script、预算确认、生成素材、词级字幕、竖屏成片及批量变体。PLAN.md 保持原样，Tauri 尚未实现。M3 当前实现与验证边界见本文末尾。

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

依赖分别保存在 `backend/.venv` 与 `frontend/node_modules`。M1 固定 `playwright==1.56.0`；M2 新增 `scenedetect`、`opencv-python-headless`，`httpx` 移入运行依赖。现有 uv.lock 尚未联网更新，宿主运行 `uv sync` 更新并保留锁文件。不需要重新安装前端依赖。

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

## M2 智能混剪

1. 在「设置」配置 `llm` Provider（OpenAI 兼容 Base URL，例如以 `/v1` 结尾；程序追加 `/chat/completions`）、模型和 Key。分析需要支持 `image_url` 多模态输入的模型。
2. 打开「混剪」，从素材库多选，或登记本地视频绝对路径、扫描文件夹。支持 mp4/mov/mkv/webm/avi；扫描不跟随目录符号链接或 Windows junction，不登记符号链接文件，拒绝相对路径和 `..` 跳转。源文件不复制、不修改。
3. 填要求、目标时长、画幅（默认 1080×1920）、可选 BGM 本地路径、字幕、转场和画面填充方式，保存项目。修改并保存项目设置会清除已有时间线和成片关联；历史输出文件仍在素材库。
4. 查看预计发送图片数，再「分析素材」。取消勾选“发送关键帧给模型”可只切镜头。未分析时按固定分段估算图片数；切分后按未理解镜头精确计数，实际模型切点可能令估计增加。默认每批 6 张，单批最多尝试 3 次，因此失败重试也可能产生用量。
5. 「生成时间线」后可编辑片段起止、顺序、速度、转场、字幕、静音，保存时严格校验。时间线时长容差为目标 ±5%，最少 ±0.5 秒；交叉/滑动转场的重叠会扣除。`src_start/src_end` 是素材绝对秒数，字幕 offset 是变速后片段内秒数。字幕是模型生成的文案，未实现语音识别或逐字音频对齐。
6. 「渲染成片」显示队列进度，可取消；完成后用 `<video>` 预览。发布页可选择新素材，混剪页会显示其 asset ID。

数据目录新增 `frames/*.jpg`；SQLite 增量创建 `shots`、`remix_projects`、`remix_project_assets`，不改已有表记录。镜头按 asset ID + 切镜参数缓存，共享项目复用；关键帧缺失会按原 ID 补抽。打标缓存记录 Provider ID/Base URL/model 的指纹，配置变化后需重新分析，不包含 Key；同一个镜头仅保留最后一次 Provider 的理解结果。素材 ID 视为不可变输入，替换原文件后应重新登记成新素材。

模型失败：单批打标尝试 3 次仍失败则为空描述、`tagged=false`，下次分析可重试；编排最多请求 3 次，校验失败会返回具体校验原因给模型修正，最终使用质量排序、均匀取段的规则回退并标 `fallback=true`。规则回退采用硬切和空字幕，不伪造镜头理解；素材不足且不允许重复时明确失败。队列不会自动重复整个混剪任务，避免扩大模型费用；可手动重试失败任务。同步 HTTP 请求最长 90 秒，取消模型调用要等本次请求返回/超时；渲染子进程约每 0.1 秒检测取消。SceneDetect 的单次检测同样需等函数返回后响应取消。

渲染逐片段统一尺寸、fps、yuv420p、48kHz 双声道，再 concat/xfade/acrossfade；支持 crop/模糊背景、fade/crossfade/slide、变速、原声静音和循环 BGM 混音。BGM 时原声系数 0.6，BGM 系数默认 0.2，没有自动音量包络检测。输出 libx264 + AAC + faststart MP4，登记为新 asset。字体优先使用时间线 `global_caption_style.font`，其次项目 `font_path`，再次存在时使用 `C:\Windows\Fonts\msyh.ttc`，否则使用 ffmpeg 默认字体；字体需支持字幕中的字符。字幕文本通过临时 UTF-8 文件加 `expansion=none` 传入，字体复制为内部固定文件名，避免文本/路径进入 filtergraph 语法。所有命令都用参数数组，不使用 shell。

M2 API（JSON，沿用本机来源限制）：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/assets/scan` | `{folder,recursive}` 扫描；返回 assets 和 skipped |
| GET / POST | `/api/remix/projects` | 项目列表 / 创建 |
| GET / PUT / DELETE | `/api/remix/projects/{id}` | 读取 / 全量更新 / 删除项目（保留媒体） |
| GET | `/api/remix/projects/{id}/estimate` | images、approximate、中文用量说明 |
| POST | `/api/remix/projects/{id}/analyze` | `{tag:true}` 分析队列任务，`false` 仅切镜头 |
| GET | `/api/remix/projects/{id}/shots` | 镜头及 frame_url |
| POST | `/api/remix/projects/{id}/plan` | 生成时间线队列任务 |
| PUT | `/api/remix/projects/{id}/timeline` | 请求体直接传完整时间线，校验保存 |
| POST | `/api/remix/projects/{id}/render` | 渲染队列任务 |
| GET | `/api/remix/projects/{id}/preview-frames` | 每个 clip 的镜头关键帧 URL（镜头中间帧） |
| GET | `/api/remix/frames/{name}` | 仅服务 frames 下已登记 JPEG |
| GET | `/api/remix/output/{asset_id}` | 仅服务项目关联且位于 media 下的 MP4，支持范围请求 |

创建/更新示例：`{name,brief_text,target_seconds,width,height,asset_ids:[...],settings:{provider_id,bgm_path,captions,transition,threshold,segment_seconds,allow_repeat,fit,font_path}}`；可选字段按默认值填充。队列接口沿用 `/api/tasks/{id}`、`/cancel`、`/retry`；同一项目只允许一个活动任务，运行中不可改设置、时间线或删除。

M2 离线验证：现场生成明显切点视频、均分回退、关键帧生成/恢复/缓存，mock LLM 的 JSON 围栏/坏 JSON/重试/降级、非法时间线和重复检测，API 扫描→分析→编排→保存→真实渲染→成片读取，三片段带交叉/滑动转场、特殊字符字幕、BGM，以及 blur、原声、变速和执行中取消。测试不会调用真实 Provider。

本轮完整测试尝试结果为 **51 passed、2 skipped、1 failed**：既有 Playwright 本地 HTML 测试在启动驱动前被沙箱管道权限拒绝（WinError 5）；SceneDetect 尚未安装、Windows 符号链接权限不足的两个测试跳过。最后一次排除该已确认受阻用例的运行结果为 **51 passed、2 skipped、1 deselected**。pytest 仍需使用 M1 所述的仅测试进程临时目录兼容方式；未修改原测试或系统权限来掩盖失败。TypeScript 检查通过，Vite `spawn EPERM`，完整 build 未通过。常见明文 Key / 私钥格式的源文件扫描无匹配；此模式扫描不等于能识别任意格式的秘密。宿主需执行：

```powershell
cd D:\下载\tiktol\backend
uv sync
uv run pytest -q
cd ..\frontend
npm.cmd run build
```

**未验证**：真实 LLM 多模态调用、各 Provider JSON/图片格式兼容性、真实素材规模下的模型成本与吞吐、浏览器页面交互和中文字体视觉效果。未联网、未安装依赖、未更新 uv.lock、未执行 Git 操作。独立审查留给宿主 Claude，本轮由 GPT 实现与离线测试。

## M3 爆款克隆（钩子复刻）

本模块为独立实现，只使用“理解结构、迁移角色、重建镜头”的方法思想，没有下载、复制或使用 Hypit 的源码、文档文字或 SVML。默认只分析前 5 秒，可选整条（最长 3600 秒）。参考视频的画面、原音与音乐不进入生成时间线。产品证据、人物肖像、上传素材与用户 BGM 的使用权由用户确认；发布生成内容应勾选 AI 标注。

### 使用路径

1. 在设置配置多模态 `llm`、`video_gen`、`tts`；可选 `asr` 与 `image_gen`。API Key 仍只存系统凭据管理器。`extra` 禁止凭据、认证头及含凭据的 URL。
2. 打开「爆款克隆」，粘贴 HTTPS TikTok 链接，或选择素材库视频 / 上传本地文件。其他平台先自行下载再导入；在线解析复用现有 TikTok 下载器。
3. 在 Brief 选择 Provider 并保存，然后预览分析费用、确认并解读。可查看词级转写、beat 时间轴、作用说明及不确定问题。未配置 ASR 时只解读画面，不编造参考台词；抽帧本身不能确认音乐或音效。
4. 填写产品名、真实卖点与证据、禁用词、人物/产品图、目标语言/时长、风格、voice、变体数与预算。可选用户 BGM、自己的后半段视频。人物/产品图可用文件上传或素材库多选。
5. 预览规划费用并确认，生成 Treatment 和 Script。Treatment 分离角色迁移说明与实际镜头；Script 是唯一口播来源，`display` 为显示文字、`spoken` 为读音。可编辑两份 JSON，保存时严格校验。
6. 生成素材前先 dry run，看缓存命中、已知费用、未知单价、共享预算和费用预留，再确认调用。逐镜头可预览、重新生成、替换上传素材或使用参考图 Ken Burns 静态兜底。发生“提交结果未知”时先核查供应商账单，不会自动重复付费 POST；确认后可强制重生成。
7. 组装时间线并渲染。配音按段合成，ASR 用于确定词时间；无法可靠按词匹配或无 ASR 时按 Script 读音字符权重估算时间并标注 `estimated`。字幕采用 ASS `kf` 逐词高亮，镜头按台词词区间切换，源镜头音轨全部静音。每段的最后一个镜头包含音频尾部静音，不丢尾音。
8. 一个 Brief 可展开最多 20 个变体。`person` 候选是已选择的参考图 asset ID，`product` / `dialogue` / `style` / `cta` 是文本候选；按维度笛卡尔积取前 N 个，不足 N 个则拒绝。变体共用参考解读与素材缓存。可逐个操作，也可从根项目预览全部变体的规划/生成合计费用，再批量规划或生成成片（生成后自动组装、渲染）。全局 TaskQueue 限制并发，同一项目只允许一个活动任务。

编辑 Brief 会使旧方案、时间线与输出关联失效；编辑 Script / Treatment 会使时间线和输出关联失效。组装只选择与当前方案指纹匹配的音频/视频，不使用旧台词的配音。排队后 Provider 配置若变化，任务暂停为 `needs_attention`，须重新确认。删除项目只删记录，保留付费素材以便缓存复用；有子项目的根项目须先删除子项目。

### Provider 配置

旧数据库仅增量迁移：检测 `PRAGMA table_info(providers)` 后添加 `api_style`、`extra`，重复启动不重复 ALTER；旧配置默认 `openai_compat` / `{}`。

| type | api_style | Base URL 与接口 |
|---|---|---|
| llm | openai_compat | Base URL 通常以 `/v1` 结尾，追加 `/chat/completions`；必须支持 image_url |
| asr | openai_compat | 追加 `/audio/transcriptions`，multipart、verbose_json、timestamp_granularities[]=word |
| image_gen | openai_compat | 无参考图 `/images/generations`；有参考图 multipart `/images/edits` |
| tts | openai_compat | `/audio/speech`，请求 WAV，voice 来自 Brief |
| video_gen | fal_queue | Base URL 使用队列根，例如 `https://queue.fal.run`；POST `/{model}`，Key 认证，保存 request_id/status_url/response_url |
| video_gen | replicate | Base URL 使用根，例如 `https://api.replicate.com`；POST `/v1/predictions`，model 填供应商要求的 version 标识；Bearer 认证 |
| video_gen | custom_http | submit_path / poll_path / body_template / JSON 路径均在 extra 配置；Bearer 认证 |

OpenAI 兼容的视频 Provider 尚无统一标准；对它应选择 `custom_http` 并配置模板。`openai_compat` 视频类型也按 custom_http 默认 `/jobs` 接口处理。轮询认证 URL 必须与配置 Base URL 同源，拒绝跨主机发送密钥。结果下载不附认证头，不自动跟随重定向，须得到直接结果 URL。

示例 `extra`（视频，单价由用户填写，示例数字不是供应商报价）：

```json
{
  "supported_durations": [5, 10],
  "pricing": {"per_second": 0.05},
  "defaults": {},
  "poll_seconds": 2,
  "timeout_seconds": 900,
  "image_upload": "data_url"
}
```

可用 `min_duration` 与 `max_duration` 替代离散 `supported_durations`，必须同时提供；没有配置时只允许 5/10 秒。费用键：LLM `per_request`（分析最多 3 次、规划最多 6 次）、视频 `per_second`、图片 `per_image`、TTS `per_character`、ASR `per_minute`。没有键就是“未知”，不是免费；显式 0 表示该服务不收费。支持端点覆盖 `transcriptions_path`、`speech_path`、`generations_path`、`edits_path`。

custom_http 示例：

```json
{
  "submit_path": "/jobs",
  "poll_path": "/jobs/{job_id}",
  "job_id_path": "data.id",
  "status_path": "data.status",
  "result_path": "data.outputs.0.url",
  "success_statuses": ["succeeded", "completed"],
  "body_template": {
    "model": "{model}",
    "input": {"images": "{images}", "prompt": "{prompt}", "duration": "{duration}", "aspect": "{aspect}"}
  },
  "supported_durations": [5, 10]
}
```

模板是 JSON 树替换，不执行代码。整值 `{images}` / `{duration}` 保持数组/数值类型；支持 `{image}`（第一张）、`{prompt}`、`{model}`、`{aspect}`。JSON 路径使用点分隔字段和数组下标。`image_upload: "upload"` 时需提供 `upload_path` 和 `upload_result_path`，multipart 字段为 `file`；默认使用 data URL。对多张参考图，使用 image_gen 合成同一首帧，或视频模板 `{images}` / `multi_image_field` 指向模型的多图输入字段，不能悄悄只保留第一个主体。

image_gen 会先生成逐主体锚图；多主体镜头额外生成组合首帧，并纳入费用估算。锚图与最终视频都有参数/输入文件内容哈希缓存。缓存键包含 Provider 配置及模型、不包含 API Key。相同缓存键有进程内互斥锁，避免并发变体同时提交同一请求。

### 预算、恢复和状态

分析、规划、生成默认 `dry_run:true`，只返回费用/计划，不调用付费服务。真正执行需要 `dry_run:false`，未知/非零费用或超过根预算时还需要 `confirm_budget:true`。明确确认代表接受当前显示的未知费用或超支；预算不是供应商硬性扣费上限。根项目及其所有变体的 `spent_usd` 与未完成任务预留一起参与检查。批量运行先检查所有子项目，再按合计费用确认。

`spent_usd` 是保守估计，不是供应商实际账单：分析/规划按最大 JSON 修正请求次数预留并记账；生成提交按配置估价记录，即使请求失败也不假定供应商未收费。未知单价的实际金额不会被编造为零，费用项保持 null，已知小计单独显示。ASR 按目标时长估算，真实 TTS 时长、模型重试、供应商最小计费单位等均可能令账单偏离。缓存命中不新增费用。

状态：`created → analyzing → analyzed → planning → planned → generating → planned（素材已就绪）→ assembling → assembled → rendering → rendered`，异常为 `failed` / `needs_attention`。批量成片跳过手动组装点击。每个阶段可独立重跑。取消通过共用 `/api/tasks/{id}/cancel`，每镜头循环、轮询、退避和 ffmpeg 可取消；正在进行的同步 HTTP 最多等当前超时返回（90 秒），本地取消不等于供应商远程作业取消。

付费 POST 不自动重发，GET 轮询/下载遇限流或暂态错误最多退避重试两次。已拿到的远程 job 信息保存在 generated_media 的内部 job 字段，不经媒体 API 返回；下次非强制生成继续轮询，避免重复提交。TTS 已完成但后续失败时保存音频以便继续。应用中断后预留释放；遗留付费任务没有有效预留就拒绝调用，必须从项目入口重新确认。未知提交结果不自动重发，用户可核查后选择强制生成；静态兜底与用户素材替换不调用付费 API。不可用/损坏的下载结果不登记成功，视频下载后以 ffprobe 验证。

### M3 API

所有项目路径前缀为 `/api/clone/projects`；错误保持中文，不回显第三方异常和密钥。沿用应用的本机 Origin/Host 限制。

| 方法 | 路径 | 请求 / 用途 |
|---|---|---|
| GET / POST | `/api/clone/projects` | 列表 / 新建 `{name,source_url或source_asset_id,hook_seconds:5,mode:"hook",brief:{...}}` |
| GET / PUT / DELETE | `/api/clone/projects/{id}` | 读取 / 全量更新并使派生结果失效 / 删除项目记录 |
| POST | `/{id}/analyze` | `{dry_run:true}` 估算；`{dry_run:false,confirm_budget:true}` 排队分析 |
| GET | `/{id}/analysis` | 结构化 Analysis |
| PUT | `/{id}/brief` | 完整 Brief JSON |
| POST | `/{id}/plan` | dry run / 确认后生成 Treatment 与 Script |
| PUT | `/{id}/script`、`/{id}/treatment` | 完整 JSON，严格校验与引用检查 |
| POST | `/{id}/generate` | dry run / 确认后生成；可选 `force`、`auto_assemble_render` |
| POST | `/{id}/shots/{shot_id}/regenerate` | 同生成选项，可选 `force:true`、`fallback:true` 或 `replacement_asset_id` |
| POST | `/{id}/assemble` | 将当前生成结果按词级时间组装 |
| POST | `/{id}/render` | 渲染并登记 output_asset_id |
| POST | `/{id}/variants` | `{count,dimensions:{style:[...],cta:[...]}}`，创建子项目 |
| POST | `/{id}/batch/plan`、`/{id}/batch/generate` | 全部变体合计 dry run / 确认后批量规划或生成成片 |
| GET | `/{id}/cost` | 生成估算；可选 `shot_id`、`force` 查询单镜头重生成费用 |
| GET | `/{id}/media` | 逐生成物状态、尝试次数、费用与降级标记（不返回远程 job） |
| GET | `/api/clone/media/{media_id}` | 仅服务已登记且位于 clone 目录中的生成文件 |
| GET | `/api/clone/output/{asset_id}` | 成片预览 |
| POST | `/api/clone/assets/upload?kind=image或video或audio` | 原始二进制请求体，最大 512MB，ffprobe 校验后登记 |

Script 段内 `words` 包含 `{display,spoken,marks:["emphasis"或"cta"或"cut"或"pause"]}`。Treatment 镜头的 `audio_ref` 引用段 ID，`word_start` / `word_end` 为从 0 开始、包含末词的范围。所有镜头词区间必须按顺序覆盖所有段的所有词，不能遗漏或重叠。为了可靠地把 B-roll 放在语义点，可以在编辑 Treatment 时将同一段分给相邻镜头。当前语义标记用于编排信息，ASS 对所有词使用同一种逐词高亮样式。

时间线保留 M2 的 version=1 和 clips 结构，扩展字段统一使用 `clone_` 前缀；调用 M2 renderer 前去掉扩展字段，复用其格式归一化和竖屏渲染，然后添加配音、ASS、BGM，最后按需接用户后半段。M2 schema 和行为不变。实际成片长度跟随 TTS/词时间，名义镜头时长用于生成和预算；若单镜头变速超出 0.25–4 倍则拒绝组装，需改台词/镜头时长。没有自动修改台词来硬凑秒数。

### 文件与验证交接

新增：

- `backend/modules/clone/`：`schemas.py`、`brief.py`、`analysis.py`、`treatment.py`、`script.py`、`generation.py`、`assemble.py`、`variants.py`、`service.py` 及包初始化。
- `backend/modules/clone/adapters/`：`base.py`、`llm_client.py`（复用 M2）、`asr_client.py`、`image_client.py`、`tts_client.py`、`video_client.py` 及包初始化。
- `backend/api/clone.py`、`frontend/src/clone.tsx`。
- `backend/tests/test_clone.py`、`test_clone_adapters.py`、`test_clone_flow.py`：HTTP MockTransport、迁移、校验/修正、缓存、预算、变体、API 全流程及真实 ffmpeg/ASS 集成测试代码。

修改：`backend/core/db.py`、`backend/core/providers.py`、`backend/api/app.py`、`backend/modules/downloader.py`（返回登记的 asset，原下载 API 不变）、`frontend/src/main.tsx`、`frontend/src/style.css`、`README.md`。新增表为 clone_projects、generated_media、clone_analysis_cache、clone_reservations。没有改 PLAN.md，没有增加依赖，没有联网安装或 Git 操作。

本轮由 GPT 直接实现，使用用户提供的 Claude brief；没有调用 Claude/Claude-cx、Gemini 或外部角色，也没有自行做独立审查。宿主已确认的基线是 **53 passed / 1 skipped、前端 build 通过**。上面 M2 章节的旧沙箱测试记录属于历史记录，不是这次基线。

按本轮要求，**没有运行 uv、pytest、Vite 或基线验证**。仅对 backend/core、api、modules、tests 执行 Python compileall，以及前端 `tsc --noEmit` 静态检查；这些不证明运行时测试或完整构建通过。首次对 backend 根目录 compileall 遇到 `.pytest_cache` 无法列出，随后改为只编译源码目录并通过，没有因此停工。

宿主机应执行（不需要重新安装依赖）：

```powershell
cd D:\下载\tiktol\backend
uv run pytest -q
cd ..\frontend
npm.cmd run build
```

宿主 Claude 应独立验收测试结果与 diff，尤其预算并发、恢复幂等性、ASS 字幕视觉效果与请求字段。真实 fal / Replicate / OpenAI 兼容接口的字段、模型版本、队列 URL、上传协议和多图支持未实测；真实视频质量、人物/产品一致性、中文字体与浏览器完整交互未验证。缓存与预算均为本地单服务进程设计，不支持同时运行两个后端实例对同一数据库进行付费生成。人物一致性依赖供应商模型能力，锚图不是效果保证。
