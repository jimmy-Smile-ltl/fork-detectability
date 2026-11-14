# -*- coding: utf-8 -*-
"""批处理驱动：用「子进程 + 硬超时」绕过线程卡死。

为什么需要它
------------
隧道连接会**挂住**而不是超时返回（实测 p95 = 超时值）。
`ThreadPoolExecutor` 里的线程一旦卡住就无法回收，
32 个 worker 会在 10–20 分钟内全部被吃掉 —— 表现为"进程活着但进度不动"。

解法：**把批次交给子进程，超时就整进程杀掉**。
已落盘的终态不会重跑，所以杀进程只损失正在飞行的几个请求。

用法
----
    py scripts/collect/collect_loop.py --repo 666ghj/BettaFish \
        --dims D1,D8H,D4L,D9,D14,D2 --workers 32 --batches 40

每轮跑 `--batch-seconds` 秒，然后杀掉重启，直到没有新进展。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def count_target(tag: str, dim: str) -> int:
    """该维度已有多少有效（OK/EMPTY）账号 —— 唯一可信的进度指标。"""
    import json
    from collect_core import dim_path
    p = dim_path(dim, tag, os.path.join(BASE, "data", "raw"))
    if not os.path.exists(p):
        return 0
    seen = set()
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("outcome") in ("OK", "EMPTY") and r.get("login"):
            seen.add(r["login"].lower())
    return len(seen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dims", required=True)
    ap.add_argument("--mode", default="per_account", choices=["per_account","per_dim"])
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--batches", type=int, default=60, help="最多跑多少轮")
    ap.add_argument("--batch-seconds", type=int, default=240,
                    help="每轮最多跑多久（到点杀进程）")
    ap.add_argument("--target", type=int, default=0, help="达到多少人后停（0=自动读名单）")
    args = ap.parse_args()

    repo = args.repo.split("/", 1)[1]
    tag = repo.lower().replace("-", "").replace("_", "")
    dims = [d.strip() for d in args.dims.split(",") if d.strip()]

    lst = os.path.join(BASE, "data", "raw",
                       f"forks_all_{args.repo.split('/')[0]}__{repo}.jsonl")
    total = args.target
    if not total and os.path.exists(lst):
        total = sum(1 for _ in open(lst, encoding="utf-8"))
    print(f"目标 {total:,} 人 × {len(dims)} 维度 | 每轮 {args.batch_seconds}s")

    last = {d: count_target(tag, d) for d in dims}
    print("起始：" + " ".join(f"{d}={last[d]:,}" for d in dims))

    script = os.path.join(BASE, "scripts", "collect", "collect_run.py")
    for i in range(1, args.batches + 1):
        cmd = [sys.executable, script, "--repo", args.repo,
               "--mode", args.mode, "--dims", ",".join(dims),
               "--workers", str(args.workers),
               "--max-minutes", str(args.batch_seconds / 60 * 1.5)]
        t0 = time.time()
        try:
            subprocess.run(cmd, cwd=BASE, timeout=args.batch_seconds,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            killed = False
        except subprocess.TimeoutExpired:
            killed = True          # 子进程被强制杀死，卡住的线程一并消失
        el = time.time() - t0

        cur = {d: count_target(tag, d) for d in dims}
        gain = {d: cur[d] - last[d] for d in dims}
        rate = sum(gain.values()) / max(el, 1) * 60
        print(f"轮{i:>2} {el:5.0f}s {'(杀)' if killed else '(完)'} | "
              + " ".join(f"{d}+{gain[d]:,}" for d in dims)
              + f" | {rate:.0f} 人次/分钟", flush=True)

        # 全员到齐 或 本轮毫无进展 → 收工
        if total and all(cur[d] >= total * 0.995 for d in dims):
            print("全部维度已到目标，结束")
            break
        if sum(gain.values()) == 0 and i > 2:
            print("连续两轮无进展，结束")
            break
        last = cur

    print("最终：" + " ".join(f"{d}={count_target(tag, d):,}" for d in dims))
    return 0


if __name__ == "__main__":
    sys.exit(main())
