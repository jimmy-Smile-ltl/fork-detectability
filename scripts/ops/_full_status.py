# -*- coding: utf-8 -*-
"""所有「一人一条」档案的完整度一览。

    python scripts/ops/_full_status.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")

TERM = {"OK", "EMPTY", "DELETED", "NOT_FOUND", "PRIVATE", "AUTH"}
HTML = {"D13", "D15"}
TAGS = ["rea", "openclaw", "bettafish", "shardeum", "union", "anoma",
        "nodejsargo", "frp", "agencyagents", "ollama", "langchain"]


def stats(path: str):
    best: dict[str, tuple[int, int]] = {}
    if not os.path.exists(path):
        return None
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if not lg:
            continue
        dims = {k: v for k, v in (r.get("outcomes") or {}).items() if k not in HTML}
        sc = sum(1 for v in dims.values() if v in TERM)
        n = len(dims)
        cur = best.get(lg)
        if cur is None or sc > cur[0]:
            best[lg] = (sc, n)
    tot = len(best)
    full = sum(1 for s, n in best.values() if n and s == n)
    mtime = os.path.getmtime(path)
    return tot, full, mtime


def main() -> int:
    print("=" * 76)
    print(f"一人一条档案完整度  {time.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 76)
    print(f"{'仓库':14s} {'账号':>8s} {'完整':>8s} {'完整率':>8s} {'大小':>9s} {'最后写入':>10s}")
    print("-" * 76)
    T = F = 0
    for t in TAGS:
        p = os.path.join(RAW, f"accounts_full_{t}.jsonl")
        r = stats(p)
        if r is None:
            print(f"{t:14s} {'—':>8s}")
            continue
        tot, full, mt = r
        T += tot
        F += full
        age = time.time() - mt
        age_s = f"{age:.0f}s" if age < 600 else f"{age/60:.0f}min"
        print(f"{t:14s} {tot:>8,} {full:>8,} {full/max(tot,1)*100:>7.1f}% "
              f"{os.path.getsize(p)/1e6:>8.1f}MB {age_s:>10s}")
    print("-" * 76)
    print(f"{'合计':14s} {T:>8,} {F:>8,} {F/max(T,1)*100:>7.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
