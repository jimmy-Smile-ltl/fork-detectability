# -*- coding: utf-8 -*-
"""画 fork 增长时间轴（改进版）：全景 + 爆发窗放大 + 归一化累积曲线。

对应总纲 §1.3：rea 58.5% 的 fork 挤在极少数 3 小时窗口里。

输出（figs/）：
    fork_timeline_rea.png          上下两栏：全景 + 爆发期放大
    fork_timeline_openclaw.png     同上
    fork_cumulative_norm.png       按"起点后天数"归一化的累积曲线（两仓可直接比）

用法：py _plot_fork_timeline.py
"""
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "figs")
os.makedirs(OUT, exist_ok=True)

BURST_WINDOW_H = 3
BURST_Z = 2.0

for f in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"):
    try:
        matplotlib.font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [f]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False


def load_forks(path):
    ts = []
    if not os.path.exists(path):
        return ts
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        s = (r.get("created_at") or r.get("fork_created_at") or r.get("forked_at") or "")
        if not s:
            continue
        try:
            dt = datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
            ts.append(dt.replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    ts.sort()
    return ts


def burst(ts):
    hours = [int(t // 3600) for t in ts]
    if len(hours) < 10:
        return set(), None, []
    lo, hi = min(hours), max(hours)
    dense = [0] * (hi - lo + 1)
    for h in hours:
        dense[h - lo] += 1
    w = min(BURST_WINDOW_H, len(dense))
    sums = [sum(dense[i:i + w]) for i in range(len(dense) - w + 1)]
    mean = sum(sums) / len(sums)
    sd = math.sqrt(sum((x - mean) ** 2 for x in sums) / len(sums))
    thr = mean + BURST_Z * sd
    flagged, top = set(), []
    for i, s in enumerate(sums):
        if s > thr:
            top.append((lo + i, s))
            for h in range(lo + i, lo + i + w):
                flagged.add(h)
    top.sort(key=lambda x: -x[1])
    share = sum(1 for h in hours if h in flagged) / len(hours)
    return flagged, share, top


def hourly(ts):
    if not ts:
        return [], []
    lo = int(min(ts) // 3600)
    hi = int(max(ts) // 3600)
    cnt = [0] * (hi - lo + 1)
    for t in ts:
        cnt[int(t // 3600) - lo] += 1
    x = [datetime.fromtimestamp((lo + i) * 3600, tz=timezone.utc) for i in range(len(cnt))]
    return x, cnt


def shade(ax, flagged, label=True):
    first = True
    for h in sorted(flagged):
        d = datetime.fromtimestamp(h * 3600, tz=timezone.utc)
        ax.axvspan(d, d + timedelta(hours=1), color="#e5484d", alpha=0.25,
                   label="爆发窗（滚动 3h 超过 μ+2σ）" if (first and label) else None)
        first = False


def plot_one(tag, title, path):
    ts = load_forks(path)
    if not ts:
        print(f"{tag}: 无数据")
        return None
    flagged, share, top = burst(ts)
    x, cnt = hourly(ts)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.4), dpi=150,
                                   gridspec_kw={"height_ratios": [1, 1.25]})

    # 上：全景
    shade(ax1, flagged)
    ax1.plot(x, cnt, lw=0.7, color="#1f6feb")
    ax1.fill_between(x, cnt, color="#1f6feb", alpha=0.15)
    ax1.set_title(f"{title}　逐小时 fork 数（共 {len(ts):,} 个 fork）", fontsize=11)
    ax1.set_ylabel("fork / 小时", fontsize=9)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax1.tick_params(labelsize=8)
    ax1.grid(alpha=0.25, lw=0.5)
    ax1.legend(fontsize=8, loc="upper left")

    # 下：峰值前后 12 天，按天聚合
    peak = top[0][0] if top else int(max(ts) // 3600)
    pdt = datetime.fromtimestamp(peak * 3600, tz=timezone.utc)
    zoom_lo, zoom_hi = pdt - timedelta(days=12), pdt + timedelta(days=12)
    dcount = {}
    for t in ts:
        d = datetime.fromtimestamp(t, tz=timezone.utc).replace(hour=0, minute=0, second=0)
        if zoom_lo <= d <= zoom_hi:
            dcount[d] = dcount.get(d, 0) + 1
    days = sorted(dcount)
    ax2.bar(days, [dcount[d] for d in days], width=0.8, color="#1f6feb", alpha=0.85)
    bdays = {datetime.fromtimestamp(h * 3600, tz=timezone.utc).replace(hour=0, minute=0, second=0)
             for h in flagged}
    for d in sorted(bdays & set(days)):
        ax2.axvspan(d, d + timedelta(days=1), color="#e5484d", alpha=0.22)
    ax2.set_title("放大：峰值前后 24 天，按天聚合（红色 = 含爆发窗的日期）", fontsize=10)
    ax2.set_ylabel("fork / 天", fontsize=9)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax2.xaxis.set_major_locator(mticker.MaxNLocator(10))
    ax2.tick_params(labelsize=8)
    ax2.grid(alpha=0.25, lw=0.5, axis="y")

    txt = f"爆发窗占比 {share*100:.1f}%"
    if top:
        txt += f"\n最高 3 小时窗口 {top[0][1]:,} 个"
        txt += f"\n峰值日 {pdt.strftime('%Y-%m-%d')}"
    ax1.text(0.99, 0.95, txt, transform=ax1.transAxes, ha="right", va="top",
             fontsize=9, bbox=dict(fc="white", ec="#e5484d", alpha=0.95, lw=0.8))

    fig.tight_layout()
    dst = os.path.join(OUT, f"fork_timeline_{tag}.png")
    fig.savefig(dst)
    plt.close(fig)
    print(f"{tag}: {len(ts):,} fork | 爆发窗 {share*100:.1f}% | top3h {top[0][1] if top else 0} → {dst}")
    return ts


def plot_norm(series):
    """归一化累积：x = **距峰值日的天数**（负=峰值前），y = 累积占比。

    为什么不用"自首个 fork 起的天数"：两仓首个 fork 距爆发期差 60–100 天，
    横轴会被拉平，看不出集中度。以峰值日为参照点才可比。
    """
    fig, ax = plt.subplots(figsize=(9, 4.4), dpi=150)
    colors = {"rea": "#e5484d", "openclaw": "#1f6feb"}
    for tag, ts in series.items():
        if not ts:
            continue
        # 用"最大单日 fork 数"的那天作为峰值日
        days = {}
        for t in ts:
            d = datetime.fromtimestamp(t, tz=timezone.utc).replace(hour=0, minute=0, second=0)
            days[d] = days.get(d, 0) + 1
        peak_day = max(days, key=lambda d: days[d])
        xs = [(t - peak_day.timestamp()) / 86400 for t in ts]
        ys = [(i + 1) / len(ts) * 100 for i in range(len(ts))]
        ax.plot(xs, ys, lw=1.6, color=colors.get(tag, "gray"),
                label=f"{tag}（{len(ts):,} 个，峰值日 {peak_day:%Y-%m-%d} 共 {days[peak_day]:,} 个）")
        for q in (50, 90):
            for xi, yi in zip(xs, ys):
                if yi >= q:
                    ax.plot([xi], [q], "o", ms=3.5, color=colors.get(tag, "gray"))
                    ax.annotate(f"{q}% @ {xi:+.1f} 天", (xi, q),
                                textcoords="offset points", xytext=(7, -11),
                                fontsize=8, color=colors.get(tag, "gray"))
                    break
    ax.axvline(0, color="#888", lw=0.8, ls="--")
    ax.set_xlim(-30, 30)
    ax.set_xlabel("距「峰值日」的天数（0 = 单日 fork 数最高的那天）", fontsize=9)
    ax.set_ylabel("累积 fork 占比 %", fontsize=9)
    ax.set_title("以峰值日对齐的累积曲线：竖线越陡 = 增长越集中在同一时点", fontsize=11)
    ax.grid(alpha=0.25, lw=0.5)
    ax.tick_params(labelsize=8)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    dst = os.path.join(OUT, "fork_cumulative_norm.png")
    fig.savefig(dst)
    plt.close(fig)
    print(f"峰值对齐累积 → {dst}")


def main():
    series = {}
    for tag, title, fn in (
        ("rea", "morluto/rea", "forks_all_morluto__rea.jsonl"),
        ("openclaw", "openclaw/openclaw", "forks_all_openclaw__openclaw.jsonl"),
    ):
        series[tag] = plot_one(tag, title, os.path.join(RAW, fn))
    plot_norm(series)


if __name__ == "__main__":
    main()
