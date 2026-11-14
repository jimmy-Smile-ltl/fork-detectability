# Supervisor: restart the collector automatically when it stalls, until done.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 as ANSI,
# so non-ASCII text breaks parsing (tested 2026-11-10).
param(
    [string]$Tier = "screen",
    [string]$From = "data/raw/openclaw_forkers_priority.txt",
    [string]$Suffix = "_openclaw_all",
    [int]$Target = 82574,
    [int]$StallMin = 15,
    [int]$Tolerance = 600,
    [int]$Workers = 4,
    [int]$Attempts = 4,
    [int]$RestartEveryMin = 0,
    [int]$IpNum = 4,
    [string]$ExtraArgs = "",
    [int]$MaxRuns = 30
)

$ErrorActionPreference = "Continue"
$file = "data\raw\profile_tier_${Tier}${Suffix}.jsonl"
$run = 0

function Get-Pending($tier, $suffix, $target) {
    $out = & py scripts/ops/_pending.py --file "data/raw/profile_tier_${tier}${suffix}.jsonl" --target $target 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return [int]$out } else { return -1 }
}

while ($run -lt $MaxRuns) {
    $run++
    $pending = Get-Pending $Tier $Suffix $Target
    Write-Output ("[supervise] run {0} | pending {1} / {2}" -f $run, $pending, $Target)
    if ($pending -ge 0 -and $pending -le $Tolerance) {
        Write-Output ("[supervise] pending {0} <= tolerance {1}, done" -f $pending, $Tolerance)
        break
    }

    $a = @("scripts/collect/_profiles_parallel.py", "--tier", $Tier, "--from-file", $From,
           "--workers", $Workers, "--out-suffix", $Suffix,
           "--ip-num", $IpNum, "--attempts", $Attempts)
    if ($ExtraArgs) { $a += $ExtraArgs.Split(" ") }
    $proc = Start-Process -FilePath "py" -ArgumentList $a -PassThru -NoNewWindow
    Write-Output ("[supervise] started pid {0}" -f $proc.Id)

    $started = Get-Date
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 60
        if (Test-Path $file) {
            $age = ((Get-Date) - (Get-Item $file).LastWriteTime).TotalMinutes
            if ($age -gt $StallMin) {
                Write-Output ("[supervise] stalled {0:N0} min -> kill and restart" -f $age)
                try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
                break
            }
        }
        # 进程跑久了会渐进退化成个位数/分钟；定时重启能一直保持高吞吐
        if ($RestartEveryMin -gt 0 -and ((Get-Date) - $started).TotalMinutes -gt $RestartEveryMin) {
            Write-Output ("[supervise] ran {0} min -> planned restart" -f $RestartEveryMin)
            try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
            break
        }
    }
    Start-Sleep -Seconds 20
}
Write-Output ("[supervise] finished after {0} runs" -f $run)
