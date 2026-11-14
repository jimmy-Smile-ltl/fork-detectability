# -*- coding: utf-8 -*-
"""对比「隧道代理」与「KDL 提取型代理」在 GitHub 匿名 API 上的真实吞吐。

核心问题：GitHub 的 60 次/小时是**按出口 IP**算的，所以
    吞吐 ∝ 每小时能用到的不同出口 IP 数
隧道如果**每请求换 IP**，60/h 这个限制就自然失效。

因此本实验测四个数：
  1. 出口 IP 去重率（N 次请求拿到几个不同 IP）← 最关键
  2. GitHub API 成功率（200 vs 403）
  3. 单请求延迟 p50/p95
  4. 单位时间可用请求数（由 1、2、3 推算）

注意：隧道**不能复用连接**（keep-alive 会导致不换 IP），
所以每次请求都新建连接并显式 Connection: close。

用法：py _test_proxy_throughput.py --n 30
"""
import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IP_ECHO = "https://ifconfig.me/ip"
API_PROBE = "https://api.github.com/rate_limit"
UA = {"User-Agent": "curl/8.5.0", "Accept": "application/json",
      "Connection": "close"}


def tunnel_proxies():
    cfg = json.load(open(os.path.join(BASE, "data", "proxy_tunnel.json"), encoding="utf-8"))
    url = f"http://{cfg['username']}:{cfg['password']}@{cfg['host']}/"
    return {"http": url, "https": url}


def kdl_one_ip():
    """取 KDL 的一个 IP 作为固定出口（对照组：一个 IP 只有 60 次/小时）。"""
    cfg = json.load(open(os.path.join(BASE, "data", "proxy_kdl.json"), encoding="utf-8"))
    from curl_cffi import requests as cffi
    r = cffi.get(cfg["api_url"].format(num=5), timeout=20, impersonate="firefox133")
    ips = [x.strip() for x in r.text.splitlines() if x.strip()]
    if not ips:
        return None
    ip = ips[0]
    url = f"http://{cfg['username']}:{cfg['password']}@{ip}"
    return {"http": url, "https": url}


def probe(label, proxies, n, do_api=True):
    """→ dict(ips, distinct, lat, ok, code)"""
    from curl_cffi import requests as cffi
    ips, lat, codes = [], [], []
    for i in range(n):
        t0 = time.time()
        try:
            r = cffi.get(IP_ECHO, timeout=15, impersonate="chrome", proxies=proxies,
                         headers=UA)
            if r.status_code == 200:
                ips.append(r.text.strip())
        except Exception:
            pass
        lat.append(time.time() - t0)
    ok = 0
    if do_api:
        for i in range(min(n, 20)):
            try:
                r = cffi.get(API_PROBE, timeout=15, impersonate="chrome", proxies=proxies,
                             headers=UA)
                codes.append(r.status_code)
                if r.status_code == 200:
                    ok += 1
            except Exception:
                codes.append(0)
        time.sleep(0.2)
    lat.sort()
    p50 = lat[len(lat) // 2] if lat else 0
    p95 = lat[int(len(lat) * 0.95)] if lat else 0
    cnt = Counter(ips)
    return {"label": label, "n": n, "ip_ok": len(ips), "distinct": len(set(ips)),
            "p50": p50, "p95": p95, "api_ok": ok, "api_n": len(codes),
            "codes": dict(Counter(codes)),
            "top_ips": cnt.most_common(3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()

    res = []
    tp = tunnel_proxies()
    print(f"=== 隧道（{args.n} 次请求，每次新连接）===", flush=True)
    res.append(probe("隧道", tp, args.n))

    kp = kdl_one_ip()
    if kp:
        print(f"=== KDL 单 IP（{args.n} 次请求，同一出口）===", flush=True)
        res.append(probe("KDL单IP", kp, min(args.n, 12)))

    print("\n" + "=" * 68)
    print(f"{'通道':10s} {'请求':>4s} {'拿到IP':>7s} {'不同IP':>7s} {'p50':>7s} {'p95':>7s} {'API2xx':>7s}")
    for r in res:
        print(f"{r['label']:10s} {r['n']:4d} {r['ip_ok']:7d} {r['distinct']:7d} "
              f"{r['p50']:6.2f}s {r['p95']:6.2f}s {r['api_ok']:3d}/{r['api_n']:<3d}")
    print("=" * 68)

    # 推算吞吐
    for r in res:
        if r["distinct"] and r["n"]:
            ip_rate = r["distinct"] / r["n"]          # 每请求换几个新 IP
            # 若每次请求都是新 IP，则 60/h 不构成约束，瓶颈是延迟
            if ip_rate > 0.8:
                tput = 3600 / max(r["p50"], 0.05)      # 每秒 1/p50 个请求
                note = "每次请求≈新 IP → 60/h 配额不构成约束"
            else:
                tput = 60 * r["distinct"] / max(r["p50"] * r["n"], 1e-6) * r["p50"]
                tput = 60 * r["distinct"]              # 保守：每小时只能拿到这么多不同 IP
                note = f"同一 IP 复用明显（去重率 {ip_rate:.0%}）→ 仍受 60/h 约束"
            print(f"{r['label']}: 理论吞吐 ≈ {tput:,.0f} 请求/小时 | {note}")
    print("\n隧道 IP 出现频次 top3:", res[0]["top_ips"])
    if len(res) > 1:
        print("KDL  IP 出现频次 top3:", res[1]["top_ips"])
    print("HTTP 码分布:", {r["label"]: r["codes"] for r in res})


if __name__ == "__main__":
    main()
