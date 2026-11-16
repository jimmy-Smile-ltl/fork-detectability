# -*- coding: utf-8 -*-
"""D15 批量补采：对 6 个仓库各随机采样 N 人，串行执行。

    python scripts/collect/_d15_batch.py [--sample 1000] [--workers 3]

为什么只采 D15 不采 D13：
    实测 github.com 的贡献页（230KB）在隧道下成功率为 0，主页（40KB）约 40%。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPOS = [
    ("shardeum/shardeum", "shardeum"),
    ("unionlabs/union", "union"),
    ("anoma/anoma", "anoma"),
    ("ollama/ollama", "ollama"),
    ("langchain-ai/langchain", "langchain"),
    ("666ghj/BettaFish", "BettaFish"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--minutes", type=int, default=100)
    ap.add_argument("--tag", default="", help="只跑某个仓库的 tag")
    args = ap.parse_args()

    todo = [x for x in REPOS if not args.tag or x[1] == args.tag]
    print(f"D15 批量：{len(todo)} 个仓库 × 采样 {args.sample} 人，并发 {args.workers}\n", flush=True)

    for repo, tag in todo:
        t0 = time.time()
        print(f"=== {tag} @ {time.strftime('%H:%M:%S')} ===", flush=True)
        cmd = [sys.executable, "scripts/collect/collect_run.py",
               "--repo", repo, "--mode", "per_dim", "--dims", "D15",
               "--workers", str(args.workers),
               "--sample", str(args.sample),
               "--max-minutes", str(args.minutes)]
        r = subprocess.run(cmd, capture_output=True)
        out = r.stdout.decode("utf-8", errors="replace")
        for line in out.splitlines()[-5:]:
            print("   " + line, flush=True)
        print(f"   用时 {time.time()-t0:.0f}s，退出码 {r.returncode}\n", flush=True)

    print(f"全部完成 @ {time.strftime('%H:%M:%S')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
