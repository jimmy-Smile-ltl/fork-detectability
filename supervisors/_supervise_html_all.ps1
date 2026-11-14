# Supervisor for the HTML profile collector (scripts/collect/_profile_html_all.py).
# Reason: measured 2026-11-13 - one process kept running 11 h degraded from
# 206/min to 9/min. Restarting every N minutes restores full speed; progress is
# kept in the output JSONL so restarts are lossless.
# NOTE: keep ASCII-only (Windows PowerShell 5.1 reads .ps1 as ANSI).
param(
    [string]$From = "data/raw/openclaw_forkers_priority.txt",
    [string]$Out = "data/raw/profile_html_openclaw.jsonl",
    [int]$Workers = 10,
    [int]$RunMin = 15,
    [int]$MaxRuns = 500,
    [string]$Proxy = "http://127.0.0.1:7898"
)

$ErrorActionPreference = "Continue"
$run = 0

while ($run -lt $MaxRuns) {
    $run++
    $lines = 0
    if (Test-Path $Out) { $lines = (Get-Content $Out -ReadCount 0).Count }
    Write-Output ("[htmlall] run {0} | file lines {1}" -f $run, $lines)

    $a = @("scripts/collect/_profile_html_all.py", "--from-file", $From, "--out", $Out,
           "--workers", $Workers, "--proxy", $Proxy)
    $proc = Start-Process -FilePath "py" -ArgumentList $a -PassThru -NoNewWindow
    Write-Output ("[htmlall] started pid {0}, will run {1} min" -f $proc.Id, $RunMin)

    $started = Get-Date
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 20
        $elapsedMin = ((Get-Date) - $started).TotalMinutes
        if ($elapsedMin -gt $RunMin) {
            Write-Output "[htmlall] planned restart"
            try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
            break
        }
    }
    Start-Sleep -Seconds 3
}
Write-Output ("[htmlall] finished after {0} runs" -f $run)
