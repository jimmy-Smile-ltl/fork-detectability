# -*- coding: utf-8 -*-
"""生成报告用的图表 10–17 与 20（共 9 张，多类型）。

图 18–19、21–23 由同目录的其他 `_make_*.py` 生成，不在本脚本范围内。

设计原则：
  - 统一配色：rea = 红 #e5484d，openclaw = 蓝 #1f6feb
  - 中文标签、浅网格、无多余装饰
  - 每张图都标注关键数字，让图能独立读懂

产出（figs/）：
  10_effect_ranking.png    判据效应量横向条形（按维度着色）
  11_fork_heatmap.png      周内×小时的 fork 分布热力图（两仓对照）
  12_star_fork_cdf.png     star→fork 延迟的累积分布（对数轴）
  13_feature_matrix.png    六个关键特征的分布对比（小提琴 + 箱线）
  14_shape_stack.png       档案形状堆叠条
  15_lorenz.png            fork 时间分布的洛伦兹曲线（集中度）
  16_quadrant.png          两轴四象限（自证 × 批量性）
  17_scatter_dev.png       开发事件占比 vs 活跃天数的散点密度
  20_avatar_age.png        头像上传率 vs 账号年龄（时间控制反例）

用法：py _make_figures.py
"""
import csv
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
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
    "axes.unicode_minus": False,
    "axes.edgecolor": "#d0d7de",
    "axes.linewidth": 0.8,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "grid.color": "#eaeef2",
    "grid.linewidth": 0.7,
})


def load_csv(tag):
    p = os.path.join(OUT, f"features_{tag}.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def col(rows, key):
    return [x for x in (num(r.get(key)) for r in rows) if x is not None]


# ---------------------------------------------------------------- 图 10
def fig_effect_ranking():
    items = [
        ("fork 后动过率（≤30d 控制）", 30.9, "行为后续"),
        ("同小时 star+fork", 16.6, "动作耦合"),
        ("先 star 后 fork", 16.5, "动作顺序"),
        ("零粉丝", 13.5, "社交图谱"),
        ("star 过目标仓", 10.8, "消费行为"),
        ("协作徽章", 9.9, "开发行为"),
        ("Pull Shark", 9.7, "开发行为"),
        ("仓名含目标仓", 6.5, "命名行为"),
        ("活跃 ≤5 天", 4.0, "综合"),
    ]
    cats = {"行为后续": "#e5484d", "动作耦合": "#f0883e", "动作顺序": "#d4a72c",
            "社交图谱": "#8250df", "消费行为": "#1f6feb", "开发行为": "#1a7f37",
            "命名行为": "#bf3989", "综合": "#8b949e"}
    fig, ax = plt.subplots(figsize=(9.5, 4.8), dpi=170)
    names = [i[0] for i in items][::-1]
    vals = [i[1] for i in items][::-1]
    cs = [cats[i[2]] for i in items][::-1]
    bars = ax.barh(names, vals, color=cs, alpha=0.85, height=0.62)
    for b, v in zip(bars, vals):
        ax.text(v + 0.5, b.get_y() + b.get_height() / 2, f"{v:.1f} pp",
                va="center", fontsize=9.5, fontweight="bold", color="#24292f")
    ax.set_xlim(0, 36)
    ax.set_xlabel("rea 与 openclaw 的差异（百分点）", fontsize=10)
    ax.set_title("判据效应量排名：前四名分属四个独立维度", fontsize=12.5, pad=12)
    ax.grid(axis="x", alpha=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    # 图例
    handles = [Rectangle((0, 0), 1, 1, fc=c, alpha=0.85) for c in cats.values()]
    ax.legend(handles, cats.keys(), fontsize=8.5, ncol=4, loc="lower right",
              frameon=False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "10_effect_ranking.png"))
    plt.close(fig)
    print("10_effect_ranking.png")


# ---------------------------------------------------------------- 图 11
def fig_heatmap(rea, oc):
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.9), dpi=170)
    for ax, rows, name, cmap in ((axes[0], rea, "morluto/rea", "Reds"),
                                 (axes[1], oc, "openclaw/openclaw", "Blues")):
        m = np.zeros((7, 24))
        for r in rows:
            h, w = num(r.get("fork_hour")), num(r.get("fork_weekday"))
            if h is None or w is None:
                continue
            m[int(w) % 7, int(h) % 24] += 1
        pct = m / m.sum() * 100 if m.sum() else m
        im = ax.imshow(pct, aspect="auto", cmap=cmap, interpolation="nearest")
        ax.set_xticks(range(0, 24, 3))
        ax.set_xticklabels([f"{h:02d}" for h in range(0, 24, 3)], fontsize=8)
        ax.set_yticks(range(7))
        ax.set_yticklabels(["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
                           fontsize=8)
        ax.set_xlabel("fork 时刻（UTC 小时）", fontsize=9)
        ax.set_title(f"{name}　共 {m.sum():,.0f} 个", fontsize=10.5)
        cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
        cb.ax.tick_params(labelsize=7)
        # 峰值格标注
        iy, ix = np.unravel_index(np.argmax(pct), pct.shape)
        ax.text(ix, iy, f"{pct[iy, ix]:.1f}%", ha="center", va="center",
                fontsize=8, fontweight="bold",
                color="white" if pct[iy, ix] > pct.max() * 0.6 else "#24292f")
    fig.suptitle("fork 的周内 × 小时分布：颜色越深 = 该时段的 fork 占比越高",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "11_fork_heatmap.png"), bbox_inches="tight")
    plt.close(fig)
    print("11_fork_heatmap.png")


