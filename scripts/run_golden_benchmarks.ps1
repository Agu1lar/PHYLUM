# Run local golden tasks and domain benchmarks (offline-safe).
param(
    [string[]]$Domain,
    [string[]]$Tag,
    [switch]$SkipRequires
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$args = @("evaluation/golden_runner.py")
if ($Domain) { foreach ($d in $Domain) { $args += @("--domain", $d) } }
if ($Tag) { foreach ($t in $Tag) { $args += @("--tag", $t) } }
if ($SkipRequires) { $args += "--skip-requires" }

python @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python evaluation/intent_golden_runner.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m pytest tests/test_golden_tasks.py tests/test_domain_benchmarks.py tests/test_intent_golden.py -m "golden or benchmark" -q --tb=short
exit $LASTEXITCODE
