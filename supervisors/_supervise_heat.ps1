# Supervisor for the contributions-calendar (D13) collectors.
# Restarts the collector every N minutes because a long-running process
# degrades from ~250/min to <10/min after 10-30 minutes (observed 2026-11-10).
# Progress is kept in the output JSONL, so restarting is free.
# NOTE: keep ASCII-only (Windows PowerShell 5.1 reads .ps1 as ANSI).
param(
    [string]$From = "data/raw/openclaw_forkers_priority.txt",
    [string]$Out = "data/raw/heat_openclaw_all.jsonl",
    [int]$Workers = 5,
    [int]$RunMin = 8,
    [int]$MaxRuns = 200,
    [string]$Proxy = "http://127.0.0.1:7898"
)

$ErrorActionPreference = "Continue"
$run = 0

while ($run -lt $MaxRuns) {
    $run++
    $lines = 0
    if (Test-Path $Out) { $lines = (Get-Content $Out | Measure-Object -Line).Lines }
    Write-Output ("[heat] run {0} | file lines {1}" -f $run, $lines)

    $a = @("scripts/collect/_profile_heat.py", "--from-file", $From, "--years", "2026",
           "--workers", $Workers, "--delay", "0.05", "--proxy", $Proxy,
           "--switch-every", "0", "--out", $Out)
    $proc = Start-Process -FilePath "py" -ArgumentList $a -PassThru -NoNewWindow
    Write-Output ("[heat] started pid {0}, will run {1} min" -f $proc.Id, $RunMin)

    $started = Get-Date
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 30
        if (((Get-Date) - $started).TotalMinutes -gt $RunMin) {
            Write-Output "[heat] planned restart"
            try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
            break
        }
    }
    Start-Sleep -Seconds 5
}
Write-Output ("[heat] finished after {0} runs" -f $run)
