# -*- coding: utf-8 -*-
"""给 forker 名单排优先级：**先采最可疑的**，让任何中断都不亏。

排序（前置优先）：
  1. `TS` / `PHONE` 登录名（时间戳/手机号形态）—— 批量性轴最直接的证据
  2. 其它 5–11 位纯数字
  3. 落在 fork 爆发窗（滚动 3h > μ+2σ）里的账号 —— 批量执行痕迹
  4. 其余（按 fork 时间倒序，先新的）

用法：
    py _order_forkers.py                      # 读 rea 名单，写 rea_forkers_priority.txt
    py _order_forkers.py --repo openclaw/openclaw
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")

WINDOW_H, Z = 3, 2.0


def iso_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def login_kind(login):
    runs = re.findall(r"\d+", login or "")
    run = max(runs, key=len) if runs else ""
    n = len(run)
    if n == 10 and 10 ** 9 <= int(run) < 2 * 10 ** 9:
        seq = "0123456789" * 2
        if run in seq or run in seq[::-1] or len(set(run)) <= 2:
            return 3
        return 1                      # TS
    if n == 11 and run[0] == "1" and run[1] in "3456789":
        return 1                      # PHONE
    if 5 <= n <= 11:
        return 2                      # 纯数字
    return 9


def burst_hours(ts_list):
    hours = [int(t // 3600) for t in ts_list]
    if len(hours) < 10:
        return set()
    lo, hi = min(hours), max(hours)
    dense = [0] * (hi - lo + 1)
    for h in hours:
        dense[h - lo] += 1
    w = min(WINDOW_H, len(dense))
    sums = [sum(dense[i:i + w]) for i in range(len(dense) - w + 1)]
    mean = sum(sums) / len(sums)
    var = sum((x - mean) ** 2 for x in sums) / len(sums)
    thr = mean + Z * math.sqrt(var)
    flagged = set()
    for i, s in enumerate(sums):
        if s > thr:
            flagged.update(range(lo + i, lo + i + w))
    return flagged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="morluto/rea")
    args = ap.parse_args()
    key = args.repo.replace("/", "__")
    src = os.path.join(RAW, f"forks_all_{key}.jsonl")

    rows = []
    for line in open(src, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        lg = (r.get("login") or "").strip()
        ts = iso_ts(r.get("fork_created_at"))
        if lg and ts:
            rows.append((lg, ts))
    burst = burst_hours([t for _, t in rows])

    scored = {}
    for lg, ts in rows:
        k = login_kind(lg)
        in_burst = 1 if int(ts // 3600) in burst else 0
        # 优先级：形态可疑 > 爆发窗 > 其它；同档内按 fork 时间倒序
        tier = 0 if k == 1 else (1 if k == 2 else (2 if in_burst else 3))
        scored[lg.lower()] = (tier, -ts, lg)

    ordered = [v[2] for v in sorted(scored.values())]
    out = os.path.join(RAW, f"{key.split('__')[-1]}_forkers_priority.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(ordered) + "\n")
    from collections import Counter
    c = Counter(v[0] for v in scored.values())
    print(f"{len(ordered):,} 人 → {os.path.basename(out)}")
    print(f"  第1档 形态可疑(TS/PHONE): {c[0]:,}")
    print(f"  第2档 其它纯数字:        {c[1]:,}")
    print(f"  第3档 爆发窗内:          {c[2]:,}")
    print(f"  第4档 其余:              {c[3]:,}")


if __name__ == "__main__":
    main()
