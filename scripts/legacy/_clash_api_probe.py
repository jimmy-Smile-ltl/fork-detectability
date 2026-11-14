# -*- coding: utf-8 -*-
"""扫描 Clash 专用池的每一个节点，记录它对 GitHub API 的剩余配额。

为什么需要：专用池是共享订阅，89 个节点的出口 IP 被所有订阅者共用，
GitHub 的 60/h 额度经常被"别人"打空。但额度每小时滚动重置，
所以**唯一办法是周期性探测**，把"当前有额度的节点"挑出来用。

实现要点：**直接调 Clash 控制 API**（PUT /proxies/{group}），
不用 ClashManager.switch_node()——后者会验证出口 IP 变化，
而配额打空时验证请求本身失败，会被误判成"切换失败"并卡住（实测）。

输出：data/out/clash_api_nodes.json

用法：
    py _clash_api_probe.py              # 全扫
    py _clash_api_probe.py --limit 20   # 抽查
"""
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "out")
STATE = os.path.join(BASE, "data", "state", "clash_node_state.json")
POOL_LOCAL = r"C:\Users\JimmySmile\Documents\Python code\Spider\开源 整理\clash_proxy\local"

TEST_URL = "https://api.github.com/rate_limit"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--group", default=None)
    args = ap.parse_args()

    if not os.path.isdir(POOL_LOCAL):
        raise SystemExit(f"找不到专用池目录：{POOL_LOCAL}")
    sys.path.insert(0, POOL_LOCAL)
    from proxy_pool import proxies                     # noqa: E402
    from clash_cli import get_config, load_dotenv      # noqa: E402
    import requests                                    # noqa: E402
    from curl_cffi import requests as cffi             # noqa: E402

    load_dotenv(str(os.path.join(POOL_LOCAL, "..", ".env")))
    cfg = get_config()
    api = cfg.get("api_url") or "http://127.0.0.1:9098"
    secret = cfg.get("secret") or os.environ.get("CLASH_SECRET", "")
    group = args.group or cfg.get("group_name") or "GLOBAL"
    H = {"Authorization": f"Bearer {secret}"} if secret else {}

    r = requests.get(f"{api}/proxies/{group}", headers=H, timeout=15)
    if r.status_code != 200:
        raise SystemExit(f"读组失败 HTTP {r.status_code}（secret 不对？）")
    info = r.json()
    names = [n for n in info.get("all", []) if n not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS")]
    print(f"组 {group} 共 {len(names)} 个节点 | 当前: {info.get('now')}", flush=True)

    if args.limit:
        names = names[:args.limit]

    nodes = []
    for i, name in enumerate(names, 1):
        try:
            requests.put(f"{api}/proxies/{group}", headers=H,
                         json={"name": name}, timeout=10)
        except Exception:
            nodes.append({"node": name, "remaining": -3})
            continue
        time.sleep(0.15)                      # 让配置生效
        rem = None
        reset = None
        try:
            q = cffi.get(TEST_URL, timeout=18, impersonate="firefox133", proxies=proxies(),
                         headers={"Accept": "application/vnd.github+json"})
            if q.status_code == 200:
                core = q.json()["resources"]["core"]
                rem = core.get("remaining")
                reset = core.get("reset")
            else:
                rem = -1
        except Exception:
            rem = -2
        nodes.append({"node": name, "remaining": rem, "reset": reset})
        if i % 15 == 0:
            usable = sum(1 for x in nodes if isinstance(x["remaining"], int) and x["remaining"] > 0)
            print(f"  已扫 {i}/{len(names)} | 有额度 {usable}", flush=True)
        time.sleep(args.sleep)

    usable = [x for x in nodes if isinstance(x["remaining"], int) and x["remaining"] > 0]
    os.makedirs(OUT, exist_ok=True)
    payload = {"ts": int(time.time()), "group": group, "scanned": len(nodes),
               "usable": len(usable), "nodes": nodes}
    with open(os.path.join(OUT, "clash_api_nodes.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print(f"\n扫描 {len(nodes)} 个 | 有额度 {len(usable)} 个")
    if usable:
        for x in sorted(usable, key=lambda y: -y["remaining"])[:12]:
            print(f"  {x['node'][:44]:44s} 剩余 {x['remaining']}")
        print(f"合计可用: {sum(x['remaining'] for x in usable):,} 次")
    else:
        zero = sum(1 for x in nodes if x["remaining"] == 0)
        print(f"配额为 0 的 {zero} 个 —— 等一小时重试")


if __name__ == "__main__":
    main()
