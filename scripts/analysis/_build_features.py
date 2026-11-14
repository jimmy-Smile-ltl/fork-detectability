# -*- coding: utf-8 -*-
"""特征工程：把「fork 名单 + 账户档案」合成一张**账号级特征表**。

输入（都可缺，缺的列留空并在报告中标注）：
    data/raw/forks_all_{key}.jsonl             fork 时间 / 是否动过
    data/raw/profile_tier_screen_{suffix}.jsonl   D1 档案
    data/raw/profile_tier_deep_{suffix}.jsonl     deep 单元（可选，覆盖 screen）

输出：
    data/out/features_{key}.csv   一行一个账号，列 = 全部特征
    data/out/features_{key}.json   摘要（各特征缺失率、分布）

用法：
    py _build_features.py --repo morluto/rea --suffix _rea_all
    py _build_features.py --repo openclaw/openclaw --suffix _openclaw_all
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")

PROFILE_KEYS = ("name", "company", "blog", "location", "bio", "twitter_username")


def iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
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


def load_last(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        lg = (r.get("login") or "").lower()
        if lg:
            out[lg] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="morluto/rea")
    ap.add_argument("--suffix", default="_rea_all")
    ap.add_argument("--tier", default="screen")
    args = ap.parse_args()

    key = args.repo.replace("/", "__")
    forks = load_last(os.path.join(RAW, f"forks_all_{key}.jsonl"))
    screen = load_last(os.path.join(RAW, f"profile_tier_{args.tier}{args.suffix}.jsonl"))
    deep = load_last(os.path.join(RAW, f"profile_tier_deep{args.suffix}.jsonl"))

    # 爆发窗（3h / μ+2σ）——与 metrics_fork.py 同口径
    import math
    ts_all = [t for t in (iso(v.get("fork_created_at")) for v in forks.values()) if t]
    burst = set()
    if len(ts_all) > 10:
        hours = [int(t.timestamp() // 3600) for t in ts_all]
        lo, hi = min(hours), max(hours)
        dense = [0] * (hi - lo + 1)
        for h in hours:
            dense[h - lo] += 1
        w = min(3, len(dense))
        sums = [sum(dense[i:i + w]) for i in range(len(dense) - w + 1)]
        mean = sum(sums) / len(sums)
        var = sum((x - mean) ** 2 for x in sums) / len(sums)
        thr = mean + 2.0 * math.sqrt(var)
        for i, s in enumerate(sums):
            if s > thr:
                burst.update(range(lo + i, lo + i + w))

    now = datetime.now(timezone.utc)
    rows = []
    for lg, f in forks.items():
        src = deep.get(lg) if (lg in deep and deep[lg].get("http") == 200) else screen.get(lg)
        d1 = (src or {}).get("d1") or {}
        d8 = (src or {}).get("d8") or {}
        d4 = (src or {}).get("d4") or {}
        d2 = (src or {}).get("d2") or {}
        d6 = (src or {}).get("d6") or {}

        ft = iso(f.get("fork_created_at"))
        fp = iso(f.get("fork_pushed_at"))
        ct = iso(d1.get("created_at"))
        filled = sum(1 for k in PROFILE_KEYS if (d1.get(k) or "").strip())
        name, loc = (d1.get("name") or "").strip(), (d1.get("location") or "").strip()
        shape = ("EMPTY" if filled == 0 else
                 "TEMPLATE" if (name and loc and filled == 2) else
                 "RICH" if filled >= 4 else "PARTIAL")

        rows.append({
            "login": f.get("login"),
            "login_kind": login_kind(f.get("login") or ""),
            "user_id": d1.get("user_id"),
            "type": d1.get("type"),
            "fork_ts": int(ft.timestamp()) if ft else "",
            "fork_touched": int(bool(ft and fp and fp > ft)),
            "in_burst": int(bool(ft and int(ft.timestamp() // 3600) in burst)),
            "account_age_days": (now - ct).days if ct else "",
            "age_at_fork_days": int((ft - ct).total_seconds() // 86400) if (ft and ct) else "",
            "profile_filled": filled if d1 else "",
            "profile_shape": shape if d1 else "",
            "has_name": int(bool(name)) if d1 else "",
            "has_bio": int(bool((d1.get("bio") or "").strip())) if d1 else "",
            "has_blog": int(bool((d1.get("blog") or "").strip())) if d1 else "",
            "has_location": int(bool(loc)) if d1 else "",
            "has_company": int(bool((d1.get("company") or "").strip())) if d1 else "",
            "has_twitter": int(bool((d1.get("twitter_username") or "").strip())) if d1 else "",
            "public_repos": d1.get("public_repos", ""),
            "public_gists": d1.get("public_gists", ""),
            "followers": d1.get("followers", ""),
            "following": d1.get("following", ""),
            "starred_total": d4.get("starred_total", ""),
            "orgs_n": len(d6.get("orgs") or []) if d6 else "",
            "events_total": d2.get("events_total", ""),
            "dev_events": d2.get("dev_events", ""),
            "own_count": d8.get("own_count", ""),
            "fork_count": d8.get("fork_count", ""),
            "own_touched": d8.get("own_touched", ""),
            "has_profile_repo": d8.get("has_profile_repo", ""),
            "repos_complete": d8.get("repos_complete", ""),
            "data_tier": "deep" if (lg in deep and deep[lg].get("http") == 200) else
                         ("screen" if (lg in screen and screen[lg].get("http") == 200) else "none"),
        })

    os.makedirs(OUT, exist_ok=True)
    csv_path = os.path.join(OUT, f"features_{key}.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # 摘要：覆盖与缺失率
    n = len(rows)
    filled = {c: sum(1 for r in rows if r[c] != "") for c in rows[0]}
    summary = {
        "accounts": n,
        "tier": dict(Counter(r["data_tier"] for r in rows)),
        "login_kind": dict(Counter(r["login_kind"] for r in rows)),
        "touched_rate": sum(r["fork_touched"] for r in rows) / max(n, 1),
        "burst_share": sum(r["in_burst"] for r in rows) / max(n, 1),
        "field_coverage": {k: v / max(n, 1) for k, v in filled.items()},
    }
    with open(os.path.join(OUT, f"features_{key}.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)

    print(f"账号 {n:,} → {os.path.basename(csv_path)}")
    print(f"  数据层级: {summary['tier']}")
    print(f"  登录名类型: {summary['login_kind']}")
    print(f"  fork 后动过率: {summary['touched_rate']:.2%} | 爆发窗占比: {summary['burst_share']:.2%}")
    miss = [(k, v) for k, v in summary["field_coverage"].items() if v < 0.5]
    print(f"  覆盖率 <50% 的字段: {[k for k, _ in miss] or '无'}")


if __name__ == "__main__":
    main()
