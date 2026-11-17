# -*- coding: utf-8 -*-
"""抓 GitHub Trending（日/周/月 × 多语言），与候选仓比对。

    python scripts/ops/_check_trending.py

输出 data/out/trending_check.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from curl_cffi import requests as cffi   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "out")
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64) Chrome/131.0",
     "Connection": "close"}

PERIODS = ["daily", "weekly", "monthly"]
LANGS = ["", "python", "javascript", "typescript", "go", "java", "c++", "rust"]


def proxies():
    p = os.path.join(BASE, "data", "proxy_tunnel.json")
    if not os.path.exists(p):
        return None
    c = json.load(open(p, encoding="utf-8"))
    u = f"http://{c['username']}:{c['password']}@{c['host']}/"
    return {"http": u, "https": u}


def fetch(url, px, retry=3):
    for i in range(retry):
        try:
            r = cffi.get(url, headers=H, proxies=px, timeout=25, impersonate="chrome")
            if r.status_code == 200 and len(r.text) > 3000:
                return r.text
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return None


def parse(html: str):
    """从 trending 页提取 repo 全名（owner/name）。"""
    if not html:
        return []
    # trending 页里仓库链接形如 <h2><a href="/owner/repo">
    return re.findall(r'<h2[^>]*>\s*<a[^>]*href="/([^/"]+/[^/"]+)"', html)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=os.path.join(OUT, "candidate_repos.md"))
    args = ap.parse_args()

    # 读候选
    cands = []
    if os.path.exists(args.candidates):
        for line in open(args.candidates, encoding="utf-8"):
            m = re.search(r"\*\*([\w.-]+/[\w.-]+)\*\*", line)
            if m:
                cands.append(m.group(1))
    print(f"候选仓库：{len(cands)} 个", flush=True)

    px = proxies()
    seen = {}
    for period in PERIODS:
        for lang in LANGS:
            url = f"https://github.com/trending/{lang}?since={period}" if lang \
                else f"https://github.com/trending?since={period}"
            html = fetch(url, px)
            repos = parse(html)
            tag = f"{period}/{lang or 'all'}"
            print(f"  {tag:20s} → {len(repos)} 个仓库", flush=True)
            for r in repos:
                seen.setdefault(r.lower(), set()).add(tag)
            time.sleep(1.2)

    print(f"\nTrending 抓到 {len(seen)} 个不同仓库", flush=True)

    hit = {c: seen[c.lower()] for c in cands if c.lower() in seen}
    L = ["# GitHub Trending 比对结果", "",
         f"> 抓取期间 {len(PERIODS)} × {len(LANGS)} 个 Trending 页，"
         f"共 {len(seen)} 个不同仓库",
         f"> 候选 {len(cands)} 个中，**{len(hit)} 个曾上榜**", ""]
    if hit:
        L += ["## 上榜的候选仓", "", "| 仓库 | 上榜页面 |", "|---|---|"]
        for c, tags in sorted(hit.items(), key=lambda x: -len(x[1])):
            L.append(f"| **{c}** | {', '.join(sorted(tags))} |")
    else:
        L += ["（本次抓取期间无候选仓上榜 —— 榜单每日变化，可换时间再试）"]

    L += ["", "## 本次抓到的全部 Trending 仓库（供参考）", ""]
    for r, tags in sorted(seen.items()):
        L.append(f"- `{r}` — {', '.join(sorted(tags))}")

    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "trending_check.md")
    open(dst, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\n→ {dst}")
    if hit:
        print("\n上榜的候选仓：")
        for c in hit:
            print(f"  ✅ {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
