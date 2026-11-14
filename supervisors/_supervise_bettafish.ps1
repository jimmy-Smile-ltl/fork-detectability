$py = "C:\Windows\py.exe"
$wd = "C:\Users\JimmySmile\Documents\Python code\Spider\开源 整理\git_repo_fake"
$log = Join-Path $wd "_bf_supervisor.log"
function Round-Info($msg) { "[$(Get-Date -Format HH:mm:ss)] $msg" | Tee-Object -FilePath $log -Append }

Round-Info "=== 第 1 遍：7 个维度（不含 D13）==="
for ($i=1; $i -le 30; $i++) {
    $before = (Get-Content (Join-Path $wd "data/raw/profile_tier_screen_bettafish_all.jsonl") -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
    if ($before -ge 7600) { Round-Info "D1 已 $before 条，第1遍完成"; break }
    $p = Start-Process -FilePath $py -ArgumentList "scripts/collect/collect_run.py","--repo","666ghj/BettaFish","--mode","per_account","--workers","32","--dims","D1,D15,D8H,D9,D14,D2,D4L","--max-minutes","8" -WorkingDirectory $wd -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $wd "_bf_p1.log") -RedirectStandardError (Join-Path $wd "_bf_p1.err")
    $p | Wait-Process -Timeout 600 -ErrorAction SilentlyContinue
    if (-not $p.HasExited) { $p | Stop-Process -Force -ErrorAction SilentlyContinue }
    $after = (Get-Content (Join-Path $wd "data/raw/profile_tier_screen_bettafish_all.jsonl") -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
    Round-Info "轮$i : D1 $before -> $after (+$($after-$before))"
}
Round-Info "=== 第 2 遍：补 D13（低并发 6）==="
for ($i=1; $i -le 20; $i++) {
    $before = (Get-Content (Join-Path $wd "data/raw/heat_bettafish_all.jsonl") -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
    if ($before -ge 7000) { Round-Info "D13 已 $before 条，完成"; break }
    $p = Start-Process -FilePath $py -ArgumentList "scripts/collect/collect_run.py","--repo","666ghj/BettaFish","--mode","per_dim","--workers","6","--dims","D13","--max-minutes","10" -WorkingDirectory $wd -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $wd "_bf_p2.log") -RedirectStandardError (Join-Path $wd "_bf_p2.err")
    $p | Wait-Process -Timeout 720 -ErrorAction SilentlyContinue
    if (-not $p.HasExited) { $p | Stop-Process -Force -ErrorAction SilentlyContinue }
    $after = (Get-Content (Join-Path $wd "data/raw/heat_bettafish_all.jsonl") -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
    Round-Info "D13 轮$i : $before -> $after (+$($after-$before))"
}
Round-Info "全部完成"
