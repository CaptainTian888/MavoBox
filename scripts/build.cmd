@echo off
rem Build the packaged backend (onedir) with the frontend bundled. Output: backend\dist\mavobox-server\
setlocal
set "ROOT=%~dp0.."
set "PATH=C:\Users\Captain\AppData\Local\hermes\bin;C:\Users\Captain\AppData\Local\hermes\node;%PATH%"
pushd "%ROOT%\frontend" || exit /b 1
call npm run build || exit /b 1
popd
pushd "%ROOT%\backend" || exit /b 1
rem Set FFMPEG_BIN_DIR to a folder containing ffmpeg.exe + ffprobe.exe to bundle them (students need no install).
set "FFARGS="
if defined FFMPEG_BIN_DIR set FFARGS=--add-binary "%FFMPEG_BIN_DIR%\ffmpeg.exe;bin" --add-binary "%FFMPEG_BIN_DIR%\ffprobe.exe;bin"
if not defined FFMPEG_BIN_DIR echo WARNING: FFMPEG_BIN_DIR not set - ffmpeg will NOT be bundled
rem Every build fetches + verifies the LATEST GPT-Load release (cached when already current). The build FAILS if
rem the latest version cannot be confirmed, so an outdated gateway is never shipped silently.
rem (Set GPTLOAD_ALLOW_STALE=1 only for offline experiments: it reuses the local copy and warns loudly.)
set "STALEARG="
if "%GPTLOAD_ALLOW_STALE%"=="1" set "STALEARG=--allow-stale"
uv run --no-sync python "%ROOT%\scripts\fetch_gptload.py" %STALEARG% || exit /b 1
set "GWARGS=--add-binary "%ROOT%\vendor\gpt-load\gpt-load.exe;gateway" --add-data "%ROOT%\vendor\gpt-load\VERSION;gateway" --add-data "%ROOT%\vendor\gpt-load\licenses;gateway\licenses""
uv run --no-sync pyinstaller --noconfirm --clean --onedir --name mavobox-server %FFARGS% %GWARGS% ^
  --add-data "..\frontend\dist;web" ^
  --collect-all playwright --collect-all yt_dlp --collect-all scenedetect ^
  --collect-submodules keyring.backends --hidden-import win32timezone ^
  --collect-submodules uvicorn --collect-submodules api --collect-submodules core --collect-submodules modules ^
  launcher.py || exit /b 1
popd
echo Built: %ROOT%\backend\dist\mavobox-server\mavobox-server.exe
