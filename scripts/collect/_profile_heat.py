# -*- coding: utf-8 -*-
"""年度贡献日历采集：`github.com/users/{login}/contributions?from=Y-01-01&to=Y-12-31`。

为什么单独一条通道：
  * API 的 `/users/{login}/events/public` 只有**最近 90 天**、≤300 条；
  * 这个 HTML 端点给**一整个自然年**的逐日贡献（365 格，含精确计数），
    **不消耗 GitHub API 配额**；
  * 但它是 github.com，会被 TLS 指纹选择性抑制，所以必须重试 + 档位轮换，
    失败要记 unavailable，**绝不能记 0**（用户可隐藏贡献，隐藏≠无活动）。

解析结构（实测 2026-11-06）：
    <td data-date="2026-06-18" data-level="4" ...></td>
    <tool-tip ...>1 contribution on June 18th.</tool-tip>

用法：
    py _profile_heat.py --logins 18534516725 --years 2026
    py _profile_heat.py --from-file data/raw/x.txt --years 2025,2026
"""
import argparse
import datetime
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests as cffi

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
HEADERS = {"Accept": "text/html,application/xhtml+xml",
           "Accept-Language": "en-US,en;q=0.9"}
PROFILES = ["firefox133", "chrome", "safari17_0", "edge101"]

CELL = re.compile(r'data-date="([\d-]+)"[^>]*data-level="([0-4])"[^>]*></td>\s*'
                  r'<tool-tip[^>]*>(.*?)</tool-tip>', re.S)
COUNT = re.compile(r'(?:([\d,]+)|No)\s+contributions?\b')


PROXIES = [None]          # 由 --proxy 填充；None = 直连
SWITCHER = [None]         # 由 --switch-every 填充；None = 不切节点


