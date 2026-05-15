# Export run metrics to JSONL or compare before/after snapshots.
param(
    [string]$Output = "reports/run_metrics.jsonl",
    [string]$Db,
    [string[]]$RequestId,
    [int]$Limit = 0,
    [switch]$Append,
    [switch]$IncludeWithoutCost,
    [string]$CompareBefore,
    [string]$CompareAfter,
    [string]$Summary
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pyArgs = @("scripts/export_run_metrics.py")

if ($CompareBefore -and $CompareAfter) {
    $pyArgs += @("--compare", $CompareBefore, $CompareAfter)
    if ($Output) { $pyArgs += @("--output", $Output) }
} elseif ($Summary) {
    $pyArgs += @("--summary", $Summary)
} else {
    $pyArgs += @("--output", $Output)
    if ($Db) { $pyArgs += @("--db", $Db) }
    if ($RequestId) { foreach ($id in $RequestId) { $pyArgs += @("--request-id", $id) } }
    if ($Limit -gt 0) { $pyArgs += @("--limit", $Limit) }
    if ($Append) { $pyArgs += "--append" }
    if ($IncludeWithoutCost) { $pyArgs += "--include-without-cost" }
}

python @pyArgs
exit $LASTEXITCODE
