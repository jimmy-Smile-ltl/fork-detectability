# -*- coding: utf-8 -*-
"""D4 的 HTML 版：从 `github.com/{login}?tab=stars` 数星标条目，**不消耗 API 配额**。

用户 2026-11-12 的思路：与其调 `/users/{login}/starred` 拿 Link 尾页计数
（每人 1 个 API 请求，配额稀缺），不如直接数网页。

实测结论（2026-11-12）：
  - 页面每页 30 条，`<h3><a href="/owner/repo">` 就是一条
  - 少于 30 条时 = 精确值（torvalds 页面 2 条，个人页 "Stars 2" 一致）
  - 等于 30 条时 = "≥30"，用 `starred_capped=1` 标记

对判据够用：我们关心的是 "star = 0" 与 "star ≤ 3" 这两档，29 以内都是精确值。

用法：
    py _profile_starcount_html.py --from-file data/raw/rea_forkers_priority.txt \
        --workers 8 --proxy http://127.0.0.1:7898 --out data/raw/stars_html_rea.jsonl
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
HEADERS = {"Accept": "text/html,application/xhtml+xml",
           "Accept-Language": "en-US,en;q=0.9"}
PROFILES = ["firefox133", "chrome", "safari17_0", "edge101"]
ITEM = re.compile(r'<h3[^>]*>\s*<a[^>]*href="/([^/"]+)/([^/"]+)"')
PROXIES = [None]


def fetch(login, tries=3):
    """→ (definitive, html|None)"""
    from curl_cffi import requests as cffi
    url = f"https://github.com/{login}?tab=stars"
    for _ in range(tries):
        for prof in PROFILES:
            try:
                r = cffi.get(url, headers=HEADERS, timeout=12, impersonate=prof,
                             proxies=PROXIES[0])
            except Exception:
                time.sleep(0.3)
                continue
            if r.status_code == 200:
                return True, r.text
            if r.status_code == 404:
                return True, None
            time.sleep(0.3)
    return False, None


def collect(login):
    ok, html = fetch(login)
    if not ok:
        return {"login": login, "http": 0}
    if html is None:
        return {"login": login, "http": 404}
    items = ITEM.findall(html)
    n = len(items)
    return {"login": login, "http": 200, "source": "html",
            "starred_count": n,
            "starred_capped": 1 if n >= 30 else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--delay", type=float, default=0.03)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.proxy:
        PROXIES[0] = {"http": args.proxy, "https": args.proxy}
        print(f"使用代理 {args.proxy}", flush=True)

    logins = [l.strip() for l in open(args.from_file, encoding="utf-8")
              if l.strip() and not l.startswith("#")]
    done = set()
    if os.path.exists(args.out):
        for line in open(args.out, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("http") in (200, 404):
                done.add((r.get("login") or "").lower())
    todo = [x for x in logins if x.lower() not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"待采 {len(todo):,}（已完成 {len(done):,}）→ {os.path.basename(args.out)}", flush=True)

    lock = threading.Lock()
    n = [0, 0]
    t0 = time.time()
    out = open(args.out, "a", encoding="utf-8")

    def work(lg):
        rec = collect(lg)
        with lock:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n[0] += 1
            if rec.get("http") == 200:
                n[1] += 1
            if n[0] % 50 == 0:
                rate = n[0] / max(time.time() - t0, 1e-6) * 60
                print(f"[{n[0]}/{len(todo)}] {rate:.0f} 人/分钟 | ok={n[1]}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    out.close()
    print(f"完成 {n[1]}/{n[0]}", flush=True)


if __name__ == "__main__":
    main()
