# -*- coding: utf-8 -*-
"""全量 fork 时间轴分析 —— 把「最新 100 人」的爆发窗结论扩展到全部 8 万次 fork。

读 data/raw/probe_forks_all.jsonl（由 --stage forks-all [--proxy] 产出），
复用 _forker_probe.detect_bursts 的滚动窗口算法，但作用于**整个仓库生命周期**。

输出：
    * 全量按日/按小时的 fork 速率与爆发窗标记
    * 10-19 爆发窗在全量时间轴上的异常度（z 分数 vs 全体窗口分布）
    * top 爆发窗表（开始时间 / 持续 / 数量 / z）

用法：
    py _forks_all_report.py            # 出报告到 data/out/forks_all_report.md
"""
import sys, os, json, math
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from crawler.config import RAW_DIR, OUT_DIR, iso_to_ts, ts_to_iso

SRC = os.path.join(RAW_DIR, "probe_forks_all.jsonl")
OUT = os.path.join(OUT_DIR, "forks_all_report.md")
WINDOW_H, Z = 3, 2.0


def load() -> list:
    """返回全部 (fork_id, ts, login, pushed)。半行 JSON（中断残留）跳过。"""
    rows = []
    if not os.path.exists(SRC):
        return rows
    with open(SRC, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            ts = iso_to_ts(o.get("fork_created_at") or o.get("created_at") or "")
            if ts:
                rows.append((o.get("fork_id"), ts, o.get("login") or ""))
    return rows


def window_stats(hours: list):
    """滚动 3h 窗口统计：返回 (sums, mean, var)。hours = 整点小时列表。"""
    if not hours:
        return [], 0.0, 0.0
    lo, hi = min(hours), max(hours)
    dense = [0] * (hi - lo + 1)
    for h in hours:
        dense[h - lo] += 1
    n = len(dense)
    w = min(WINDOW_H, n)
    sums = [sum(dense[i:i + w]) for i in range(n - w + 1)]
    mean = sum(sums) / len(sums)
    var = sum((x - mean) ** 2 for x in sums) / len(sums)
    return sums, mean, var


def fmt_hour(h: int) -> str:
    return ts_to_iso(h * 3600)[:13] + ":00"


def main() -> int:
    rows = load()
    if not rows:
        print(f"[forks-all-report] 没有数据：{SRC} 不存在或为空")
        return 1

    total = len(rows)
    hours = [int(ts // 3600) for _, ts, _ in rows]
    day_counts = Counter(ts // 86400 for _, ts, _ in rows)

    sums, mean, var = window_stats(hours)
    sd = math.sqrt(var)
    thr = mean + Z * sd

    # 找出全部超阈值窗口（z 降序），合并相邻窗口
    hot = [(i, s, (s - mean) / sd if sd else 0) for i, s in enumerate(sums) if s > thr]
    hot.sort(key=lambda x: -x[2])
    lo = min(hours)
    merged = []
    for i, s, zv in hot:
        start = lo + i
        if merged and start <= merged[-1][1] + 1:
            if s > merged[-1][2]:
                merged[-1] = (merged[-1][0], start + WINDOW_H - 1, s, zv)
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], start + WINDOW_H - 1),
                              merged[-1][2], merged[-1][3])
        else:
            merged.append((start, start + WINDOW_H - 1, s, zv))

    days_span = (max(ts for _, ts, _ in rows) - min(ts for _, ts, _ in rows)) / 86400
    top_days = day_counts.most_common(15)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"# `openclaw/openclaw` 全量 fork 时间轴分析\n\n")
        f.write(f"> 数据：{total:,} 条 fork（probe_forks_all.jsonl，API sort=oldest 全量翻页）\n")
        f.write(f"> 采集：2026-10-24，快代理出口（用户决策，见 README 决策记录）\n")
        f.write(f"> 口径：滚动 {WINDOW_H}h 窗口，阈值 = 均值 + {Z}σ（数据自标定）\n\n")

        f.write("## 一、总览\n\n| 指标 | 值 |\n|---|---:|\n")
        f.write(f"| fork 总数（可列出） | {total:,} |\n")
        f.write(f"| 时间跨度 | {ts_to_iso(min(ts for _, ts, _ in rows))[:10]} → "
                f"{ts_to_iso(max(ts for _, ts, _ in rows))[:10]}（{days_span:.0f} 天） |\n")
        f.write(f"| 日均 | {total / days_span:.0f} 次/天 |\n")
        f.write(f"| 窗口均值 μ / σ | {mean:.1f} / {sd:.1f}（每 {WINDOW_H}h） |\n")
        f.write(f"| 超阈值窗口数 | {len(merged)} 个 |\n\n")

        f.write(f"## 二、Top 爆发窗（滚动 {WINDOW_H}h，按 z 降序，最多 20 个）\n\n")
        f.write("| # | 窗口起点 (UTC) | 持续 | fork 数 | z |\n|---|---|---|---:|---:|\n")
        for k, (s, e, cnt, zv) in enumerate(merged[:20], 1):
            f.write(f"| {k} | {fmt_hour(s)} | {e - s + 1}h | {cnt:,} | +{zv:.1f}σ |\n")
        f.write("\n")

        f.write("## 三、Top 天数（按日 fork 数）\n\n| 日期 | fork 数 | 占全量 |\n|---|---:|---:|\n")
        for d, c in top_days:
            f.write(f"| {ts_to_iso(d * 86400)[:10]} | {c:,} | {c / total * 100:.2f}% |\n")
        f.write("\n")

        # 10-19 爆发窗在全量中的位置
        probe_day = None
        for d in day_counts:
            if ts_to_iso(d * 86400)[:10] == "2026-10-19":
                probe_day = (d, day_counts[d])
        f.write("## 四、10-19 样本日的全量视角\n\n")
        if probe_day:
            d, c = probe_day
            rank = sorted(day_counts.values(), reverse=True).index(c) + 1
            f.write(f"2026-10-19 全天 {c:,} 次 fork，在 {len(day_counts)} 个有 fork 的天里排第 {rank} 名，"
                    f"占全量 {c / total * 100:.2f}%。\n\n")
            f.write(f"> 之前「最新 100 人」样本里 34% 落在 10-19 18:00–21:59，是**最新一页的**局部现象；\n")
            f.write(f"> 全量时间轴给出的才是基准：该日若在 Top 表里靠前，说明聚集异常度可量化；\n")
            f.write(f"> 若只是中游，则说明这种峰值在仓库生命周期里属常态波动。\n\n")
        else:
            f.write("2026-10-19 不在数据里（全量名单位于该日之前或之后，或采集窗口不含该日）。\n\n")

    print(f"[forks-all-report] {total:,} 条 → {OUT}")
    print(f"  爆发窗 {len(merged)} 个；μ={mean:.1f} σ={sd:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