# ---------------------------------------------------------------- 图 12
def fig_cdf(rea, oc):
    fig, ax = plt.subplots(figsize=(8.6, 4.4), dpi=170)
    for rows, name, c in ((rea, "rea", RED), (oc, "openclaw", BLUE)):
        # 只取 star 过目标仓的人；同秒（差值 0）保留，log 轴下夹到 0.002h≈7秒
        raw = [abs(x) for x in col(rows, "star_fork_delay_h")]
        if not raw:
            continue
        v = sorted(max(x, 0.002) for x in raw)
        n = len(v)
        xs = v
        ys = [(i + 1) / n * 100 for i in range(n)]
        ax.plot(xs, ys, lw=1.9, color=c, label=f"{name}（n={n:,}）")
        for q, lbl in ((50, "50%"), (90, "90%")):
            for xi, yi in zip(xs, ys):
                if yi >= q:
                    ax.plot([xi], [q], "o", ms=4, color=c)
                    ax.annotate(f"{lbl} @ {xi:.0f}h", (xi, q), fontsize=8,
                                color=c, textcoords="offset points", xytext=(6, -11))
                    break
    ax.set_xscale("log")
    ax.set_xlim(0.002, 3e4)
    ax.set_xlabel("star 与 fork 的时间差（小时，对数轴）", fontsize=10)
    ax.set_ylabel("累积占比 %", fontsize=10)
    ax.set_title("star→fork 延迟的累积分布：rea 的曲线更陡 = 动作更同步",
                 fontsize=12, pad=10)
    ax.grid(alpha=0.6, which="both")
    ax.legend(fontsize=9.5, loc="lower right", frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "12_star_fork_cdf.png"))
    plt.close(fig)
    print("12_star_fork_cdf.png")


# ---------------------------------------------------------------- 图 13
def fig_matrix(rea, oc):
    specs = [
        ("active_days", "全年活跃天", (0, 60)),
        ("total_contribs", "年度贡献数", (0, 200)),
        ("followers", "粉丝数", (0, 20)),
        ("starred_count", "star 总数", (0, 100)),
        ("own_count", "自建仓数", (0, 20)),
        ("commit_total", "自建仓提交数", (0, 60)),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.2), dpi=165)
    for ax, (key, label, xlim) in zip(axes.ravel(), specs):
        data, labels, colors = [], [], []
        for rows, name, c in ((rea, "rea", RED), (oc, "openclaw", BLUE)):
            v = [x for x in col(rows, key) if xlim[0] <= x <= xlim[1]]
            if v:
                data.append(v)
                labels.append(f"{name}\nn={len(v):,}")
                colors.append(c)
        if len(data) < 2:
            ax.set_visible(False)
            continue
        parts = ax.violinplot(data, showextrema=False, widths=0.82)
        for pc, c in zip(parts["bodies"], colors):
            pc.set_facecolor(c)
            pc.set_alpha(0.55)
            pc.set_edgecolor(c)
        bp = ax.boxplot(data, widths=0.16, patch_artist=True, showfliers=False)
        for patch, c in zip(bp["boxes"], colors):
            patch.set_facecolor("white")
            patch.set_edgecolor(c)
            patch.set_linewidth(1.2)
        for m in bp["medians"]:
            m.set_color("#24292f")
            m.set_linewidth(1.6)
        ax.set_xticks([1, 2])
        ax.set_xticklabels(labels, fontsize=8.5)
        ax.set_title(label, fontsize=10.5)
        ax.grid(axis="y", alpha=0.55)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        # 中位数差标注
        m1, m2 = np.median(data[0]), np.median(data[1])
        ax.text(0.97, 0.94, f"中位 {m1:,.0f} vs {m2:,.0f}",
                transform=ax.transAxes, ha="right", va="top", fontsize=8.5,
                bbox=dict(fc="#f6f8fa", ec="#d0d7de", lw=0.6, alpha=0.95))
    fig.suptitle("六个关键特征的分布对比（小提琴 = 密度，箱 = 四分位；已裁掉极端长尾）",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "13_feature_matrix.png"), bbox_inches="tight")
    plt.close(fig)
    print("13_feature_matrix.png")


