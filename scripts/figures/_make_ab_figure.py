# -*- coding: utf-8 -*-
"""图 12：A/B 组判据分离度（最终结论图）。

    python scripts/figures/_make_ab_figure.py

把 8 个仓库按 A 组（3 个外部研究疑似仓）与 B 组（5 个无相关报道仓）分开，
画出 6 条判据的分布区间 —— 一眼看出哪几条零重叠、哪几条重叠。
"""
from __future__ import annotations

import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "out")
FIGS = os.path.join(BASE, "figs")

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

# 4A / 4B（2026-11-18 终版）
A = [("shardeum", "shardeum"), ("union", "union"), ("anoma", "anoma"),
     ("nodejsargo", "nodejs-argo")]
B = [("ollama", "ollama"), ("langchain", "langchain"),
     ("frp", "frp"), ("agencyagents", "agency-agents")]

# (列名, 显示名, 越高越像 A 组)
JUDGES = [
    ("pull_shark", "Pull Shark", False),
    ("has_own", "有自建仓", False),
    ("commit_zero", "零提交率", True),
    ("has_collab", "协作徽章", False),
    ("avatar_photo", "自传头像", False),
    ("followers_zero", "零粉丝", True),
    ("fork_touched", "fork 后动过", False),
]


def load(tag):
    """返回画像 dict。新仓从「一人一条」合并档案算；其余从 features CSV 算。

    `fork_touched` 不在 9 个维度里（来自 fork 名单），
    对合并档案的仓单独从 features CSV 补这一列。
    """
    import importlib.util
    cf_p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "analysis", "_compare_full.py")
    spec = importlib.util.spec_from_file_location("cf", cf_p)
    cf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cf)

    full_p = os.path.join(BASE, "data", "raw", f"accounts_full_{tag}.jsonl")
    if os.path.exists(full_p):
        prof = cf.profile_from_recs(cf.best_records(full_p))
    else:
        prof = cf.from_csv(tag)

    # fork_touched 兜底：从 features CSV 取
    if prof.get("fork_touched") is None:
        csv_p = os.path.join(OUT, f"features_{tag}.csv")
        if os.path.exists(csv_p):
            with open(csv_p, encoding="utf-8-sig", errors="replace") as fh:
                rows = list(csv.DictReader(fh))
            x = [r for r in rows if r.get("fork_touched") not in (None, "")]
            if x:
                prof["fork_touched"] = (sum(1 for r in x if str(r["fork_touched"]) == "1")
                                        / len(x) * 100)
    return prof


def rate(prof, k):
    v = prof.get(k)
    return v if isinstance(v, (int, float)) else None


def main() -> int:
    data = {t: load(t) for t, _ in A + B}

    fig, axes = plt.subplots(1, len(JUDGES), figsize=(3.9 * len(JUDGES), 5.4))
    fig.suptitle("图 12｜A 组 vs B 组：7 条判据的分离度（★ = 零重叠）",
                 fontsize=15, y=0.97)

    for ax, (col, name, higher_is_a) in zip(axes, JUDGES):
        a_vals, a_labels = [], []
        for tag, lab in A:
            v = rate(data[tag], col)
            if v is not None:
                a_vals.append(v)
                a_labels.append(lab)
        b_vals, b_labels = [], []
        for tag, lab in B:
            v = rate(data[tag], col)
            if v is not None:
                b_vals.append(v)
                b_labels.append(lab)
        if not a_vals or not b_vals:
            ax.set_visible(False)
            continue
        sep = (min(a_vals) > max(b_vals)) or (max(a_vals) < min(b_vals))

        ax.scatter([1] * len(a_vals), a_vals, s=140, c="#d62728",
                   edgecolors="white", linewidths=1.5, zorder=3, label="A 组")
        ax.scatter([2] * len(b_vals), b_vals, s=140, c="#1f77b4",
                   edgecolors="white", linewidths=1.5, zorder=3, label="B 组")
        for x, vs in ((1, a_vals), (2, b_vals)):
            ax.plot([x - 0.13, x + 0.13], [min(vs)] * 2, lw=2.5,
                    c="#d62728" if x == 1 else "#1f77b4", zorder=2)
            ax.plot([x - 0.13, x + 0.13], [max(vs)] * 2, lw=2.5,
                    c="#d62728" if x == 1 else "#1f77b4", zorder=2)
            ax.vlines(x, min(vs), max(vs), lw=1.2, alpha=0.45,
                      color="#d62728" if x == 1 else "#1f77b4", zorder=1)
            labs = a_labels if x == 1 else b_labels
            order = sorted(range(len(vs)), key=lambda i: vs[i])
            for rank, i in enumerate(order):
                dy = -11 if rank % 2 == 0 else 5
                ax.annotate(labs[i], (x, vs[i]), textcoords="offset points",
                            xytext=(-46 if x == 1 else 11, dy),
                            fontsize=8, color="#333",
                            ha="right" if x == 1 else "left")

        gap = ""
        if sep:
            if min(a_vals) > max(b_vals):
                gap = f"间距 {min(a_vals)-max(b_vals):.1f}pp"
            else:
                gap = f"间距 {min(b_vals)-max(a_vals):.1f}pp"
        title = f"{name}\n{'★ 零重叠 ' + gap if sep else '重叠（不可用）'}"
        ax.set_title(title, fontsize=11.5,
                     color="#2ca02c" if sep else "#999999", fontweight="bold")
        ax.set_xticks([1, 2])
        ax.set_xticklabels(["A 组\n(外部研究疑似)", "B 组\n(无相关报道)"], fontsize=9)
        ax.set_xlim(0.18, 2.82)
        lo = min(min(a_vals), min(b_vals))
        hi = max(max(a_vals), max(b_vals))
        pad = max(6, (hi - lo) * 0.18)
        ax.set_ylim(max(0, lo - pad), min(100, hi + pad))
        ax.grid(axis="y", alpha=0.25, ls="--")
        ax.set_ylabel("%", fontsize=10)

    axes[0].legend(loc="upper left", fontsize=9, framealpha=0.9)
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    dst = os.path.join(FIGS, "21_ab_separation.png")
    os.makedirs(FIGS, exist_ok=True)
    plt.savefig(dst, dpi=155, bbox_inches="tight", facecolor="white")
    plt.close()
    print(f"→ {dst}")

    # 文字汇总
    print("\n判据分离度汇总：")
    for col, name, _ in JUDGES:
        av = [rate(data[t], col) for t, _ in A]
        bv = [rate(data[t], col) for t, _ in B]
        av = [v for v in av if v is not None]
        bv = [v for v in bv if v is not None]
        if not av or not bv:
            continue
        sep = (min(av) > max(bv)) or (max(av) < min(bv))
        print(f"  {name:12s} A {min(av):5.1f}-{max(av):5.1f} | "
              f"B {min(bv):5.1f}-{max(bv):5.1f} | {'★零重叠' if sep else '重叠'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
