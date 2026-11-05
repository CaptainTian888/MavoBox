param([string]$Config = '')
# Build the Tauri NSIS installer from a normal (non-virtualized) user shell.
# Prereqs: Rust (rustup), VS Build Tools (C++), Node, and `scripts\build.cmd` already run.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PATH = "$env:USERPROFILE\.cargo\bin;C:\Users\Captain\AppData\Local\hermes\node;$env:PATH"
cargo --version
$exe = Join-Path $root 'backend\dist\mavobox-server\mavobox-server.exe'
if (-not (Test-Path $exe)) { throw "Missing $exe - run scripts\build.cmd first" }
# Sign the updater artifacts (.sig) with the private updater key unless the caller already set one.
$key = Join-Path $root 'MavoBox-File\mavobox_updater.key'
if (-not $env:TAURI_SIGNING_PRIVATE_KEY -and (Test-Path $key)) {
  $env:TAURI_SIGNING_PRIVATE_KEY = $key
  $pw = Join-Path $root 'MavoBox-File\updater_key_password.txt'
  if (-not $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -and (Test-Path $pw)) { $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = (Get-Content -Raw $pw).Trim() }
}
if ($Config) { npx --yes "@tauri-apps/cli@2" build --config $Config } else { npx --yes "@tauri-apps/cli@2" build }
Get-ChildItem -Recurse -Filter *.exe (Join-Path $root 'src-tauri\target\release\bundle') | Select-Object FullName, Length
