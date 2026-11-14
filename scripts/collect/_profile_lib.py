# -*- coding: utf-8 -*-
"""用户画像采集共享库：节点池 + 配额驱动请求 + Link 尾页解析。

NodePool 与 quota_get 供各采集器共用；连接参数读 config/settings.json
（gitignored，见 config/settings.example.json），环境变量 CLASH_* 可覆盖。
两采集器可并发（都从响应头自适应配额），但会互相切走对方的节点——
小规模冒烟无妨，大批量建议错峰。

配额语义：
  匿名 60 req/h/IP；安全线 rem<=3 主动换节点；403+rem=0 冷却到重置时间戳；
  其他 403/429 指数冷却；连接异常冷却+轮换；全池冷却睡到最早解冻。
"""
import json
import os
import re
import time

from curl_cffi import requests as cffi

# ---- Clash 连接参数：config/settings.json > 环境变量 > 内置默认 ----
# 只影响「代理轮换」类采集器（forker 全量 / 画像档位）；不配则连不上时报错退出，
# 直连（DIRECT 伪节点）仍可用但失去多出口配额分摊。
def _load_clash_cfg():
    cfg = {}
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "settings.json")
    if os.path.exists(p):
        try:
            cfg = json.load(open(p, encoding="utf-8")).get("clash", {}) or {}
        except (OSError, ValueError):
            cfg = {}
    return {
        "api_url": os.environ.get("CLASH_API_URL") or cfg.get("api_url") or "http://127.0.0.1:9090",
        "secret": os.environ.get("CLASH_SECRET") or cfg.get("secret") or "",
        "group": os.environ.get("CLASH_GROUP") or cfg.get("group") or "🚀节点选择",
        "proxy_port": int(os.environ.get("CLASH_PROXY_PORT") or cfg.get("proxy_port") or 7890),
    }


_CLASH = _load_clash_cfg()
CLASH_API = _CLASH["api_url"]
CLASH_SECRET = _CLASH["secret"]
CLASH_GROUP = _CLASH["group"]
PROXY_PORT = _CLASH["proxy_port"]
PROXIES = {"http": f"http://127.0.0.1:{PROXY_PORT}", "https": f"http://127.0.0.1:{PROXY_PORT}"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
      "Accept": "application/vnd.github+json"}

COOLDOWN_BASE = 120     # 秒，指数冷却基数（×2 递增）
COOLDOWN_MAX = 3600
SAFE_REMAIN = 3         # 剩余配额 ≤ 此值 → 主动换节点
PROFILES = ["chrome", "firefox133", "safari17_0", "edge101"]  # TLS 指纹轮换


