# Build the Tauri NSIS installer from a normal (non-virtualized) user shell.
# Prereqs: Rust (rustup), VS Build Tools (C++), Node, and `scripts\build.cmd` already run.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PATH = "$env:USERPROFILE\.cargo\bin;C:\Users\Captain\AppData\Local\hermes\node;$env:PATH"
cargo --version
$exe = Join-Path $root 'backend\dist\tiktol-server\tiktol-server.exe'
if (-not (Test-Path $exe)) { throw "Missing $exe - run scripts\build.cmd first" }
npx --yes "@tauri-apps/cli@2" build
Get-ChildItem -Recurse -Filter *.exe (Join-Path $root 'src-tauri\target\release\bundle') | Select-Object FullName, Length