# ---------------------------------------------------------------- 图 14
def fig_shape(rea, oc):
    order = ["EMPTY", "TEMPLATE", "PARTIAL", "RICH"]
    cn = {"EMPTY": "空档案", "TEMPLATE": "模板形\n(仅姓名+地点)",
          "PARTIAL": "部分填写", "RICH": "完整(≥4项)"}
    ca = Counter(r.get("shape") for r in rea if r.get("shape"))
    cb = Counter(r.get("shape") for r in oc if r.get("shape"))
    ta, tb = sum(ca.values()) or 1, sum(cb.values()) or 1
    fig, ax = plt.subplots(figsize=(8.8, 4.2), dpi=170)
    x = np.arange(len(order))
    w = 0.36
    va = [ca[o] / ta * 100 for o in order]
    vb = [cb[o] / tb * 100 for o in order]
    b1 = ax.bar(x - w / 2, va, w, label="rea", color=RED, alpha=0.88)
    b2 = ax.bar(x + w / 2, vb, w, label="openclaw", color=BLUE, alpha=0.88)
    for bars, vals in ((b1, va), (b2, vb)):
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.8, f"{v:.1f}%",
                    ha="center", fontsize=9, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([cn[o] for o in order], fontsize=9.5)
    ax.set_ylabel("占比 %", fontsize=10)
    ax.set_ylim(0, 55)
    ax.set_title("档案形状分布：两仓几乎一致 —— 填不填档案不是判据", fontsize=12, pad=10)
    ax.grid(axis="y", alpha=0.55)
    ax.set_axisbelow(True)
    ax.legend(fontsize=10, frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    # 差异标注
    ax.annotate(f"RICH 差 {va[3]-vb[3]:+.1f}pp", xy=(3, max(va[3], vb[3]) + 3),
                ha="center", fontsize=9, color="#57606a")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "14_shape_stack.png"))
    plt.close(fig)
    print("14_shape_stack.png")


# ---------------------------------------------------------------- 图 15
def fig_lorenz(rea, oc):
    """集中度曲线：横轴 = 取 fork 量最高的前 N 天，纵轴 = 这 N 天覆盖的 fork 占比。"""
    fig, ax = plt.subplots(figsize=(8.4, 4.8), dpi=170)
    xs_ticks = [1, 2, 3, 5, 10, 20, 30, 50, 100, 200]
    for rows, name, c in ((rea, "rea", RED), (oc, "openclaw", BLUE)):
        ts = [num(r.get("fork_ts")) for r in rows if num(r.get("fork_ts"))]
        if not ts:
            continue
        days = Counter(int(t // 86400) for t in ts)
        vals = sorted(days.values(), reverse=True)
        total = sum(vals)
        n_days = len(vals)
        cum = np.cumsum(vals) / total * 100
        xs = list(range(1, min(len(cum), 300) + 1))
        ax.plot(xs, cum[:len(xs)], lw=2.1, color=c,
                label=f"{name}（{n_days} 天，{total:,} 个 fork）")
        # 标注 50% / 80% 需要几天
        for q in (50, 80):
            idx = int(np.searchsorted(cum, q)) + 1
            if 1 <= idx <= 300:
                ax.plot([idx], [q], "o", ms=4.5, color=c)
                ax.annotate(f"{q}% → {idx} 天", (idx, q), fontsize=8.5, color=c,
                            textcoords="offset points", xytext=(7, -12))
    ax.set_xscale("log")
    ax.set_xlim(1, 300)
    ax.set_ylim(0, 102)
    ax.set_xlabel("取 fork 量最高的前 N 天（对数轴）", fontsize=10)
    ax.set_ylabel("这些天覆盖的 fork 占比 %", fontsize=10)
    ax.set_title("集中度曲线：rea 的 fork 集中在极少数几天", fontsize=12, pad=10)
    ax.grid(alpha=0.55, which="both")
    ax.legend(fontsize=9.5, loc="lower right", frameon=False)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "15_lorenz.png"))
    plt.close(fig)
    print("15_lorenz.png")


