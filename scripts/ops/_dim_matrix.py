# -*- coding: utf-8 -*-
"""全仓库 × 全维度覆盖矩阵（权威版）。

    python scripts/ops/_dim_matrix.py

同时读两种数据源：
  1. 分维度文件（data/raw/{dim 对应文件名}）
  2. 一人一条档案（data/raw/accounts_full_{tag}.jsonl）
取两者中覆盖更高者。
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import RAW, iter_jsonl  # noqa: E402

TERM = {"OK", "EMPTY", "DELETED", "NOT_FOUND", "PRIVATE", "AUTH"}

# (tag, fork 文件名, 分母, 是否采样)
#   采样仓的分母 = **计划采样的账号数**（不是全量 fork 数），
#   否则覆盖率会被严重低估。
REPOS = [
    ("rea", "morluto__rea", 108352, False),
    ("openclaw", "openclaw__openclaw", 82753, False),
    ("bettafish", "666ghj__BettaFish", 7690, False),
    ("shardeum", "shardeum__shardeum", 716, False),
    ("union", "unionlabs__union", 4297, False),
    ("anoma", "anoma__anoma", 4139, False),
    ("nodejsargo", "eooce__nodejs-argo", 2000, True),
    ("frp", "fatedier__frp", 2000, True),
    ("agencyagents", "msitarzewski__agency-agents", 2000, True),
    ("ollama", "ollama__ollama", 5000, True),
    ("langchain", "langchain-ai__langchain", 5000, True),
]

# 维度 → 分维度文件名模板
DIM_FILE = {
    "D1":  "profile_tier_screen_{t}_all.jsonl",
    "D2":  "atom_{t}.jsonl",
    "D2B": "events90_{t}.jsonl",
    "D4L": "starlist_{t}.jsonl",
    "D6":  "orgs_{t}.jsonl",
    "D8H": "repos_html_{t}.jsonl",
    "D9":  "commits_{t}.jsonl",
    "D13": "heat_{t}_all.jsonl",
    "D14": "avatar_{t}.jsonl",
    "D15": "profile_html_{t}.jsonl",
}
DIMS = list(DIM_FILE)


def usable(r: dict) -> bool:
    oc = r.get("outcome")
    if oc in TERM:
        return True
    st = r.get("status")
    if isinstance(st, str):
        return st in ("ok", "no_cells")
    if isinstance(st, int) and st == 200:
        return True
    if r.get("http") == 200:
        return True
    if r.get("complete"):
        return True
    if r.get("type") in ("photo", "identicon", "default"):
        return True
    return False


def count_per_dim(tag: str, dim: str) -> int:
    p = os.path.join(RAW, DIM_FILE[dim].format(t=tag))
    if not os.path.exists(p):
        return 0
    seen = set()
    for r in iter_jsonl(p):
        if usable(r):
            lg = (r.get("login") or "").lower()
            if lg:
                seen.add(lg)
    return len(seen)


def count_combined(tag: str) -> dict[str, int]:
    p = os.path.join(RAW, f"accounts_full_{tag}.jsonl")
    out = {d: 0 for d in DIMS}
    if not os.path.exists(p):
        return out
    seen: dict[str, set] = {d: set() for d in DIMS}
    for r in iter_jsonl(p):
        lg = (r.get("login") or "").lower()
        if not lg:
            continue
        for d, oc in (r.get("outcomes") or {}).items():
            if d in seen and oc in TERM:
                seen[d].add(lg)
    return {d: len(s) for d, s in seen.items()}


def main() -> int:
    print("=" * 108)
    print("全仓库 × 全维度覆盖矩阵")
    print("=" * 108)
    head = f"{'仓库':14s}{'分母':>9s}" + "".join(f"{d:>8s}" for d in DIMS)
    print(head)
    print("-" * 108)

    results = {}
    for tag, forkf, total, sampled in REPOS:
        # fork 名单
        fp = os.path.join(RAW, f"forks_all_{forkf}.jsonl")
        n_fork = 0
        if os.path.exists(fp):
            n_fork = sum(1 for _ in open(fp, encoding="utf-8", errors="replace"))
        comb = count_combined(tag)
        # 分母 = 该仓档案里的唯一账号数（= 实际采过的规模）
        cf = os.path.join(RAW, f"accounts_full_{tag}.jsonl")
        uniq = set()
        if os.path.exists(cf):
            for r in iter_jsonl(cf):
                lg = (r.get("login") or "").lower()
                if lg:
                    uniq.add(lg)
        denom = len(uniq) or total
        row, cell = {}, []
        for d in DIMS:
            a = count_per_dim(tag, d)
            b = comb.get(d, 0)
            best = max(a, b)
            row[d] = (best, a, b)
            if best == 0:
                cell.append(f"{'—':>8s}")
            else:
                cell.append(f"{best/denom*100:>7.1f}%")
        results[tag] = {"total": denom, "fork": n_fork, "dims": row, "sampled": sampled}
        print(f"{tag:14s}{denom:>9,}" + "".join(cell))

    print("-" * 108)
    print("\n覆盖率 = 有终态记录的账号数 ÷ API 报告的 fork 数\n")

    # 缺口清单
    print("=" * 108)
    print("缺口清单（覆盖率 < 90%）")
    print("=" * 108)
    for tag, info in results.items():
        gaps = []
        for d in DIMS:
            best = info["dims"][d][0]
            cov = best / max(info["total"], 1) * 100
            if cov < 90:
                gaps.append(f"{d} {cov:.1f}%")
        if gaps:
            print(f"  {tag:14s} " + " | ".join(gaps))
        else:
            print(f"  {tag:14s} ✅ 全部维度 ≥ 90%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
