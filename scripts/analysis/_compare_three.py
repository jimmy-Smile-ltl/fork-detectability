# -*- coding: utf-8 -*-
"""三个仓库的判据对照：rea vs openclaw vs BettaFish。

统一的判据定义见 docs/00_项目总纲.md §5.3。
本脚本只算**能在三仓上一致计算**的判据，缺失的明确标注。

    python scripts/analysis/_compare_three.py
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "out")


def load(tag: str) -> list[dict]:
    p = os.path.join(OUT, f"features_{tag}.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig", errors="replace") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rate(rows, key, val="1"):
    v = [r for r in rows if r.get(key) not in (None, "")]
    if not v:
        return None, 0
    hit = sum(1 for r in v if str(r.get(key)) == val)
    return hit / len(v) * 100, len(v)


def med(rows, key):
    v = sorted(x for x in (num(r.get(key)) for r in rows) if x is not None)
    return (v[len(v) // 2], len(v)) if v else (None, 0)


def ci(va, na, vb, nb):
    """两比例差的 95% 区间。"""
    if not na or not nb:
        return None
    pa, pb = va / na, vb / nb
    se = math.sqrt(pa * (1 - pa) / na + pb * (1 - pb) / nb)
    d = (pa - pb) * 100
    return d - 1.96 * se * 100, d + 1.96 * se * 100


def burst_members(tag: str, fork_file: str) -> set:
    """逐人判断是否落在爆发窗（μ+2σ 的 3 小时窗）。"""
    ts = {}
    p = os.path.join(BASE, "data", "raw", fork_file)
    if not os.path.exists(p):
        return set()
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        s = r.get("fork_created_at") or ""
        if not lg or not s:
            continue
        try:
            ts[lg] = datetime.strptime(s.replace("Z", "")[:19],
                                       "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc).timestamp()
        except Exception:
            pass
    if not ts:
        return set()
    hours = [int(t // 3600) for t in ts.values()]
    lo, hi = min(hours), max(hours)
    dense = [0] * (hi - lo + 1)
    for h in hours:
        dense[h - lo] += 1
    sums = [sum(dense[i:i + 3]) for i in range(len(dense) - 2)]
    m = sum(sums) / len(sums)
    sd = math.sqrt(sum((x - m) ** 2 for x in sums) / len(sums))
    thr = m + 2 * sd
    flag = set()
    for i, s in enumerate(sums):
        if s > thr:
            for h in range(lo + i, lo + i + 3):
                flag.add(h)
    return {lg for lg, t in ts.items() if int(t // 3600) in flag}


def main() -> int:
    repos = {
        "rea": ("forks_all_morluto__rea.jsonl", "122,752"),
        "openclaw": ("forks_all_openclaw__openclaw.jsonl", "82,753"),
        "BettaFish": ("forks_all_666ghj__BettaFish.jsonl", "7,690"),
    }
    data = {k: load(k.lower() if k != "BettaFish" else "bettafish") for k in repos}
    for k, rows in data.items():
        print(f"{k:10s} {len(rows):,} 行")

    L = ["# 三仓判据对照：rea vs openclaw vs BettaFish", "",
         "> 口径与 docs/00_项目总纲.md §5.3 一致；缺失维度标 `unavailable`。", "",
         "## 一、比例型判据", "",
         "| 判据 | rea | openclaw | BettaFish | 说明 |",
         "|---|---:|---:|---:|---|"]

    JUDGES = [
        ("fork_touched", "fork 后动过（全量，未控龄）"),
        ("followers_zero", "零粉丝"),
        ("following_zero", "零关注"),
        ("avatar_photo", "自己上传头像"),
        ("has_profile_repo", "有个人主页仓"),
        ("repo_named_target", "自建仓名含目标仓"),
        ("has_pull_shark", "Pull Shark（D15）"),
        ("has_collab", "协作徽章（D15）"),
        ("commit_zero", "自建仓零提交（D9）"),
        ("active_le5", "全年活跃≤5天（D13）"),
    ]
    for key, name in JUDGES:
        cells, note = [], ""
        for k in repos:
            r, n = rate(data[k], key)
            cells.append(f"{r:.1f}% ({n:,})" if r is not None else "—")
        L.append(f"| {name} | " + " | ".join(cells) + f" | `{key}` |")

    # 爆发窗（需要 fork 时间戳，逐人重算）
    L += ["", "## 二、形态与时间判据", "",
          "| 判据 | rea | openclaw | BettaFish |", "|---|---:|---:|---:|"]
    bset = {}
    for k, (ff, _) in repos.items():
        bset[k] = burst_members(k, ff)
        rows = data[k]
        hit = sum(1 for r in rows if (r.get("login") or "").lower() in bset[k])
        n = len(rows)
        bset[k] = (hit, n)
    L.append("| fork 落在爆发窗内 | " + " | ".join(
        f"{bset[k][0]/bset[k][1]*100:.1f}% ({bset[k][0]:,}/{bset[k][1]:,})"
        if bset[k][1] else "—" for k in repos) + " |")

    # 中位数
    L += ["", "## 三、连续型（中位数）", "",
          "| 指标 | rea | openclaw | BettaFish |", "|---|---:|---:|---:|"]
    for key, name in (("acct_age_days", "账号年龄（天）"),
                      ("followers", "粉丝数"),
                      ("own_count", "自建仓数"),
                      ("commit_total", "自建仓提交数"),
                      ("starred_count", "star 数（D15）"),
                      ("starred_count_api", "star 数（API）"),
                      ("active_days", "活跃天数（D13）")):
        cells = []
        for k in repos:
            m, n = med(data[k], key)
            cells.append(f"{m:,.0f} (n={n:,})" if m is not None else "—")
        L.append(f"| {name} | " + " | ".join(cells) + " |")

    # 覆盖率表
    L += ["", "## 四、各仓数据覆盖度（判据可信度）", "",
          "| 维度 | rea | openclaw | BettaFish |", "|---|---:|---:|---:|"]
    # 各维度在特征表里对应的"代理字段"（旧 CSV 没有 *_outcome 列）
    PROXY = {
        "D1": "acct_age_days", "D2": "events_n", "D8H": "own_count",
        "D9": "commit_total", "D13": "active_days", "D14": "avatar_photo",
        "D15": "achievement_n", "D4L": "starred_count",
    }
    for dim, proxy in PROXY.items():
        cells = []
        for k in repos:
            rows = data[k]
            hit = sum(1 for r in rows if r.get(proxy) not in (None, ""))
            if not hit:   # 旧表可能用别的列名
                hit = sum(1 for r in rows
                          if r.get(f"{dim}_outcome") in ("OK", "EMPTY"))
            cells.append(f"{hit/max(len(rows),1)*100:.1f}%" if rows else "—")
        L.append(f"| {dim} | " + " | ".join(cells) + " |")

    md = "\n".join(L) + "\n"
    dst = os.path.join(OUT, "three_way_compare.md")
    open(dst, "w", encoding="utf-8").write(md)
    print(md)
    print(f"→ {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
