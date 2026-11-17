# -*- coding: utf-8 -*-
"""多轮修复：反复重采「不完整」的账号，直到完整率达标。

    python scripts/collect/_repair_loop.py --repo fatedier/frp --sample 2000 \
        --target 0.85 --max-rounds 8

为什么需要多轮：
    单次请求成功率约 47%，7 个 API 维全成功的概率约 26%。
    但失败在各轮之间近似独立，所以反复重采未完成的账号会收敛：
        1 轮 26%  2 轮 45%  3 轮 59%  4 轮 70%  6 轮 83%  8 轮 90%

已完成的账号会被跳过（collect_full 的 done 集合只记「API 维全终态」的记录）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
HTML_DIMS = {"D13", "D15"}
TERM = {"OK", "EMPTY", "DELETED", "NOT_FOUND", "PRIVATE", "AUTH"}


def stats(path: str) -> tuple[int, int, int]:
    """→ (总账号数, API 维完整数, 平均已完成维数×100)"""
    best: dict[str, dict] = {}
    if not os.path.exists(path):
        return 0, 0, 0
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if not lg:
            continue
        os_ = r.get("outcomes", {})
        api = {k: v for k, v in os_.items() if k not in HTML_DIMS}
        score = sum(1 for v in api.values() if v in TERM)
        cur = best.get(lg)
        if cur is None or score > cur["_s"]:
            r["_s"] = score
            r["_n"] = len(api)
            best[lg] = r
    total = len(best)
    full = sum(1 for r in best.values() if r["_n"] and r["_s"] == r["_n"])
    return total, full, 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--sample", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--target", type=float, default=0.85, help="目标完整率")
    ap.add_argument("--max-rounds", type=int, default=8)
    ap.add_argument("--dims", default="D1,D2B,D4L,D8H,D9,D14,D6")
    ap.add_argument("--round-minutes", type=float, default=25)
    args = ap.parse_args()

    owner, _, repo = args.repo.partition("/")
    tag = args.tag or repo.replace("-", "")
    path = os.path.join(RAW, f"accounts_full_{tag}.jsonl")

    for rnd in range(1, args.max_rounds + 1):
        total, full, _ = stats(path)
        rate = full / max(total, 1)
        print(f"\n{'='*62}\n第 {rnd} 轮开始 | 当前 {total:,} 账号，完整 {full:,}（{rate*100:.1f}%）\n{'='*62}",
              flush=True)
        if rate >= args.target:
            print(f"✅ 已达目标 {args.target*100:.0f}%，停止")
            return 0

        cmd = [sys.executable, "scripts/collect/collect_full.py",
               "--repo", args.repo, "--sample", str(args.sample),
               "--workers", str(args.workers), "--dims", args.dims,
               "--max-minutes", str(args.round_minutes)]
        if args.tag:
            cmd += ["--tag", args.tag]
        r = subprocess.run(cmd, capture_output=True)
        out = r.stdout.decode("utf-8", errors="replace")
        for line in out.splitlines()[-3:]:
            print("   " + line, flush=True)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace")
            print(f"   ⚠️ 退出码 {r.returncode}: {err[-200:]}")

        total, full, _ = stats(path)
        rate = full / max(total, 1)
        print(f"   轮末：完整 {full:,}/{total:,} = {rate*100:.1f}%", flush=True)

    total, full, _ = stats(path)
    print(f"\n达到最大轮数。最终：{full:,}/{total:,} = {full/max(total,1)*100:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