class NodeSwitcher:
    """每 N 个请求切一次 Clash 节点，把 HTML 负载摊到 98 个出口上。

    为什么必需：单出口连续请求几万次后，github.com 会开始大量返回 429/403，
    表现为 `unavailable` 比例飙升（2026-11-10 实测：单出口跑到 4 万条后
    成功率从 98% 掉到 20% 以下）。
    """

    def __init__(self, api, secret, group, every, verbose=True):
        import threading
        self.api = api
        self.hdr = {"Authorization": f"Bearer {secret}"} if secret else {}
        self.group = group
        self.every = every
        self.verbose = verbose
        self.lock = threading.Lock()
        self.n = 0
        self.i = 0
        self.names = []
        self._load()

    def _load(self):
        import requests
        try:
            r = requests.get(f"{self.api}/proxies/{self.group}", headers=self.hdr, timeout=10)
            self.names = [x for x in r.json().get("all", [])
                          if x not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS")]
            if self.verbose:
                print(f"[switch] 共 {len(self.names)} 个节点可轮换", flush=True)
        except Exception as e:
            if self.verbose:
                print(f"[switch] 读节点失败 {type(e).__name__}", flush=True)

    def tick(self):
        """每次请求后调用；累计到 every 就切一个节点。"""
        if not self.every or not self.names:
            return
        import requests
        with self.lock:
            self.n += 1
            if self.n % self.every:
                return
            self.i = (self.i + 1) % len(self.names)
            name = self.names[self.i]
            try:
                requests.put(f"{self.api}/proxies/{self.group}", headers=self.hdr,
                             json={"name": name}, timeout=8)
            except Exception:
                pass


def fetch(login, year, tries=3):
    """取一个账号的年度日历。

    返回值：
        (True, html)  成功
        (True, None)  **确定拿不到**：404 —— 账号已删/改名，或者是组织
                      （组织没有贡献日历）。这是数据事实，不是失败。
        (False, None) 网络/限流失败，可重试

    ⚠️ 超时 12s 而不是 40s：Clash 出口拥塞时单个请求会挂满 40s，
    6 个 worker 全挂起 → 吞吐掉到 ~10/分钟（2026-11-10 实测）。
    """
    url = (f"https://github.com/users/{login}/contributions"
           f"?from={year}-01-01&to={year}-12-31")
    px = PROXIES[0]
    for _ in range(tries):
        for prof in PROFILES:
            try:
                r = cffi.get(url, headers=HEADERS, timeout=12, impersonate=prof, proxies=px)
            except Exception:
                time.sleep(0.4)
                continue
            if r.status_code == 200:
                return True, r.text
            if r.status_code == 404:
                return True, None
            time.sleep(0.4)
    return False, None


def parse_calendar(html):
    """→ [(date, level, count)]；空列表 = 没解析到格子（隐藏或改版）。"""
    days = []
    for d, lv, tip in CELL.findall(html):
        m = COUNT.search(tip)
        cnt = 0
        if m and m.group(1):
            cnt = int(m.group(1).replace(",", ""))
        days.append((d, int(lv), cnt))
    return days


def summarize(login, year, days):
    if not days:
        return {"login": login, "year": year, "status": "no_cells"}
    days.sort()
    counts = [c for _, _, c in days]
    active = [d for d, _, c in days if c > 0]
    # 最长连续活跃天数
    best = cur = 0
    for d, _, c in days:
        cur = cur + 1 if c > 0 else 0
        best = max(best, cur)
    by_month = {}
    for d, _, c in days:
        if c:
            by_month[d[:7]] = by_month.get(d[:7], 0) + c
    return {
        "login": login, "year": year, "status": "ok",
        "cells": len(days), "span": [days[0][0], days[-1][0]],
        "active_days": len(active), "total_contribs": sum(counts),
        "max_streak": best,
        "first_active": active[0] if active else None,
        "last_active": active[-1] if active else None,
        "by_month": by_month,
    }


def month_row(rec):
    months = [f"{rec['year']}-{m:02d}" for m in range(1, 13)]
    counts = rec.get("by_month") or {}
    return " | ".join(str(counts.get(m, 0)) for m in months)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logins", help="逗号分隔")
    ap.add_argument("--from-file", help="每行一个 login，# 注释")
    ap.add_argument("--years", default=str(datetime.date.today().year))
    ap.add_argument("--workers", type=int, default=6,
                    help="并发线程数（直连 github.com，0 API 配额；实测 6 线程 ~120 人/分钟）")
    ap.add_argument("--delay", type=float, default=0.1,
                    help="每个线程两次请求之间的间隔秒数")
    ap.add_argument("--proxy", default="",
                    help="HTTP 代理（如 http://127.0.0.1:7898）。"
                         "**中国大陆直连 github.com 不可达，必须走本地代理**；实测直连 5,000 次后会整机断连")
    ap.add_argument("--switch-every", type=int, default=0,
                    help="每 N 个请求切一次 Clash 节点（0=不切）。"
                         "单出口跑几万次会被限流；切成 98 个出口能显著提高成功率")
    ap.add_argument("--clash-api", default="http://127.0.0.1:9098")
    ap.add_argument("--clash-secret", default="")
    ap.add_argument("--group", default="GLOBAL")
    ap.add_argument("--out", default=os.path.join(RAW, "heat_calendar.jsonl"))
    args = ap.parse_args()

    logins = []
    if args.logins:
        logins = [s.strip() for s in args.logins.split(",") if s.strip()]
    elif args.from_file:
        logins = [l.strip() for l in open(args.from_file, encoding="utf-8")
                  if l.strip() and not l.startswith("#")]
    if not logins:
        raise SystemExit("需要 --logins 或 --from-file")
    years = [int(y) for y in args.years.split(",") if y.strip()]

    if args.proxy:
        PROXIES[0] = {"http": args.proxy, "https": args.proxy}
        print(f"使用代理 {args.proxy}", flush=True)
    if args.switch_every:
        secret = args.clash_secret
        if not secret:
            envp = os.path.join(os.path.dirname(POOL_LOCAL_DIR), ".env") \
                if (POOL_LOCAL_DIR := r"C:\Users\JimmySmile\Documents\Python code\Spider\开源 整理\clash_proxy\local") else ""
            try:
                for line in open(envp, encoding="utf-8"):
                    if line.startswith("CLASH_SECRET="):
                        secret = line.split("=", 1)[1].strip().strip('"')
                        break
            except OSError:
                pass
        SWITCHER[0] = NodeSwitcher(args.clash_api, secret, args.group, args.switch_every)

    done = set()
    if os.path.exists(args.out):
        for line in open(args.out, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") in ("ok", "no_cells", "gone"):
                done.add((r["login"].lower(), r["year"]))

    todo = [(lg, y) for lg in logins for y in years if (lg.lower(), y) not in done]
    print(f"待采 {len(todo)} 个 (login, year)（已完成 {len(done)}）→ {os.path.basename(args.out)}",
          flush=True)

    rows = []
    lock = threading.Lock()
    done_n = [0]
    t0 = time.time()

    def work(item):
        lg, y = item
        definitive, html = fetch(lg, y)
        if not definitive:
            rec = {"login": lg, "year": y, "status": "unavailable"}
        elif html is None:
            # 404：账号已删/改名，或是组织（组织没有贡献日历）
            rec = {"login": lg, "year": y, "status": "gone"}
        else:
            rec = summarize(lg, y, parse_calendar(html))
        sw = SWITCHER[0]
        if sw is not None:
            sw.tick()                      # 每 N 个请求换一个出口
        with lock:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            rows.append(rec)
            done_n[0] += 1
            i = done_n[0]
            if i % 20 == 0:
                rate = i / max(time.time() - t0, 1e-6) * 60
                print(f"[{i}/{len(todo)}] {rate:.0f} 人/分钟 | 最近: {rec.get('login')} "
                      f"{rec.get('status')} active={rec.get('active_days')}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    with open(args.out, "a", encoding="utf-8") as out:
        if args.workers <= 1:
            for item in todo:
                work(item)
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                list(ex.map(work, todo))

    ok = [r for r in rows if r.get("status") == "ok"]
    print(f"\n完成 {len(ok)}/{len(rows)}；失败 {sum(1 for r in rows if r.get('status')!='ok')}")
    if ok:
        print("\n| login | 年 | 活跃天 | 总贡献 | 最长连续 | 最后活跃 | 1..12 月贡献 |")
        print("|---|---|---:|---:|---:|---|---|")
        for r in ok:
            print(f"| {r['login']} | {r['year']} | {r['active_days']} | {r['total_contribs']} | "
                  f"{r['max_streak']} | {r['last_active']} | {month_row(r)} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
