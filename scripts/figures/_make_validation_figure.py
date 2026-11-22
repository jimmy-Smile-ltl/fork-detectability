# -*- coding: utf-8 -*-
"""独立验证图：watchers/★ 与 Pull Shark 的关系。

左图：横轴 w/★（对数），纵轴 Pull Shark，点=仓库，颜色=A/B 侧
右图：同一数据换成柱状，按 w/★ 排序，标出 A/B 区间

    python scripts/figures/_make_validation_figure.py
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import FIG  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# (标签, w/★ %, Pull Shark %, 组)
# 组: A=已有 A 侧, B=已有 B 侧, newA/newB=独立验证里判为 A/B
DATA = [
    ("shardeum",      0.94, 12.0, "A"),
    ("union",         2.17,  8.5, "A"),
    ("anoma",         0.57,  4.3, "A"),
    ("nodejs-argo",   0.18,  3.4, "A"),
    ("ollama",        0.55, 41.3, "B"),
    ("langchain",     0.62, 52.3, "B"),
    ("frp",           1.42, 24.3, "B"),
    ("agency-agents", 0.71, 31.2, "B"),
    ("dsh",           0.09, 14.1, "newA"),
    ("cc-switch",     0.18, 27.2, "newB"),
    ("VoiceStudio",   0.33, 33.4, "newB"),
    ("autoresearch",  0.73, 47.4, "newB"),
    ("gods-eye-view", 1.78, 39.4, "newB"),
]

COLOR = {"A": "#d62728", "B": "#1f77b4", "newA": "#ff9896", "newB": "#aec7e8"}
MARK = {"A": "o", "B": "o", "newA": "D", "newB": "D"}


def main() -> int:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6.2))

    # --- 左：散点 + 对数横轴
    for lab, w, ps, g in DATA:
        ax1.scatter(w, ps, s=150, c=COLOR[g], marker=MARK[g],
                    edgecolors="black", linewidths=0.8, zorder=3)
        dx = 1.10
        ax1.annotate(lab, (w, ps), xytext=(w * dx, ps + 1.2), fontsize=8.5)

    ax1.set_xscale("log")
    ax1.set_xlabel("watchers / ★（%，对数轴）", fontsize=11)
    ax1.set_ylabel("Pull Shark 持有率（%）", fontsize=11)
    ax1.set_title("w/★ 与 Pull Shark 无关（A/B 组完全重叠；新仓因按 w/★ 选样而假性单调）", fontsize=11)
    ax1.grid(alpha=0.3, which="both")

    # 区间带
    ax1.axhspan(3.4, 12.0, color="#d62728", alpha=0.08)
    ax1.axhspan(24.3, 52.3, color="#1f77b4", alpha=0.08)
    ax1.text(0.085, 12.6, "A 侧区间 3.4–12.0%", color="#d62728", fontsize=9)
    ax1.text(0.085, 23.0, "B 侧区间 24.3–52.3%", color="#1f77b4", fontsize=9)

    from matplotlib.lines import Line2D
    ax1.legend(handles=[
        Line2D([], [], marker="o", color="w", markerfacecolor=COLOR["A"],
               markersize=11, markeredgecolor="k", label="已有 A 侧（4 仓）"),
        Line2D([], [], marker="o", color="w", markerfacecolor=COLOR["B"],
               markersize=11, markeredgecolor="k", label="已有 B 侧（4 仓）"),
        Line2D([], [], marker="D", color="w", markerfacecolor=COLOR["newA"],
               markersize=10, markeredgecolor="k", label="新仓判为 A"),
        Line2D([], [], marker="D", color="w", markerfacecolor=COLOR["newB"],
               markersize=10, markeredgecolor="k", label="新仓判为 B"),
    ], loc="upper left", fontsize=9)

    # --- 右：按 w/★ 排序的柱状
    srt = sorted(DATA, key=lambda x: x[1])
    labels = [d[0] for d in srt]
    vals = [d[2] for d in srt]
    cols = [COLOR[d[3]] for d in srt]
    bars = ax2.bar(range(len(srt)), vals, color=cols, edgecolor="black", linewidth=0.6)
    for i, (b, d) in enumerate(zip(bars, srt)):
        ax2.text(i, b.get_height() + 0.8, f"{d[1]:.2f}%",
                 ha="center", fontsize=7.5, rotation=90)
        if d[3].startswith("new"):
            b.set_hatch("//")

    ax2.axhline(12.0, color="#d62728", ls="--", lw=1, label="A 侧上限 12.0%")
    ax2.axhline(24.3, color="#1f77b4", ls="--", lw=1, label="B 侧下限 24.3%")
    ax2.set_xticks(range(len(srt)))
    ax2.set_xticklabels(labels, rotation=38, ha="right", fontsize=9)
    ax2.set_ylabel("Pull Shark 持有率（%）", fontsize=11)
    ax2.set_title("按 w/★ 升序：已有 A 侧并不集中在低端（反证）", fontsize=11)
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", alpha=0.3)

    fig.suptitle("图 22 ｜ watchers/★ 不能区分 A/B（左）；dsh 的具体先验被证实（右）",
                 fontsize=14, y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    p = os.path.join(FIG, "22_out_of_sample_validation.png")
    fig.savefig(p, dpi=170)
    print(f"→ {p}")

    # 一致性检查
    ok = sum(1 for _, w, ps, g in DATA
             if (g in ("A", "newA") and ps <= 12.0) or (g in ("B", "newB") and ps >= 24.3))
    print(f"落在预期区间的点：{ok}/{len(DATA)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
