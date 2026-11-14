# -*- coding: utf-8 -*-
"""rea 总体分析 v2：把 10.8 万 forker 摊到「身份自证 × 批量性」两轴上。

v1 的两个缺陷（已修正）：
  1. 身份轴分档退化：`档案≥4 且 仓≥5` 把 87% 的人压进"中"，"低"只剩 3 人
     → 改成 0–5 分制，分档更均匀
  2. **循环论证**：批量性里包含了「爆发窗/同秒/数字名」，然后又拿它们当"判据区分度"，
     等于用定义验证定义
     → 批量性**只用时间事实**（同秒碰撞 / 爆发窗）定义；
        判据全部换成**与时间无关的画像特征**

输入：data/out/features_{repo}.csv
输出：data/out/analysis_{repo}.md
"""
import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "out")


def num(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def bucket_age(d):
    if d is None:
        return "未知"
    if d < 30:
        return "<30天"
    if d < 365:
        return "1-12月"
    if d < 1095:
        return "1-3年"
    if d < 2190:
        return "3-6年"
    return ">6年"


def bucket_repos(n):
    if n is None:
        return "未知"
    if n == 0:
        return "0"
    if n == 1:
        return "1"
    if n < 10:
        return "2-9"
    if n < 50:
        return "10-49"
    return "50+"


def bucket_followers(n):
    if n is None:
        return "未知"
    return "0" if n == 0 else ("1-9" if n < 10 else "10+")


def fmt_hour(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="morluto/rea")
    ap.add_argument("--key", default=None)
    args = ap.parse_args()
    key = args.key or args.repo.replace("/", "__")
    rows = list(csv.DictReader(open(os.path.join(OUT, f"features_{key}.csv"), encoding="utf-8-sig")))
    n = len(rows)
    have = [r for r in rows if r["data_tier"] != "none"]

    sec = Counter(r["fork_ts"] for r in rows if r["fork_ts"])
    collide = {s for s, c in sec.items() if c >= 2}

    def identity(r):
        """身份自证 0–5 分：档案填充、作品量、社交、可验证外链。"""
        filled = num(r["profile_filled"])
        repos = num(r["public_repos"])
        if filled is None or repos is None:
            return "无信息", None
        s = 0
        s += 1 if filled >= 4 else 0
        s += 1 if repos >= 10 else 0
        s += 1 if num(r["followers"], 0) >= 1 else 0
        s += 1 if r["has_bio"] == "1" else 0
        s += 1 if r["has_blog"] == "1" else 0
        level = "高" if s >= 4 else ("中" if s >= 2 else "低")
        return level, s

    def batch(r):
        """批量性**只**用时间事实：同一秒 ≥2 人 = 强机器信号；爆发窗 = 弱信号。"""
        if r["fork_ts"] in collide:
            return "高"
        if r["in_burst"] == "1":
            return "中"
        return "低"

    # 一、总体分布
    dist = {
        "号龄": Counter(bucket_age(num(r["account_age_days"])) for r in have),
        "仓数": Counter(bucket_repos(num(r["public_repos"])) for r in have),
        "粉丝": Counter(bucket_followers(num(r["followers"])) for r in have),
        "档案形状": Counter(r["profile_shape"] or "无信息" for r in have),
        "命名类型": Counter(r["login_kind"] for r in rows),
    }

    # 二、两轴矩阵
    matrix = defaultdict(int)
    id_score = {}
    for r in rows:
        lv, s = identity(r)
        id_score[r["login"]] = s
        matrix[(lv, batch(r))] += 1
    ids = ["高", "中", "低", "无信息"]
    bts = ["高", "中", "低"]

    # 三、判据：**与时间无关**的画像特征，看它们在三个批量档里的占比
    groups = {b: [r for r in rows if batch(r) == b] for b in bts}
    rules = [
        ("档案全空", lambda r: r["profile_shape"] == "EMPTY"),
        ("档案 RICH", lambda r: r["profile_shape"] == "RICH"),
        ("有 bio", lambda r: r["has_bio"] == "1"),
        ("有 blog", lambda r: r["has_blog"] == "1"),
        ("粉丝 = 0", lambda r: num(r["followers"]) == 0),
        ("粉丝 ≥10", lambda r: num(r["followers"], 0) >= 10),
        ("仓数 ≤ 1", lambda r: (num(r["public_repos"], 10 ** 6) or 10 ** 6) <= 1),
        ("仓数 ≥50", lambda r: num(r["public_repos"], 0) >= 50),
        ("号龄 <30 天", lambda r: (num(r["account_age_days"], 10 ** 6) or 10 ** 6) < 30),
        ("号龄 >6 年", lambda r: num(r["account_age_days"], 0) > 2190),
        ("数字名", lambda r: r["login_kind"] in ("TS", "PHONE", "NUM")),
        ("fork 后没动过", lambda r: r["fork_touched"] == "0"),
    ]

    def sh(g, pred):
        return sum(1 for r in g if pred(r)) / max(len(g), 1)

    # 四、同秒批次规模
    size_dist = Counter(sec[s] for s in collide)

    # 五、峰值时段
    by_hour = Counter()
    for r in rows:
        ts = num(r["fork_ts"])
        if ts:
            by_hour[ts // 3600] += 1
    top_hours = by_hour.most_common(5)

    L = []
    A = L.append
    A(f"# `{args.repo}` 总体分析\n")
    A(f"> 账号 **{n:,}** · 有档案 **{len(have):,}**（{len(have)/n*100:.1f}%）· 本地计算，0 请求\n")
    A("> 两轴定义：**身份自证**（0–5 分：档案≥4项 / 仓≥10 / 粉丝≥1 / 有 bio / 有 blog）"
      " × **批量性**（只用时间事实：同秒碰撞=高、爆发窗=中、其余=低）\n")

    A("\n## 一、总体分布\n")
    A("| 维度 | 分布 |")
    A("|---|---|")
    for k, c in dist.items():
        tot = sum(c.values())
        A(f"| **{k}** | " + " · ".join(f"{kk} {vv:,}（{vv/tot*100:.1f}%）" for kk, vv in c.most_common()) + " |")

    A("\n## 二、两轴矩阵\n")
    A("| 身份自证 \\ 批量性 | 高（同秒碰撞） | 中（爆发窗） | 低 | 合计 |")
    A("|---|---:|---:|---:|---:|")
    for i in ids:
        cells = [matrix[(i, b)] for b in bts]
        A(f"| **{i}** | {cells[0]:,} | {cells[1]:,} | {cells[2]:,} | {sum(cells):,} |")
    col = [sum(matrix[(i, b)] for i in ids) for b in bts]
    A(f"| **合计** | {col[0]:,} | {col[1]:,} | {col[2]:,} | {n:,} |")

    A("\n## 三、画像判据在三个批量档里的占比\n")
    A("> 全部是**与时间无关**的画像特征，因此不构成循环论证。\n")
    A("| 判据 | 高批量 | 中批量 | 低批量 | 高−低 |")
    A("|---|---:|---:|---:|---:|")
    for name, pred in rules:
        a, m, b = sh(groups["高"], pred), sh(groups["中"], pred), sh(groups["低"], pred)
        A(f"| {name} | {a*100:.1f}% | {m*100:.1f}% | {b*100:.1f}% | **{(a-b)*100:+.1f}pp** |")

    A("\n## 四、同秒碰撞的批次规模\n")
    A(f"> 有碰撞的账号 **{sum(size_dist[k]*k for k in size_dist):,}** 个，"
      f"分布在 **{len(collide):,}** 个「同一秒」里。\n")
    A("| 同秒人数 | 该规模的秒数 |")
    A("|---|---:|")
    for k in sorted(size_dist):
        A(f"| {k} 人 | {size_dist[k]:,} |")

    A("\n## 五、fork 峰值时段（UTC）\n")
    A("| 时段 | fork 数 | 数字名占比 |")
    A("|---|---:|---:|")
    for h, c in top_hours:
        numn = sum(1 for r in rows if num(r["fork_ts"]) and num(r["fork_ts"]) // 3600 == h
                   and r["login_kind"] in ("TS", "PHONE", "NUM"))
        A(f"| {fmt_hour(h*3600)} | {c:,} | {numn/c*100:.1f}% |")

    md = "\n".join(L) + "\n"
    dst = os.path.join(OUT, f"analysis_{key}.md")
    open(dst, "w", encoding="utf-8").write(md)
    print(md)
    print(f"→ {dst}")


if __name__ == "__main__":
    main()
