<p align="center">
  <img src="assets/brand/logo.svg" width="120" alt="MAVO BOX"/>
</p>

<h1 align="center">MAVO BOX</h1>
<p align="center">A local TikTok video toolkit: viral-hook cloning · smart remix · multi-account scheduled publishing</p>
<p align="center"><b>English</b> · <a href="README.zh-CN.md">简体中文</a></p>

---

MAVO BOX is a **Windows / macOS desktop app** (a Tauri shell around a local Python backend). All data, footage and API keys stay on your own computer: there is no cloud account system and no telemetry. AI capabilities come from the APIs **you configure** (LLM, speech, image, video generation); the cost is billed to your own accounts, and the app shows an estimate and waits for your confirmation before spending anything.

## What it does

| Module | You give it | It does |
|---|---|---|
| **Download** | A single TikTok video link | Downloads it with yt-dlp and registers the first N seconds in the asset library |
| **Viral clone** | A reference video (link or local file) + your product / person images + requirements | Reads the hook (word-level transcript, shot rhythm, why it works) → rewrites the script and shot list for your product → calls your configured image / voice / video-generation APIs → assembles a vertical video with word-by-word highlighted captions aligned to word timings; up to 20 variants at once |
| **Smart remix** | A batch of footage (files or a folder) + editing instructions + an LLM | Detects shots and extracts keyframes → the LLM understands the shots and outputs only an edit-timeline JSON → validated, then rendered to a 9:16 video by ffmpeg; the timeline is hand-editable |
| **Accounts & publishing** | Local videos + caption / hashtags + a schedule + several TikTok accounts | One isolated browser environment and proxy per account, publishes on schedule; random staggering, concurrency / per-account / per-proxy mutual exclusion, retries, restart recovery, and hand-off to a human on captchas |

