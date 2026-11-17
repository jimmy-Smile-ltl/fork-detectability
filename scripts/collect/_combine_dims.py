# -*- coding: utf-8 -*-
"""把「按维度分文件」的旧数据合并成「一人一条」的完整档案。

    python scripts/collect/_combine_dims.py --tag frp --repos fatedier__frp
    python scripts/collect/_combine_dims.py --tag nodejsargo

为什么要这个工具：
    项目早期是 collect_run.py 按维度写入 9 个文件（profile_tier_screen_*.jsonl、
    repos_html_*.jsonl …）。这些数据已经采过了，没必要重新跑一遍网络。
    本脚本把它们按 login 聚合成 accounts_full_{tag}.jsonl（与 collect_full.py 同格式）。

合并规则：
    同一 (login, dim) 有多条记录时，按「成功优先」取值：
        OK > EMPTY > 终态(DELETED/NOT_FOUND/PRIVATE/AUTH) > 可重试(NETWORK/RATE_LIMIT/BAD_RESPONSE)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from collect_core import DIMS, dim_path, legacy_to_outcome   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")

PRI = {"OK": 5, "EMPTY": 4, "DELETED": 3, "NOT_FOUND": 3, "PRIVATE": 3, "AUTH": 3,
       "NETWORK": 1, "RATE_LIMIT": 1, "BAD_RESPONSE": 1}


def score(oc: str) -> int:
    return PRI.get(oc, 0)


def payload_of(rec: dict) -> dict:
    if isinstance(rec.get("payload"), dict) and rec["payload"]:
        return rec["payload"]
    # 旧格式：除元字段外都是 payload
    return {k: v for k, v in rec.items()
            if k not in ("login", "outcome", "http", "ts", "attempt", "note")}


def outcome_of(rec: dict) -> str:
    oc = rec.get("outcome")
    if oc:
        return oc
    try:
        return legacy_to_outcome(rec).value
    except Exception:
        return "BAD_RESPONSE"


def load_dim(dim: str, tag: str) -> dict[str, tuple[str, dict]]:
    """→ {login: (outcome, payload)}，同一 login 取最优。"""
    p = dim_path(dim, tag, RAW)
    best: dict[str, tuple[str, dict]] = {}
    if not os.path.exists(p):
        return best
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if not lg:
            continue
        oc = outcome_of(r)
        cur = best.get(lg)
        if cur is None or score(oc) > score(cur[0]):
            best[lg] = (oc, payload_of(r))
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="仓库标签，如 frp / nodejsargo")
    ap.add_argument("--dims", default="", help="逗号分隔；默认全部已存在的维度")
    ap.add_argument("--out", default="", help="输出文件，默认 accounts_full_{tag}.jsonl")
    args = ap.parse_args()

    dims = [d.strip() for d in args.dims.split(",") if d.strip()] or list(DIMS)
    per_dim = {}
    for d in dims:
        m = load_dim(d, args.tag)
        if m:
            per_dim[d] = m
            print(f"  {d:5s} {len(m):>7,} 个账号", flush=True)
    if not per_dim:
        raise SystemExit(f"❌ {args.tag} 没有任何维度的数据")

    # 所有出现过的 login
    logins = set()
    for m in per_dim.values():
        logins |= set(m)
    print(f"\n合并 {len(logins):,} 个账号 × {len(per_dim)} 个维度", flush=True)

    dst = args.out or os.path.join(RAW, f"accounts_full_{args.tag}.jsonl")
    n_ok = n_full = 0
    with open(dst, "w", encoding="utf-8") as fh:
        for lg in sorted(logins):
            outcomes, dims_payload, notes = {}, {}, {}
            for d, m in per_dim.items():
                if lg not in m:
                    continue
                oc, payload = m[lg]
                outcomes[d] = oc
                dims_payload[d] = payload
            n_ok += 1
            api_dims = {k: v for k, v in outcomes.items() if k not in ("D13", "D15")}
            if api_dims and all(v in ("OK", "EMPTY", "DELETED", "NOT_FOUND", "PRIVATE", "AUTH")
                                for v in api_dims.values()):
                n_full += 1
            rec = {"login": lg, "ts": 0, "elapsed": 0, "outcomes": outcomes,
                   "dims": dims_payload, "_source": "merged_from_per_dim"}
            fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

    size = os.path.getsize(dst) / 1e6
    print(f"\n→ {dst}")
    print(f"  账号 {n_ok:,} | API 维完整 {n_full:,}（{n_full/max(n_ok,1)*100:.1f}%）| {size:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
