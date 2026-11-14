# -*- coding: utf-8 -*-
"""从 fork 名单重建 forker 全量名单（去重、按 login 排序，确定性）。

用途：fork 名单采完后，用它生成账户采集的输入队列；采集中也可以反复跑（幂等）。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")


def rebuild(src_name, dst_name):
    src = os.path.join(RAW, src_name)
    dst = os.path.join(RAW, dst_name)
    seen = {}
    for line in open(src, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        lg = (r.get("login") or "").strip()
        if lg:
            seen[lg.lower()] = lg
    logins = sorted(seen.values(), key=str.lower)
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(logins) + "\n")
    print(f"{src_name} → {len(logins):,} 个唯一 forker → {dst_name}")
    return len(logins)


if __name__ == "__main__":
    rebuild("forks_all_morluto__rea.jsonl", "rea_forkers_all.txt")
