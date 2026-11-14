# -*- coding: utf-8 -*-
"""从 screen 结果里挑出要深挖（deep 档，8 req/人）的账号。

为什么要挑：deep 每人 8 个请求，20 万人 × 8 = 152 万请求，
在快代理 ~3,600 请求/小时的池子上要 18 天，超出 7 天窗口。
所以 deep 只给**判据最需要的那批人**，其余人保留 screen 档案。

分层（按判据价值降序）：
  A 登录名 TS/PHONE           → 全采（批量性轴最直接）
  B 其它纯数字 + 档案空        → 全采
  C public_repos == 1          → 全采（名下只有一个仓 = 这次 fork）
  D 爆发窗内                   → 按 md5 排序抽样
  E 其余                       → 随机对照抽样

用法：
    py _select_deep.py --screen data/raw/profile_tier_screen_rea_all.jsonl \
                       --forks data/raw/forks_all_morluto__rea.jsonl \
                       --out data/raw/rea_deep.txt --cap 20000
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
            return "NUM"
        return "TS"
    if n == 11 and run[0] == "1" and run[1] in "3456789":
        return "PHONE"
    if 5 <= n <= 11:
        return "NUM"
    return "OTHER"


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


def md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", required=True)
    ap.add_argument("--forks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cap", type=int, default=20000)
    ap.add_argument("--burst-sample", type=int, default=10000)
    ap.add_argument("--random-sample", type=int, default=3000)
    args = ap.parse_args()

    # fork 时间与爆发窗
    fork_ts = {}
    for line in open(args.forks, encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        lg = (r.get("login") or "").lower()
        ts = iso_ts(r.get("fork_created_at"))
        if lg and ts:
            fork_ts[lg] = ts
    burst = burst_hours(list(fork_ts.values()))

    # screen 档案（同名多条取最后一条）
    prof = {}
    for line in open(args.screen, encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        lg = (r.get("login") or "").lower()
        if lg:
            prof[lg] = r

    A, B, C, D, E = [], [], [], [], []
    for lg, r in prof.items():
        if r.get("http") != 200:
            continue
        d1 = r.get("d1") or {}
        if d1.get("http") != 200:
            continue
        kind = login_kind(lg)
        empty = not any((d1.get(k) or "").strip()
                        for k in ("name", "bio", "blog", "location", "company", "twitter_username"))
        repos = d1.get("public_repos")
        in_burst = int(fork_ts.get(lg, 0) // 3600) in burst if lg in fork_ts else False
        if kind in ("TS", "PHONE"):
            A.append(lg)
        elif kind == "NUM" and empty:
            B.append(lg)
        elif repos == 1:
            C.append(lg)
        elif in_burst:
            D.append(lg)
        else:
            E.append(lg)

    A.sort()
    B.sort()
    C.sort(key=md5)                      # 确定性抽样
    D.sort(key=md5)
    E.sort(key=md5)
    picked = A + B + C + D[:args.burst_sample] + E[:args.random_sample]
    picked = list(dict.fromkeys(picked))[:args.cap]

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(picked) + "\n")

    print(f"screen 可用账号 {sum(1 for r in prof.values() if r.get('http') == 200):,}")
    print(f"   A 登录名 TS/PHONE     {len(A):6,}  → 全采")
    print(f"   B 纯数字 + 档案空      {len(B):6,}  → 全采")
    print(f"   C public_repos == 1   {len(C):6,}  → 全采")
    print(f"   D 爆发窗内            {len(D):6,}  → 抽 {min(args.burst_sample, len(D)):,}")
    print(f"   E 其余                {len(E):6,}  → 抽 {min(args.random_sample, len(E)):,}")
    print(f"→ deep 名单 {len(picked):,} 人 → {os.path.basename(args.out)} "
          f"（约 {len(picked) * 8 / 3600:.0f} 小时 @3,600 req/h）")


if __name__ == "__main__":
    main()
