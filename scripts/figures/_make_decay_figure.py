# -*- coding: utf-8 -*-
"""判据衰减图：同一条判据，随着对照变干净，间距如何缩水。

    python scripts/figures/_make_decay_figure.py

横轴是"对照规模"的三个阶段，纵轴是 A/B 两组之间的间距（pp）。
0 表示两组区间重叠（判据失效）。
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import FIG  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

STAGES = ["3A / 2B\n（8 仓 · 25 万人）", "4A / 4B\n（未补齐）", "4A / 4B\n（全量）"]

# (判据, [三阶段间距 pp], 最终是否显著)
# 0 = 两组区间重叠（判据在该阶段完全失效）
JUDGES = [
    ("Pull Shark",      [32.3, 11.1, 12.3], True),
    ("协作徽章",         [20.1,  1.4,  2.0], False),
    ("零提交",           [14.6,  5.5,  6.7], True),
    ("自传头像",         [12.9,  0.0,  2.1], False),
    ("零粉丝",           [12.0,  0.3,  6.8], True),
    ("零关注",           [12.0,  0.0,  2.8], False),
    ("有自建仓",         [11.6,  6.7, 10.2], True),
]

COLOR = {
    "Pull Shark": "#d62728",
    "协作徽章": "#9467bd",
    "零提交": "#2ca02c",
    "自传头像": "#8c564b",
    "零粉丝": "#1f77b4",
    "零关注": "#7f7f7f",
    "有自建仓": "#ff7f0e",
}


def main() -> int:
    fig, ax = plt.subplots(figsize=(12.5, 7))

    # 噪声区：±3pp 以内基本无法与抽样误差区分
    ax.axhspan(0, 3.0, color="gray", alpha=0.15, zorder=0)
    ax.text(0.02, 1.4, "噪声区（<3pp，与抽样误差同级）", fontsize=9.5,
            color="#555", va="center")
    ax.axhline(0, color="black", lw=0.8, ls="-")

    xs = range(len(STAGES))

    # 末端标签防重叠：按最终值排序，间距 <1.6pp 的错开
    order = sorted(range(len(JUDGES)), key=lambda i: JUDGES[i][1][-1])
    offsets = {}
    last_y, stack = None, 0
    for i in order:
        y = JUDGES[i][1][-1]
        if last_y is not None and abs(y - last_y) < 1.6:
            stack += 1
        else:
            stack = 0
        offsets[i] = stack * 11 * (1 if stack % 2 else -1) if stack else 0
        last_y = y

    for idx, (name, ys, sig) in enumerate(JUDGES):
        ax.plot(xs, ys, marker="o", markersize=9 if sig else 7,
                lw=2.6 if sig else 1.6,
                ls="-" if sig else "--",
                color=COLOR[name], alpha=1.0 if sig else 0.65,
                zorder=3 if sig else 2, label=name)
        ax.annotate(f"{ys[-1]:.1f}", (xs[-1], ys[-1]),
                    xytext=(10, offsets[idx]), textcoords="offset points",
                    fontsize=9.5, va="center",
                    color=COLOR[name],
                    fontweight="bold" if sig else "normal")

    # 首端标注（只在最高处标，避免拥挤）
    ax.annotate("32.3", (0, 32.3), xytext=(-30, 4), textcoords="offset points",
                fontsize=9.5, color=COLOR["Pull Shark"], fontweight="bold")

    ax.set_xticks(list(xs))
    ax.set_xticklabels(STAGES, fontsize=11)
    ax.set_ylabel("A/B 两组之间的间距（百分点）", fontsize=12)
    ax.set_xlabel("对照规模", fontsize=12)
    ax.set_ylim(-1.5, 36)
    ax.set_xlim(-0.35, len(STAGES) - 0.55)
    ax.grid(axis="y", alpha=0.3)

    ax.set_title("图 23 ｜ 每加一个干净对照，判据就弱一分",
                 fontsize=15, pad=16)

    # 两次关键事件注解
    ax.annotate("加入 frp 作为对照\n32.3 → 11.1pp", xy=(0.55, 22),
                fontsize=9, color="#333",
                bbox=dict(boxstyle="round,pad=0.4", fc="#fff3cd", ec="#e0c068"))
    ax.annotate("补齐 D15 / D6 /\nagency-agents 样本\n部分判据回升", xy=(1.62, 15),
                fontsize=9, color="#333",
                bbox=dict(boxstyle="round,pad=0.4", fc="#d4edda", ec="#7cb887"))

    ax.legend(handles=[
        Line2D([], [], color="#444", lw=2.6, marker="o", ms=8,
               label="最终统计显著（4 条）"),
        Line2D([], [], color="#444", lw=1.6, ls="--", marker="o", ms=6,
               label="最终仅提示性 / 失效（3 条）"),
    ], loc="upper right", fontsize=10, framealpha=0.95)

    fig.text(0.5, 0.005,
             "注：0 表示两组区间重叠，判据在该阶段完全失效。"
             "「4A/4B（未补齐）」阶段 D15/D6 覆盖率仅 10–23%，补齐后才可比。",
             ha="center", fontsize=8.5, color="#666")

    fig.tight_layout(rect=(0, 0.02, 1, 1))
    p = os.path.join(FIG, "23_judge_decay.png")
    fig.savefig(p, dpi=170)
    print(f"→ {p}")

    print("\n衰减汇总：")
    for name, ys, sig in JUDGES:
        print(f"  {name:12s} {ys[0]:5.1f} → {ys[1]:5.1f} → {ys[2]:5.1f}pp "
              f"{'✅显著' if sig else '❌提示性'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
