param(
  [string]$PythonExe = "",
  [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $PythonExe) {
  $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) {
    $PythonExe = $venvPython
  } else {
    $PythonExe = "python"
  }
}

$buildRoot = Join-Path $repoRoot ".build\pyinstaller"
$distRoot = Join-Path $buildRoot "dist"
$workRoot = Join-Path $buildRoot "work"
$specRoot = Join-Path $buildRoot "spec"
$binariesDir = Join-Path $repoRoot "frontend\src-tauri\binaries"
$finalBinary = Join-Path $binariesDir ("agente-backend-{0}.exe" -f $TargetTriple)

New-Item -ItemType Directory -Force -Path $buildRoot, $distRoot, $workRoot, $specRoot, $binariesDir | Out-Null

& $PythonExe -m pip install pyinstaller | Out-Host

& $PythonExe -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --bootloader-ignore-signals `
  --name "agente-backend" `
  --distpath $distRoot `
  --workpath $workRoot `
  --specpath $specRoot `
  --hidden-import "uvicorn.logging" `
  --hidden-import "uvicorn.loops.auto" `
  --hidden-import "uvicorn.protocols.http.auto" `
  --hidden-import "uvicorn.protocols.websockets.auto" `
  --hidden-import "uvicorn.lifespan.on" `
  --hidden-import "aiosqlite" `
  --hidden-import "keyring.backends.Windows" `
  --hidden-import "win32timezone" `
  (Join-Path $repoRoot "desktop_backend.py") | Out-Host

Copy-Item -Force (Join-Path $distRoot "agente-backend.exe") $finalBinary
Write-Host "Bundled backend sidecar created at $finalBinary"