Also included: a first-run wizard (environment check, one-click browser component install, AI-service presets), licence-code activation, and in-app auto-update. The LLM can point at any OpenAI-compatible endpoint, including a local gateway such as [GPT-Load](https://github.com/tbphp/gpt-load) (key pools, subscription channels), with a "test connection / list models" button.

**Output quality & troubleshooting**
- **Quality check**: one click to check any video for aspect ratio (9:16), duration, frame rate, black frames, frozen picture, silence, loudness and clipping, plus a 12-frame contact sheet. Available in the asset library, remix and clone pages.
- **Loudness normalisation**: remix and clone outputs are brought to about -14 LUFS (typical for short video), with true peaks no higher than -1.5 dBTP.
- **Diagnostics & logs**: copy a secret-free diagnostics report or open the log folder with one click; logs are scrubbed automatically and rotated. The window appears immediately at launch, and if the backend cannot start it shows the reason and troubleshooting steps.

## How it works

```
        ┌──────────────────────── MAVO BOX (desktop window, Tauri) ──────────────────┐
        │  React UI  ──HTTP(127.0.0.1)──►  Python backend (FastAPI)                  │
        │                                    ├─ task queue / scheduler (SQLite)      │
        │                                    ├─ ffmpeg (bundled) · yt-dlp · Playwright│
        │                                    └─ OS credential store (API keys)       │
        └────────────────────────────────────────────────────────────────────────────┘
                                   │ only after you confirm
                                   ▼
        Your configured LLM / ASR / TTS / image / video APIs (OpenAI-compatible, fal.ai, Replicate, custom HTTP)
```

## For students: download and use

1. **Install**: run `MAVO BOX_x.x.x_x64-setup.exe` (Windows) or open the `.dmg` (macOS). Download it from the [Releases](https://github.com/CaptainTian888/MavoBox/releases/latest) page.
2. **Activate**: on first launch the app shows a **machine code**; send it to your teacher to get a licence code, then paste it in (the code is bound to this computer and may have an expiry date).
3. **Wizard**: check that ffmpeg is ✅ (bundled) → if you need publishing, click "Install browser component" → pick a service type and enter your own API key.
4. **Start**: Download / Library / Remix / Clone / Accounts / Publish / Settings.

Detailed steps, cost and compliance notes and FAQ: **[Student manual](docs/学员手册.md)** (Chinese).

## Cost, security and privacy

- **Local only**: the backend binds to `127.0.0.1` and validates Host and Origin, so scripts on web pages cannot call your local API; only one instance may use a data directory at a time.
- **No plaintext keys**: API keys and proxy passwords live only in the OS credential store (Windows Credential Manager / macOS Keychain); the API only ever reports "set / not set", and keys never appear in logs or error messages.
- **Confirm before spending**: analysis / planning / generation are dry runs by default and only return estimates; a real call needs a confirmation that carries the latest quote, and a stale confirmation is rejected (HTTP 409) if prices changed. Paid requests are never resent automatically; "outcome unknown" requests need you to check your bill and retry manually.
- **No double publishing**: a publish whose outcome is unknown after the click is handed to a human, and you must confirm the video was not posted before retrying.
- **Files and network**: result URLs returned by providers may not point at this machine or a private network, downloads have a size cap, and file endpoints are confined to the data directory.
- **Licence codes**: offline Ed25519 signatures, machine-bound, with optional expiry. They are meant to stop casual sharing and are **not an anti-cracking measure**.

## Architecture and tech stack

| Layer | Technology |
|---|---|
| Desktop shell | Tauri 2 (Rust): starts the backend, waits for readiness, opens the window, and ends child processes on exit (Windows Job Object); checks for signed updates at launch |
| UI | React 19 + TypeScript + Vite (static files served by the backend once packaged) |
| Backend | Python 3.11+ · FastAPI · SQLite · built-in task queue and scheduler · packaged with PyInstaller |
| Media | ffmpeg / ffprobe (bundled) · yt-dlp · PySceneDetect + OpenCV |
| Automation | Playwright (per-account persistent profile and proxy, plus per-account locale / timezone / viewport) |
| Security | keyring · cryptography (licence codes / signature verification) |

## Repository layout

```
backend/           Python backend
  api/             HTTP routes (app, publisher, remix, clone, qa, static)
  core/            config, database, task queue, credentials, licence, environment check, logs, diagnostics, ffmpeg lookup, single-instance lock
  modules/         downloader · publisher (scheduler + Playwright driver) · remix · clone · qa
  tools/           license_tool.py (issue licence codes)
  tests/           pytest tests (all offline)
  launcher.py      desktop entry point (port selection, single instance, starts the server)
frontend/          React UI (one file per feature under src/)
src-tauri/         Tauri shell, installer configuration, icons
scripts/           build.cmd / build.sh (package the backend) · tauri-build.ps1 (build the installer) · make_release.py · dev.cmd
assets/brand/      logo and icon sources
docs/              Student manual · Teacher manual · Feature & API reference (Chinese)
.github/workflows/ ci.yml (tests, build, secret scan) · release.yml (manual Windows + macOS installer build)
MavoBox-File/      ⚠ all keys and the backup manifest (licence-signing key, update-signing key and its password; git-ignored and never committed — back it up separately)
```

## Development

**Requirements**: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 18+, ffmpeg + ffprobe (on PATH, or set `FFMPEG_PATH`). Building the desktop installer also needs Rust and, on Windows, the Visual Studio Build Tools C++ workload. On Windows the `python` command may be the Microsoft Store placeholder, so always use `uv run`.

```powershell
# Backend dependencies + the browser needed for publishing
cd backend
uv sync
uv run playwright install chromium

# Frontend dependencies
cd ..\frontend
npm install
```

**Run (development mode)**: `scripts\dev.cmd` starts the backend (8000) and Vite (5173); open http://127.0.0.1:5173. The PATH at the top of the script points at the author's local uv / node folders — change that line on another machine. Or manually:

```powershell
cd backend
$env:TIKTOL_DEV = "1"      # let the backend accept the Vite dev server origin
uv run --no-sync uvicorn main:app --host 127.0.0.1 --port 8000
# in another terminal
cd frontend; npm run dev
```

Development mode does not require a licence code by default; set `TIKTOL_REQUIRE_LICENSE=1` to test the activation flow. Always keep `--host 127.0.0.1`; when publishing for real, run a single backend process and do not use `--reload`.

**Tests**:

```powershell
cd backend
uv run pytest -q          # baseline: 148 passed, 1 skipped (the skipped case needs symlink privileges)
cd ..\frontend
npm run build             # tsc type-check + vite build
```

The tests are fully offline: external APIs are mocked, and ffmpeg-related tests generate test videos on the fly and perform real renders.

### Environment variables

| Variable | Purpose |
|---|---|
| `TIKTOL_DATA_DIR` | Data directory. Default: Windows `%LOCALAPPDATA%\TikTolTool`, macOS `~/Library/Application Support/TikTolTool` (the old folder name is kept so existing data is not orphaned) |
| `TIKTOL_PORT` | Preferred port (default 8000; a free port is chosen if it is taken) |
| `TIKTOL_DEV` | `1` = additionally trust the Vite dev server (5173) origin |
| `TIKTOL_REQUIRE_LICENSE` | `1` = require a licence code outside packaged builds too (packaged builds always require it) |
| `TIKTOL_SERVE_UI` | `0` = do not serve the frontend static files from the backend |
| `TIKTOL_STATIC_DIR` | Frontend build directory (default `frontend/dist` or the bundled copy) |
| `FFMPEG_PATH` | Folder or file of ffmpeg / ffprobe (the bundled copy takes precedence) |
| `FFMPEG_BIN_DIR` | **Build time**: folder containing `ffmpeg` and `ffprobe`, bundled into the installer |
| `PLAYWRIGHT_BROWSERS_PATH` | Browser component location (packaged builds set it to `browsers` inside the data directory) |

## Build and release

```powershell
# 1) Package the backend (with the frontend static files and ffmpeg)
$env:FFMPEG_BIN_DIR = "D:\path\to\ffmpeg-bin"      # contains ffmpeg.exe and ffprobe.exe
scripts\build.cmd
# 2) In a normal terminal (not inside the Claude desktop app's sandbox) build the installer with update signing
powershell -ExecutionPolicy Bypass -File scripts\tauri-build.ps1
# Output: src-tauri\target\release\bundle\nsis\MAVO BOX_<version>_x64-setup.exe and its .sig
```

On macOS use `scripts/build.sh` + `npx @tauri-apps/cli@2 build --bundles dmg,app`, run on a Mac or a GitHub Actions macOS runner. Issuing licence codes, signing-key custody, and the format and hosting of the update manifest `latest.json` are covered in the **[Teacher manual](docs/教师手册.md)** (Chinese).

## Documentation

| Document | Audience | Contents |
|---|---|---|
| [Student manual](docs/学员手册.md) (Chinese) | Students | Install, activation, wizard, features, cost and compliance, FAQ |
| [Teacher manual](docs/教师手册.md) (Chinese) | Teachers / release owners | Issuing licence codes, key custody, releases and auto-update, pre-distribution checklist |
| [Feature & API reference](docs/功能与接口参考手册.md) (Chinese) | Developers / advanced users | Scheduler parameters, provider configuration formats and examples, budget and state machine, HTTP APIs |
| [AI subscriptions & gateways](docs/接入AI订阅与网关.md) (Chinese) | Students / advanced users | Connect multi-key pools or subscription accounts through a local gateway such as GPT-Load, risks, troubleshooting |
| [Changelog](CHANGELOG.md) | Everyone | Release notes |
| [Third-party notices](THIRD_PARTY_NOTICES.md) | Release owners | Dependency licences, ffmpeg provenance and checksums |
| [Security policy](SECURITY.md) | Everyone | How to report vulnerabilities |
| [Plan](PLAN.md) (Chinese) | Developers | The original research and plan (parts have since evolved) |

## Copyright

© 2026 SHINEVISTA GLOBAL GROUP LIMITED(HK). All rights reserved. This repository ships without an open-source licence; use requires authorisation. Third-party components remain under their own licences, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
