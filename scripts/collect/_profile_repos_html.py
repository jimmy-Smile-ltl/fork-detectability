# -*- coding: utf-8 -*-
"""D8 的 HTML 版：用 github.com 的仓库列表页取「自建 vs fork」结构。

为什么需要：
  D8 走 api.github.com 要消耗 60/h 的 core 配额（19 万人 × 1-2 请求 ≈ 30 万请求 ≈ 90 小时）。
  而 `github.com/{login}?tab=repositories` 是**纯 HTML，0 API 配额**，
  可以走代理池，与 API 通道并行、互不挤占。

能拿到的字段（与 D8 对齐）：
  sampled_repos / fork_count / own_count / own_empty / has_profile_repo
  / own_details / fork_details / repos_complete

解析依赖（实测 2026-11-11）：
  - 每个仓库是一个 `<li ... itemprop="owns" ...>` 块
  - 仓库名在 `itemprop="name codeRepository"`
  - fork 仓含 "forked from" 文本
  - 更新时间在 `<relative-time datetime="...">`

用法：
    py _profile_repos_html.py --from-file data/raw/openclaw_forkers_priority.txt \
        --workers 6 --proxy http://your-proxy:8080 --out data/raw/repos_html_openclaw.jsonl
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

ITEM = re.compile(r'<li[^>]*itemprop="owns"[^>]*>(.*?)</li>', re.S)
NAME = re.compile(r'itemprop="name codeRepository"[^>]*>\s*([^<]+)')
LANG = re.compile(r'itemprop="programmingLanguage">\s*([^<]+)')
TIME = re.compile(r'<relative-time[^>]*datetime="([^"]+)"')
STARS = re.compile(r'/stargazers"[^>]*>\s*(?:<[^>]+>\s*)*([\d,]+)?')
PAGER = re.compile(r'page=(\d+)')

PROXIES = [None]


def fetch(login, page, tries=3):
    """→ (definitive, html|None)"""
    from curl_cffi import requests as cffi
    url = f"https://github.com/{login}?tab=repositories&page={page}"
    for _ in range(tries):
        for prof in PROFILES:
            try:
                r = cffi.get(url, headers=HEADERS, timeout=15, impersonate=prof,
                             proxies=PROXIES[0])
            except Exception:
                time.sleep(0.4)
                continue
            if r.status_code == 200:
                return True, r.text
            if r.status_code == 404:
                return True, None
            time.sleep(0.4)
    return False, None


def parse_page(html, login):
    """→ (repos, has_next)；repos = [{name, fork, lang, updated}]"""
    out = []
    for blk in ITEM.findall(html):
        m = NAME.search(blk)
        if not m:
            continue
        name = m.group(1).strip()
        low = blk.lower()
        # fork 仓的特征：出现 "forked from"，或带 fork 图标 aria-label
        is_fork = ("forked from" in low) or ('aria-label="forked"' in low) \
            or ('octicon-repo-forked' in low)
        lg = LANG.search(blk)
        tm = TIME.search(blk)
        out.append({"name": name, "fork": bool(is_fork),
                    "lang": lg.group(1).strip() if lg else None,
                    "updated": tm.group(1) if tm else None})
    has_next = bool(re.search(r'[?&]page=(\d+)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*Next',
                              html, re.I)) or ('rel="next"' in html)
    return out, has_next


def collect(login, max_pages=2, expected=None):
    """返回与 D8 对齐的记录。

    ``expected`` = D1 里的 public_repos。有了它就能**精确算需要翻几页**
    （GitHub 仓库列表每页 30 个，且按更新时间排序——前几页可能全是 fork，
    自建仓在后面，页数不够就会把 own_count 算少）。
    """
    if expected is not None and expected >= 0:
        need = min(max(1, (expected + 29) // 30), max_pages)
    else:
        need = max_pages
    repos = []
    complete = True
    used = 0
    for page in range(1, need + 1):
        ok, html = fetch(login, page)
        used += 1
        if not ok:
            return {"login": login, "http": 0, "error": "fetch_failed", "pages_used": used}
        if html is None:
            return {"login": login, "http": 404, "pages_used": used}
        page_repos, has_next = parse_page(html, login)
        repos.extend(page_repos)
        if not page_repos:
            break                      # 空页 = 真的到底了
        if not has_next and page == need and expected and len(repos) < expected:
            complete = False           # 页数不够（被 max_pages 截断）
        if not has_next and len(page_repos) < 30:
            break                      # 最后一页
    if expected and len(repos) < expected:
        complete = False
    own = [r for r in repos if not r["fork"]]
    forks = [r for r in repos if r["fork"]]
    lg = (login or "").lower()
    has_profile_repo = any((r["name"] or "").lower() == lg for r in own)
    own_touched = sum(1 for r in own if r.get("updated"))
    own_newest = max((r["updated"] for r in own if r.get("updated")), default=None)
    return {
        "login": login, "http": 200, "source": "html",
        "sampled_repos": len(repos),
        "own_count": len(own),
        "fork_count": len(forks),
        "own_touched": own_touched,
        "own_empty": None,
        "own_newest_push": own_newest,
        "has_profile_repo": 1 if has_profile_repo else 0,
        "repos_complete": 1 if complete else 0,
        "pages_used": used,
        "own_details": own[:20],
        "fork_details": forks[:20],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--delay", type=float, default=0.05)
    ap.add_argument("--max-pages", type=int, default=8)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--d1-file", default="",
                    help="D1 的 JSONL（用来读 public_repos 精确算页数）；不传就盲翻 max-pages 页")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    exp = {}
    if args.d1_file and os.path.exists(args.d1_file):
        for line in open(args.d1_file, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            lg = (r.get("login") or "").lower()
            d1 = r.get("d1") or {}
            pr = d1.get("public_repos")
            if lg and isinstance(pr, int):
                exp[lg] = pr            # 后写的覆盖前写的（D1 不会变）
        print(f"读入 D1 public_repos {len(exp):,} 条", flush=True)

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
        rec = collect(lg, args.max_pages, expected=exp.get(lg.lower()))
        with lock:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n[0] += 1
            if rec.get("http") == 200:
                n[1] += 1
            if n[0] % 20 == 0:
                rate = n[0] / max(time.time() - t0, 1e-6) * 60
                print(f"[{n[0]}/{len(todo)}] {rate:.0f} 人/分钟 | ok={n[1]} | 最近 {lg}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    out.close()
    print(f"完成 {n[1]}/{n[0]}", flush=True)


if __name__ == "__main__":
    main()
