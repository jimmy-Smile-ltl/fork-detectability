# -*- coding: utf-8 -*-
"""D9：本人在自建仓里的提交数（"自己写代码"的硬证据）。

为什么单独一个脚本：D9 依赖 D8 的结果（要先知道这个人有哪些自建仓），
而且**只对自建仓有意义**——fork 仓里的提交是上游作者写的。

输入：D8 的结果（HTML 版或 API 版都行），从中取 own_details 的前 N 个仓。
输出：data/raw/commits_{tag}.jsonl，一人一条：
    login / repos_checked / commit_total / per_repo[{repo, commits}] / http

用法：
    py _profile_commits.py --d8 data/raw/repos_html_rea.jsonl --tag rea \
        --workers 6 --max-repos 3 --proxy-kdl
    py _profile_commits.py --d8 data/raw/repos_html_openclaw.jsonl --tag openclaw \
        --workers 6 --max-repos 3
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
API = "https://api.github.com"
UA = {"Accept": "application/vnd.github+json", "User-Agent": "fork-study/1.0",
      "Connection": "close"}          # 隧道必须 close，否则不换 IP
PROFILES = ["firefox133", "chrome", "safari17_0", "edge101"]


def load_kdl():
    p = os.path.join(BASE, "data", "proxy_kdl.json")
    d = json.load(open(p, encoding="utf-8"))
    return d["api_url"], d["username"], d["password"]


def load_tunnel():
    """隧道代理：单一固定端点，**每个请求自动换出口 IP**。

    2026-11-12 实测（_test_proxy_throughput.py）：
      25 次请求 → 24 个不同出口 IP → GitHub 的 60/h/IP 限制天然失效
      20 线程并发：22,053 请求/小时（有效成功 18,300/h）
      对比 KDL 提取型：约 3,300/h → **快 5.5 倍**

    注意：必须每次新建连接（Connection: close），否则隧道不换 IP。
    """
    p = os.path.join(BASE, "data", "proxy_tunnel.json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf-8"))
    url = f"http://{d['username']}:{d['password']}@{d['host']}/"
    return {"http": url, "https": url}


class KdlPool:
    """与 _profiles_parallel 里的同款：取 IP → 用到底 → 换。"""

    def __init__(self, ip_num=4):
        self.api_url, self.user, self.pwd = load_kdl()
        self.ip_num = ip_num
        self.ips = []
        self.idx = 0
        self.lock = threading.Lock()
        self._last = [0.0]

    def refill(self):
        with self.lock:
            if time.time() - self._last[0] < 1.5:
                time.sleep(1.5)
            self._last[0] = time.time()
        try:
            from curl_cffi import requests as cffi
            r = cffi.get(self.api_url.format(num=self.ip_num), timeout=20, impersonate="firefox133")
            if r.status_code == 200:
                ips = [x.strip() for x in r.text.splitlines() if x.strip()]
                if ips:
                    with self.lock:
                        self.ips, self.idx = ips, 0
                    return True
        except Exception:
            pass
        return False

    def current(self):
        for _ in range(3):
            with self.lock:
                if self.ips and self.idx < len(self.ips):
                    return self.ips[self.idx]
            if self.refill():
                continue
            time.sleep(3)
        return None

    def proxies_for(self, node):
        if not node:
            return None
        return {"http": f"http://{self.user}:{self.pwd}@{node}",
                "https": f"http://{self.user}:{self.pwd}@{node}"}

    def rotate(self):
        with self.lock:
            self.idx += 1


class FixedPool:
    """隧道模式：固定端点，每次请求都是新连接（由 Connection: close 保证）。"""

    def __init__(self):
        self.px = load_tunnel()

    def current(self):
        return "tunnel" if self.px else None

    def proxies_for(self, node):
        return self.px

    def rotate(self):
        pass


def get_json(pool, url, tries=4):
    """返回 resp；**409/422 也视为定论**。

    GitHub 对空仓库的 commits 端点返回 409 Conflict（"Git Repository is empty"），
    这不是失败而是"没有提交"——早期版本把它当失败，导致 85% 的人被记成 http=0
    （2026-11-11 实测）。
    """
    from curl_cffi import requests as cffi
    for i in range(tries):
        node = pool.current()
        try:
            r = cffi.get(url, headers=UA, timeout=20, impersonate=PROFILES[i % len(PROFILES)],
                         proxies=pool.proxies_for(node))
        except Exception:
            pool.rotate()
            continue
        if r.status_code in (200, 404, 409, 422):
            return r
        pool.rotate()
        time.sleep(0.5)
    return None


def link_last(resp):
    lk = resp.headers.get("Link", "") or ""
    for part in lk.split(","):
        if 'rel="last"' in part:
            import re
            m = re.search(r"[?&]page=(\d+)", part)
            if m:
                return int(m.group(1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--d8", required=True, help="D8 结果 JSONL（HTML 或 API 版）")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--max-repos", type=int, default=3)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tunnel", action="store_true",
                    help="走隧道代理（每请求换 IP，实测 18,300 有效请求/小时，比 KDL 快 5.5 倍）")
    args = ap.parse_args()

    out_path = args.out or os.path.join(RAW, f"commits_{args.tag}.jsonl")
    done = set()
    if os.path.exists(out_path):
        for line in open(out_path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("http") in (200, 404):
                done.add((r.get("login") or "").lower())

    # 从 D8 里取每个人的自建仓（只处理 own_count > 0 的人）
    todo = []
    for line in open(args.d8, encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("http") != 200:
            continue
        lg = (r.get("login") or "").strip()
        if not lg or lg.lower() in done:
            continue
        own = r.get("own_details") or []
        if not own:
            continue
        repos = [x.get("name") or x.get("repo") for x in own[:args.max_repos]]
        repos = [x for x in repos if x]
        if not repos:
            continue
        todo.append((lg, repos))
        if args.limit and len(todo) >= args.limit:
            break

    print(f"待采 {len(todo):,} 人（已完成 {len(done):,}）→ {os.path.basename(out_path)}", flush=True)
    pool = FixedPool() if args.tunnel else KdlPool()
    if args.tunnel:
        print("使用隧道代理（每请求换 IP）", flush=True)
    lock = threading.Lock()
    n = [0, 0]
    t0 = time.time()
    out = open(out_path, "a", encoding="utf-8")

    def work(item):
        lg, repos = item
        per = []
        total = 0
        bad = False
        for rp in repos:
            url = f"{API}/repos/{lg}/{rp}/commits?author={lg}&per_page=1"
            resp = get_json(pool, url)
            if resp is None:
                bad = True
                break
            if resp.status_code == 404:
                per.append({"repo": rp, "commits": None})
                continue
            if resp.status_code in (409, 422):
                per.append({"repo": rp, "commits": 0})       # 空仓库 = 0 次提交
                continue
            last = link_last(resp)
            try:
                n_items = len(resp.json())
            except Exception:
                n_items = 0
            cnt = last if last is not None else n_items
            per.append({"repo": rp, "commits": cnt})
            total += cnt or 0
        rec = {"login": lg, "repos_checked": len(per),
               "commit_total": total if not bad else None,
               "per_repo": per, "http": 0 if bad else 200}
        with lock:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n[0] += 1
            if rec["http"] == 200:
                n[1] += 1
            if n[0] % 20 == 0:
                rate = n[0] / max(time.time() - t0, 1e-6) * 60
                print(f"[{n[0]}/{len(todo)}] {rate:.0f} 人/分钟 | ok={n[1]}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    out.close()
    print(f"完成 {n[1]}/{n[0]}", flush=True)


if __name__ == "__main__":
    main()
