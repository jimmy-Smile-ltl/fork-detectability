# -*- coding: utf-8 -*-
"""采集状态一览（替代重复的巡检命令）。

    python scripts/ops/_status_report.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")

FORKS = {
    "rea": 108352, "openclaw": 82753, "BettaFish": 7688,
    "shardeum": 715, "union": 4297, "anoma": 4139,
    "ollama": 5000, "langchain": 5000,
}
DIMS = [("D1", "profile_tier_screen_{t}_all.jsonl"),
        ("D2B", "events90_{t}.jsonl"),
        ("D4L", "starlist_{t}.jsonl"),
        ("D8H", "repos_html_{t}.jsonl"),
        ("D9", "commits_{t}.jsonl"),
        ("D13", "heat_{t}_all.jsonl"),
        ("D14", "avatar_{t}.jsonl"),
        ("D15", "profile_html_{t}.jsonl")]


def usable(r):
    """兼容新旧两种格式：新格式看 outcome，旧格式看 http / status / type。"""
    oc = r.get("outcome")
    if oc in ("OK", "EMPTY"):
        return True
    st = r.get("status")
    if isinstance(st, str):
        return st in ("ok", "no_cells")
    if isinstance(st, int) and st == 200:
        return True
    if r.get("http") == 200:
        return True
    if r.get("complete"):
        return True
    if r.get("type") in ("photo", "identicon", "default"):
        return True
    return False


def scan(path):
    if not os.path.exists(path):
        return None, 0, Counter(), None
    c = Counter()
    ok = set()
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        good = usable(r)
        key = r.get("outcome") or r.get("status") or r.get("http")
        c["OK" if good else str(key)] += 1
        if good:
            lg = (r.get("login") or "").lower()
            if lg:
                ok.add(lg)
    return len(ok), len(ok), c, os.path.getmtime(path)


def main():
    print("=" * 78)
    print(f"采集状态一览  {time.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 78)
    print(f"\n{'仓库':11s}" + "".join(f"{d:>7s}" for d, _ in DIMS))
    print("-" * 78)
    for tag, total in FORKS.items():
        cells = []
        for d, fn in DIMS:
            p = os.path.join(RAW, fn.format(t=tag))
            n, _, _, _ = scan(p)
            cells.append(f"{n/total*100:6.0f}%" if n is not None else "     —")
        print(f"{tag:11s}" + "".join(cells))

    # 最近写入
    print("\n最近写入：")
    rows = []
    for fn in os.listdir(RAW):
        if not fn.endswith(".jsonl"):
            continue
        p = os.path.join(RAW, fn)
        age = time.time() - os.path.getmtime(p)
        if age < 3600:
            rows.append((age, fn, os.path.getsize(p) / 1e6))
    if rows:
        for age, fn, mb in sorted(rows):
            print(f"  {age:6.0f}s 前  {fn:44s} {mb:6.1f} MB")
    else:
        print("  （1 小时内无写入）")

    # D15 详情
    print("\nD15 详情：")
    print(f"  {'仓库':11s} {'有效':>7s} {'目标':>7s} {'尝试':>7s} {'成功率':>7s}")
    for tag in FORKS:
        p = os.path.join(RAW, f"profile_html_{tag}.jsonl")
        n, _, c, _ = scan(p)
        if n is None:
            continue
        tot = sum(c.values())
        succ = (c.get("OK", 0) + c.get("EMPTY", 0)) / max(tot, 1) * 100
        print(f"  {tag:11s} {n:>7,} {FORKS[tag]:>7,} {tot:>7,} {succ:6.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
