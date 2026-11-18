# -*- coding: utf-8 -*-
"""D15（成就徽章）全仓补齐：把各仓的 D15 样本补到目标数量。

    python scripts/collect/_d15_fill.py --target 0.9 --round-minutes 55

为什么只补 D15：
    `has_pull_shark` / `has_collab` 是经 A/B 对照检验后**最强的两条判据**
    （间距 11.1pp / 1.4pp），但 6 个仓的 D15 样本只有 10–23%，
    CI 约 ±3pp。补到 90% 后 CI 可降到 ±1pp。

顺序：A 侧先（样本少、对结论影响大），再 B 侧。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
TERM = {"OK", "EMPTY", "DELETED", "NOT_FOUND", "PRIVATE", "AUTH"}

# (repo, tag, 账号池上限, 分组)
REPOS = [
    ("unionlabs/union", "union", 4202, "A"),
    ("anoma/anoma", "anoma", 4139, "A"),
    ("eooce/nodejs-argo", "nodejsargo", 3744, "A"),
    ("fatedier/frp", "frp", 3762, "B"),
    ("ollama/ollama", "ollama", 8718, "B"),
    ("langchain-ai/langchain", "langchain", 9016, "B"),
]


def d15_count(tag: str) -> int:
    p = os.path.join(RAW, f"accounts_full_{tag}.jsonl")
    if not os.path.exists(p):
        return 0
    seen = set()
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if lg and (r.get("outcomes") or {}).get("D15") in TERM:
            seen.add(lg)
    return len(seen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=0.9, help="目标覆盖率")
    ap.add_argument("--round-minutes", type=float, default=55)
    ap.add_argument("--rounds-per-repo", type=int, default=3)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--tag", default="", help="只跑某个 tag")
    args = ap.parse_args()

    todo = [x for x in REPOS if not args.tag or x[1] == args.tag]
    print(f"D15 补齐：{len(todo)} 个仓库，目标覆盖率 {args.target*100:.0f}%", flush=True)

    for repo, tag, pool, grp in todo:
        cur0 = d15_count(tag)
        target_n = int(pool * args.target)
        print(f"\n{'='*66}\n{tag}（{grp} 侧）当前 D15 {cur0:,}/{pool:,} "
              f"= {cur0/pool*100:.1f}% | 目标 {target_n:,}\n{'='*66}", flush=True)
        if cur0 >= target_n:
            print("  已达标，跳过", flush=True)
            continue

        for rnd in range(1, args.rounds_per_repo + 1):
            cur = d15_count(tag)
            if cur >= target_n:
                print(f"  ✅ 达标（{cur:,}）", flush=True)
                break
            print(f"  第 {rnd} 轮 | 已有 {cur:,} | 需再补 {target_n-cur:,}", flush=True)
            cmd = [sys.executable, "scripts/collect/collect_full.py",
                   "--repo", repo, "--sample", str(pool), "--workers", str(args.workers),
                   "--dims", "D15", "--max-minutes", str(args.round_minutes)]
            r = subprocess.run(cmd, capture_output=True)
            out = r.stdout.decode("utf-8", errors="replace")
            for line in out.splitlines()[-3:]:
                print("     " + line, flush=True)
            time.sleep(3)

        print(f"  最终 D15 {d15_count(tag):,}/{pool:,} "
              f"= {d15_count(tag)/pool*100:.1f}%", flush=True)

    print(f"\n全部完成 @ {time.strftime('%H:%M')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
