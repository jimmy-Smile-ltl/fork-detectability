# -*- coding: utf-8 -*-
"""fork list 与账户质量的完整统计（供报告引用）。

    python scripts/analysis/_fork_quality_stats.py

输出 data/out/fork_quality_stats.md
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")

REPOS = [
    ("rea", "morluto__rea", "主题·中文历史地图"),
    ("openclaw", "openclaw__openclaw", "主题·AI 工具"),
    ("BettaFish", "666ghj__BettaFish", "测试·中文 AI"),
    ("shardeum", "shardeum__shardeum", "对照·已知造假"),
    ("union", "unionlabs__union", "对照·已知造假"),
    ("anoma", "anoma__anoma", "对照·已知造假"),
    ("ollama", "ollama__ollama", "对照·干净"),
    ("langchain", "langchain-ai__langchain", "对照·干净"),
]


def ts(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s).replace("Z", "")[:19],
                                 "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def gini(vals):
    """基尼系数（衡量 fork 集中度）。"""
    v = sorted(x for x in vals if x is not None)
    n = len(v)
    if n < 2 or sum(v) == 0:
        return None
    cum = 0.0
    for i, x in enumerate(v, 1):
        cum += i * x
    return (2 * cum) / (n * sum(v)) - (n + 1) / n


def load_forks(fork_file):
    rows = []
    p = os.path.join(RAW, f"forks_all_{fork_file}.jsonl")
    if not os.path.exists(p):
        return rows
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("login"):
            rows.append(r)
    return rows


def analyze_fork_list(tag, fork_file, note):
    rows = load_forks(fork_file)
    n = len(rows)
    if not n:
        return None
    d = {"tag": tag, "note": note, "n": n}

    # --- 时间分布 ---
    times = [t for t in (ts(r.get("fork_created_at")) for r in rows) if t]
    d["n_time"] = len(times)
    if times:
        days = Counter(int(t // 86400) for t in times)
        hours = Counter(int(t // 3600) for t in times)
        d["days"] = len(days)
        d["span_days"] = (max(times) - min(times)) / 86400
        top_day, top_cnt = days.most_common(1)[0]
        d["peak_day"] = datetime.fromtimestamp(top_day * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
        d["peak_day_cnt"] = top_cnt
        d["peak_day_share"] = top_cnt / len(times) * 100
        # 3 小时滚动窗口
        dense = [0] * (max(hours) - min(hours) + 1)
        lo = min(hours)
        for h, c in hours.items():
            dense[h - lo] = c
        w = 3
        sums = [sum(dense[i:i + w]) for i in range(len(dense) - w + 1)]
        if sums:
            m = statistics.mean(sums)
            sd = statistics.pstdev(sums)
            thr = m + 2 * sd
            flagged = set()
            for i, s in enumerate(sums):
                if s > thr:
                    for h in range(lo + i, lo + i + w):
                        flagged.add(h)
            inb = sum(1 for t in times if int(t // 3600) in flagged)
            d["burst_share"] = inb / len(times) * 100
            d["burst_thr"] = thr
        # 集中度
        day_vals = [c for _, c in days.items()]
        d["gini_day"] = gini(day_vals)
        d["top1pct_day_share"] = (sum(sorted(day_vals, reverse=True)[:max(1, len(day_vals) // 100)])
                                  / sum(day_vals) * 100)
    # --- fork 自身属性 ---
    d["touched"] = sum(1 for r in rows
                       if ts(r.get("fork_pushed_at")) and ts(r.get("fork_created_at"))
                       and ts(r["fork_pushed_at"]) > ts(r["fork_created_at"]))
    d["touched_pct"] = d["touched"] / n * 100
    sizes = [r.get("size_kb") for r in rows if isinstance(r.get("size_kb"), (int, float))]
    d["size_med"] = statistics.median(sizes) if sizes else None
    d["size_zero"] = sum(1 for x in sizes if x == 0)
    d["size_zero_pct"] = d["size_zero"] / len(sizes) * 100 if sizes else None
    br = Counter((r.get("default_branch") or "").lower() for r in rows)
    d["branch_main"] = (br.get("main", 0) + br.get("master", 0)) / n * 100
    d["lang_nonnull"] = sum(1 for r in rows if r.get("fork_language")) / n * 100
    st = [r.get("fork_stars") for r in rows if isinstance(r.get("fork_stars"), (int, float))]
    d["fork_stars_zero"] = sum(1 for x in st if x == 0) / len(st) * 100 if st else None
    d["org_pct"] = sum(1 for r in rows if (r.get("owner_type") or "") == "Organization") / n * 100
    return d


def quality_stats(tag):
    """从特征表读账户质量指标。"""
    p = os.path.join(OUT, f"features_{tag.lower() if tag != 'BettaFish' else 'bettafish'}.csv")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(fh))

    def rate(k, v="1"):
        x = [r for r in rows if r.get(k) not in (None, "")]
        if not x:
            return None, 0
        return sum(1 for r in x if str(r[k]) == v) / len(x) * 100, len(x)

    def med(k):
        vals = []
        for r in rows:
            s = r.get(k)
            if s in (None, ""):
                continue
            try:
                vals.append(float(s))
            except ValueError:
                pass
        vals.sort()
        return (vals[len(vals) // 2], len(vals)) if vals else (None, 0)

    q = {"n": len(rows)}
    for k in ("has_own", "commit_zero", "followers_zero", "following_zero",
              "avatar_photo", "repo_named_target", "has_profile_repo",
              "has_pull_shark", "has_collab", "active_le5", "fork_touched"):
        q[k], q[k + "_n"] = rate(k)
    for k in ("acct_age_days", "followers", "own_count", "commit_total",
              "starred_count", "active_days", "filled_n"):
        q[k], q[k + "_n"] = med(k)
    return q


def main() -> int:
    L = ["# fork list 与账户质量：完整统计", "",
         "> 全部数字由 `_fork_quality_stats.py` 从 `data/raw/` + `data/out/features_*.csv` 直接计算。", ""]

    # ---------- 第一部分：fork list ----------
    L += ["## 一、fork 名单的形态", "",
          "| 仓库 | 性质 | forker | 覆盖天数 | 峰值日 | 峰值占比 | 爆发窗占比 | 日集中度(基尼) |",
          "|---|---|---:|---:|---|---:|---:|---:|"]
    fork_stats = []
    for tag, ff, note in REPOS:
        d = analyze_fork_list(tag, ff, note)
        if not d:
            continue
        fork_stats.append(d)
        L.append(f"| **{tag}** | {note} | {d['n']:,} | {d.get('days',0):,} | "
                 f"{d.get('peak_day','—')} | {d.get('peak_day_share',0):.2f}% | "
                 f"**{d.get('burst_share',0):.1f}%** | {d.get('gini_day') or 0:.3f} |")

    L += ["", "### 1.1 fork 自身的属性（0 请求得到）", "",
          "| 仓库 | fork 后动过 | 体积中位(KB) | 空 fork 占比 | 分支标准 | 有语言 | fork 星=0 | 组织 fork |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for d in fork_stats:
        L.append(f"| {d['tag']} | **{d['touched_pct']:.1f}%** | {d['size_med'] or 0:,.0f} | "
                 f"{d['size_zero_pct'] or 0:.1f}% | {d['branch_main']:.1f}% | "
                 f"{d['lang_nonnull']:.1f}% | {d['fork_stars_zero'] or 0:.1f}% | {d['org_pct']:.2f}% |")

    # ---------- 第二部分：账户质量 ----------
    L += ["", "## 二、fork 账户质量", ""]
    qs = {}
    for tag, ff, note in REPOS:
        q = quality_stats(tag)
        if q:
            qs[tag] = q

    L += ["### 2.1 八项质量指标（比例）", "",
          "| 仓库 | 有自建仓 | 零提交 | 零粉丝 | 零关注 | 自传头像 | 有主页仓 | Pull Shark | 协作徽章 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for tag in qs:
        q = qs[tag]
        def f(k):
            return f"{q[k]:.1f}%" if q.get(k) is not None else "—"
        L.append(f"| **{tag}** | {f('has_own')} | {f('commit_zero')} | {f('followers_zero')} | "
                 f"{f('following_zero')} | {f('avatar_photo')} | {f('has_profile_repo')} | "
                 f"{f('has_pull_shark')} | {f('has_collab')} |")

    L += ["", "### 2.2 质量画像（中位数）", "",
          "| 仓库 | 账号年龄(天) | 粉丝 | 自建仓 | 提交数 | star 数 | 档案填充 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for tag in qs:
        q = qs[tag]
        def m(k):
            v = q.get(k)
            return f"{v:,.0f}" if v is not None else "—"
        L.append(f"| **{tag}** | {m('acct_age_days')} | {m('followers')} | {m('own_count')} | "
                 f"{m('commit_total')} | {m('starred_count')} | {m('filled_n')} |")

    # ---------- 第三部分：质量分层 ----------
    L += ["", "### 2.3 质量分层（三个门槛）", "",
          "把每个账号按「是否有自建仓」「是否有提交」「是否零粉丝」分层：", "",
          "| 仓库 | A 扎实（有仓+有提交+有粉丝） | B 一般（有仓+有提交） | C 薄（有仓但零提交） | D 空（无自建仓） |",
          "|---|---:|---:|---:|---:|"]
    for tag in qs:
        q = qs[tag]
        n = q["n"]
        # 用比例估算（各指标分母可能不同，这里以 has_own/commit_zero 的分母为准做近似）
        has_own = q["has_own"]
        commit_zero = q["commit_zero"]
        fz = q["followers_zero"]
        if has_own is None or commit_zero is None or fz is None:
            L.append(f"| {tag} | — | — | — | — |")
            continue
        d = 100 - has_own
        c = has_own * commit_zero / 100
        b = has_own - c
        a = b * (100 - fz) / 100
        b2 = b * fz / 100
        L.append(f"| **{tag}** | {a:.1f}% | {b2:.1f}% | {c:.1f}% | {d:.1f}% |")

    # ---------- 第四部分：判据区分度 ----------
    L += ["", "## 三、判据区分度（造假组 vs 干净组）", "",
          "| 判据 | 造假三仓均值 | 干净五仓均值 | 差值 | 是否分离 |",
          "|---|---:|---:|---:|:---:|"]
    fake = [t for t in qs if t in ("shardeum", "union", "anoma")]
    clean = [t for t in qs if t in ("rea", "openclaw", "BettaFish", "ollama", "langchain")]
    for k, name in (("has_own", "有自建仓"), ("commit_zero", "零提交"),
                    ("followers_zero", "零粉丝"), ("fork_touched", "fork 后动过"),
                    ("avatar_photo", "自传头像"), ("repo_named_target", "仓名含目标")):
        fv = [qs[t][k] for t in fake if qs[t].get(k) is not None]
        cv = [qs[t][k] for t in clean if qs[t].get(k) is not None]
        if not fv or not cv:
            continue
        fm, cm = statistics.mean(fv), statistics.mean(cv)
        sep = "✅ 分离" if (min(fv) > max(cv) or max(fv) < min(cv)) else "❌ 重叠"
        L.append(f"| {name} | {fm:.1f}% ({min(fv):.1f}–{max(fv):.1f}) | "
                 f"{cm:.1f}% ({min(cv):.1f}–{max(cv):.1f}) | {fm-cm:+.1f}pp | {sep} |")

    # 中位数类
    L += ["", "| 判据(中位) | 造假三仓 | 干净五仓 | 是否分离 |", "|---|---|---|:---:|"]
    for k, name in (("commit_total", "提交数"), ("own_count", "自建仓数"),
                    ("acct_age_days", "账号年龄")):
        fv = [qs[t][k] for t in fake if qs[t].get(k) is not None]
        cv = [qs[t][k] for t in clean if qs[t].get(k) is not None]
        if not fv or not cv:
            continue
        sep = "✅" if (min(fv) > max(cv) or max(fv) < min(cv)) else "❌"
        L.append(f"| {name} | {'/'.join(f'{x:,.0f}' for x in fv)} | "
                 f"{'/'.join(f'{x:,.0f}' for x in cv)} | {sep} |")

    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "fork_quality_stats.md")
    open(dst, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n→ {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
