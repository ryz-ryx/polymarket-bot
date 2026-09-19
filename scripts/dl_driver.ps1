# Bulk driver for scripts/dl_trades.py: 2 stratified windows per hour over the last N days, pulled from the
# Railway container in chunks and stored locally as data/trades/chunk_<first_window>.csv.gz. Resumable
# (existing chunk files are skipped). Read-only on Railway. Run from the repo root:
#   powershell -NoProfile -File scripts\dl_driver.ps1 [-Days 14] [-Chunk 6]
param([int]$Days = 14, [int]$Chunk = 6, [int]$Seed = 42, [int]$PerHour = 2, [string]$OutDir = "data/trades")
$ErrorActionPreference = "Continue"
$env:Path += ";" + (npm prefix -g).Trim()
New-Item -ItemType Directory -Force $OutDir | Out-Null

$now = [int][double]::Parse((Get-Date -UFormat %s))
$end = ([int]([math]::Floor($now / 3600)) - 2) * 3600          # skip the last 2h: not finalized
$rng = New-Object System.Random $Seed                           # fixed seed: same sample every run
$windows = @()
for ($h = $end - $Days * 86400; $h -lt $end; $h += 3600) {
    $picks = 0..11 | Sort-Object { $rng.Next() } | Select-Object -First $PerHour | Sort-Object
    foreach ($p in $picks) { $windows += $h + 300 * $p }
}
Write-Host "windows: $($windows.Count)  chunks: $([math]::Ceiling($windows.Count / $Chunk))"

$src = [IO.File]::ReadAllText("scripts/dl_trades.py")
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($src))
$log = "$OutDir/driver.log"
for ($i = 0; $i -lt $windows.Count; $i += $Chunk) {
    $grp = $windows[$i..([math]::Min($i + $Chunk, $windows.Count) - 1)]
    $file = "$OutDir/chunk_$($grp[0]).csv.gz"
    if (Test-Path $file) { continue }
    $arg = ($grp -join ",")
    $raw = railway ssh "echo $b64 | base64 -d | python3 - $arg 0" 2>&1 | Where-Object { $_ -notmatch "Config as Code|Migrate|Using SSH|Existing files" }
    $idx = [array]::IndexOf($raw, "BEGIN")
    $end_i = [array]::IndexOf($raw, "END")
    $stat = ($raw | Where-Object { $_ -like "#STATS*" }) -join " "
    if ($idx -ge 0 -and $end_i -gt $idx) {
        [IO.File]::WriteAllBytes($file, [Convert]::FromBase64String(($raw[($idx + 1)..($end_i - 1)] -join "")))
        "$(Get-Date -Format s) OK  $file $stat" | Add-Content $log
    } else {
        "$(Get-Date -Format s) FAIL chunk starting $($grp[0]) $($raw | Select-Object -First 2)" | Add-Content $log
        Start-Sleep -Seconds 20
    }
}
Write-Host "done: $((Get-ChildItem "$OutDir/chunk_*.csv.gz").Count) chunks"
