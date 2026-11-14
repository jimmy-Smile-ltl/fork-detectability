# -*- coding: utf-8 -*-
"""图 18-19：时间控制专用图（本报告方法学的核心一课）。

图 18  按 fork 龄分桶看两个特征：
       (a) fork 后动过率   —— 控制后差异反而更大（真判据）
       (b) 自建仓 fork 后更新过 —— 控制后差异消失（假判据）

图 19  两仓的 fork 龄分布对比 —— 解释为什么必须控制

用法：py _make_time_confsound_figures.py
"""
import csv
import os
import sys
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "out")
FIG = os.path.join(BASE, "figs")
os.makedirs(FIG, exist_ok=True)

RED, BLUE, GRAY = "#e5484d", "#1f6feb", "#8b949e"
for f in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"):
    try:
        matplotlib.font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [f]
        break
    except Exception:
        continue
plt.rcParams.update({
    "axes.unicode_minus": False, "axes.edgecolor": "#d0d7de", "axes.linewidth": 0.8,
    "figure.facecolor": "white", "grid.color": "#eaeef2", "grid.linewidth": 0.7,
})


def load(tag):
    p = os.path.join(OUT, f"features_{tag}.csv")
    with open(p, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


BUCKETS = [(0, 7, "≤7天"), (7, 30, "8–30天"), (30, 60, "31–60天"),
           (60, 180, "61–180天"), (180, 10 ** 6, ">180天")]


def rate_by_bucket(rows, key):
    out = []
    for lo, hi, _ in BUCKETS:
        sel = [r for r in rows
               if num(r.get("fork_age_days")) is not None
               and lo < num(r.get("fork_age_days")) <= hi
               and r.get(key) not in (None, "")]
        if not sel:
            out.append((None, 0))
            continue
        hit = sum(1 for r in sel if r.get(key) == "1")
        out.append((hit / len(sel) * 100, len(sel)))
    return out


def fig_time_control(rea, oc):
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.6), dpi=170)
    specs = [
        ("fork_touched", "(a) fork 后动过率：控制时间后差异更大（31–33pp）"),
        ("own_updated_after_fork", "(b) 自建仓 fork 后更新过：差异被放大，但控制后仍剩 17–24pp"),
    ]
    x = np.arange(len(BUCKETS))
    w = 0.36
    for ax, (key, title) in zip(axes, specs):
        ra = rate_by_bucket(rea, key)
        rb = rate_by_bucket(oc, key)
        va = [v if v is not None else 0 for v, _ in ra]
        vb = [v if v is not None else 0 for v, _ in rb]
        na = [n for _, n in ra]
        nb = [n for _, n in rb]
        b1 = ax.bar(x - w / 2, va, w, label="rea", color=RED, alpha=0.88)
        b2 = ax.bar(x + w / 2, vb, w, label="openclaw", color=BLUE, alpha=0.88)
        for bars, vals, ns in ((b1, va, na), (b2, vb, nb)):
            for b, v, n in zip(bars, vals, ns):
                if n == 0:
                    continue
                ax.text(b.get_x() + b.get_width() / 2, v + 1.2,
                        f"{v:.1f}%", ha="center", fontsize=7.5, fontweight="bold")
                ax.text(b.get_x() + b.get_width() / 2, -4.5,
                        f"n={n:,}", ha="center", fontsize=6.5, color=GRAY)
        ax.set_xticks(x)
        ax.set_xticklabels([b[2] for b in BUCKETS], fontsize=9)
        ax.set_xlabel("fork 龄（距今天数分桶）", fontsize=9.5)
        ax.set_ylabel("占比 %", fontsize=9.5)
        ax.set_title(title, fontsize=10.5)
        ax.set_ylim(-6, 105)
        ax.grid(axis="y", alpha=0.55)
        ax.set_axisbelow(True)
        ax.legend(fontsize=9, frameon=False)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    fig.suptitle("时间控制实验：分桶后 (a) 判据更稳，(b) 差异被高估约 1.5 倍但并未消失",
                 fontsize=12.5, y=1.02)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "18_time_control.png"), bbox_inches="tight")
    plt.close(fig)
    print("18_time_control.png")


def fig_age_dist(rea, oc):
    fig, ax = plt.subplots(figsize=(9.2, 4.2), dpi=170)
    x = np.arange(len(BUCKETS))
    w = 0.36
    ca = [rate_by_bucket(rea, "fork_touched")[i][1] for i in range(len(BUCKETS))]
    cb = [rate_by_bucket(oc, "fork_touched")[i][1] for i in range(len(BUCKETS))]
    ta, tb = sum(ca) or 1, sum(cb) or 1
    pa = [c / ta * 100 for c in ca]
    pb = [c / tb * 100 for c in cb]
    b1 = ax.bar(x - w / 2, pa, w, label="rea（中位 30.6 天）", color=RED, alpha=0.88)
    b2 = ax.bar(x + w / 2, pb, w, label="openclaw（中位 258.5 天）", color=BLUE, alpha=0.88)
    for bars, vals in ((b1, pa), (b2, pb)):
        for b, v in zip(bars, vals):
            if v >= 1:
                ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.0f}%",
                        ha="center", fontsize=8.5, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([b[2] for b in BUCKETS], fontsize=9.5)
    ax.set_xlabel("fork 龄分桶", fontsize=10)
    ax.set_ylabel("该桶人数占全体的比例 %", fontsize=10)
    ax.set_title("两仓的 fork 龄分布：openclaw 有 97% 的 fork 超过 180 天，rea 几乎都在 30 天内",
                 fontsize=11.5, pad=10)
    ax.grid(axis="y", alpha=0.55)
    ax.set_axisbelow(True)
    ax.legend(fontsize=10, frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "19_fork_age_dist.png"))
    plt.close(fig)
    print("19_fork_age_dist.png")


def main():
    rea, oc = load("rea"), load("openclaw")
    fig_time_control(rea, oc)
    fig_age_dist(rea, oc)
    # 打印关键数字，便于写报告
    for key in ("fork_touched", "own_updated_after_fork"):
        print(f"\n{key}:")
        for lbl, (va, na), (vb, nb) in zip([b[2] for b in BUCKETS],
                                           rate_by_bucket(rea, key),
                                           rate_by_bucket(oc, key)):
            fa = f"{va:.1f}%" if va is not None else "—"
            fb = f"{vb:.1f}%" if vb is not None else "—"
            print(f"  {lbl:10s} rea {fa:>6s} (n={na:>7,}) | oc {fb:>6s} (n={nb:>7,})")


if __name__ == "__main__":
    main()
