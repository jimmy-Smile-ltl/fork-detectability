# -*- coding: utf-8 -*-
"""正式指标计算：比例 + Wilson 置信区间 + 效应量（RD / OR）。

    python scripts/analysis/_indicators_formal.py

输出 data/out/indicators_formal.md
"""
from __future__ import annotations

import csv
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import OUT  # noqa: E402

# 仓库 → (标签, 特征文件名, 分组)
REPOS = [
    ("rea",        "rea",          "theme"),
    ("openclaw",   "openclaw",     "theme"),
    ("BettaFish",  "bettafish",    "theme"),
    ("shardeum",   "shardeum",     "A"),
    ("union",      "union",        "A"),
    ("anoma",      "anoma",        "A"),
    ("ollama",     "ollama",       "B"),
    ("langchain",  "langchain",    "B"),
]

# 比例型指标：列名、显示名、方向（True = 高值更像 A 组）
BIN = [
    ("commit_zero",      "零提交率",        True),
    ("has_own",          "有自建仓",        False),
    ("avatar_photo",     "自传头像",        False),
    ("followers_zero",   "零粉丝",          True),
    ("following_zero",   "零关注",          True),
    ("fork_touched",     "fork 后动过",     False),
    ("repo_named_target","仓名含目标",      True),
    ("has_profile_repo", "有主页仓",        False),
    ("has_pull_shark",   "Pull Shark",      False),
    ("has_collab",       "协作徽章",        False),
]

# 中位数型指标
MED = [
    ("commit_total",   "提交数"),
    ("own_count",      "自建仓数"),
    ("acct_age_days",  "账号年龄(天)"),
    ("followers",      "粉丝数"),
    ("starred_count",  "star 数"),
    ("filled_n",       "档案填充数"),
]


