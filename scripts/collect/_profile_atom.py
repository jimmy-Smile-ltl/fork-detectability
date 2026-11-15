# -*- coding: utf-8 -*-
"""D2 的免费版：从 `github.com/{login}.atom`（RSS）取最近 30 条公开事件的类型。

为什么需要：
  API 的 `/users/{l}/events/public` 要 1–3 个配额请求（90 天 / 300 条），
  而 RSS 是 **HTML 通道，0 API 配额**，给最近 30 条。
  对"事件类型构成"（Push / Watch / Fork / IssueComment）够用。

实测（2026-11-12）：torvalds.atom = 138KB / 30 条 / 全是 PushEvent；
                    无公开活动的账号返回 428 字节空 feed。

用法：
    py _profile_atom.py --from-file data/raw/rea_forkers_priority.txt \
        --out data/raw/atom_rea.jsonl --workers 8 --proxy http://your-proxy:8080
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
HEADERS = {"Accept": "application/atom+xml", "Accept-Language": "en-US,en;q=0.9",
           "Connection": "close"}
PROFILES = ["safari17_0", "chrome", "firefox133", "edge101"]
ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
KIND = re.compile(r"<id>tag:github\.com,2008:(\w+)")
STAMP = re.compile(r"<updated>([^<]+)</updated>")
PROXIES = [None]

# 独享代理 IP 池（HTML 不受 API 配额限制，但单个 IP 会失效，需要轮换）
import threading as _th
_KDL_CFG = None
_KDL_IPS = []
_KDL_LOCK = _th.Lock()


def _kdl_load():
    global _KDL_CFG, _KDL_IPS
    from curl_cffi import requests as cffi
    if _KDL_CFG is None:
        _KDL_CFG = json.load(open(os.path.join(BASE, "data", "proxy_kdl.json"), encoding="utf-8"))
    try:
        r = cffi.get(_KDL_CFG["api_url"].format(num=10), timeout=20, impersonate="firefox133")
        ips = [x.strip() for x in r.text.splitlines() if x.strip()]
        with _KDL_LOCK:
            _KDL_IPS = ips
        return ips
    except Exception:
        return []


def _kdl_proxies():
    with _KDL_LOCK:
        if not _KDL_IPS:
            need = True
        else:
            need = False
            ip = _KDL_IPS.pop()
    if need:
        ips = _kdl_load()
        if not ips:
            return None
        with _KDL_LOCK:
            ip = _KDL_IPS.pop() if _KDL_IPS else None
        if ip is None:
            return None
    c = _KDL_CFG
    u = f"http://{c['username']}:{c['password']}@{ip}"
    return {"http": u, "https": u}


def fetch(login, tries=3):
    from curl_cffi import requests as cffi
    url = f"https://github.com/{login}.atom"
    for _ in range(tries):
        for prof in PROFILES:
            px = PROXIES[0] if PROXIES[0] else _kdl_proxies()
            try:
                r = cffi.get(url, headers=HEADERS, timeout=15, impersonate=prof,
                             proxies=px)
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
    ok, xml = fetch(login)
    if not ok:
        return {"login": login, "http": 0}
    if xml is None:
        return {"login": login, "http": 404}
    kinds, stamps = [], []
    for blk in ENTRY.findall(xml):
        m = KIND.search(blk)
        if m:
            kinds.append(m.group(1))
        s = STAMP.search(blk)
        if s:
            stamps.append(s.group(1))
    c = Counter(kinds)
    return {"login": login, "http": 200, "events_n": len(kinds),
            "event_types": dict(c),
            "has_push": 1 if c.get("PushEvent") else 0,
            "has_watch": 1 if c.get("WatchEvent") else 0,
            "latest_event": max(stamps) if stamps else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--delay", type=float, default=0.03)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--kdl-pool", action="store_true",
                    help="走独享代理 IP 池（HTML 不受 API 配额限制，适合大包）")
    ap.add_argument("--tunnel", action="store_true",
                    help="走 KDL 隧道（凭据从 data/proxy_tunnel.json 读，不出现在命令行）")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.kdl_pool:
        print("使用独享代理 IP 池", flush=True)
    elif args.tunnel:
        cfg = json.load(open(os.path.join(BASE, "data", "proxy_tunnel.json"), encoding="utf-8"))
        u = f"http://{cfg['username']}:{cfg['password']}@{cfg['host']}/"
        PROXIES[0] = {"http": u, "https": u}
        print("使用隧道代理", flush=True)
    elif args.proxy:
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
                if rec.get("events_n"):
                    n[2] += 1
            if n[0] % 100 == 0:
                rate = n[0] / max(time.time() - t0, 1e-6) * 60
                print(f"[{n[0]}/{len(todo)}] {rate:.0f} 人/分钟 | ok={n[1]} | 有事件={n[2]}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    out.close()
    print(f"完成 {n[1]}/{n[0]}（有事件 {n[2]}）", flush=True)


if __name__ == "__main__":
    main()
