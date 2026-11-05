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
uv run pyinstaller --noconfirm --clean --onedir --name mavobox-server "${FF[@]}" \
  --add-data "../frontend/dist:web" \
  --collect-all playwright --collect-all yt_dlp --collect-all scenedetect \
  --collect-submodules keyring.backends \
  --collect-submodules uvicorn --collect-submodules api --collect-submodules core --collect-submodules modules \
  launcher.py
echo "Built: $ROOT/backend/dist/mavobox-server/mavobox-server"
