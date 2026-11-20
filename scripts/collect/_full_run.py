# -*- coding: utf-8 -*-
"""全量采集驱动：把每个仓库的 forker 全部采完（不抽样）。

    python scripts/collect/_full_run.py --phase api     # 先跑 API 维
    python scripts/collect/_full_run.py --phase d15     # 再补 D15

设计：
  * `--sample 0`（默认）= 不抽样，采 fork 名单里的所有人
  * 分两阶段：
      1. api —— D1/D2B/D4L/D6/D8H/D9/D14，快（~30–50 人/分钟）
      2. d15 —— 成就徽章，慢（HTML，~25–40 人/分钟）
    分成两阶段是因为把 D15 混进主流程会拖慢 3 倍。
  * 每仓多轮，直到剩余 < 1% 或达到轮次上限
  * 进度写 _full_run.log，可随时中断续跑（记录粒度 = 每人一条）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")

TERM = {"OK", "EMPTY", "DELETED", "NOT_FOUND", "PRIVATE", "AUTH"}
# 高成功率维度：先用来把「每人一条」铺满
CORE_DIMS = ["D1", "D2B", "D4L", "D8H", "D14"]
# 低成功率维度：单独一轮补（D9 需 2 仓都成功，D6 是后期加的）
HARD_DIMS = ["D9", "D6"]
API_DIMS = CORE_DIMS

# (repo, tag, fork 文件, 优先级)  优先级 1 = A/B 对照仓，2 = 独立验证仓
REPOS = [
    ("eooce/nodejs-argo",          "nodejsargo",   "eooce__nodejs-argo",            1),
    ("fatedier/frp",               "frp",          "fatedier__frp",                 1),
    ("ollama/ollama",              "ollama",       "ollama__ollama",                1),
    ("langchain-ai/langchain",     "langchain",    "langchain-ai__langchain",       1),
    ("msitarzewski/agency-agents", "agencyagents", "msitarzewski__agency-agents",   1),
    ("farion1231/cc-switch",       "ccswitch",     "farion1231__cc-switch",         2),
    ("debpalash/VoiceStudio",      "voice",        "debpalash__VoiceStudio",        2),
    ("bilawalsidhu/gods-eye-view", "gods",         "bilawalsidhu__gods-eye-view",   2),
    ("karpathy/autoresearch",      "autores",      "karpathy__autoresearch",        2),
]


def fork_logins(forkf: str) -> set[str]:
    p = os.path.join(RAW, f"forks_all_{forkf}.jsonl")
    out: set[str] = set()
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if lg:
            out.add(lg)
    return out


def done_logins(tag: str, dims: list[str]) -> set[str]:
    p = os.path.join(RAW, f"accounts_full_{tag}.jsonl")
    out: set[str] = set()
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if not lg:
            continue
        oc = r.get("outcomes") or {}
        if all(oc.get(d) in TERM for d in dims):
            out.add(lg)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["api", "d15", "hard"], default="api")
    ap.add_argument("--round-minutes", type=float, default=55)
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--prio", type=int, default=0, help="只跑某优先级（0=全部）")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    if args.phase == "api":
        dims = CORE_DIMS
    elif args.phase == "hard":
        dims = HARD_DIMS
    else:
        dims = ["D15"]
    todo = [x for x in REPOS
            if (not args.prio or x[3] == args.prio) and (not args.tag or x[1] == args.tag)]

    print(f"阶段 {args.phase} | 维度 {','.join(dims)} | {len(todo)} 个仓库", flush=True)
    t_all = time.time()

    for repo, tag, forkf, prio in todo:
        fl = fork_logins(forkf)
        if not fl:
            print(f"\n{tag}: 无 fork 名单，跳过", flush=True)
            continue
        print(f"\n{'='*70}\n{tag}  (P{prio})  名单 {len(fl):,}\n{'='*70}", flush=True)

        for rnd in range(1, args.rounds + 1):
            dn = done_logins(tag, dims)
            pending = len(fl - dn)
            pct = len(dn & fl) / max(len(fl), 1) * 100
            print(f"  第 {rnd} 轮 | 完成 {len(dn & fl):,}/{len(fl):,} = {pct:.1f}% "
                  f"| 待采 {pending:,}", flush=True)
            if pending <= len(fl) * 0.01:
                print("  ✅ 达标（剩余 <1%）", flush=True)
                break
            t0 = time.time()
            cmd = [sys.executable, "scripts/collect/collect_full.py",
                   "--repo", repo, "--tag", tag, "--workers", str(args.workers),
                   "--dims", ",".join(dims), "--max-minutes", str(args.round_minutes)]
            r = subprocess.run(cmd, capture_output=True)
            out = r.stdout.decode("utf-8", errors="replace")
            for line in out.splitlines()[-3:]:
                print("     " + line, flush=True)
            print(f"     用时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
            time.sleep(3)

        dn = done_logins(tag, dims)
        print(f"  最终 {len(dn & fl):,}/{len(fl):,} = {len(dn & fl)/max(len(fl),1)*100:.1f}%",
              flush=True)

    print(f"\n全部完成 @ {time.strftime('%Y-%m-%d %H:%M')} | 总耗时 {(time.time()-t_all)/3600:.1f} 小时",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
