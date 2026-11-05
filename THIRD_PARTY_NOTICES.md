# Third-party notices

MAVO BOX bundles or depends on the following open-source software. Each remains under its own licence;
the notes below are a summary, not a substitute for the licence texts shipped by those projects.

| Component | Licence | Notes |
|---|---|---|
| FFmpeg / FFprobe (bundled binaries) | **GPL-2.0-or-later / GPL-3.0** (builds with libx264) | Distributed as separate executables inside the app folder, unmodified. Source code and build scripts are available from the project/builder you obtained them from (see below). When you distribute MAVO BOX with these binaries you must pass the GPL notice on and offer the corresponding source. |
| yt-dlp | Unlicense | Python library used for downloading. |
| Playwright (Python) / Chromium | Apache-2.0 / BSD-style | Chromium is downloaded on the user's machine at first use, not redistributed. |
| FastAPI, Starlette, Uvicorn, Pydantic, httpx | MIT / BSD-3 | |
| keyring, cryptography | MIT / Apache-2.0 or BSD | |
| PySceneDetect, OpenCV (headless) | BSD-3 / Apache-2.0 | |
| React, Vite | MIT | |
| Tauri, tauri-plugin-updater, tauri-plugin-dialog, windows-sys | MIT or Apache-2.0 | |
| PyInstaller | GPL-2.0 with bootloader exception | The exception permits bundling in non-GPL applications. |

**FFmpeg source (Windows build shipped):** `ffmpeg-9.0.2-essentials_build` by gyan.dev
(SHA-256 `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba`, matches the checksum published at
`https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip.sha256`). Licence text: `vendor/ffmpeg-win/LICENSE`
(GPL-3.0, configured with `--enable-gpl --enable-version3`). Corresponding source: https://ffmpeg.org/releases/ (ffmpeg-9.0.2)
and the build recipes at https://www.gyan.dev/ffmpeg/builds/. Keep this entry updated whenever the bundled build changes.
macOS builds must record their own ffmpeg vendor/version here.

Quality-check and diagnostics ideas (verify what was produced; loudness targets; a copyable support report) were inspired by the public
OpenFilm project (MIT, https://github.com/openfilm/openfilm). No OpenFilm code is included.

Design ideas for the clone workflow were studied from public methodology of the Hypit project; no Hypit
source code or documentation text is included (its licence restricts commercial redistribution of derivatives).