def wilson(k: int, n: int, z: float = 1.96):
    """Wilson 分数区间（小样本也稳健）。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d * 100, (c + r) / d * 100)


def load(fn: str):
    p = os.path.join(OUT, f"features_{fn}.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig", errors="replace") as fh:
        return list(csv.DictReader(fh))


def prop(rows, col):
    """→ (k, n, p%)，只统计该列非空的行。"""
    x = [r for r in rows if r.get(col) not in (None, "")]
    k = sum(1 for r in x if str(r[col]) == "1")
    n = len(x)
    return k, n, (k / n * 100 if n else None)


def med(rows, col):
    v = []
    for r in rows:
        s = r.get(col)
        if s in (None, ""):
            continue
        try:
            v.append(float(s))
        except ValueError:
            pass
    if not v:
        return None, 0
    v.sort()
    n = len(v)
    return (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2), n


def main() -> int:
    data = {tag: load(fn) for tag, fn, _ in REPOS}
    grp = {tag: g for tag, _, g in REPOS}
    L = ["# 指标计算（正式版，含 95% 置信区间）", "",
         "> 生成脚本：`scripts/analysis/_indicators_formal.py`",
         "> 比例用 **Wilson 区间**（小样本稳健），效应量给 **RD（率差）** 与 **OR（比值比）**。",
         "> 分组：**theme** = 研究主题仓；**A** = 外部研究列为疑似的 3 个仓；**B** = 无相关报道的 2 个对照仓。", ""]

    # ---------- 表 1：比例型指标 ----------
    for col, name, higher_a in BIN:
        L += [f"## {name}（`{col}`）", "",
              "| 仓库 | 组 | k / n | 比例 | 95% CI |", "|---|:---:|---|---:|---|"]
        vals = {}
        for tag, _, g in REPOS:
            k, n, p = prop(data[tag], col)
            if p is None:
                L.append(f"| {tag} | {g} | — | — | — |")
                continue
            lo, hi = wilson(k, n)
            vals[tag] = (k, n, p)
            L.append(f"| {tag} | {g} | {k:,} / {n:,} | **{p:.1f}%** | {lo:.1f}–{hi:.1f}% |")

        # 组间效应量（A 组合并 vs B 组合并）
        ka = sum(vals[t][0] for t in vals if grp[t] == "A")
        na = sum(vals[t][1] for t in vals if grp[t] == "A")
        kb = sum(vals[t][0] for t in vals if grp[t] == "B")
        nb = sum(vals[t][1] for t in vals if grp[t] == "B")
        kt = sum(vals[t][0] for t in vals if grp[t] == "theme")
        nt = sum(vals[t][1] for t in vals if grp[t] == "theme")
        L.append("")
        if na and nb:
            pa, pb = ka / na * 100, kb / nb * 100
            # RD 的 95% CI
            se = math.sqrt(pa * (100 - pa) / na + pb * (100 - pb) / nb)
            rd, lo, hi = pa - pb, pa - pb - 1.96 * se, pa - pb + 1.96 * se
            # OR 及 CI（Haldane 修正防 0）
            a2, b2 = ka + 0.5, na - ka + 0.5
            c2, d2 = kb + 0.5, nb - kb + 0.5
            orv = (a2 * d2) / (b2 * c2)
            se_or = math.sqrt(1 / a2 + 1 / b2 + 1 / c2 + 1 / d2)
            olo, ohi = math.exp(math.log(orv) - 1.96 * se_or), math.exp(math.log(orv) + 1.96 * se_or)
            L.append(f"**A 组 vs B 组**：A {pa:.1f}%（{ka:,}/{na:,}） vs B {pb:.1f}%（{kb:,}/{nb:,}）　"
                     f"**RD = {rd:+.1f}pp**（95% CI {lo:+.1f} ~ {hi:+.1f}）　"
                     f"**OR = {orv:.2f}**（{olo:.2f}–{ohi:.2f}）")
            sig = "✅ 显著" if (lo > 0 or hi < 0) else "⚠️ 不显著"
            L.append(f"→ RD 的 95% CI {'不含 0' if (lo > 0 or hi < 0) else '含 0'}：**{sig}**")
        if nt:
            L.append(f"（参考）**主题仓**：{kt/nt*100:.1f}%（{kt:,}/{nt:,}）")
        L.append("")

    # ---------- 表 2：中位数型指标 ----------
    L += ["## 中位数型指标", ""]
    for col, name in MED:
        L += [f"### {name}（`{col}`）", "",
              "| 仓库 | 组 | 中位数 | n |", "|---|:---:|---:|---:|"]
        for tag, _, g in REPOS:
            m, n = med(data[tag], col)
            L.append(f"| {tag} | {g} | {m:,.1f} | {n:,} |" if m is not None
                     else f"| {tag} | {g} | — | {n:,} |")
        L.append("")

    # ---------- 表 3：分离度汇总（两种 B 组口径） ----------
    L += ["## 分离度汇总（两种口径）", "",
          "**口径 1（严格）**：B 组只取 2 个「无相关报道」的独立对照仓（ollama / langchain）",
          "**口径 2（保守）**：B 组取全部 5 个「无相关报道」的仓（含 3 个主题仓）",
          "",
          "口径 2 更保守 —— 它不挑选「最干净的」对照，因此结论更稳。", ""]
    for label, bset in (("口径 1：B = ollama + langchain", {"ollama", "langchain"}),
                        ("口径 2：B = 全部 5 个无相关报道仓", {"rea", "openclaw", "bettafish",
                                                              "ollama", "langchain"})):
        L += [f"### {label}", "",
              "| 指标 | A 组区间 | B 组区间 | 间距 | 判定 |", "|---|---:|---:|---:|:---:|"]
        n_sep = 0
        for col, name, _ in BIN:
            av = [prop(data[t], col)[2] for t, _, g in REPOS if g == "A"]
            bv = [prop(data[t], col)[2] for t, _, g in REPOS if t.lower() in bset]
            av = [v for v in av if v is not None]
            bv = [v for v in bv if v is not None]
            if len(av) < 2 or len(bv) < 2:
                continue
            sep = (min(av) > max(bv)) or (max(av) < min(bv))
            gap = (min(av) - max(bv)) if min(av) > max(bv) else (min(bv) - max(av))
            if sep:
                n_sep += 1
            L.append(f"| **{name}** | {min(av):.1f}–{max(av):.1f}% | {min(bv):.1f}–{max(bv):.1f}% | "
                     f"{gap:+.1f}pp | {'✅ 零重叠' if sep else '❌ 重叠'} |")
        L += ["", f"**共 {n_sep} 条零重叠**", ""]

    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "indicators_formal.md")
    open(dst, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n→ {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
