# Helper: create .github\workflows and move ci_github.yml into place
$src = Join-Path $PSScriptRoot 'ci_github.yml'
$destDir = Join-Path $PSScriptRoot '.github\workflows'
$dest = Join-Path $destDir 'ci.yml'
if (-not (Test-Path $src)) {
    Write-Error "Source $src not found. Ensure ci_github.yml exists in repo root."
    exit 1
}
New-Item -ItemType Directory -Force -Path $destDir | Out-Null
Move-Item -Path $src -Destination $dest -Force
Write-Host "Moved ci_github.yml -> .github\workflows\ci.yml"