# -*- coding: utf-8 -*-
"""forker 主页画像全量采集：全部 fork owner 的 /users/{login}（每人 1 req）。

路线：本地 Clash（mihomo）订阅节点池轮换出口 IP，分摊匿名 60 req/h/IP 配额。
连接参数读 config/settings.json（gitignored）或 CLASH_* 环境变量。

为什么不直接用通用代理管理器：那些为「长下载」设计，一次切换 = sleep(3) +
三重验证 ≈ 46s。而本任务是**配额驱动轮换**——大量节点出口配额被共享 IP 的
其他租户吃光或链路死，一次成功采集后就要换节点。46s/换 = 吞吐 60 req/h，
等于没换。本脚本自管轻量池：PUT 策略组(0.2s) + 用业务响应自身的 X-RateLimit
头当切换信号，冷却/指数退避/全池熔断语义与 _profile_lib.NodePool 一致。

配额数学：匿名 60 req/h/IP；每节点安全线 = 剩余 ≤3 即换（给同 IP 租户留量）。
  有余量节点 × 平均余量 → 实测 ~900-1,600 req/h；8.2 万人 ≈ 2-5 天。

断点续采：done 集合只认 200/404；http:0 失败墓碑下轮重扫。Ctrl-C 随时停。
输出: data/raw/forker_profiles_all.jsonl
用法:
  py _forker_profiles_all.py --limit 20     # 冒烟
  py _forker_profiles_all.py               # 全量后台长跑
"""
import argparse
import datetime
import json
import os
import time

from curl_cffi import requests as cffi

# 节点池/配额语义与 _profile_lib.py 同源；此脚本独立实现（长跑中不 import 改动），
# 连接参数同样读 config/settings.json / CLASH_* 环境变量。
from _profile_lib import CLASH_API, CLASH_SECRET, CLASH_GROUP, PROXIES  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_PATH = os.path.join(BASE, "data", "raw", "forker_profiles_all.jsonl")
FORKS_PATH = os.path.join(BASE, "data", "raw", "probe_forks_all.jsonl")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
      "Accept": "application/vnd.github+json"}

COOLDOWN_BASE = 120     # 秒，指数冷却基数（×2 递增）
COOLDOWN_MAX = 3600
SAFE_REMAIN = 3         # 剩余配额 ≤ 此值 → 主动换节点
PROFILES = ["chrome", "firefox133", "safari17_0", "edge101"]  # TLS 指纹轮换（直连抑制窗口用）

FIELDS = ("user_id", "type", "name", "company", "blog", "location", "hireable",
          "bio", "twitter_username", "public_repos", "public_gists",
          "followers", "following", "created_at", "updated_at")


class NodePool:
    """轻量节点池：轮换 + 指数冷却 + 全池熔断（PUT 策略组 ~0.2s 切换）。

    直连（无代理）作为伪节点 "DIRECT" 混在池里——本机直连也有独立 60/h，
    且此前所有采集都靠它跑通过（抑制窗口靠 TLS 指纹轮换硬抗）。
    """

    def __init__(self):
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
        # 全池冷却：睡到最早解冻点（全池熔断）
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


def load_forker_logins():
    """fork 名单 → 有序去重 login 列表（保持 probe 文件顺序，确定性）。"""
    logins, seen = [], set()
    for line in open(FORKS_PATH, encoding="utf-8"):
        try:
            u = json.loads(line).get("login")
        except json.JSONDecodeError:
            continue
        if u and u not in seen:
            seen.add(u)
            logins.append(u)
    return logins


def load_done_logins():
    """完成集合 = 200/404 墓碑（http:0 失败墓碑不算，下轮重扫）。
    含既有画像文件（probe_users 等试点，不重复花钱）。"""
    done = set()
    for name in ("forker_profiles_all.jsonl", "probe_users.jsonl",
                 "user_profiles_pilot.jsonl", "user_profiles_newrepos.jsonl",
                 "issue_contrib_authors.jsonl"):
        p = os.path.join(BASE, "data", "raw", name)
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("login") and rec.get("http") in (200, 404):
                done.add(rec["login"])
    return done


def emit(out, login, d, status):
    rec = {"login": login, "http": status,
           "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if status == 200:
        for k in FIELDS:
            rec[k] = d.get("id" if k == "user_id" else k)
        rec["has_email"] = bool(d.get("email"))  # 项目纪律：邮箱只落布尔
    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    out.flush()


def hdr_int(r, name, default=None):
    try:
        return int(r.headers.get(name))
    except (TypeError, ValueError):
        return default


def fetch_user(pool, login):
    """采一个 login：配额驱动轮换。返回 (status, resp|None)。
    只认可 200/404；连换 12 节点仍拿不到 → 放弃写 0 墓碑。"""
    prof_i = 0
    for attempt in range(12):
        node = pool.current()
        try:
            r = cffi.get(f"https://api.github.com/users/{login}",
                         headers=UA, proxies=pool.proxies_for(node),
                         timeout=20, impersonate=PROFILES[prof_i % len(PROFILES)])
            prof_i += 1
        except Exception:
            pool.record_dead(node)       # 链路死 → 冷却该节点
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
                pool.record_dead(node, until_ts=reset)  # 该 IP 本小时配额光 → 冷到重置点
            else:
                pool.record_dead(node)   # 无配额头（Retry-After/风控）→ 常规指数冷却
            pool.rotate()
            ra = hdr_int(r, "Retry-After", default=0)
            if ra:
                time.sleep(min(ra, 30))
            continue

        # 5xx 等：同节点短退避重试（不冷却，GitHub 侧偶发）
        time.sleep(3)
    return 0, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="冒烟测试：只采前 N 个")
    args = ap.parse_args()

    logins = load_forker_logins()
    done = load_done_logins()
    todo = [u for u in logins if u not in done]
    print(f"forker 总数 {len(logins)} | 已完成(200/404) {len(logins) - len(todo)} | "
          f"待采 {len(todo)}", flush=True)
    if args.limit:
        todo = todo[:args.limit]
        print(f"冒烟模式：只采 {len(todo)} 个", flush=True)

    pool = NodePool()
    print(f"节点池 {len(pool.nodes)} 个（含 DIRECT）| 起始 {pool.current()}", flush=True)

    t0 = time.time()
    n_ok = n_404 = n_err = 0
    out = open(OUT_PATH, "a", encoding="utf-8")
    try:
        for i, login in enumerate(todo, 1):
            status, r = fetch_user(pool, login)
            if status == 200:
                emit(out, login, r.json(), 200)
                n_ok += 1
            elif status == 404:
                emit(out, login, {}, 404)  # 账号已删 → 永久墓碑
                n_404 += 1
            else:
                emit(out, login, {}, 0)    # 失败墓碑，下轮重扫
                n_err += 1

            if i % 100 == 0:
                rate = i / (time.time() - t0) * 3600
                eta_h = (len(todo) - i) / max(rate, 1)
                print(f"[{i}/{len(todo)}] ok={n_ok} 404={n_404} err={n_err} | "
                      f"{rate:.0f} req/h | ETA {eta_h:.1f}h | 节点 {pool.current()} | "
                      f"切换 {pool.stats['switch']}", flush=True)
            time.sleep(0.8)
    except KeyboardInterrupt:
        print(f"\n手动中断：ok={n_ok} 404={n_404}，断点续采可恢复", flush=True)
    finally:
        out.close()
        pool.restore()
        print(f"收尾: ok={n_ok} 404={n_404} err={n_err} | 切换 {pool.stats['switch']} 次 | "
              f"耗时 {(time.time()-t0)/60:.1f} 分钟 → {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
