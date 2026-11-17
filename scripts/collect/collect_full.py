# -*- coding: utf-8 -*-
"""一次性采集一个账户的全部信息，**一人一条记录**（不按维度拆文件）。

    python scripts/collect/collect_full.py --repo fatedier/frp --workers 16 --sample 500
    python scripts/collect/collect_full.py --repo fatedier/frp --workers 16 --limit 1000

输出 data/raw/accounts_full_{tag}.jsonl，每行：
    {
      "login": "...",
      "ts": 1794900000.0,
      "elapsed": 3.2,
      "outcomes": {"D1":"OK","D8H":"OK",...},
      "dims": {"D1": {...}, "D8H": {...}, ...}
    }

与 collect_run.py 的区别：
    collect_run.py  按维度写入 9 个文件（一维一行）—— 适合大规模分维度补采
    本脚本          按账户写入 1 个文件（一人一行）—— 适合拿"一个人的完整档案"

断点续采：已在该文件中出现的 login 会跳过（可用 --redo 强制重采）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import collect_core as C          # noqa: E402
import fetchers as F              # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")

# 默认采集顺序：API 的快维先跑，HTML 的 D15 最后。
# D13（贡献日历，230KB）实测成功率 0%，默认**不采** —— 需要时用 --dims 显式加上。
DEFAULT_ORDER = ["D1", "D2B", "D4L", "D8H", "D9", "D14", "D6", "D15"]


def load_fork_list(owner: str, repo: str) -> list[str]:
    p = os.path.join(RAW, f"forks_all_{owner}__{repo}.jsonl")
    if not os.path.exists(p):
        raise SystemExit(f"❌ fork 名单不存在：{p}\n"
                         f"   请先跑：python scripts/collect/collect_run.py "
                         f"--repo {owner}/{repo} --list-only")
    out, seen = [], set()
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if lg and lg not in seen:
            seen.add(lg)
            out.append(lg)
    return out


HTML_DIMS = {"D13", "D15"}
TERMINAL = {"OK", "EMPTY", "DELETED", "NOT_FOUND", "PRIVATE", "AUTH"}


def api_complete(rec: dict) -> bool:
    """除 HTML 维外，全部拿到终态。"""
    return all(v in TERMINAL for k, v in rec.get("outcomes", {}).items()
               if k not in HTML_DIMS)


def load_done(path: str) -> set[str]:
    """只把「API 维全部完成」的账号视为已完成，其余下次重跑。"""
    if not os.path.exists(path):
        return set()
    done = set()
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if lg and api_complete(r):
            done.add(lg)
    return done


def best_per_login(path: str) -> dict[str, dict]:
    """读回合并结果：同一 login 多条记录时，取「完成的维度最多」的那条。"""
    best: dict[str, dict] = {}
    if not os.path.exists(path):
        return best
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if not lg:
            continue
        score = sum(1 for v in r.get("outcomes", {}).values() if v in TERMINAL)
        cur = best.get(lg)
        if cur is None or score > cur["_score"]:
            r["_score"] = score
            best[lg] = r
    return best


class CombinedWriter:
    """一人一行，只追加；失败自动重试，全部成功才认作完成。"""

    def __init__(self, path: str, redo: bool = False):
        self.path = path
        self.lock = threading.Lock()
        self.done: set[str] = set() if redo else load_done(path)
        if redo and os.path.exists(path):
            os.remove(path)

    def write(self, rec: dict) -> None:
        with self.lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            # 只有 API 维齐全才记「完成」，否则下次重跑会再来一次
            if api_complete(rec):
                self.done.add(rec["login"])

    def pending(self, logins: list[str]) -> list[str]:
        return [l for l in logins if l not in self.done]


RETRYABLE = {"NETWORK", "RATE_LIMIT", "BAD_RESPONSE"}


def collect_one(login: str, ctx: F.Ctx, order: list[str],
                retries: int = 3, dim_rounds: int = 3) -> dict:
    """一个账户的**全部维度**，并反复重试失败的那些维度。

    为什么要"按维重试 + 多轮"：
        单次请求成功率实测波动很大（20%–85%）。如果每个维度只试 3 次，
        7 个维度全成功的概率会低到 1%。改成「先跑一遍，再把没成功的维度
        重新跑 2 轮」，同一个账号能显著凑齐。
        注意：这仍然是"一次采一个账户的全部信息"，只是内部会补几次。
    """
    t0 = time.time()
    outcomes, dims, notes = {}, {}, {}
    pending = list(order)

    for rnd in range(dim_rounds):
        if not pending:
            break
        if rnd:
            time.sleep(0.4 * rnd)                  # 换一个隧道窗口
        still = []
        for dim in pending:
            last = None
            for attempt in range(1, retries + 1):
                try:
                    oc, payload, code, note = F.fetch(dim, login, ctx)
                except Exception as e:
                    oc, payload, code, note = C.Outcome.NETWORK, {}, None, type(e).__name__
                last = (oc, payload, code, note)
                if oc.value not in RETRYABLE:
                    break
                if attempt < retries:
                    time.sleep(0.2 * attempt)
            oc, payload, code, note = last
            outcomes[dim] = oc.value
            dims[dim] = payload
            if note:
                notes[dim] = note
            if oc.value in RETRYABLE:
                still.append(dim)
        pending = still

    if notes:
        dims["_notes"] = notes
    return {"login": login, "ts": time.time(), "elapsed": round(time.time() - t0, 2),
            "outcomes": outcomes, "dims": dims}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--tag", default="", help="输出文件标签，默认用 repo 名（去连字符）")
    ap.add_argument("--dims", default="", help="逗号分隔；默认全部（HTML 维最后）")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--sample", type=int, default=0, help="随机抽样 N 人（种子 42）")
    ap.add_argument("--limit", type=int, default=0, help="按名单顺序取前 N 人")
    ap.add_argument("--max-minutes", type=float, default=0, help="0 = 不限时")
    ap.add_argument("--redo", action="store_true", help="忽略已有记录，全部重采")
    ap.add_argument("--no-proxy", action="store_true")
    args = ap.parse_args()

    owner, _, repo = args.repo.partition("/")
    if not repo:
        raise SystemExit("--repo 需要 owner/repo 格式")
    tag = args.tag or repo.replace("-", "")

    order = [d.strip() for d in args.dims.split(",") if d.strip()] or DEFAULT_ORDER
    unknown = [d for d in order if d not in C.DIMS]
    if unknown:
        raise SystemExit(f"未知维度：{unknown}；可用：{sorted(C.DIMS)}")
    missing = [d for d in C.DIMS if d not in order]
    if missing:
        print(f"⚠️  本次未采集 {len(missing)} 个维度 → {', '.join(missing)}", flush=True)

    logins = load_fork_list(owner, repo)
    print(f"fork 名单：{len(logins):,} 人", flush=True)

    if args.sample:
        import random
        rnd = random.Random(42)
        logins = sorted(rnd.sample(logins, min(args.sample, len(logins))))
        print(f"随机采样 {len(logins):,} 人（seed=42）", flush=True)
    elif args.limit:
        logins = logins[: args.limit]
        print(f"按名单顺序取前 {len(logins):,} 人", flush=True)

    dst = os.path.join(RAW, f"accounts_full_{tag}.jsonl")
    w = CombinedWriter(dst, redo=args.redo)
    todo = w.pending(logins)
    print(f"待采 {len(todo):,} 人 → {os.path.basename(dst)}", flush=True)
    if not todo:
        print("无需采集（已全部完成）")
        return 0

    # 代理
    proxies = None
    if not args.no_proxy:
        cfg_p = os.path.join(BASE, "data", "proxy_tunnel.json")
        if os.path.exists(cfg_p):
            c = json.load(open(cfg_p, encoding="utf-8"))
            u = f"http://{c['username']}:{c['password']}@{c['host']}/"
            proxies = {"http": u, "https": u}
    ctx = F.Ctx(year=2026, proxies=proxies, proxies_html=proxies,
                timeout=20, known_user_id={})

    t0 = time.time()
    limit_t = args.max_minutes * 60 if args.max_minutes else None
    n_ok = n_fail = 0
    lock = threading.Lock()

    def work(lg):
        rec = collect_one(lg, ctx, order)
        w.write(rec)
        return api_complete(rec)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, lg): lg for lg in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                ok = f.result()
                n_ok += 1 if ok else 0
                n_fail += 0 if ok else 1
            except Exception:
                n_fail += 1
            if i % 100 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"  {i:,}/{len(todo):,} | "
                      f"{i/max(el,1)*60:.0f} 人/分钟 | 全部成功 {n_ok:,} | 有失败 {n_fail:,}",
                      flush=True)
            if limit_t and time.time() - t0 > limit_t:
                print("⏱ 到达时限，停止（可重跑续采）", flush=True)
                for ff in futs:
                    ff.cancel()
                break

    print(f"\n完成：{os.path.basename(dst)}", flush=True)
    print(f"  文件行数 {len(w.done):,} | 用时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
