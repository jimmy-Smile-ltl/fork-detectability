# -*- coding: utf-8 -*-
"""通用采集 runner：取 fork 名单 → 按两种策略之一采全部维度。

    # 一次性采完一个人（本命令，BettaFish 实测用这个）
    py scripts/collect/collect_run.py --repo 666ghj/BettaFish --mode per_account

    # 一维采完再下一维
    py scripts/collect/collect_run.py --repo 666ghj/BettaFish --mode per_dim --dims D1,D15

    # 只取名单（不采详情）
    py scripts/collect/collect_run.py --repo 666ghj/BettaFish --list-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collect_core import Collector, DIMS, Outcome, Record, dim_path, TERMINAL
import collect_core as core
import fetchers as F

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")

API_HEADERS = {"Accept": "application/vnd.github+json",
               "User-Agent": "fork-study/1.0", "Connection": "close"}


def load_tunnel() -> dict:
    p = os.path.join(BASE, "data", "proxy_tunnel.json")
    if not os.path.exists(p):
        return {}
    c = json.load(open(p, encoding="utf-8"))
    url = f"http://{c['username']}:{c['password']}@{c['host']}/"
    return {"http": url, "https": url}


def fetch_fork_list(owner: str, repo: str, proxies: dict,
                    out_path: str, on_page=None) -> list[dict]:
    """逐页取 fork 名单，**每页立即落盘**（断点续采）。"""
    from curl_cffi import requests as cffi
    # 已落盘多少页
    done_pages = 0
    if os.path.exists(out_path):
        done_pages = sum(1 for _ in open(out_path, encoding="utf-8"))
        done_pages //= 100
        print(f"  续采：已有约 {done_pages} 页")
    out, page = [], done_pages + 1
    while True:
        url = f"https://api.github.com/repos/{owner}/{repo}/forks"
        r = None
        for attempt in range(6):
            px = proxies if attempt < 4 else {}      # 隧道失败 4 次后降级直连
            try:
                r = cffi.get(url, params={"per_page": 100, "page": page,
                                          "sort": "oldest"},
                             headers=API_HEADERS, proxies=px,
                             timeout=25, impersonate="chrome")
                if r.status_code == 200:
                    break
                if r.status_code == 403 and "rate limit" in r.text.lower():
                    print(f"  · p{page} 配额耗尽，等 20s", flush=True)
                    time.sleep(20)
                    continue
                time.sleep(1.2 * (attempt + 1))
            except Exception:
                time.sleep(1.2 * (attempt + 1))
        if r is None or r.status_code != 200:
            print(f"  ! p{page} 失败（HTTP {getattr(r,'status_code',None)}），停止", flush=True)
            break
        batch = r.json() or []
        if not batch:
            break
        rows = []
        for it in batch:
            rows.append({
                "login": (it.get("owner") or {}).get("login"),
                "fork_id": it.get("id"),
                "fork_full_name": it.get("full_name"),
                "fork_created_at": it.get("created_at"),
                "fork_pushed_at": it.get("pushed_at"),
                "size_kb": it.get("size"),
                "default_branch": it.get("default_branch"),
                "fork_language": it.get("language"),
                "fork_stars": it.get("stargazers_count"),
                "owner_type": (it.get("owner") or {}).get("type"),
                "collected_at": time.time(),
            })
        with open(out_path, "a", encoding="utf-8") as fh:
            for r2 in rows:
                fh.write(json.dumps(r2, ensure_ascii=False) + "\n")
        out.extend(rows)
        print(f"  p{page:>3} +{len(batch):>3} → 累计 {len(out):,}", flush=True)
        if 'rel="next"' not in r.headers.get("Link", ""):
            open(out_path + ".done", "w", encoding="utf-8").write(str(page))
            print(f"  名单完整（最后一页 p{page}）", flush=True)
            break
        page += 1
        time.sleep(0.05)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--mode", default="per_account",
                    choices=["per_account", "per_dim"])
    ap.add_argument("--dims", default="", help="逗号分隔；空=全部 8 个")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0, help="只采前 N 人（0=全部）")
    ap.add_argument("--sample", type=int, default=0,
                    help="随机抽 N 人（固定种子 42，可复现；与 --limit 互斥）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--deep-star", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="到点主动退出（0=不限）；配合外层定时重启可绕开卡死线程")
    args = ap.parse_args()

    owner, repo = args.repo.split("/", 1)
    tag = repo.lower().replace("-", "").replace("_", "")
    proxies = {} if args.no_proxy else load_tunnel()

    # ---- 1) fork 名单 ----
    lst_path = os.path.join(RAW, f"forks_all_{owner}__{repo}.jsonl")
    logins: list[str] = []
    complete = os.path.exists(lst_path + ".done")
    if complete:
        seen = set()
        for line in open(lst_path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            lg = (r.get("login") or "").lower()
            if lg:
                seen.add(lg)
        logins = sorted(seen)
        print(f"复用完整名单：{len(logins):,} 人 ← {os.path.basename(lst_path)}")
    else:
        if os.path.exists(lst_path):
            print(f"名单不完整，续采（已有 "
                  f"{sum(1 for _ in open(lst_path, encoding='utf-8')):,} 条）")
        print(f"取 fork 名单 {owner}/{repo} …")
        fetch_fork_list(owner, repo, proxies, lst_path)
        seen = set()
        for line in open(lst_path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            lg = (r.get("login") or "").lower()
            if lg:
                seen.add(lg)
        logins = sorted(seen)
        print(f"名单已存：{len(logins):,} 人 → {os.path.basename(lst_path)}")

    if args.list_only:
        return 0
    if args.sample:
        import random
        rnd = random.Random(args.seed)
        logins = sorted(rnd.sample(logins, min(args.sample, len(logins))))
        print(f"随机采样 {len(logins):,} 人（seed={args.seed}，可复现）", flush=True)
    elif args.limit:
        logins = logins[: args.limit]
        print(f"按名单顺序取前 {len(logins):,} 人", flush=True)

    dims = [d.strip() for d in args.dims.split(",") if d.strip()] or \
        F.ORDER_PER_ACCOUNT
    _ = dims  # 提前定义，Collector 只加载这些维度

    # ---- 2) 采详情 ----
    # 本机直连 github.com 不可用（被墙），HTML 与 API 都走隧道；
    # 若你的环境能直连 github.com，把 proxies_html 改成 {} 可显著提速。
    ctx = F.Ctx(year=args.year, proxies=proxies, proxies_html=proxies,
                timeout=15, deep_star=args.deep_star)
    col = Collector(tag=tag, out_dir=RAW, dims=dims,
                    fetch=lambda dim, login: F.fetch(dim, login, ctx))
    t0 = time.time()
    done = {"n": 0}

    def one_account(lg: str) -> None:
        """per_account 的采集单元：一个人跑完所有维度。"""
        uid = None
        for dim in dims:
            w = col.writers[dim]
            if w.done(lg):
                continue
            if dim == "D14" and not uid:
                # 先用 D1 拿 user_id（D14 需要），没有就跳过
                rec = col._unit("D1", lg)
                w1 = col.writers["D1"]
                w1.write(rec)
                if rec.outcome == Outcome.OK.value:
                    uid = (rec.payload or {}).get("user_id")
                    ctx.known_user_id = ctx.known_user_id or {}
                    ctx.known_user_id[lg] = uid
                if not uid:
                    continue
            w.write(col._unit(dim, lg))
            if dim == "D1":
                rec = None
                # 从刚写的记录里取 user_id 供 D14 用
                # （Writer 已落盘，这里重读一次太重，改用缓存）
        return None

    # D14 需要 user_id：先扫一遍 D1 文件建缓存
    d1_path = dim_path("D1", tag, RAW)
    uid_map: dict[str, int] = {}
    if os.path.exists(d1_path):
        for line in open(d1_path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = r.get("payload") or {}
            if p.get("user_id"):
                uid_map[(r.get("login") or "").lower()] = p["user_id"]
    ctx.known_user_id = uid_map

    deadline = (time.time() + args.max_minutes * 60) if args.max_minutes else 0
    el = 0.0

    def one_account2(lg: str) -> None:
        if deadline and time.time() > deadline:
            return
        nonlocal_done = None
        for dim in dims:
            w = col.writers[dim]
            if w.done(lg):
                continue
            rec = col._unit(dim, lg)
            w.write(rec)
            if dim == "D1" and rec.outcome == Outcome.OK.value:
                uid = (rec.payload or {}).get("user_id")
                if uid:
                    ctx.known_user_id[lg] = uid
        with lock:
            done["n"] += 1
            if done["n"] % 100 == 0:
                el = time.time() - t0
                rate = done["n"] / el * 60
                eta = (len(logins) - done["n"]) / max(rate, 1e-9)
                print(f"  {done['n']:,}/{len(logins):,} | {rate:.0f} 人/分钟 | "
                      f"ETA {eta:.0f} 分钟", flush=True)

    import threading
    lock = threading.Lock()

    def do_dim(lg: str, dim: str) -> None:
        """采集单元：某人的某一维度。**两种模式的唯一积木。**"""
        w = col.writers[dim]
        if w.done(lg):
            return
        rec = col._unit(dim, lg)
        w.write(rec)
        if dim == "D1" and rec.outcome == Outcome.OK.value:
            uid = (rec.payload or {}).get("user_id")
            if uid:
                ctx.known_user_id[lg] = uid

    print(f"开始采详情：{len(logins):,} 人 × {len(dims)} 维度 | "
          f"mode={args.mode} | 并发={args.workers}", flush=True)

    if args.mode == "per_dim":
        # 一维采完再下一维：32 个 worker 全部服务同一个维度 → 通道打满
        for dim in dims:
            todo = col.writers[dim].pending(logins)
            if not todo:
                print(f"  {dim:4s} 已全部完成，跳过", flush=True)
                continue
            print(f"  {dim:4s} 待采 {len(todo):,}", flush=True)
            n = {"c": 0}

            def _w(lg, _dim=dim):
                if deadline and time.time() > deadline:
                    return
                do_dim(lg, _dim)
                with lock:
                    n["c"] += 1

            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                list(ex.map(_w, todo))
            st = col.writers[dim].stats()
            print(f"  {dim:4s} 完成 | {st}", flush=True)
    else:
        # 一人采完再下一个：**外层必须有线程池**，否则退化成单线程串行
        cnt = {"c": 0}

        def _one_account(lg: str) -> None:
            if deadline and time.time() > deadline:
                return
            for dim in dims:                 # 该账号的 8 个维度依次采完
                do_dim(lg, dim)
            with lock:
                cnt["c"] += 1
                if cnt["c"] % 200 == 0:
                    _el = max(time.time() - t0, 1e-9)
                    print(f"  {cnt['c']:,}/{len(logins):,} | "
                          f"{cnt['c']/_el*60:.0f} 人/分钟", flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(_one_account, logins))

    print(f"\n完成，用时 {el/60:.1f} 分钟")
    for d in dims:
        st = col.writers[d].stats()
        total = sum(st.values())
        ok = st.get("OK", 0) + st.get("EMPTY", 0)
        print(f"  {d:4s} {DIMS[d].name:12s} 终态 {total:>6,} 有效 {ok:>6,} "
              f"({ok/max(total,1)*100:5.1f}%)  {st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