# ---------------------------------------------------------------- 图 16
def fig_quadrant(rea, oc):
    """两轴四象限：横轴 = 自证强度，纵轴 = 批量性强度（都用可观测代理）。"""
    def bucket(rows):
        out = {}
        # 自证：有自建仓且(RICH 或 有 Pull Shark)
        # 批量：同小时 star+fork 或 零粉丝
        hi_self = lo_self = hi_batch = lo_batch = 0
        quad = Counter()
        for r in rows:
            own = num(r.get("own_count"))
            rich = 1 if r.get("shape") == "RICH" else 0
            ps = num(r.get("has_pull_shark"))
            if own is None:
                continue
            self_hi = 1 if (own > 0 and (rich or ps == 1)) else 0
            sh = num(r.get("star_same_hour"))
            f0 = num(r.get("followers_zero"))
            batch_hi = 1 if (sh == 1 or f0 == 1) else 0
            quad[("高" if self_hi else "低", "高" if batch_hi else "低")] += 1
        return quad

    qa, qb = bucket(rea), bucket(oc)
    # (自证, 批量) 与坐标严格对应：x=1 自证高，y=1 批量高
    labels = [("高", "高"), ("高", "低"), ("低", "高"), ("低", "低")]
    cn = {("高", "低"): "自证高·批量低\n(正常开发者)",
          ("高", "高"): "自证高·批量高\n(最值得关注)",
          ("低", "低"): "自证低·批量低\n(潜水/新号)",
          ("低", "高"): "自证低·批量高\n(批量小号)"}
    ta = sum(qa.values()) or 1
    tb = sum(qb.values()) or 1
    fig, ax = plt.subplots(figsize=(8.4, 6.2), dpi=170)
    x = [1, 1, 0, 0]
    y = [1, 0, 1, 0]
    for (s, b), xi, yi in zip(labels, x, y):
        pa = qa[(s, b)] / ta * 100
        pb = qb[(s, b)] / tb * 100
        ax.add_patch(Rectangle((xi, yi), 1, 1, fc="#f6f8fa",
                               ec="#d0d7de", lw=1.2))
        ax.text(xi + 0.5, yi + 0.68, cn[(s, b)], ha="center", va="center",
                fontsize=9.5, color="#24292f")
        ax.text(xi + 0.5, yi + 0.40, f"rea {pa:.1f}%", ha="center", va="center",
                fontsize=11, color=RED, fontweight="bold")
        ax.text(xi + 0.5, yi + 0.22, f"openclaw {pb:.1f}%", ha="center", va="center",
                fontsize=11, color=BLUE, fontweight="bold")
        if pa - pb > 3:
            ax.text(xi + 0.5, yi + 0.06, f"▲ rea 多 {pa-pb:.1f}pp", ha="center",
                    fontsize=8.5, color=RED)
        elif pb - pa > 3:
            ax.text(xi + 0.5, yi + 0.06, f"▼ rea 少 {pb-pa:.1f}pp", ha="center",
                    fontsize=8.5, color=BLUE)
    ax.set_xlim(0, 2)
    ax.set_ylim(0, 2)
    ax.set_xticks([0.5, 1.5])
    ax.set_xticklabels(["自证 低", "自证 高"], fontsize=11)
    ax.set_yticks([0.5, 1.5])
    ax.set_yticklabels(["批量 低", "批量 高"], fontsize=11)
    ax.set_title("两轴四象限（禁止把两轴加成一个分数）", fontsize=12.5, pad=12)
    for s in ("top", "right", "left", "bottom"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "16_quadrant.png"))
    plt.close(fig)
    print("16_quadrant.png")


# ---------------------------------------------------------------- 图 17
def fig_scatter(rea, oc):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), dpi=165, sharey=True)
    for ax, rows, name, cmap in ((axes[0], rea, "morluto/rea", "Reds"),
                                 (axes[1], oc, "openclaw/openclaw", "Blues")):
        xs, ys = [], []
        for r in rows:
            d = num(r.get("dev_event_ratio"))
            a = num(r.get("active_days"))
            if d is None or a is None:
                continue
            xs.append(a)
            ys.append(d * 100)
        if not xs:
            continue
        hb = ax.hexbin(xs, ys, gridsize=32, cmap=cmap, bins="log",
                       extent=(0, 120, 0, 100), mincnt=1)
        ax.set_xlabel("全年活跃天数", fontsize=9.5)
        ax.set_title(f"{name}　n={len(xs):,}", fontsize=10.5)
        cb = fig.colorbar(hb, ax=ax, fraction=0.046, pad=0.03)
        cb.ax.tick_params(labelsize=7)
        cb.set_label("人数（对数）", fontsize=8)
        ax.grid(alpha=0.35)
    axes[0].set_ylabel("开发事件占比 %（push+PR / 近 30 条）", fontsize=9.5)
    fig.suptitle("活跃天数 × 开发事件占比：rea 大量账号落在「有活跃但零开发事件」",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "17_scatter_dev.png"), bbox_inches="tight")
    plt.close(fig)
    print("17_scatter_dev.png")


