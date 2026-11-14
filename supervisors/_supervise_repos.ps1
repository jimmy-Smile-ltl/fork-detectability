# Supervisor for the HTML repo-structure collector (scripts/collect/_profile_repos_html.py).
# Same reason as _supervise_heat.ps1: a long-running process degrades from
# ~400/min to a few per minute. Restarting every N minutes keeps it fast;
# progress is preserved in the output JSONL.
# NOTE: keep ASCII-only (Windows PowerShell 5.1 reads .ps1 as ANSI).
param(
    [string]$From = "data/raw/openclaw_forkers_priority.txt",
    [string]$Out = "data/raw/repos_html_openclaw.jsonl",
    [string]$D1File = "data/raw/profile_tier_screen_openclaw_all.jsonl",
    [int]$Workers = 8,
    [int]$MaxPages = 3,
    [int]$RunMin = 5,
    [int]$MaxRuns = 400,
    [string]$Proxy = "http://127.0.0.1:7898"
)

$ErrorActionPreference = "Continue"
$run = 0

while ($run -lt $MaxRuns) {
    $run++
    $lines = 0
    if (Test-Path $Out) { $lines = (Get-Content $Out | Measure-Object -Line).Lines }
    Write-Output ("[repos] run {0} | file lines {1}" -f $run, $lines)

    $a = @("scripts/collect/_profile_repos_html.py", "--from-file", $From, "--out", $Out,
           "--workers", $Workers, "--max-pages", $MaxPages, "--proxy", $Proxy)
    if (Test-Path $D1File) { $a += @("--d1-file", $D1File) }
    $proc = Start-Process -FilePath "py" -ArgumentList $a -PassThru -NoNewWindow
    Write-Output ("[repos] started pid {0}, will run {1} min" -f $proc.Id, $RunMin)

    $started = Get-Date
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 20
        if (((Get-Date) - $started).TotalMinutes -gt $RunMin) {
            Write-Output "[repos] planned restart"
            try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
            break
        }
    }
    Start-Sleep -Seconds 3
}
Write-Output ("[repos] finished after {0} runs" -f $run)
