# Pull the latest tick log + L2 data from Railway and run the pre-registered analyses.
# Read-only against Railway. Writes data/tick_log_pulled.jsonl.gz, data/l2_pulled/, and a dated
# report data/research_check_YYYYMMDD.txt. Run from the repo root:  powershell -File scripts\research_check.ps1
$ErrorActionPreference = "Continue"   # the Railway CLI prints a harmless banner on stderr
$env:Path += ";" + (npm prefix -g).Trim()
$py = ".\venv\Scripts\python.exe"
$noise = "Config as Code|Migrate|Using SSH|Existing files|^\s*$"

function Pull-B64($remoteCmd, $localPath) {
    $out = (railway ssh $remoteCmd 2>&1 | Where-Object { $_ -notmatch $noise } | Out-String).Trim()
    [IO.File]::WriteAllBytes($localPath, [Convert]::FromBase64String($out))
}

Write-Host "Pulling tick log..."
Pull-B64 "gzip -c data/tick_log.jsonl | base64 -w0" "data/tick_log_pulled.jsonl.gz"

Write-Host "Pulling L2 data..."
New-Item -ItemType Directory -Force data/l2_pulled | Out-Null
Pull-B64 "tar -czf - -C data l2 | base64 -w0" "data/l2_pulled/l2.tar.gz"
tar -xzf data/l2_pulled/l2.tar.gz -C data/l2_pulled

$report = "data/research_check_$(Get-Date -Format yyyyMMdd).txt"
$sections = @(
    @("== C: lead-lag (pre-registered) ==",   "scripts/lead_lag_test.py --ticks data/tick_log_pulled.jsonl.gz"),
    @("== T3 confirmation on executable L2 prices ==", "scripts/lead_lag_l2.py"),
    @("== Phase 1: arb scan ==",              "scripts/arb_scan.py --log data/l2_pulled/l2/arb_log.jsonl"),
    @("== Phase 2: maker replay ==",           'scripts/mm_replay.py --l2 "data/l2_pulled/l2/l2_*.jsonl*" --ticks data/tick_log_pulled.jsonl.gz'),
    @("== Phase 2 control: queue ignored ==", 'scripts/mm_replay.py --l2 "data/l2_pulled/l2/l2_*.jsonl*" --ticks data/tick_log_pulled.jsonl.gz --no-queue')
)
"Research check $(Get-Date -Format u)" | Out-File $report -Encoding utf8
foreach ($s in $sections) {
    $s[0] | Tee-Object -FilePath $report -Append
    (Invoke-Expression "$py $($s[1]) 2>&1" | Out-String) | Tee-Object -FilePath $report -Append
}
Write-Host "Report: $report"
