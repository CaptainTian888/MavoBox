#!/usr/bin/env bash
# macOS / Linux counterpart of build.cmd: build the frontend, then package the backend with PyInstaller.
# Output: backend/dist/mavobox-server/  (bundled into the Tauri app by src-tauri/tauri.conf.json)
# Optional: FFMPEG_BIN_DIR=/path/with/ffmpeg+ffprobe to bundle them (students then need no install).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
(cd "$ROOT/frontend" && npm ci && npm run build)
cd "$ROOT/backend"
FF=()
if [[ -n "${FFMPEG_BIN_DIR:-}" ]]; then
  FF=(--add-binary "$FFMPEG_BIN_DIR/ffmpeg:bin" --add-binary "$FFMPEG_BIN_DIR/ffprobe:bin")
else
  echo "WARNING: FFMPEG_BIN_DIR not set - ffmpeg will NOT be bundled" >&2
fi
uv sync
# Always fetch + verify the LATEST GPT-Load (fails the build if the latest version cannot be confirmed).
# GPTLOAD_PLATFORM: macos-arm64 (default) | macos-amd64 | linux-amd64 | linux-arm64
PLATFORM="${GPTLOAD_PLATFORM:-macos-arm64}"
STALE=(); [[ "${GPTLOAD_ALLOW_STALE:-}" == "1" ]] && STALE=(--allow-stale)
uv run python ../scripts/fetch_gptload.py --platform "$PLATFORM" "${STALE[@]}"
chmod +x ../vendor/gpt-load/gpt-load
uv run pyinstaller --noconfirm --clean --onedir --name mavobox-server "${FF[@]}" \
  --add-binary "../vendor/gpt-load/gpt-load:gateway" --add-data "../vendor/gpt-load/VERSION:gateway" \
  --add-data "../vendor/gpt-load/licenses:gateway/licenses" \
  --add-data "../frontend/dist:web" \
  --collect-all playwright --collect-all yt_dlp --collect-all scenedetect \
  --collect-submodules keyring.backends \
  --collect-submodules uvicorn --collect-submodules api --collect-submodules core --collect-submodules modules \
  launcher.py
echo "Built: $ROOT/backend/dist/mavobox-server/mavobox-server"
