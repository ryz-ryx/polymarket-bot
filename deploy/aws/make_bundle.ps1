# Build polybundle.tar.gz containing ONLY what the server needs: the collector, the analysis scripts and the small
# helper modules. No .env, no keys, no data, no trading bot code. Run from the repo root:
#   powershell -File deploy\aws\make_bundle.ps1
$ErrorActionPreference = "Stop"
$items = @(
    "scripts/collect_l2.py", "scripts/health_check.py", "scripts/lead_lag_l2.py", "scripts/venue_lead_test.py",
    "scripts/mm_replay.py", "scripts/arb_scan.py", "scripts/hist_tests.py",
    "src/resolution.py", "src/mm", "deploy/aws/setup.sh"
)
if (Test-Path polybundle.tar.gz) { Remove-Item polybundle.tar.gz }
# Stage the files, then force Unix (LF) line endings on shell scripts: CRLF breaks bash on Ubuntu.
$stage = Join-Path $env:TEMP ("polybundle_stage_" + (Get-Date -Format HHmmss))
foreach ($i in $items) {
    $dest = Join-Path $stage $i
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Copy-Item $i $dest -Recurse -Force
}
Get-ChildItem $stage -Recurse -Filter *.sh | ForEach-Object {
    $text = [IO.File]::ReadAllText($_.FullName) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($_.FullName, $text, (New-Object Text.UTF8Encoding($false)))
}
tar --exclude=__pycache__ -czf polybundle.tar.gz -C $stage .
Write-Host "Built polybundle.tar.gz ($([math]::Round((Get-Item polybundle.tar.gz).Length / 1KB)) KB). Contents:"
tar -tzf polybundle.tar.gz
