@echo off
rem Build the packaged backend (onedir) with the frontend bundled. Output: backend\dist\mavobox-server\
setlocal
set "ROOT=%~dp0.."
set "PATH=C:\Users\Captain\AppData\Local\hermes\bin;C:\Users\Captain\AppData\Local\hermes\node;%PATH%"
pushd "%ROOT%\frontend" || exit /b 1
call npm run build || exit /b 1
popd
pushd "%ROOT%\backend" || exit /b 1
uv run --no-sync pyinstaller --noconfirm --clean --onedir --name mavobox-server ^
  --add-data "..\frontend\dist;web" ^
  --collect-all playwright --collect-all yt_dlp --collect-all scenedetect ^
  --collect-submodules keyring.backends --hidden-import win32timezone ^
  --collect-submodules uvicorn --collect-submodules api --collect-submodules core --collect-submodules modules ^
  launcher.py || exit /b 1
popd
echo Built: %ROOT%\backend\dist\mavobox-server\mavobox-server.exe