class NodePool:
    """轻量节点池：轮换 + 指数冷却 + 全池熔断（PUT 策略组 ~0.2s 切换）。

    直连（无代理）作为伪节点 "DIRECT" 混在池里——本机直连也有独立 60/h，
    抑制窗口靠 TLS 指纹轮换硬抗。
    """

    def __init__(self):
        if not CLASH_SECRET:
            raise SystemExit("未配置 Clash：复制 config/settings.example.json 为 settings.json "
                             "并填写 clash 段（或设 CLASH_API_URL/CLASH_SECRET/CLASH_GROUP 环境变量）")
        self.hdr = {"Authorization": f"Bearer {CLASH_SECRET}", "Content-Type": "application/json"}
        self.group_url = f"{CLASH_API}/proxies/{CLASH_GROUP}"
        g = cffi.get(self.group_url, headers=self.hdr, timeout=10).json()
        self.orig_node = g.get("now")
        self.nodes = [p for p in g.get("all", [])
                      if p not in ("DIRECT", "REJECT", "♻️自动选择", CLASH_GROUP)
                      and not any(w in p for w in ("官网", "剩余流量", "过期时间", "订阅",
                                                    "到期", "发布", "网址", "重置"))] + ["DIRECT"]
        self.idx = 0
        self.fail = {}       # node -> 连续失败次数（指数冷却依据）
        self.dead_until = {}  # node -> 时间戳（冷却截止）
        self.stats = {"switch": 0, "ok": 0, "stall_sleep": 0}

    # ---------- 基础 ----------
    def current(self):
        return self.nodes[self.idx]

    def proxies_for(self, node):
        return None if node == "DIRECT" else PROXIES

    def _switch_to(self, idx):
        node = self.nodes[idx]
        if node != "DIRECT":
            cffi.put(self.group_url, headers=self.hdr, timeout=10,
                     data=json.dumps({"name": node}))
        self.idx = idx
        self.stats["switch"] += 1
        return node

    def rotate(self):
        """换到下一个不在冷却中的节点；全池冷却则睡到最早解冻（全池熔断）。"""
        now = time.time()
        n = len(self.nodes)
        for off in range(1, n + 1):
            j = (self.idx + off) % n
            if self.dead_until.get(self.nodes[j], 0) <= now:
                return self._switch_to(j)
        earliest = min(self.dead_until.values())
        wait = min(max(earliest - now + 2, 30), COOLDOWN_MAX)
        print(f"    [pool-stall] 全池冷却，睡 {wait/60:.0f} 分钟", flush=True)
        self.stats["stall_sleep"] += 1
        time.sleep(wait)
        return self._switch_to((self.idx + 1) % n)

    # ---------- 判定上报（ok / dead / 冷却时间戳） ----------
    def record_dead(self, node, until_ts=None):
        """链路死/配额光：指数冷却。until_ts 提供时用绝对时间（配额重置点）。"""
        self.fail[node] = self.fail.get(node, 0) + 1
        if until_ts:
            self.dead_until[node] = until_ts + 5
        else:
            self.dead_until[node] = time.time() + min(
                COOLDOWN_BASE * (2 ** (self.fail[node] - 1)), COOLDOWN_MAX)

    def record_ok(self, node):
        self.fail[node] = 0
        self.dead_until.pop(node, None)
        self.stats["ok"] += 1

    def restore(self):
        try:
            if self.orig_node and self.orig_node != "DIRECT":
                cffi.put(self.group_url, headers=self.hdr, timeout=10,
                         data=json.dumps({"name": self.orig_node}))
        except Exception:
            pass


def hdr_int(r, name, default=None):
    try:
        return int(r.headers.get(name))
    except (TypeError, ValueError):
        return default


def quota_get(pool, url, max_attempts=12, timeout=20, headers=None):
    """配额驱动 GET：与 _forker_profiles_all.fetch_user 同语义，URL 参数化。

    返回 (status, resp|None)。只认可 200/404 为定论；连换 12 节点仍拿不到
    → (0, None) 由调用方落失败墓碑。

    headers：在默认 UA 之上追加/覆盖（如 `Accept: application/vnd.github.star+json`
    才能拿到 `starred_at`）。
    """
    hdr = dict(UA)
    if headers:
        hdr.update(headers)
    prof_i = 0
    for _ in range(max_attempts):
        node = pool.current()
        try:
            r = cffi.get(url, headers=hdr, proxies=pool.proxies_for(node),
                         timeout=timeout, impersonate=PROFILES[prof_i % len(PROFILES)])
            prof_i += 1
        except Exception:
            pool.record_dead(node)
            pool.rotate()
            continue

        if r.status_code in (200, 404):
            pool.record_ok(node)
            rem = hdr_int(r, "X-RateLimit-Remaining", default=SAFE_REMAIN + 1)
            if rem is not None and rem <= SAFE_REMAIN:
                pool.rotate()             # 配额见底 → 先换好下一个
            return r.status_code, r

        if r.status_code in (403, 429):
            rem = hdr_int(r, "X-RateLimit-Remaining", default=None)
            reset = hdr_int(r, "X-RateLimit-Reset", default=None)
            if rem == 0 and reset:
                pool.record_dead(node, until_ts=reset)  # 该 IP 本小时配额光
            else:
                pool.record_dead(node)   # 无配额头（Retry-After/风控）
            pool.rotate()
            ra = hdr_int(r, "Retry-After", default=0)
            if ra:
                time.sleep(min(ra, 30))
            continue

        time.sleep(3)                    # 5xx 等：同节点短退避（GitHub 侧偶发）
    return 0, None


def link_last_page(r):
    """解析响应头 Link: rel="last" 的页码。

    配 per_page=1 用 = 列表总数（1 req 拿计数，最便宜的口径）。
    无 Link 头（结果只有 1 页或空）→ None，调用方用 len(items) 兜底。
    """
    link = r.headers.get("Link") or ""
    m = re.search(r'<[^>]*[?&]page=(\d+)>;\s*rel="last"', link)
    return int(m.group(1)) if m else None
