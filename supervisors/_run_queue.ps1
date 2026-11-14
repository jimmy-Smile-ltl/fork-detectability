# 采集队列：按顺序把多个「单指标补充档」跑完。
#
# 背景：D1/D8/D13 之外还有 D2/D3/D4/D6 等单元未采。用户会续费快代理，
# 所以这里做成**可长期运行**的队列：每一阶段跑完自动进下一阶段，
# 中途断了重启本脚本即可续跑（每个阶段都有自己的状态与 pending 判定）。
#
# 用法：
#   powershell -File _run_queue.ps1                 # 从第 1 阶段开始
#   powershell -File _run_queue.ps1 -StartAt 3      # 从第 3 阶段开始
#   powershell -File _run_queue.ps1 -Only 2,4       # 只跑第 2、4 阶段
#
# NOTE: keep ASCII-only (Windows PowerShell 5.1 reads .ps1 as ANSI).

param(
    [int]$StartAt = 1,
    [int[]]$Only = @(),
    [int]$Workers = 6,
    [int]$Attempts = 3,
    [int]$StallMin = 12,
    [int]$Tolerance = 500,
    [int]$RestartEveryMin = 8,
    [int]$MaxRounds = 40
)

$ErrorActionPreference = "Continue"

# phase: name | tier | from | suffix | target | extra
$phases = @(
    @{ n = "openclaw D1 finish";   t = "screen";   f = "data/raw/openclaw_forkers_priority.txt"; s = "_openclaw_all";  g = 82574;  x = "" },
    # ---- 以下为 API 队列（2026-11-12 用户定优先级）----
    # 已删除 D4 stars / D6 orgs：这两项现在由 D15（个人主页 HTML）免费提供，
    # 再调 API 就是浪费配额。D4 只在需要 >30 的精确值时单独补。
    # 1) D4L star 明细+时间（用户明确要）
    @{ n = "D4L starlist rea";     t = "starlist"; f = "data/raw/rea_forkers_priority.txt";      s = "_rea_starlist"; g = 108340; x = "--d4l-pages 3" },
    @{ n = "D4L starlist openclaw"; t = "starlist"; f = "data/raw/openclaw_forkers_priority.txt"; s = "_openclaw_starlist"; g = 82574; x = "--d4l-pages 3" },
    # 2) D2 事件流
    @{ n = "D2 events rea";        t = "events";   f = "data/raw/rea_forkers_priority.txt";      s = "_rea_events";   g = 108340; x = "--d2-pages 1" },
    @{ n = "D2 events openclaw";   t = "events";   f = "data/raw/openclaw_forkers_priority.txt"; s = "_openclaw_events"; g = 82574; x = "--d2-pages 1" },
    # 3) D3 仓计数（价值低：仓数 HTML 已有；只有 newest_push 是新增）
    @{ n = "D3 repocount rea";     t = "repocount"; f = "data/raw/rea_forkers_priority.txt";     s = "_rea_repos";    g = 108340; x = "" },
    @{ n = "D3 repocount openclaw"; t = "repocount"; f = "data/raw/openclaw_forkers_priority.txt"; s = "_openclaw_repos"; g = 82574; x = "" },
    # 4) D5 watch（用户：意义不大，靠后）
    @{ n = "D5 watch rea";         t = "watch";    f = "data/raw/rea_forkers_priority.txt";      s = "_rea_watch";    g = 108340; x = "" },
    @{ n = "D5 watch openclaw";    t = "watch";    f = "data/raw/openclaw_forkers_priority.txt"; s = "_openclaw_watch"; g = 82574; x = "" }
)

function Get-Pending($file, $target) {
    $out = & py scripts/ops/_pending.py --file $file --target $target 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return [int]$out } else { return -1 }
}

# 配额感知：探一个出口的 X-RateLimit-Remaining；为 0 就等下一轮 IP 刷新，
# 避免整轮请求全打 403（2026-11-12 实测：配额被打空后 50 次全失败）。
function Test-Quota {
    $out = & py scripts/ops/_quota_check.py 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return [int]$out } else { return -1 }
}

for ($i = 0; $i -lt $phases.Count; $i++) {
    $idx = $i + 1
    if ($idx -lt $StartAt) { continue }
    if ($Only.Count -gt 0 -and ($Only -notcontains $idx)) { continue }

    $p = $phases[$i]
    $file = "data/raw/profile_tier_" + $p.t + $p.s + ".jsonl"
    Write-Output ("=== [phase {0}/{1}] {2} ===" -f $idx, $phases.Count, $p.n)

    $round = 0
    $ran = 0          # 真正启动采集器的次数（配额等待不算，否则会误触发上限跳过阶段）
    while ($ran -lt $MaxRounds) {
        $round++
        $pending = Get-Pending $file $p.g
        Write-Output ("[{0}] round {1} | pending {2} / {3}" -f $p.n, $round, $pending, $p.g)
        if ($pending -ge 0 -and $pending -le $Tolerance) {
            Write-Output ("[{0}] done (pending {1})" -f $p.n, $pending)
            break
        }
        if ($pending -lt 0) {
            # -1 通常只是"该阶段还没产出文件"，不代表失败：按全量待采处理，继续跑
            $pending = $p.g
        }

        # 配额检查：IP 是随机抽的，单次探到 0 不代表全池枯竭
        # → 快速连探 3 次（间隔 20s），任一次有额度就开跑；全 0 才长等
        $q = 0
        for ($k = 1; $k -le 3; $k++) {
            $q = Test-Quota
            if ($q -gt 0) { break }
            if ($k -lt 3) { Start-Sleep -Seconds 20 }
        }
        if ($q -le 0) {
            Write-Output ("[{0}] quota exhausted after 3 probes, wait 5 min" -f $p.n)
            Start-Sleep -Seconds 300
            continue
        }
        Write-Output ("[{0}] quota {1} -> go" -f $p.n, $q)

        $a = @("scripts/collect/_profiles_parallel.py", "--tier", $p.t, "--from-file", $p.f,
               "--workers", $Workers, "--out-suffix", $p.s,
               "--ip-num", "4", "--attempts", $Attempts)
        if ($p.x) { $a += $p.x.Split(" ") }
        $proc = Start-Process -FilePath "py" -ArgumentList $a -PassThru -NoNewWindow
        $ran++
        Write-Output ("[{0}] started pid {1} (run {2})" -f $p.n, $proc.Id, $ran)

        $started = Get-Date
        while (-not $proc.HasExited) {
            Start-Sleep -Seconds 30
            if (Test-Path $file) {
                $age = ((Get-Date) - (Get-Item $file).LastWriteTime).TotalMinutes
                if ($age -gt $StallMin) {
                    Write-Output ("[{0}] stalled {1:N0} min -> restart" -f $p.n, $age)
                    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
                    break
                }
            }
            if ($RestartEveryMin -gt 0 -and ((Get-Date) - $started).TotalMinutes -gt $RestartEveryMin) {
                try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
                break
            }
        }
        Start-Sleep -Seconds 5
    }
}
Write-Output "=== queue finished ==="
