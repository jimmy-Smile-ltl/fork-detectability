# -*- coding: utf-8 -*-
"""筛选适合采集的仓库：fork > 10k 且由**个人**（非组织）创建。

    python scripts/ops/_screen_repos.py [--min-forks 10000] [--pages 5]

为什么强调「个人创建」：
    组织仓的 fork 动机混杂（员工、合作方、CI 镜像），个人仓的 fork 更接近
    「真实使用者 vs 围观者」的二分，适合研究 fork 账户质量。

输出 data/out/candidate_repos.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from curl_cffi import requests as cffi   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "out")

API = "https://api.github.com"
H = {"Accept": "application/vnd.github+json",
     "User-Agent": "repo-screen/1.0", "Connection": "close"}


def proxies():
    p = os.path.join(BASE, "data", "proxy_tunnel.json")
    if not os.path.exists(p):
        return None
    c = json.load(open(p, encoding="utf-8"))
    u = f"http://{c['username']}:{c['password']}@{c['host']}/"
    return {"http": u, "https": u}


def get(url, px, retry=3):
    for i in range(retry):
        try:
            r = cffi.get(url, headers=H, proxies=px, timeout=25, impersonate="chrome")
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 429):
                print(f"    ⏳ 限流，等 20s（{i+1}/{retry}）", flush=True)
                time.sleep(20)
                continue
            print(f"    HTTP {r.status_code}", flush=True)
            return None
        except Exception as e:
            print(f"    网络 {type(e).__name__}（{i+1}/{retry}）", flush=True)
            time.sleep(3)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-forks", type=int, default=10000)
    ap.add_argument("--pages", type=int, default=5, help="每页 100，最多 10 页")
    args = ap.parse_args()

    px = proxies()
    print(f"筛选条件：fork ≥ {args.min_forks:,} · owner 必须是 User · 未归档\n", flush=True)

    cands = {}
    for page in range(1, min(args.pages, 10) + 1):
        q = f"fork:>={args.min_forks} archived:false"
        url = (f"{API}/search/repositories?q={q.replace(' ','+')}"
               f"&sort=forks&order=desc&per_page=100&page={page}")
        print(f"  第 {page} 页 …", flush=True)
        d = get(url, px)
        if not d or not d.get("items"):
            break
        for it in d["items"]:
            full = it["full_name"]
            if full in cands:
                continue
            owner = it.get("owner") or {}
            cands[full] = {
                "full_name": full,
                "owner": owner.get("login"),
                "owner_type": owner.get("type"),
                "stars": it.get("stargazers_count"),
                "forks": it.get("forks_count"),
                "watchers": it.get("subscribers_count"),
                "lang": it.get("language"),
                "desc": (it.get("description") or "").replace("\n", " ")[:90],
                "created": (it.get("created_at") or "")[:10],
                "pushed": (it.get("pushed_at") or "")[:10],
                "topics": ",".join((it.get("topics") or [])[:4]),
                "archived": it.get("archived"),
                "size_kb": it.get("size"),
            }
        if len(d["items"]) < 100:
            break
        time.sleep(7)      # 匿名 search 限 10 次/分钟

    rows = list(cands.values())
    print(f"\n候选总数：{len(rows)}", flush=True)

    users = [r for r in rows if r["owner_type"] == "User"]
    orgs = [r for r in rows if r["owner_type"] == "Organization"]

    # 排序：fork 数降序
    users.sort(key=lambda r: r["forks"] or 0, reverse=True)
    for r in users:
        f, s = r["forks"] or 0, r["stars"] or 0
        r["ratio"] = round(f / max(s, 1), 3)

    L = ["# 候选仓库筛选：fork > 10k 且**个人创建**", "",
         f"> 筛选条件：`fork:>={args.min_forks}` · `archived:false` · `owner.type == User`",
         f"> 候选池 {len(rows)} 个仓库，其中 **个人 {len(users)} 个** / 组织 {len(orgs)} 个",
         "",
         "## 一、个人创建（按 fork 数降序）", "",
         "| # | 仓库 | ★ | fork | fork/★ | 语言 | 最近 push | 建于 | 描述 |",
         "|--:|---|--:|--:|--:|---|---|---|---|"]
    for i, r in enumerate(users, 1):
        L.append(f"| {i} | **{r['full_name']}** | {r['stars']:,} | **{r['forks']:,}** | "
                 f"{r['ratio']} | {r['lang'] or '—'} | {r['pushed']} | {r['created']} | {r['desc']} |")

    L += ["", "## 二、被排除的组织仓（fork > 10k，仅供对照）", "",
          "| 仓库 | ★ | fork | fork/★ | 语言 |", "|---|--:|--:|--:|---|"]
    orgs.sort(key=lambda r: r["forks"] or 0, reverse=True)
    for r in orgs:
        ratio = round((r["forks"] or 0) / max(r["stars"] or 1, 1), 3)
        L.append(f"| {r['full_name']} | {r['stars']:,} | {r['forks']:,} | {ratio} | {r['lang'] or '—'} |")

    L += ["", "## 三、fork/★ 比值的含义", "",
          "| 比值 | 含义 |", "|---|---|",
          "| **> 0.5** | 极高的 fork 率 —— 多为「部署型」项目（用户 fork 去自建），或存在异常 |",
          "| 0.2–0.5 | 正常偏高（工具类、可定制项目） |",
          "| 0.05–0.2 | 正常（大多数人只用不 fork） |",
          "| **< 0.05** | 极低 —— 典型「收藏型」，或 fork 数据本身可疑 |", ""]

    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "candidate_repos.md")
    open(dst, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L[:40]))
    print(f"\n… 完整结果 → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
