@echo off
setlocal
set "ROOT=%~dp0.."
set "PATH=C:\Users\Captain\AppData\Local\hermes\bin;C:\Users\Captain\AppData\Local\hermes\node;%PATH%"
if not exist "%ROOT%\backend\.venv" (
  echo Please run uv sync in backend first.
  exit /b 1
)
if not exist "%ROOT%\frontend\node_modules" (
  echo Please run npm i in frontend first.
  exit /b 1
)
start "MAVO BOX backend" /min cmd /k "cd /d ""%ROOT%\backend"" && set TIKTOL_DEV=1&& uv run --no-sync uvicorn main:app --host 127.0.0.1 --port 8000"
start "MAVO BOX frontend" /min cmd /k "cd /d ""%ROOT%\frontend"" && npm run dev"
echo Open http://127.0.0.1:5173 . Close both server windows to stop.
