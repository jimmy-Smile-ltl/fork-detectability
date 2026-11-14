# -*- coding: utf-8 -*-
"""生成文档用的真实数据样例（自动脱敏）。

    python scripts/analysis/_make_samples.py

输出 docs/13_采集结果示例.md：每个维度一条真实记录（login 掩码）。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
DOCS = os.path.join(BASE, "docs")

TAG = "bettafish"
FORKS = "forks_all_666ghj__BettaFish.jsonl"

# 维度 → (文件名, 说明)
DIMS = [
    ("fork 名单", FORKS, "哪个账号在什么时间 fork、fork 后有没有 push"),
    ("D1 档案", f"profile_tier_screen_{TAG}_all.jsonl", "/users/{login} 的 15 个字段"),
    ("D2 事件", f"atom_{TAG}.jsonl", "RSS 近 30 条事件类型"),
    ("D2B 90天活动", f"events90_{TAG}.jsonl", "API /events 近 90 天事件"),
    ("D4L star 总数", f"starlist_{TAG}.jsonl", "star 了多少仓（含明细）"),
    ("star 明细", f"starred_all_{TAG}.jsonl", "每人 star 过的仓（全分页）"),
    ("D8H 仓库", f"repos_html_{TAG}.jsonl", "自建 vs fork 结构"),
    ("repo 明细", f"repos_all_{TAG}.jsonl", "每人全部仓库（全分页）"),
    ("D9 提交", f"commits_{TAG}.jsonl", "自建仓的本人提交数"),
    ("D13 日历", f"heat_{TAG}_all.jsonl", "全年 365 天逐日贡献"),
    ("D14 头像", f"avatar_{TAG}.jsonl", "identicon vs 照片"),
    ("D15 主页", f"profile_html_{TAG}.jsonl", "成就徽章/pinned/组织"),
]


def mask(v):
    if isinstance(v, str) and len(v) > 4 and "login" in str(v)[:0]:
        return v
    return v


def scrub(obj, key=""):
    """递归脱敏：login 只留前 3 位。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("login", "name", "display_name") and isinstance(v, str) and len(v) > 3:
                out[k] = v[:3] + "***"
            elif k in ("avatar_url", "bio", "email", "blog", "twitter_username"):
                out[k] = ("<已脱敏>" if v else v)
            else:
                out[k] = scrub(v, k)
        return out
    if isinstance(obj, list):
        return [scrub(x, key) for x in obj[:3]] + (["…（共 %d 条）" % len(obj)] if len(obj) > 3 else [])
    return obj


def pick(path: str, need_ok=True):
    """取一条"有效且有内容"的记录：优先 payload/items 最大的。"""
    if not os.path.exists(path):
        return None
    best, best_size = None, -1
    for i, line in enumerate(open(path, encoding="utf-8", errors="replace")):
        if i > 500:            # 只看前 500 行，够找到样例
            break
        try:
            r = json.loads(line)
        except Exception:
            continue
        if need_ok:
            oc = r.get("outcome")
            good = oc in ("OK", "EMPTY") or r.get("http") == 200 \
                or r.get("complete") or r.get("status") == "ok"
            if not good:
                continue
        body = r.get("payload") or {k: v for k, v in r.items()
                                    if k not in ("login", "outcome", "http", "ts", "attempt", "note")}
        size = len(json.dumps(body, ensure_ascii=False, default=str))
        if size > best_size:
            best, best_size = r, size
    return best


def main() -> int:
    L = ["# 采集结果示例（真实数据，已脱敏）", "",
         f"> 数据源：`data/raw/*.jsonl`（BettaFish 仓）", "",
         "所有 `login` 只保留前 3 位，`avatar_url`/`bio`/`email` 已脱敏。", ""]

    for name, fn, desc in DIMS:
        p = os.path.join(RAW, fn)
        L += [f"## {name}", "", f"> {desc}", ""]
        r = pick(p, need_ok=(name not in ("fork 名单",)))
        if r is None:
            L += ["（无可用样例）", ""]
            continue
        obj = scrub(r)
        # 大字段截断
        txt = json.dumps(obj, ensure_ascii=False, indent=2)
        if len(txt) > 2200:
            txt = txt[:2200] + "\n  …（已截断）"
        L += ["```json", txt, "```", ""]

    # 统计
    L += ["## 各维度记录数与状态分布", "",
          "| 维度 | 文件 | 行数 | 状态分布 |", "|---|---|---:|---|"]
    for name, fn, desc in DIMS:
        p = os.path.join(RAW, fn)
        if not os.path.exists(p):
            L.append(f"| {name} | `{fn}` | — | 文件不存在 |")
            continue
        c = Counter()
        n = 0
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            n += 1
            c[r.get("outcome") or r.get("status") or r.get("http")] += 1
        dist = " ".join(f"{k}:{v:,}" for k, v in c.most_common(4))
        L.append(f"| {name} | `{fn}` | {n:,} | {dist} |")

    md = "\n".join(L) + "\n"
    os.makedirs(DOCS, exist_ok=True)
    dst = os.path.join(DOCS, "13_采集结果示例.md")
    open(dst, "w", encoding="utf-8").write(md)
    print(f"→ {dst}（{len(md.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
