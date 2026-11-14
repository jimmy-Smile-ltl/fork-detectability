# -*- coding: utf-8 -*-
"""D4L：star 明细 + 点星时间（走隧道代理，每请求换 IP）。

为什么单独一个脚本：
  `_profiles_parallel.py` 的 tier 机制依赖 `_profile_lib.quota_get` 的接口约定，
  隧道池插进去报错（2026-11-13 实测 err=12/12）。而 `_profile_commits.py`
  的写法已验证可跑 200–300 人/分钟，直接照搬更快。

能回答什么：
  1. **点星时间**（`starred_at`）→ "fork 当天是否同时点星"——时间聚类的最强交叉验证
  2. **是否 star 了目标仓**（`starred_target`）→ 有多少人 fork 了却没 star
  3. star 明细（前 N 个仓）→ 消费偏好的构成

用法：
    py _profile_starlist.py --repo morluto/rea --tag rea --workers 20 --tunnel
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
API = "https://api.github.com"
UA = {"Accept": "application/vnd.github.star+json",
      "User-Agent": "fork-study/1.0",
      "Connection": "close"}          # 隧道必须 close，否则不换 IP

PROFILES = ["chrome", "safari17_0", "firefox133", "edge101"]


def tunnel_proxies():
    cfg = json.load(open(os.path.join(BASE, "data", "proxy_tunnel.json"), encoding="utf-8"))
    url = f"http://{cfg['username']}:{cfg['password']}@{cfg['host']}/"
    return {"http": url, "https": url}


def get(pool_px, url, tries=4):
    from curl_cffi import requests as cffi
    for i in range(tries):
        try:
            r = cffi.get(url, headers=UA, timeout=20,
                         impersonate=PROFILES[i % len(PROFILES)], proxies=pool_px)
        except Exception:
            time.sleep(0.3)
            continue
        if r.status_code in (200, 404):
            return r
        time.sleep(0.4)
    return None


def last_page(resp):
    lk = resp.headers.get("Link", "") or ""
    for part in lk.split(","):
        if 'rel="last"' in part:
            import re
            m = re.search(r"[?&]page=(\d+)", part)
            if m:
                return int(m.group(1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="目标仓 owner/name，用于判断是否 star 了它")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--from-file", default="", help="名单；不传则用 data/raw/{tag}_forkers_priority.txt")
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--tunnel", action="store_true", default=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    src = args.from_file or os.path.join(RAW, f"{args.tag}_forkers_priority.txt")
    out_path = args.out or os.path.join(RAW, f"starlist_{args.tag}.jsonl")
    target = args.repo.lower()

    done = set()
    if os.path.exists(out_path):
        for line in open(out_path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("http") in (200, 404):
                done.add((r.get("login") or "").lower())

    todo = []
    for l in open(src, encoding="utf-8"):
        lg = l.strip()
        if lg and not lg.startswith("#") and lg.lower() not in done:
            todo.append(lg)
    if args.limit:
        todo = todo[:args.limit]
    print(f"待采 {len(todo):,}（已完成 {len(done):,}）→ {os.path.basename(out_path)}", flush=True)

    px = tunnel_proxies()
    lock = threading.Lock()
    n = [0, 0, 0]
    t0 = time.time()
    out = open(out_path, "a", encoding="utf-8")

    def work(login):
        url = f"{API}/users/{login}/starred?per_page=100&page=1"
        r = get(px, url)
        if r is None:
            rec = {"login": login, "http": 0}
        elif r.status_code == 404:
            rec = {"login": login, "http": 404}
        else:
            try:
                items = r.json()
            except Exception:
                items = []
            total_pages = last_page(r) or 1
            stars = []
            hit_target = 0
            for it in items if isinstance(items, list) else []:
                if isinstance(it, dict) and "repo" in it:
                    fn = (it.get("repo") or {}).get("full_name") or ""
                    stars.append({"repo": fn, "at": it.get("starred_at")})
                elif isinstance(it, dict):
                    fn = it.get("full_name") or ""
                    stars.append({"repo": fn, "at": None})
            for s in stars:
                if (s["repo"] or "").lower() == target:
                    hit_target = 1
            rec = {"login": login, "http": 200, "pages": total_pages,
                   "sampled": len(stars), "complete": 1 if total_pages <= 1 else 0,
                   "starred_target": hit_target,
                   "stars": stars[:100]}
        with lock:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n[0] += 1
            if rec.get("http") == 200:
                n[1] += 1
                if rec.get("starred_target"):
                    n[2] += 1
            if n[0] % 50 == 0:
                rate = n[0] / max(time.time() - t0, 1e-6) * 60
                print(f"[{n[0]}/{len(todo)}] {rate:.0f} 人/分钟 | ok={n[1]} | star了目标仓={n[2]}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    out.close()
    print(f"完成 {n[1]}/{n[0]}（其中 {n[2]} 人 star 过 {args.repo}）", flush=True)


if __name__ == "__main__":
    main()
