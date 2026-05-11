<#
Safe helper to remove the 'venv' directory (not .venv). Use with caution.
#>
param(
    [switch]$Force
)

$target = Join-Path $PSScriptRoot 'venv'
if (-not (Test-Path $target)) {
    Write-Host "No 'venv' directory found at $target"
    exit 0
}

if (-not $Force) {
    $confirm = Read-Host "This will REMOVE the directory '$target'. Type 'yes' to confirm"
    if ($confirm -ne 'yes') {
        Write-Host "Aborted by user"
        exit 1
    }
}

Write-Host "Removing $target ..."
Remove-Item -Recurse -Force -LiteralPath $target
Write-Host "Removed 'venv'"
