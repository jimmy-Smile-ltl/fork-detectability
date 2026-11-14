# -*- coding: utf-8 -*-
"""三仓全维度数据量报告。

    python scripts/analysis/_data_volume.py

输出 data/out/data_volume.md：每个仓库 × 每个维度的
记录数 / 有效账号 / 覆盖率 / 九态分布 / 文件体积。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")

REPOS = {
    "rea": {
        "forks": "forks_all_morluto__rea.jsonl",
        "dims": {
            "D1 档案": "profile_tier_screen_rea_all.jsonl",
            "D2 事件": "atom_rea.jsonl",
            "D4L star": "starlist_rea.jsonl",
            "D8H 仓库": "repos_html_rea.jsonl",
            "D9 提交": "commits_rea.jsonl",
            "D13 日历": "heat_rea_all.jsonl",
            "D14 头像": "avatar_rea.jsonl",
            "D15 主页": "profile_html_rea.jsonl",
        },
    },
    "openclaw": {
        "forks": "forks_all_openclaw__openclaw.jsonl",
        "dims": {
            "D1 档案": "profile_tier_screen_openclaw_all.jsonl",
            "D2 事件": "atom_openclaw.jsonl",
            "D4L star": "starlist_openclaw.jsonl",
            "D8H 仓库": "repos_html_openclaw.jsonl",
            "D9 提交": "commits_openclaw.jsonl",
            "D13 日历": "heat_openclaw_all.jsonl",
            "D14 头像": "avatar_openclaw.jsonl",
            "D15 主页": "profile_html_openclaw.jsonl",
        },
    },
    "BettaFish": {
        "forks": "forks_all_666ghj__BettaFish.jsonl",
        "dims": {
            "D1 档案": "profile_tier_screen_bettafish_all.jsonl",
            "D2 事件": "atom_bettafish.jsonl",
            "D4L star": "starlist_bettafish.jsonl",
            "D8H 仓库": "repos_html_bettafish.jsonl",
            "D9 提交": "commits_bettafish.jsonl",
            "D13 日历": "heat_bettafish_all.jsonl",
            "D14 头像": "avatar_bettafish.jsonl",
            "D15 主页": "profile_html_bettafish.jsonl",
        },
    },
}


def outcome_of(r: dict) -> str:
    """统一取结果态，兼容新旧两种格式。"""
    oc = r.get("outcome")
    if oc:
        return oc
    st = r.get("status")
    if isinstance(st, str):
        return {"ok": "OK", "unavailable": "NETWORK",
                "gone": "DELETED", "no_cells": "BAD_RESPONSE"}.get(st, "BAD_RESPONSE")
    if r.get("type") in ("photo", "identicon", "default"):
        return "OK"
    http = r.get("http", st if isinstance(st, int) else None)
    return {200: "OK", 404: "DELETED", 403: "PRIVATE", 0: "NETWORK",
            401: "AUTH", 409: "EMPTY", 422: "EMPTY"}.get(http, "NETWORK")


USABLE = {"OK", "EMPTY"}


def scan(path: str) -> tuple[Counter, set, int]:
    c: Counter = Counter()
    usable: set = set()
    n = 0
    if not os.path.exists(path):
        return c, usable, 0
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            c["坏行"] += 1
            continue
        n += 1
        oc = outcome_of(r)
        c[oc] += 1
        lg = (r.get("login") or "").lower()
        if lg and oc in USABLE:
            usable.add(lg)
    return c, usable, n


def human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1e6:.2f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}K"
    return str(n)


def main() -> int:
    L = ["# 三仓全维度数据量报告", "",
         "> 生成时间：2026-11-14 · 数据源 `data/raw/*.jsonl`", ""]

    grand_records = 0
    grand_accounts = 0
    summary_rows = []

    for repo, spec in REPOS.items():
        fpath = os.path.join(RAW, spec["forks"])
        fc, fus, fn = scan(fpath)
        # fork 名单用 login 全集（无 outcome 字段）
        fork_logins = set()
        if os.path.exists(fpath):
            for line in open(fpath, encoding="utf-8", errors="replace"):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                lg = (r.get("login") or "").lower()
                if lg:
                    fork_logins.add(lg)
        n_fork = len(fork_logins)

        L += [f"## {repo}", "",
              f"**fork 名单**：{n_fork:,} 人 · 文件 {os.path.getsize(fpath)/1e6:.2f} MB",
              "",
              "| 维度 | 记录数 | 有效账号 | 覆盖率 | 文件 | 九态分布 |",
              "|---|---:|---:|---:|---:|---|"]

        tot_rec = 0
        tot_acc = 0
        for name, fn_ in spec["dims"].items():
            p = os.path.join(RAW, fn_)
            c, us, n = scan(p)
            tot_rec += n
            tot_acc += len(us)
            size = os.path.getsize(p) / 1e6 if os.path.exists(p) else 0
            dist = " ".join(f"{k}:{v:,}" for k, v in c.most_common(5) if v)
            cov = f"{len(us)/max(n_fork,1)*100:.1f}%"
            L.append(f"| {name} | {n:,} | {len(us):,} | **{cov}** | "
                     f"{size:.2f} MB | {dist} |")

        L += ["", f"**小计**：{tot_rec:,} 条记录 · 8 维有效账号合计 {tot_acc:,}",
              ""]

        grand_records += tot_rec + fn
        grand_accounts += n_fork
        summary_rows.append((repo, n_fork, tot_rec + fn))

    L += ["## 汇总", "",
          "| 仓库 | fork 名单 | 全维度记录 | 数据目录体积 |",
          "|---|---:|---:|---:|"]
    for repo, nf, tr in summary_rows:
        L.append(f"| {repo} | {nf:,} | {tr:,} | — |")
    L += [f"| **合计** | **{grand_accounts:,}** | **{grand_records:,}** | — |", ""]

    # 目录体积
    try:
        total = sum(os.path.getsize(os.path.join(RAW, f))
                    for f in os.listdir(RAW) if f.endswith(".jsonl"))
        L.append(f"`data/raw/` 全部 JSONL 合计 **{total/1e9:.2f} GB**")
    except Exception:
        pass

    md = "\n".join(L) + "\n"
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "data_volume.md")
    open(dst, "w", encoding="utf-8").write(md)
    print(md)
    print(f"→ {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
