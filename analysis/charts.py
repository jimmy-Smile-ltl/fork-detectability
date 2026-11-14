"""matplotlib 图表。中文字体是这里唯一的坑。

Windows 上 matplotlib 默认字体没有中文，不显式设置的话标题会变成一串方块。
字体按优先级探测，全都找不到时**降级成英文标签**而不是画方块 ——
一张带方块的中文图比一张正常的英文图更没用。
"""
import os
from typing import Optional

import matplotlib
matplotlib.use("Agg")            # 无 GUI 后端，必须在 pyplot 之前设置
import matplotlib.pyplot as plt
import numpy as np

from crawler.config import OUT_DIR

# ── 中文字体 ────────────────────────────────────────────
_CJK_CANDIDATES = [
    "Microsoft YaHei", "微软雅黑", "SimHei", "黑体",
    "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "WenQuanYi Zen Hei",
]
_CJK: Optional[str] = None
_EN = False


def _setup_font() -> None:
    """探测可用中文字体，设成全局默认。找不到则置 _EN=True 走英文标签。"""
    global _CJK, _EN
    try:
        from matplotlib import font_manager
        have = {f.name for f in font_manager.fontManager.ttflist}
    except Exception:
        have = set()
    for name in _CJK_CANDIDATES:
        if name in have:
            _CJK = name
            break
    plt.rcParams["font.sans-serif"] = ([_CJK] if _CJK else []) + ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False     # 负号显示成方块的经典坑
    _EN = _CJK is None
    if _EN:
        print("⚠️  未找到中文字体，图表标签降级为英文（不画方块）")


_setup_font()


def L(zh: str, en: str) -> str:
    """标签选择器：有中文字体用中文，否则用英文。"""
    return en if _EN else zh


# ── 通用 ────────────────────────────────────────────────
def _out(name: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    return os.path.join(OUT_DIR, name)


def _save(fig, name: str) -> str:
    p = _out(name)
    fig.tight_layout()
    fig.savefig(p, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return p


def _style(ax, title: str, xlabel: str = "", ylabel: str = "") -> None:
    ax.set_title(title, fontsize=12, pad=10)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=10)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(alpha=0.25, linestyle="--", linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


# ── 图 1：增长曲线（日增量 + 累计）──────────────────────
def chart_growth(m: dict, filename: str = "01_growth.png") -> Optional[str]:
    g = m.get("growth") or {}
    daily = g.get("daily") or []
    if len(daily) < 3:
        return None
    days = [d for d, _ in daily]
    v = np.array([s for _, s in daily], dtype=float)
    cum = np.cumsum(v)
    x = np.arange(len(v))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7.2), sharex=True)

    ax1.bar(x, v, color="#4a7ebb", width=0.8)
    # 标注峰值日 —— 报告里要解释这一天发生了什么
    pi = int(np.argmax(v))
    ax1.annotate(f"{days[pi]}\n{v[pi]:,.0f}", xy=(pi, v[pi]),
                 xytext=(pi, v[pi] * 1.12), ha="center", fontsize=9,
                 color="#c0392b",
                 arrowprops=dict(arrowstyle="->", color="#c0392b", lw=1))
    _style(ax1, L("每日新增 Star", "Daily New Stars"),
           ylabel=L("新增", "new"))
    ax1.set_ylim(0, v.max() * 1.28)

    ax2.plot(x, cum, color="#2e7d32", lw=1.8)
    ax2.fill_between(x, 0, cum, color="#2e7d32", alpha=0.12)
    _style(ax2, L("累计 Star", "Cumulative Stars"),
           ylabel=L("累计", "cumulative"))

    step = max(1, len(days) // 12)
    ax2.set_xticks(x[::step])
    ax2.set_xticklabels([days[i][2:] for i in range(0, len(days), step)],
                        rotation=45, ha="right", fontsize=8)
    return _save(fig, filename)


# ── 图 2：周内分布 ──────────────────────────────────────
def chart_weekday(m: dict, filename: str = "02_weekday.png") -> Optional[str]:
    w = m.get("weekday") or {}
    sh = w.get("shares_pct") or []
    if len(sh) != 7:
        return None
    labels = ([L("周一", "Mon"), L("周二", "Tue"), L("周三", "Wed"),
               L("周四", "Thu"), L("周五", "Fri"), L("周六", "Sat"),
               L("周日", "Sun")])
    colors = ["#4a7ebb"] * 5 + ["#e08a3c"] * 2

    fig, ax = plt.subplots(figsize=(8, 4.4))
    b = ax.bar(labels, sh, color=colors)
    ax.axhline(100 / 7, color="#c0392b", ls="--", lw=1.2,
               label=L("完全均匀 14.3%", "uniform 14.3%"))
    for r, s in zip(b, sh):
        ax.text(r.get_x() + r.get_width() / 2, s + 0.25, f"{s:.1f}%",
                ha="center", fontsize=8.5)
    _style(ax, L("点星的周内分布", "Stars by Weekday"),
           ylabel=L("占比 %", "share %"))
    ax.legend(fontsize=8.5)
    ax.set_ylim(0, max(sh) * 1.2)
    return _save(fig, filename)


# ── 图 3：对照组对比 ────────────────────────────────────
def chart_compare(rows: list, filename: str = "03_compare.png") -> Optional[str]:
    """rows = [(label, target_val, [control_vals...], unit), ...]"""
    rows = [r for r in rows if r[1] is not None]
    if not rows:
        return None
    n = len(rows)
    fig, axes = plt.subplots(1, n, figsize=(3.4 * n, 4.2))
    if n == 1:
        axes = [axes]

    for ax, (label, tv, cvs, unit) in zip(axes, rows):
        cvs = [c for c in cvs if c is not None]
        vals = [tv] + cvs
        cols = ["#c0392b"] + ["#9aa7b4"] * len(cvs)
        labs = [L("目标", "target")] + [f"c{i+1}" for i in range(len(cvs))]
        ax.bar(labs, vals, color=cols)
        ax.axhline(tv, color="#c0392b", ls=":", lw=1)
        _style(ax, label, ylabel=unit)
        ax.tick_params(axis="x", labelsize=8)
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:,.2f}", ha="center", va="bottom", fontsize=7.5)
    return _save(fig, filename)


