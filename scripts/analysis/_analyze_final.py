# -*- coding: utf-8 -*-
"""最终分析：把 D1（档案）+ D13（年度日历）+ D8（仓库结构）合并，产出两仓对比。

为什么不复用 _build_features.py：
  那个脚本只吃 D1；这版要把 D13 的 active_days 和 D8 的 own_count 也并进来，
  且**统一去重口径**（成功优先，避免重试把成功覆盖成失败）。

输出：
  data/out/final_{repo}.csv    一人一行，30+ 列
  data/out/final_report.md     两仓对比 + 关键分布

用法：
    py _analyze_final.py --repo morluto/rea --key rea
    py _analyze_final.py --compare        # 两仓一起跑并输出对比
"""
import argparse
import csv
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")


def load_best(path, key_field="login", ok_pred=None):
    """成功优先去重：同一 login 的多条记录，200 优先于其它。"""
    best = {}
    if not os.path.exists(path):
        return best
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        lg = (r.get(key_field) or "").lower()
        if not lg:
            continue
        good = (r.get("http") == 200) if ok_pred is None else ok_pred(r)
        prev = best.get(lg)
        if prev is None or (good and not prev[0]):
            best[lg] = (good, r)
    return {k: v[1] for k, v in best.items()}


def num(v, d=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def build(repo_key, d1_path, d13_path, d8_path, d8api_path=None):
    d1 = load_best(d1_path)
    d13 = load_best(d13_path, ok_pred=lambda r: r.get("status") == "ok")
    d8h = load_best(d8_path)
    d8a = load_best(d8api_path) if d8api_path else {}
    rows = []
    for lg, r in d1.items():
        p = r.get("d1") or {}
        if r.get("http") != 200 or p.get("http") != 200:
            continue
        cal = d13.get(lg) or {}
        h = d8h.get(lg) or {}
        a = d8a.get(lg) or {}
        own = num(h.get("own_count"))
        own_api = num(a.get("own_count"))
        rows.append({
            "login": lg,
            "created_at": p.get("created_at"),
            "public_repos": num(p.get("public_repos")),
            "followers": num(p.get("followers")),
            "following": num(p.get("following")),
            "filled": sum(1 for k in ("name", "bio", "blog", "location", "company", "twitter_username")
                          if (p.get(k) or "").strip()),
            "type": p.get("type"),
            "active_days": num(cal.get("active_days")),
            "total_contribs": num(cal.get("total_contribs")),
            "last_active": cal.get("last_active"),
            "own_count_html": own,
            "has_own_html": (1 if (own or 0) > 0 else 0) if own is not None else None,
            "own_count_api": own_api,
            "has_own_api": (1 if (own_api or 0) > 0 else 0) if own_api is not None else None,
            "fork_count_html": num(h.get("fork_count")),
            "has_profile_repo": h.get("has_profile_repo"),
        })
    return rows


def pct(a, b):
    return f"{a/max(b,1)*100:.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()

    specs = [
        ("morluto/rea", "rea",
         "profile_tier_screen_rea_all.jsonl", "heat_rea_all.jsonl",
         "repos_html_rea.jsonl", "profile_tier_repos_rea_repos.jsonl"),
        ("openclaw/openclaw", "openclaw",
         "profile_tier_screen_openclaw_all.jsonl", "heat_openclaw_all.jsonl",
         "repos_html_openclaw.jsonl", None),
    ]
    allrows = {}
    for repo, key, f1, f13, f8, f8a in specs:
        rows = build(repo, os.path.join(RAW, f1), os.path.join(RAW, f13),
                     os.path.join(RAW, f8),
                     os.path.join(RAW, f8a) if f8a else None)
        allrows[key] = rows
        if rows:
            dst = os.path.join(OUT, f"final_{key}.csv")
            with open(dst, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            print(f"{key}: {len(rows):,} 人 → {os.path.basename(dst)}")

    # 汇总对比
    L = ["# 两仓最终对比\n",
         "> 合并 D1（档案）+ D13（全年活跃）+ D8（仓库结构）。"
         "D8 来自 HTML 页面，二分类准确率 96%（50 人校验），精确计数误差较大。\n"]
    L.append("| 指标 | rea | openclaw | 差异 |")
    L.append("|---|---:|---:|---:|")

    def stat(rows, fn):
        vals = [fn(r) for r in rows if fn(r) is not None]
        return (statistics.median(vals) if vals else None), len(vals)

    keys = [("档案填充中位", lambda r: r["filled"]),
            ("仓数中位", lambda r: r["public_repos"]), ("粉丝中位", lambda r: r["followers"]),
            ("全年活跃天中位", lambda r: r["active_days"]),
            ("年度贡献中位", lambda r: r["total_contribs"])]
    L.append(f"| 人数 | {len(allrows['rea']):,} | {len(allrows['openclaw']):,} | "
             f"{len(allrows['openclaw'])-len(allrows['rea']):+,} |")
    for name, fn in keys:
        a = stat(allrows["rea"], fn)[0]
        b = stat(allrows["openclaw"], fn)[0]
        if a is None or b is None:
            continue
        L.append(f"| {name} | {a:,.0f} | {b:,.0f} | {b-a:+,.0f} |")

    # 比例类
    def ratio(rows, pred):
        n = sum(1 for r in rows if pred(r) is not None)
        v = sum(1 for r in rows if pred(r))
        return v, n

    props = [
        ("活跃 ≤5 天占比", lambda r: None if r["active_days"] is None else r["active_days"] <= 5),
        ("活跃 = 0 天占比", lambda r: None if r["active_days"] is None else r["active_days"] == 0),
        ("**无自建仓占比（HTML）**",
         lambda r: None if r["has_own_html"] is None else r["has_own_html"] == 0),
        ("档案全空占比", lambda r: r["filled"] == 0),
        ("粉丝 = 0 占比", lambda r: None if r["followers"] is None else r["followers"] == 0),
    ]
    L.append("")
    L.append("| 比例 | rea | openclaw | 差异 |")
    L.append("|---|---:|---:|---:|")
    for name, pred in props:
        va, na = ratio(allrows["rea"], pred)
        vb, nb = ratio(allrows["openclaw"], pred)
        if not na or not nb:
            continue
        pa, pb = va/na, vb/nb
        L.append(f"| {name} | {pa*100:.1f}% ({va:,}/{na:,}) | {pb*100:.1f}% ({vb:,}/{nb:,}) | {(pa-pb)*100:+.1f}pp |")

    md = "\n".join(L) + "\n"
    open(os.path.join(OUT, "final_report.md"), "w", encoding="utf-8").write(md)
    print(md)


if __name__ == "__main__":
    main()
