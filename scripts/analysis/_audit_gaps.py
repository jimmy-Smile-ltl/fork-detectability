# -*- coding: utf-8 -*-
"""数据缺口核查（权威版）：逐仓逐维度实测，不用假设。

    python scripts/analysis/_audit_gaps.py

输出 data/out/gap_audit.md
判定口径：
  OK        覆盖率 ≥ 90%
  PARTIAL   30% ≤ 覆盖率 < 90%
  LOW        0% < 覆盖率 < 30%
  MISSING   文件不存在
  N/A       该仓未纳入该维度
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

# 仓库 → (fork 名单文件, 目标人数)
REPOS = {
    "rea":      ("forks_all_morluto__rea.jsonl",          108352),
    "openclaw": ("forks_all_openclaw__openclaw.jsonl",     82753),
    "BettaFish":("forks_all_666ghj__BettaFish.jsonl",       7690),
    "shardeum": ("forks_all_shardeum__shardeum.jsonl",        716),
    "union":    ("forks_all_unionlabs__union.jsonl",         4297),
    "anoma":    ("forks_all_anoma__anoma.jsonl",             4139),
}

# 维度 → 可能的文件名模式（兼容新旧两种命名）
DIMS = {
    "D1 档案":   ["profile_tier_screen_{t}_all.jsonl"],
    "D2 事件":   ["atom_{t}.jsonl"],
    "D2B 90天":  ["events90_{t}.jsonl"],
    "D4L star":  ["starlist_{t}.jsonl"],
    "D8H 仓库":  ["repos_html_{t}.jsonl"],
    "D9 提交":   ["commits_{t}.jsonl"],
    "D13 日历":  ["heat_{t}_all.jsonl"],
    "D14 头像":  ["avatar_{t}.jsonl"],
    "D15 主页":  ["profile_html_{t}.jsonl"],
    "star列表":  ["starlist_{t}.jsonl", "starred_all_{t}.jsonl"],
    "repo列表":  ["repos_html_{t}.jsonl", "repos_all_{t}.jsonl"],
}


def usable(r: dict) -> bool:
    oc = r.get("outcome")
    if oc in ("OK", "EMPTY"):
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


def scan(path: str) -> tuple[int, int, Counter, dict]:
    """→ (行数, 唯一有效账号数, 状态分布, 额外统计)"""
    n = 0
    seen: set = set()
    c: Counter = Counter()
    extra: dict = {"items": 0, "capped": 0, "detail_cap": 0}
    if not os.path.exists(path):
        return 0, 0, c, extra
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
        ok = usable(r)
        c["有效" if ok else "无效"] += 1
        lg = (r.get("login") or "").lower()
        if ok and lg:
            seen.add(lg)
        # 列表类：统计条目数与被截断
        st = r.get("stars")
        if isinstance(st, list) and st:
            extra["items"] += len(st)
            if len(st) >= 100:
                extra["capped"] += 1
        if r.get("truncated"):
            extra["capped"] += 1
        d = r.get("own_details")
        if isinstance(d, list) and len(d) >= 12:
            extra["detail_cap"] += 1
    return n, len(seen), c, extra


def judge(cov: float | None) -> str:
    if cov is None:
        return "MISSING"
    if cov >= 90:
        return "OK"
    if cov >= 30:
        return "PARTIAL"
    if cov > 0:
        return "LOW"
    return "MISSING"


def main() -> int:
    L = ["# 数据缺口核查（权威版）", "",
         "> 逐文件实测，不依赖假设。覆盖率 = 有有效记录的账号数 ÷ fork 名单人数。", ""]
    summary: list[tuple] = []

    for tag, (fork_file, total) in REPOS.items():
        fpath = os.path.join(RAW, fork_file)
        if os.path.exists(fpath):
            logins = set()
            for line in open(fpath, encoding="utf-8", errors="replace"):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                lg = (r.get("login") or "").lower()
                if lg:
                    logins.add(lg)
            total = len(logins) or total
        L += [f"## {tag}（fork 名单 {total:,} 人）", "",
              "| 维度 | 文件 | 行数 | 有效账号 | 覆盖率 | 判定 | 备注 |",
              "|---|---|---:|---:|---:|:---:|---|"]
        for dim, pats in DIMS.items():
            hit = None
            used = None
            for p in pats:
                cand = os.path.join(RAW, p.format(t=tag))
                if os.path.exists(cand):
                    hit = cand
                    used = p
                    break
            if hit is None:
                L.append(f"| {dim} | — | — | — | — | MISSING | 文件不存在 |")
                summary.append((tag, dim, None, "MISSING", ""))
                continue
            n, u, c, ex = scan(hit)
            cov = u / max(total, 1) * 100
            j = judge(cov)
            note = []
            if ex["items"]:
                note.append(f"{ex['items']:,} 条目")
            if ex["capped"]:
                note.append(f"{ex['capped']:,} 触及上限")
            if ex["detail_cap"]:
                note.append(f"{ex['detail_cap']:,} 详情到 12 上限")
            L.append(f"| {dim} | `{os.path.basename(hit)}` | {n:,} | {u:,} | "
                     f"**{cov:.1f}%** | {j} | {' · '.join(note)} |")
            summary.append((tag, dim, cov, j, os.path.basename(hit)))
        L.append("")

    # ---- 缺口汇总 ----
    L += ["## 缺口汇总（只列非 OK 的）", "",
          "| 仓库 | 维度 | 覆盖率 | 判定 | 原因 |", "|---|---|---:|:---:|---|"]
    REASON = {
        "D9 提交": "每人查 2 仓，**两次都成功**才算（p² 损耗）",
        "D13 日历": "github.com 软反爬，连续抓即封",
        "D15 主页": "同上",
        "D2 事件": "对照组未设计该维度（用 D2B 替代）",
        "D2B 90天": "新增维度，rea/openclaw 未采",
        "star列表": "旧格式上限 100 条",
        "repo列表": "详情只存前 12 个仓",
    }
    gaps = [s for s in summary if s[3] != "OK"]
    for tag, dim, cov, j, fn in gaps:
        cs = f"{cov:.1f}%" if cov is not None else "—"
        L.append(f"| {tag} | {dim} | {cs} | {j} | {REASON.get(dim, '')} |")

    L += ["", f"**共 {len(gaps)} 个非 OK 项 / {len(summary)} 个总项**", ""]

    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "gap_audit.md")
    open(dst, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"→ {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
