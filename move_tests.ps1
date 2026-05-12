# Move all test_*.py files into tests/ directory
$srcPattern = Join-Path $PSScriptRoot 'test_*.py'
$destDir = Join-Path $PSScriptRoot 'tests'
New-Item -ItemType Directory -Force -Path $destDir | Out-Null
Get-ChildItem -Path $srcPattern -File | ForEach-Object {
    Move-Item -Path $_.FullName -Destination $destDir -Force
}
Write-Host "Moved test_*.py files to tests/"
