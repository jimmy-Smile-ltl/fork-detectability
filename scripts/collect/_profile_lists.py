# -*- coding: utf-8 -*-
"""完整列表采集：star 过的仓 + 创建/fork 的仓（**全分页，不截断**）。

为什么单独写一个
----------------
旧的 D4L / D8H 有硬上限：
  * D4L 只拉前 100 条 star（`complete=0` 表示被截断）
  * D8H 的 HTML 版只拉 3 页 = 90 个仓，且 `own_details` 只存前 12 个

本脚本用 API 分页把**两个列表都拉全**：
  GET /users/{login}/starred?per_page=100&page=N   （Accept: star+json → 带 starred_at）
  GET /users/{login}/repos?per_page=100&page=N&sort=pushed

落盘（一人一条，两个列表分开文件）：
  data/raw/starred_all_{tag}.jsonl
  data/raw/repos_all_{tag}.jsonl

每条结构：
  {"login":..., "outcome":"OK", "n_pages":N, "n_items":M, "complete":true,
   "items":[{...}, ...]}

用法：
  py scripts/collect/_profile_lists.py --tag bettafish --forks data/raw/forks_all_666ghj__BettaFish.jsonl --workers 16
  py scripts/collect/_profile_lists.py --tag rea --forks data/raw/forks_all_morluto__rea.jsonl --workers 16
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests as cffi

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
API = "https://api.github.com"
H_JSON = {"Accept": "application/vnd.github+json",
          "User-Agent": "fork-study/1.0", "Connection": "close"}
H_STAR = {**H_JSON, "Accept": "application/vnd.github.star+json"}
MAX_PAGES = 100          # 硬保护：单列表最多 1 万条


def load_tunnel() -> dict:
    p = os.path.join(BASE, "data", "proxy_tunnel.json")
    if not os.path.exists(p):
        return {}
    c = json.load(open(p, encoding="utf-8"))
    url = f"http://{c['username']}:{c['password']}@{c['host']}/"
    return {"http": url, "https": url}


def get(url, headers, proxies, params, tries=6):
    """带配额感知的 GET：403 若是限流就等，其他 4xx 直接返回。"""
    for i in range(tries):
        try:
            r = cffi.get(url, headers=headers, params=params, proxies=proxies,
                         timeout=20, impersonate="chrome")
            if r.status_code == 200:
                return r
            if r.status_code == 403:
                body = (r.text or "").lower()
                if "rate limit" in body or "secondary" in body:
                    wait = min(30, 5 * (i + 1))
                    time.sleep(wait)
                    continue
                return r            # 真无权限
            if r.status_code in (401, 404, 451):
                return r
            time.sleep(0.6 * (i + 1))
        except Exception:
            time.sleep(0.6 * (i + 1))
    return None


def has_next(r) -> bool:
    return 'rel="next"' in (r.headers.get("Link") or "")


def fetch_starred(login: str, proxies: dict) -> dict:
    """全部分页的 star 列表。"""
    items, page, t0 = [], 1, time.time()
    while page <= MAX_PAGES:
        r = get(f"{API}/users/{login}/starred", H_STAR, proxies,
                {"per_page": 100, "page": page})
        if r is None:
            return {"login": login, "outcome": "NETWORK", "n_pages": page - 1,
                    "n_items": len(items), "complete": False, "items": items}
        if r.status_code == 404:
            return {"login": login, "outcome": "DELETED", "n_pages": 0,
                    "n_items": 0, "complete": False, "items": []}
        if r.status_code == 403:
            return {"login": login, "outcome": "PRIVATE", "n_pages": page - 1,
                    "n_items": len(items), "complete": False, "items": items}
        if r.status_code != 200:
            return {"login": login, "outcome": "BAD_RESPONSE", "n_pages": page - 1,
                    "n_items": len(items), "complete": False, "items": items}
        batch = r.json() or []
        for it in batch:
            if isinstance(it, dict) and it.get("repo"):
                rp = it["repo"]
                items.append({
                    "full_name": rp.get("full_name"),
                    "at": it.get("starred_at"),
                    "stars": rp.get("stargazers_count"),
                    "lang": rp.get("language"),
                    "fork": rp.get("fork"),
                })
        if not has_next(r) or not batch:
            break
        page += 1
        time.sleep(0.05)
    return {"login": login, "outcome": "OK" if items else "EMPTY",
            "n_pages": page, "n_items": len(items), "complete": True,
            "elapsed_s": round(time.time() - t0, 2), "items": items}


def fetch_repos(login: str, proxies: dict) -> dict:
    """全部分页的仓库列表（含 fork 标记）。"""
    items, page, t0 = [], 1, time.time()
    while page <= MAX_PAGES:
        r = get(f"{API}/users/{login}/repos", H_JSON, proxies,
                {"per_page": 100, "page": page, "sort": "pushed"})
        if r is None:
            return {"login": login, "outcome": "NETWORK", "n_pages": page - 1,
                    "n_items": len(items), "complete": False, "items": items}
        if r.status_code == 404:
            return {"login": login, "outcome": "DELETED", "n_pages": 0,
                    "n_items": 0, "complete": False, "items": []}
        if r.status_code != 200:
            return {"login": login, "outcome": "BAD_RESPONSE", "n_pages": page - 1,
                    "n_items": len(items), "complete": False, "items": items}
        batch = r.json() or []
        for rp in batch:
            if not isinstance(rp, dict):
                continue
            items.append({
                "name": rp.get("name"),
                "full_name": rp.get("full_name"),
                "fork": bool(rp.get("fork")),
                "lang": rp.get("language"),
                "size_kb": rp.get("size"),
                "stars": rp.get("stargazers_count"),
                "forks": rp.get("forks_count"),
                "created_at": rp.get("created_at"),
                "pushed_at": rp.get("pushed_at"),
                "archived": rp.get("archived"),
                "description": (rp.get("description") or "")[:120],
            })
        if not has_next(r) or not batch:
            break
        page += 1
        time.sleep(0.05)
    own = sum(1 for x in items if not x["fork"])
    forks = len(items) - own
    return {"login": login, "outcome": "OK" if items else "EMPTY",
            "n_pages": page, "n_items": len(items), "own_count": own,
            "fork_count": forks, "complete": True,
            "elapsed_s": round(time.time() - t0, 2), "items": items}


def load_done(path: str) -> set:
    done = set()
    if not os.path.exists(path):
        return done
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("complete") and r.get("login"):
            done.add(r["login"].lower())
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--forks", required=True)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--what", default="both", choices=["both", "star", "repo"])
    args = ap.parse_args()

    proxies = load_tunnel()
    logins = []
    for line in open(args.forks, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if lg:
            logins.append(lg)
    logins = list(dict.fromkeys(logins))
    if args.limit:
        logins = logins[: args.limit]
    print(f"{args.tag}: 名单 {len(logins):,} 人 | workers={args.workers}", flush=True)

    tasks = []
    if args.what in ("both", "star"):
        tasks.append(("star", fetch_starred,
                      os.path.join(RAW, f"starred_all_{args.tag}.jsonl")))
    if args.what in ("both", "repo"):
        tasks.append(("repo", fetch_repos,
                      os.path.join(RAW, f"repos_all_{args.tag}.jsonl")))

    for kind, fn, path in tasks:
        done = load_done(path)
        todo = [x for x in logins if x not in done]
        print(f"\n[{kind}] 已完成 {len(done):,} | 待采 {len(todo):,}", flush=True)
        if not todo:
            continue
        t0 = time.time()
        cnt = {"n": 0, "ok": 0}
        lock = __import__("threading").Lock()

        def run(lg):
            rec = fn(lg, proxies)
            with lock:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                cnt["n"] += 1
                if rec.get("complete"):
                    cnt["ok"] += 1
                if cnt["n"] % 200 == 0:
                    el = max(time.time() - t0, 1e-9)
                    rate = cnt["n"] / el * 60
                    eta = (len(todo) - cnt["n"]) / max(rate, 1e-9)
                    print(f"  {cnt['n']:,}/{len(todo):,} | {rate:.0f} 人/分钟 | "
                          f"完成 {cnt['ok']:,} | ETA {eta:.0f} 分钟", flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(run, todo))
        print(f"[{kind}] 结束：{cnt['ok']:,} 完整 / {cnt['n']:,} 处理", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
