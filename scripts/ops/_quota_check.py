# -*- coding: utf-8 -*-
"""探快代理出口的 GitHub 剩余配额，打印**最大可用值**（-1 = 探测失败）。

为什么要采多个 IP 取最大值：
  2026-11-12 实测，每个出口的额度按小时滚动重置，**重置时刻各不相同**。
  只探一个 IP 就决定"整轮等待"，会把"其实有别的 IP 可用"的情况误判成枯竭。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from curl_cffi import requests as cffi
    cfg = json.load(open(os.path.join(BASE, "data", "proxy_kdl.json"), encoding="utf-8"))
    try:
        r = cffi.get(cfg["api_url"].format(num=8), timeout=15, impersonate="firefox133")
        ips = [x.strip() for x in r.text.splitlines() if x.strip()]
    except Exception:
        print(-1)
        return
    if not ips:
        print(-1)
        return

    best = 0
    seen = 0
    for ip in ips[:5]:
        px = {"http": f"http://{cfg['username']}:{cfg['password']}@{ip}",
              "https": f"http://{cfg['username']}:{cfg['password']}@{ip}"}
        try:
            q = cffi.get("https://api.github.com/rate_limit", timeout=10,
                         impersonate="firefox133", proxies=px)
            rem = int(q.json()["resources"]["core"]["remaining"])
            seen += 1
            best = max(best, rem)
        except Exception:
            continue
    print(best if seen else -1)


if __name__ == "__main__":
    main()