# ── 图 4：同秒碰撞批次分布 ──────────────────────────────
def chart_batch_dist(m: dict, filename: str = "04_batch.png") -> Optional[str]:
    cl = m.get("clustering") or {}
    dist = cl.get("batch_dist") or []
    if not dist:
        return None
    ks = [d["n_actors"] for d in dist]
    cs = [d["c"] for d in dist]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.bar([str(k) for k in ks], cs, color=["#9aa7b4" if k == 1 else "#c0392b" for k in ks])
    ax.set_yscale("log")
    for i, c in enumerate(cs):
        ax.text(i, c, f"{c:,}", ha="center", va="bottom", fontsize=8)
    _style(ax, L("同秒点星批次规模分布（对数轴）",
                 "Same-second Batch Size (log)"),
           xlabel=L("同秒人数", "actors in same second"),
           ylabel=L("秒数", "seconds"))
    ax.set_ylim(0.7, max(cs) * 3)
    return _save(fig, filename)


# ── 图 5：贡献者累计占比曲线 ────────────────────────────
def chart_lorenz(m: dict, filename: str = "05_lorenz.png") -> Optional[str]:
    """Top-N 累计提交占比。

    数据来自 `contributor_metrics()['cumulative']` —— 那是**完整分布**的
    等距采样，不是 top-15。只画 top-15 会让读者以为「前 15 名就占了全部」，
    那是把结论画歪。
    """
    c = m.get("contributors") or {}
    pts = c.get("cumulative") or []
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    ax.plot(xs, ys, "-", color="#c0392b", lw=2)
    ax.fill_between(xs, 0, ys, color="#c0392b", alpha=0.08)
    ax.axhline(50, color="#4a7ebb", ls="--", lw=1.2,
               label=L("50% 提交量", "50% of commits"))
    bf = c.get("bus_factor_50pct")
    if bf:
        ax.axvline(bf, color="#4a7ebb", ls=":", lw=1.2)
        ax.annotate(L(f"bus factor = {bf}", f"bus factor = {bf}"),
                    xy=(bf, 50), xytext=(bf * 1.15 + 1, 30), fontsize=9,
                    color="#4a7ebb",
                    arrowprops=dict(arrowstyle="->", color="#4a7ebb", lw=1))
    _style(ax, L(f"Top-N 贡献者累计提交占比（共 {c['n']:,} 人，基尼 {c['gini']:.3f}）",
                 f"Cumulative Share by Top-N Contributors "
                 f"(n={c['n']:,}, gini={c['gini']:.3f})"),
           xlabel=L("贡献者排名 N", "contributor rank N"),
           ylabel=L("累计占比 %", "cumulative %"))
    ax.legend(fontsize=8.5)
    ax.set_ylim(0, 105)
    return _save(fig, filename)


# ── 图 6：数据边界（CH 覆盖率）──────────────────────────
def chart_coverage(m: dict, filename: str = "06_coverage.png") -> Optional[str]:
    cov = m.get("coverage") or {}
    ms = cov.get("months") or []
    if not ms:
        return None
    labs = [x["month"] for x in ms]
    vals = [(x["coverage"] or 0) * 100 for x in ms]
    cols = ["#c0392b" if not x["usable"] else "#2e7d32" for x in ms]

    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.bar(labs, vals, color=cols)
    ax.axhline(5, color="#c0392b", ls="--", lw=1.2,
               label=L("可用下限 5%", "usable floor 5%"))
    _style(ax, L("ClickHouse 身份数据覆盖率（vs API 真实星数）",
                 "ClickHouse Coverage vs API Stars"),
           ylabel=L("覆盖率 %", "coverage %"))
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.legend(fontsize=8.5)
    return _save(fig, filename)


# ── 图 7：改名史 ────────────────────────────────────────
def chart_rename(m: dict, filename: str = "07_rename.png") -> Optional[str]:
    r = m.get("rename") or {}
    bn = r.get("by_name") or []
    if not bn:
        return None
    labels = [b["name"].split("/")[-1] for b in bn]
    vals = [b["n"] for b in bn]
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    b = ax.bar(labels, vals, color=["#4a7ebb", "#e08a3c", "#2e7d32"][:len(bn)])
    for r_, v in zip(b, vals):
        ax.text(r_.get_x() + r_.get_width() / 2, v, f"{v:,}",
                ha="center", va="bottom", fontsize=9)
    _style(ax, L("按仓库名（改名链）分布的点星事件",
                 "Star Events by Repo Name (rename chain)"),
           ylabel=L("事件数", "events"))
    ax.set_ylim(0, max(vals) * 1.15)
    return _save(fig, filename)


def all_charts(m: dict) -> list:
    """出全套图，返回生成的文件路径（失败的跳过）。"""
    out = []
    for fn in (chart_growth, chart_weekday, chart_batch_dist,
               chart_lorenz, chart_coverage, chart_rename):
        try:
            p = fn(m)
            if p:
                out.append(p)
        except Exception as e:
            print(f"  ⚠️ {fn.__name__} 失败：{type(e).__name__}: {e}")
    return out
