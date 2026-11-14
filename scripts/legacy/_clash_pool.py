# -*- coding: utf-8 -*-
"""Clash 专用池的「API 出口池」适配器。

背景（2026-11-10 实测）：
- 专用池有 98 个节点，出口 IP 各不相同 → **每个 IP 有独立的 GitHub 60/h 配额**
- 但这是共享订阅，多数节点的额度被别的用户打空
- 逐节点扫描 98 个：**34 个有额度，合计 1,927 次**（约占 35%）
- 额度每小时滚动重置，各节点重置时刻不同

所以它能当**第二条 API 通道**用，但用法特殊：
  1. 先扫一遍，拿到"当前有额度"的节点清单
  2. 每次切到一个节点，最多打 45 次（留余量），再切下一个
  3. 切换是**全局**的（Mihomo 只有一个出口），所以多线程共享同一节点，靠批次控制不超量

与 KdlPool 接口一致：current() / proxies_for() / record_dead() / record_ok() / rotate()
"""
import json
import os
import sys
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POOL_LOCAL = r"C:\Users\JimmySmile\Documents\Python code\Spider\开源 整理\clash_proxy\local"
TEST_URL = "https://api.github.com/rate_limit"


class ClashApiPool:
    """把 Clash 的 98 个节点当成一个"多 IP 出口池"来用。"""

    def __init__(self, budget_per_node=45, group=None, keep=20, verbose=True,
                 nodes_file=None, max_age_min=40):
        self.budget = budget_per_node
        self.keep = keep
        self.verbose = verbose
        self._lock = threading.Lock()
        self.stats = {"switch": 0, "dead": 0, "refresh": 0}
        self.nodes = []            # [{"node": name, "remaining": n}]
        self.idx = 0
        self.used = 0
        self.dead = set()
        self._api = None
        self._secret = ""
        self._group = group or "GLOBAL"
        self._init_api()
        # 优先用探针留下的人清单（秒级启动）；否则才扫全池（慢，98 节点可能十几分钟）
        reused = False
        if nodes_file and os.path.exists(nodes_file):
            try:
                d = json.load(open(nodes_file, encoding="utf-8"))
                age = (time.time() - d.get("ts", 0)) / 60
                if age <= max_age_min:
                    found = [{"node": x["node"], "remaining": x["remaining"]}
                             for x in d.get("nodes", [])
                             if isinstance(x.get("remaining"), int) and x["remaining"] > 5]
                    if found:
                        found.sort(key=lambda x: -x["remaining"])
                        self.nodes = found
                        reused = True
                        if self.verbose:
                            print(f"[clash] 复用探针清单（{age:.0f} 分钟前）："
                                  f"{len(found)} 个节点，合计 "
                                  f"{sum(x['remaining'] for x in found):,} 次", flush=True)
            except Exception:
                pass
        if not reused:
            self.refresh()

    # ---------- Clash 控制 API ----------
    def _init_api(self):
        if not os.path.isdir(POOL_LOCAL):
            raise RuntimeError(f"找不到专用池目录 {POOL_LOCAL}")
        # clash_cli / clash_manager 在 clash_proxy 根目录，不是 local/
        root = os.path.dirname(POOL_LOCAL)
        for p in (root, POOL_LOCAL):
            if p not in sys.path:
                sys.path.insert(0, p)
        from clash_cli import get_config, load_dotenv
        load_dotenv(os.path.join(root, ".env"))
        cfg = get_config()
        self._api = cfg.get("api_url") or "http://127.0.0.1:9098"
        self._secret = cfg.get("secret") or os.environ.get("CLASH_SECRET", "")
        self._group = cfg.get("group_name") or self._group

    def _hdr(self):
        return {"Authorization": f"Bearer {self._secret}"} if self._secret else {}

    def _switch(self, name):
        import requests
        try:
            requests.put(f"{self._api}/proxies/{self._group}", headers=self._hdr(),
                         json={"name": name}, timeout=10)
            time.sleep(0.12)
            return True
        except Exception:
            return False

    # ---------- 节点扫描 ----------
    def refresh(self):
        """扫全部节点，只留有额度的，按剩余降序。"""
        import requests
        from curl_cffi import requests as cffi
        try:
            r = requests.get(f"{self._api}/proxies/{self._group}", headers=self._hdr(), timeout=15)
            names = [n for n in r.json().get("all", [])
                     if n not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS")]
        except Exception as e:
            if self.verbose:
                print(f"[clash] 读节点列表失败 {type(e).__name__}，沿用旧清单", flush=True)
            return
        px = {"http": "http://127.0.0.1:7898", "https": "http://127.0.0.1:7898"}
        found = []
        for n in names:
            if not self._switch(n):
                continue
            rem = None
            try:
                q = cffi.get(TEST_URL, timeout=15, impersonate="firefox133", proxies=px,
                             headers={"Accept": "application/vnd.github+json"})
                if q.status_code == 200:
                    rem = q.json()["resources"]["core"].get("remaining")
            except Exception:
                rem = None
            if isinstance(rem, int) and rem > 5:
                found.append({"node": n, "remaining": rem})
            time.sleep(0.35)
        found.sort(key=lambda x: -x["remaining"])
        with self._lock:
            self.nodes = found
            self.idx = 0
            self.used = 0
            self.dead.clear()
            self.stats["refresh"] += 1
        if self.verbose:
            tot = sum(x["remaining"] for x in found)
            print(f"[clash] 刷新完成：{len(found)} 个节点可用，合计 {tot:,} 次", flush=True)

    # ---------- 与 KdlPool 一致的接口 ----------
    def current(self):
        with self._lock:
            if self.used >= min(self.budget, (self.nodes[self.idx]["remaining"] if self.nodes else 0)):
                self._advance_locked()
            if not self.nodes:
                return "clash:none"
            return self.nodes[self.idx]["node"]

    def _advance_locked(self):
        self.idx += 1
        self.used = 0
        self.stats["switch"] += 1
        if self.idx >= len(self.nodes):
            self.idx = 0
            need = True
        else:
            need = False
        if self.nodes:
            self._switch(self.nodes[self.idx % len(self.nodes)]["node"])

    def proxies_for(self, node):
        return {"http": "http://127.0.0.1:7898", "https": "http://127.0.0.1:7898"}

    def record_ok(self, node):
        with self._lock:
            self.used += 1

    def record_dead(self, node, until_ts=None):
        with self._lock:
            self.dead.add(node)
            self.stats["dead"] += 1
            self.used = self.budget          # 立刻换下一个

    def rotate(self):
        with self._lock:
            self._advance_locked()

    def note(self):
        """给上层打印一行状态用。"""
        with self._lock:
            n = self.nodes[self.idx]["node"] if self.nodes else "—"
            rem = self.nodes[self.idx]["remaining"] if self.nodes else 0
            return f"[clash] 节点 {self.idx+1}/{len(self.nodes)} | {n[:28]} | 本节点已用 {self.used}/{min(self.budget,rem)}"