# ---------------------------------------------------------------- 图 20
def fig_avatar_age(rea, oc):
    """头像上传率 vs 账号年龄：两仓在同龄段内几乎重合。"""
    buckets = ["<1年", "1-3年", "3-7年", ">7年"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.4), dpi=170,
                                   gridspec_kw={"width_ratios": [1, 1.15]})
    x = np.arange(len(buckets))
    w = 0.36
    va, vb, na, nb = [], [], [], []
    for b in buckets:
        for rows, acc, nacc in ((rea, va, na), (oc, vb, nb)):
            sel = [r for r in rows if r.get("acct_age_bucket") == b and r.get("avatar_photo") is not None]
            acc.append(sum(1 for r in sel if r["avatar_photo"] == "1") / len(sel) * 100 if sel else 0)
            nacc.append(len(sel))
    b1 = ax1.bar(x - w / 2, va, w, label="rea", color=RED, alpha=0.88)
    b2 = ax1.bar(x + w / 2, vb, w, label="openclaw", color=BLUE, alpha=0.88)
    for bars, vals, ns in ((b1, va, na), (b2, vb, nb)):
        for bb, v, n in zip(bars, vals, ns):
            ax1.text(bb.get_x() + bb.get_width() / 2, v + 1.2, f"{v:.1f}%",
                     ha="center", fontsize=8.5, fontweight="bold")
            ax1.text(bb.get_x() + bb.get_width() / 2, -4.8, f"n={n:,}",
                     ha="center", fontsize=6.5, color=GRAY)
    ax1.set_xticks(x)
    ax1.set_xticklabels(buckets, fontsize=9.5)
    ax1.set_xlabel("账号年龄", fontsize=10)
    ax1.set_ylabel("自己上传头像的比例 %", fontsize=10)
    ax1.set_ylim(-6, 72)
    ax1.set_title("分年龄段后，两仓几乎重合", fontsize=11)
    ax1.grid(axis="y", alpha=0.55)
    ax1.set_axisbelow(True)
    ax1.legend(fontsize=9.5, frameon=False)
    for sp in ("top", "right"):
        ax1.spines[sp].set_visible(False)

    # 右：两仓账号年龄分布（解释总体差异）
    for rows, name, c in ((rea, "rea（中位 2,029 天）", RED),
                          (oc, "openclaw（中位 2,354 天）", BLUE)):
        vals = [float(r["acct_age_days"]) / 365 for r in rows
                if r.get("acct_age_days") not in (None, "")]
        vals = [v for v in vals if 0 <= v <= 18]
        ax2.hist(vals, bins=60, histtype="step", lw=1.8, color=c, label=name, density=True)
    ax2.set_xlabel("账号年龄（年）", fontsize=10)
    ax2.set_ylabel("密度", fontsize=10)
    ax2.set_title("openclaw 的 forker 整体更老 —— 这解释了 2.2pp 的总体差异", fontsize=11)
    ax2.grid(alpha=0.5)
    ax2.legend(fontsize=9, frameon=False)
    for sp in ("top", "right"):
        ax2.spines[sp].set_visible(False)
    fig.suptitle("头像上传率是「账号年龄」的函数，不是「真假」的函数", fontsize=12.5, y=1.02)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "20_avatar_age.png"), bbox_inches="tight")
    plt.close(fig)
    print("20_avatar_age.png")


def main():
    rea = load_csv("rea")
    oc = load_csv("openclaw")
    print(f"载入 rea {len(rea):,} | openclaw {len(oc):,}\n")
    fig_effect_ranking()
    fig_heatmap(rea, oc)
    fig_cdf(rea, oc)
    fig_matrix(rea, oc)
    fig_shape(rea, oc)
    fig_lorenz(rea, oc)
    fig_quadrant(rea, oc)
    fig_scatter(rea, oc)
    fig_avatar_age(rea, oc)
    print("\n完成，图在 figs/")


if __name__ == "__main__":
    main()
