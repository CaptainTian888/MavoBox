# End-to-end test of the in-app update flow on THIS machine (run from a normal user terminal).
#   powershell -ExecutionPolicy Bypass -File scripts\test-update.ps1 -OldInstaller <0.1.0 setup.exe> -FeedDir <folder with latest.json + 0.1.1 setup.exe + .sig>
# It installs OLD into a throwaway folder, serves the feed on 127.0.0.1:8899, starts the app with
# MAVOBOX_UPDATE_URL / MAVOBOX_UPDATE_SILENT, waits for the app exe to become the NEW version,
# then uninstalls everything and removes the folders. Requires builds made with src-tauri\test-*.json
# (plain-http feed allowed). Nothing here is used by the real release.
param(
  [Parameter(Mandatory)][string]$OldInstaller,
  [Parameter(Mandatory)][string]$FeedDir,
  [string]$ExpectOld = '0.1.0',
  [string]$ExpectNew = '0.1.1',
  [int]$TimeoutSeconds = 240
)
$ErrorActionPreference = 'Stop'
$inst = 'D:\mavobox-update-test'
$data = Join-Path $env:TEMP 'mavobox_update_data'
$port = 8899
$server = $null
# Windows' `python` is often the Store placeholder; use the project's own venv interpreter.
$py = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "missing $py - run uv sync in backend first" }
function Version($p) { if (Test-Path $p) { (Get-Item $p).VersionInfo.ProductVersion } else { '(missing)' } }
function Cleanup {
  Get-Process mavo-box, mavobox-server -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
  $un = Join-Path $inst 'uninstall.exe'
  if (Test-Path $un) { Start-Process $un -ArgumentList '/S' -Wait; Start-Sleep 3 }
  Remove-Item $inst -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $data -Recurse -Force -ErrorAction SilentlyContinue
}
try {
  Cleanup
  Write-Host "== start feed server on 127.0.0.1:$port"
  $server = Start-Process -PassThru -WindowStyle Hidden $py -ArgumentList '-m', 'http.server', $port, '--bind', '127.0.0.1', '--directory', $FeedDir
  Start-Sleep 2
  try { $null = Invoke-WebRequest "http://127.0.0.1:$port/latest.json" -UseBasicParsing } catch { throw 'feed server not reachable' }
  Write-Host "== install OLD silently into $inst"
  Start-Process $OldInstaller -ArgumentList '/S', "/D=$inst" -Wait
  $exe = Join-Path $inst 'mavo-box.exe'
  Write-Host "installed version: $(Version $exe) (expected $ExpectOld)"
  if ((Version $exe) -ne $ExpectOld) { throw 'unexpected installed version' }
  $env:MAVOBOX_UPDATE_URL = "http://127.0.0.1:$port/latest.json"
  $env:MAVOBOX_UPDATE_SILENT = '1'
  $env:TIKTOL_DATA_DIR = $data
  Write-Host '== launch app (it should find, download, verify and install the update)'
  Start-Process $exe
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $updated = $false
  while ((Get-Date) -lt $deadline) {
    Start-Sleep 1
    $v = Version $exe
    $names = (Get-Process | Where-Object { $_.Name -like 'mavo*' -or $_.Name -like '*installer*' } | ForEach-Object { "$($_.Name)#$($_.Id)" }) -join ','
    Write-Host ("  t+{0}s version={1} procs=[{2}]" -f [int]($TimeoutSeconds - ($deadline - (Get-Date)).TotalSeconds), $v, $names)
    if ($v -eq $ExpectNew) { $updated = $true; break }
  }
  if (-not $updated) { throw "app exe never became $ExpectNew" }
  Write-Host '== updated. watching for the relaunched app (60s)'
  foreach ($i in 1..30) {
    Start-Sleep 2
    $names = (Get-Process | Where-Object { $_.Name -like 'mavo*' -or $_.Name -like '*installer*' } | ForEach-Object { "$($_.Name)#$($_.Id)" }) -join ','
    Write-Host ("  +{0}s procs=[{1}]" -f ($i * 2), $names)
  }
  try { Write-Host ('backend health: ' + (Invoke-RestMethod "http://127.0.0.1:8000/api/health").status) } catch { Write-Host 'backend health: not reachable' }
  Write-Host 'RESULT: UPDATE FLOW OK'
} catch {
  Write-Host "RESULT: FAILED - $($_.Exception.Message)"
} finally {
  Write-Host '== cleanup (uninstall + remove test folders)'
  Cleanup
  Write-Host 'cleanup done'
}
