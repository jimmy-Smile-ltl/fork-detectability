# -*- coding: utf-8 -*-
"""仓库内部对照：爆发窗内 vs 爆发窗外。

为什么这个检验比跨仓比较干净：
  跨仓比较混淆了"受众构成"（中文项目 vs 英文项目）。
  在同一个仓库内部，把 forker 按"是否落在爆发窗"分组，
  文化、项目性质、受众全部相同，唯一差别是**动作时间是否集中**。

如果爆发窗内的账号在各项行为上明显更差 → 支持"协调行动"
如果两者无差异 → 爆发只是外部注意力（一篇推文/视频），不是刷量

用法：py _internal_burst_control.py
"""
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")

SPECS = {
    "rea": ("forks_all_morluto__rea.jsonl", "features_rea.csv"),
    "openclaw": ("forks_all_openclaw__openclaw.jsonl", "features_openclaw.csv"),
}


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def burst_set(fork_file):
    ts = {}
    for line in open(os.path.join(RAW, fork_file), encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        lg = (r.get("login") or "").lower()
        s = r.get("fork_created_at") or ""
        if not lg or not s:
            continue
        try:
            ts[lg] = datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S") \
                .replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    hours = [int(t // 3600) for t in ts.values()]
    lo, hi = min(hours), max(hours)
    dense = [0] * (hi - lo + 1)
    for h in hours:
        dense[h - lo] += 1
    sums = [sum(dense[i:i + 3]) for i in range(len(dense) - 2)]
    mean = sum(sums) / len(sums)
    sd = math.sqrt(sum((x - mean) ** 2 for x in sums) / len(sums))
    thr = mean + 2 * sd
    flagged = set()
    for i, s in enumerate(sums):
        if s > thr:
            for h in range(lo + i, lo + i + 3):
                flagged.add(h)
    return {lg for lg, t in ts.items() if int(t // 3600) in flagged}


def load_csv(tag):
    with open(os.path.join(OUT, f"features_{tag}.csv"), encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def compare(tag, fork_file):
    bs = burst_set(fork_file)
    rows = load_csv(tag)
    inside = [r for r in rows if (r.get("login") or "").lower() in bs]
    outside = [r for r in rows if (r.get("login") or "").lower() not in bs]

    def rate(rs, key, val="1"):
        v = [r for r in rs if r.get(key) not in (None, "")]
        if not v:
            return None, 0
        return sum(1 for r in v if r.get(key) == val) / len(v) * 100, len(v)

    def med(rs, key):
        v = sorted(num(r.get(key)) for r in rs if num(r.get(key)) is not None)
        return (v[len(v) // 2] if v else None), len(v)

    # ⚠️ fork_touched 与爆发窗强相关（爆发窗是 fork 时间定义），这里只作参考
    items = [
        ("零粉丝", "followers_zero", "rate"),
        ("零关注", "following_zero", "rate"),
        ("有 Pull Shark", "has_pull_shark", "rate"),
        ("有协作徽章", "has_collab", "rate"),
        ("有自建仓", "has_own", "rate"),
        ("自建仓零提交", "commit_zero", "rate"),
        ("活跃 ≤5 天", "active_le5", "rate"),
        ("档案全空", "shape", "shape"),
        ("star 过目标仓", "starred_target", "rate"),
        ("≤5 分钟 star+fork", "star_same_hour", "rate"),
        ("有个人主页仓", "has_profile_repo", "rate"),
    ]
    L = [f"### {tag}（爆发窗内 {len(inside):,} 人 vs 窗外 {len(outside):,} 人）", "",
         "| 指标 | 爆发窗内 | 爆发窗外 | 差异 |", "|---|---:|---:|---:|"]
    for name, key, kind in items:
        if kind == "shape":
            a = [r for r in inside if r.get("shape") == "EMPTY"]
            b = [r for r in outside if r.get("shape") == "EMPTY"]
            na = sum(1 for r in inside if r.get("shape"))
            nb = sum(1 for r in outside if r.get("shape"))
            if not na or not nb:
                continue
            pa, pb = len(a) / na * 100, len(b) / nb * 100
            L.append(f"| {name} | {pa:.1f}% ({len(a):,}/{na:,}) | {pb:.1f}% ({len(b):,}/{nb:,}) | {pa-pb:+.1f}pp |")
            continue
        pa, na = rate(inside, key)
        pb, nb = rate(outside, key)
        if pa is None or pb is None:
            continue
        L.append(f"| {name} | {pa:.1f}% ({na:,}) | {pb:.1f}% ({nb:,}) | {pa-pb:+.1f}pp |")
    # 中位数
    L += ["", "| 中位数 | 爆发窗内 | 爆发窗外 | 差异 |", "|---|---:|---:|---:|"]
    for name, key in (("全年活跃天", "active_days"), ("粉丝数", "followers"),
                      ("star 总数", "starred_count"), ("自建仓数", "own_count"),
                      ("自建仓提交数", "commit_total"), ("账号年龄天", "acct_age_days")):
        ma, na = med(inside, key)
        mb, nb = med(outside, key)
        if ma is None or mb is None:
            continue
        L.append(f"| {name} | {ma:,.1f} (n={na:,}) | {mb:,.1f} (n={nb:,}) | {mb-ma:+,.1f} |")
    return "\n".join(L)


def main():
    parts = ["# 仓库内部对照：爆发窗内 vs 窗外", "",
             "> 同仓库、同受众、同文化，唯一差别是 fork 时间是否落在爆发窗口里。",
             "> 若两者行为一致 → 爆发只是外部注意力；若窗内明显更差 → 支持协调行动。", ""]
    for tag, (ff, cf) in SPECS.items():
        parts.append(compare(tag, ff))
        parts.append("")
    md = "\n".join(parts)
    print(md)
    open(os.path.join(OUT, "internal_burst_control.md"), "w", encoding="utf-8").write(md)


if __name__ == "__main__":
    main()
