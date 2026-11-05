# Diagnostic: does the NSIS installer relaunch the app after an in-place update (flags /P /R)?
param(
  [Parameter(Mandatory)][string]$OldInstaller,
  [Parameter(Mandatory)][string]$NewInstaller,
  [string]$Flags = '/P /R'
)
$ErrorActionPreference = 'Continue'
$inst = 'D:\mavobox-update-test'
function Cleanup {
  Get-Process mavo-box, mavobox-server -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  $un = Join-Path $inst 'uninstall.exe'
  if (Test-Path $un) { Start-Process $un -ArgumentList '/S' -Wait; Start-Sleep 3 }
  Remove-Item $inst -Recurse -Force -ErrorAction SilentlyContinue
}
try {
  Cleanup
  Start-Process $OldInstaller -ArgumentList '/S', "/D=$inst" -Wait
  Write-Host ("old installed: " + (Get-Item "$inst\mavo-box.exe").VersionInfo.ProductVersion)
  $env:TIKTOL_DATA_DIR = Join-Path $env:TEMP 'mavobox_relaunch_data'
  Start-Process "$inst\mavo-box.exe"
  Start-Sleep 12
  Write-Host ("app running before update: " + (Get-Process mavo-box -ErrorAction SilentlyContinue | Measure-Object).Count)
  # In the real flow the updater exits the app (and its backend) before the installer starts.
  Get-Process mavo-box, mavobox-server -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep 2
  Write-Host "== run NEW installer with flags: $Flags"
  # NOTE: `Start-Process -Wait` also waits for descendants, which would hang if the installer relaunches the app.
  $p = Start-Process $NewInstaller -ArgumentList ($Flags -split ' ') -PassThru
  $p.WaitForExit()
  Write-Host "installer exited with code $($p.ExitCode)"
  Write-Host ("new version on disk: " + (Get-Item "$inst\mavo-box.exe").VersionInfo.ProductVersion)
  foreach ($i in 1..10) {
    Start-Sleep 3
    $n = (Get-Process mavo-box -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host ("  +{0}s app processes: {1}" -f ($i * 3), $n)
    if ($n -gt 0) { break }
  }
} finally {
  Cleanup
  Remove-Item (Join-Path $env:TEMP 'mavobox_relaunch_data') -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host 'cleanup done'
}
