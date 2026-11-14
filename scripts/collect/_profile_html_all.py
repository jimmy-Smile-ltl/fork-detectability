# -*- coding: utf-8 -*-
"""一次请求榨干个人主页：仓库数 / star 数 / 成就徽章 / 名字。

用户要求：充分利用 HTML 资源，不要浪费。
个人主页 `github.com/{login}` 一个响应里就同时带着：

  - `Counter` 徽标：Repositories=第1个、Stars=第4个（顺序固定，实测）
  - 成就徽章：`alt="Achievement: Pull Shark"` 之类
  - 名字：`itemprop="name"`

所以**不需要**再单独请求 `?tab=stars` 拿 star 数——那是白花一次请求。

判据价值：
  - `starred_count == 0`（从不点星）
  - 成就 `Pull Shark`（合并过 PR）、`Pair Extraordinaire`（协作过）
    → 这是"真实开发者"的**直接证据**，且 0 额外请求

用法：
    py _profile_html_all.py --from-file data/raw/rea_forkers_priority.txt \
        --workers 10 --proxy http://127.0.0.1:7898 --out data/raw/profile_html_rea.jsonl
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
PROFILES = ["safari17_0", "chrome", "firefox133", "edge101"]

COUNTER = re.compile(r'<span title="([\d,]+)"[^>]*class="Counter"[^>]*>')
ACH = re.compile(r'alt="Achievement:\s*([^"]+)"')
NAME = re.compile(r'itemprop="name"[^>]*>\s*([^<]+)')
PINNED = re.compile(r'class="pinned-item-list-item-content"[\s\S]{0,400}?href="/([^/"]+/[^/"]+)"')
PROXIES = [None]

# 值得单独记录的成就（真实开发行为的强证据）
KEY_ACH = {
    "Pull Shark": "合并过 PR",
    "Pair Extraordinaire": "与他人协作提交",
    "Quickdraw": "快速关闭 issue/PR",
    "Starstruck": "自有仓库被 star",
    "Arctic Code Vault Contributor": "2020 年有公开仓",
    "Galaxy Brain": "回答被采纳",
    "YOLO": "合并过自己的 PR",
    "Public Sponsor": "赞助过开源",
}


def fetch(login, tries=3):
    from curl_cffi import requests as cffi
    url = f"https://github.com/{login}"
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

    counters = [int(x.replace(",", "")) for x in COUNTER.findall(html)]
    repos = counters[0] if len(counters) > 0 else None
    stars = counters[3] if len(counters) > 3 else None

    achs = list(dict.fromkeys(ACH.findall(html)))
    key = [a for a in achs if a in KEY_ACH]
    m = NAME.search(html)
    pinned = list(dict.fromkeys(PINNED.findall(html)))

    return {
        "login": login, "http": 200, "source": "profile_html",
        "repos_count": repos,
        "starred_count": stars,
        "achievements": achs,
        "achievement_n": len(achs),
        "key_achievements": key,
        "has_pull_shark": 1 if "Pull Shark" in achs else 0,
        "has_collab": 1 if "Pair Extraordinaire" in achs else 0,
        "display_name": (m.group(1).strip() if m else None),
        "pinned": pinned[:6],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=10)
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
    n = [0, 0, 0]
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
                if rec.get("has_pull_shark"):
                    n[2] += 1
            if n[0] % 50 == 0:
                rate = n[0] / max(time.time() - t0, 1e-6) * 60
                print(f"[{n[0]}/{len(todo)}] {rate:.0f} 人/分钟 | ok={n[1]} | 有PullShark={n[2]}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    out.close()
    print(f"完成 {n[1]}/{n[0]}（Pull Shark {n[2]}）", flush=True)


if __name__ == "__main__":
    main()
