# -*- coding: utf-8 -*-
"""并行版账户画像采集（快代理，多出口并发）。

为什么需要它：串行时单个出口 60/h、代理延迟 1–4s，实测只有 **~5 请求/分钟**；
5,000 人 × 8 req 要 5 天。并行后（每线程独占自己的出口池）可到 60–100 请求/分钟，
同样规模压到 **1–2 小时**。

复用 `_profile_tiers.collect_user()`——它只依赖 pool 的四个方法
（current / proxies_for / record_dead / record_ok / rotate），所以这里实现一个
**与 Clash NodePool 同接口**的 KdlPool 适配器即可，采集逻辑一行不用改。

用法：
    py _profiles_parallel.py --tier deep --from-file data/raw/x.txt --workers 8
    py _profiles_parallel.py --tier screen --from-file data/raw/y.txt --workers 10 --limit 5000
"""
import argparse
import json
import os
import queue
import sys
import threading
import time

from curl_cffi import requests as cffi

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _profile_tiers as PT
from collect_state import CollectState

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
KDL_CFG = os.path.join(BASE, "data", "proxy_kdl.json")


def load_kdl():
    cfg = {}
    if os.path.exists(KDL_CFG):
        cfg = json.load(open(KDL_CFG, encoding="utf-8"))
    api = os.environ.get("KDL_API_URL") or cfg.get("api_url") or ""
    user = os.environ.get("KDL_USERNAME") or cfg.get("username") or ""
    pwd = os.environ.get("KDL_PASSWORD") or cfg.get("password") or ""
    if not (api and user and pwd):
        raise SystemExit("KDL 配置缺失：data/proxy_kdl.json 或 KDL_* 环境变量")
    return api, user, pwd


API_URL, USER, PWD = load_kdl()

# 每个账号最多换几次出口。默认 12 次太贵：一次网络抖动就能烧掉 12 个请求
# （实测失败率 16% 时，光重试就浪费掉约 1 万个请求）。screen 档失败无所谓，
# 状态集合不会把它标记完成，下轮自动重扫。
_ATTEMPTS = 5


def _install_attempt_cap(n):
    """把 quota_get 的 max_attempts 全局压到 n（两个模块的引用都要换）。"""
    import _profile_lib as PL
    orig = PL.quota_get

    def capped(pool, url, max_attempts=12, **kw):
        return orig(pool, url, max_attempts=min(max_attempts, n), **kw)

    PL.quota_get = capped
    PT.quota_get = capped

# 全局提取闸门：KDL 对「提取 IP」这个动作本身有频率限制，
# 12 个线程各自猛刷会互相拖累（实测错误率飙到 32%）。
_EXTRACT_LOCK = threading.Lock()
_LAST_EXTRACT = [0.0]
EXTRACT_MIN_INTERVAL = 1.5      # 秒；全进程共享


def _extract_ips(n):
    """带全局限速的 IP 提取。"""
    with _EXTRACT_LOCK:
        gap = EXTRACT_MIN_INTERVAL - (time.time() - _LAST_EXTRACT[0])
        if gap > 0:
            time.sleep(gap)
        _LAST_EXTRACT[0] = time.time()
        try:
            r = cffi.get(API_URL.format(num=n), timeout=20, impersonate="firefox133")
            if r.status_code == 200:
                return [x.strip() for x in r.text.splitlines() if x.strip()]
        except Exception:
            pass
    return []


class TunnelPool:
    """隧道出口池：固定端点，**每请求换出口 IP**（2026-11-12 实测 24/25 请求不同 IP）。

    为什么比 KdlPool 快 5.5 倍：GitHub 的 60/h 是按出口 IP 算的，
    每请求换 IP 就等于每个 IP 只用一次，配额天然用不完。
    前提是**不能复用连接**，所以请求头带 Connection: close。
    """

    def __init__(self, ip_num=3):
        self.stats = {"switch": 0, "ok": 0, "dead": 0, "refill": 0}
        cfg = json.load(open(os.path.join(BASE, "data", "proxy_tunnel.json"), encoding="utf-8"))
        url = f"http://{cfg['username']}:{cfg['password']}@{cfg['host']}/"
        self._px = {"http": url, "https": url}
        self.ips = ["tunnel"]

    # ---- NodePool 兼容接口 ----
    def current(self):
        return "tunnel"

    def proxies_for(self, node):
        return self._px

    def rotate(self):
        self.stats["switch"] += 1

    def record_ok(self):
        self.stats["ok"] += 1

    def record_dead(self):
        self.stats["dead"] += 1


class KdlPool:
    """与 `_profile_lib.NodePool` 同接口的出口池：current / proxies_for / rotate / record_*。

    每个线程持有自己的实例，互不干扰；一个 IP 的 60 次用完（403）就丢弃换新的。
    """

    def __init__(self, ip_num=3):
        self.ip_num = ip_num
        self.ips = []
        self.idx = 0
        self.stats = {"switch": 0, "ok": 0, "dead": 0, "refill": 0}
        self._lock = threading.Lock()
        self.refill()

    def refill(self):
        """取一批新出口；失败退避重试，但**不长时间阻塞**（线程不能被卡住）。"""
        for attempt in range(3):
            ips = _extract_ips(self.ip_num)
            if ips:
                with self._lock:
                    self.ips = ips
                    self.idx = 0
                    self.stats["refill"] += 1
                return True
            time.sleep(min(3 * (attempt + 1), 10))
        with self._lock:
            self.ips = []
        return False

    # ---- NodePool 兼容接口 ----
    def current(self):
        """取出口：最多补两次，仍没有就返回哨兵让调用方**快速失败**。

        早前版本会阻塞式重试 8 轮（每轮内含 5 次退避重试），单次 current() 最长卡
        ~11 分钟；6 个线程全部卡在里面时，进程活着但完全不出数据（2026-11-08 实测
        卡死 3.6 小时）。现在的原则：宁可这一次失败、把账号退回队列，也不让线程挂住。
        """
        for _ in range(2):
            with self._lock:
                if self.ips and self.idx < len(self.ips):
                    return self.ips[self.idx]
            self.refill()
        with self._lock:
            return self.ips[0] if self.ips else "0.0.0.0:0"

    def proxies_for(self, node):
        if not node:
            return None
        auth = f"http://{USER}:{PWD}@{node}/"
        return {"http": auth, "https": auth}

    def rotate(self):
        with self._lock:
            if self.ips and self.idx < len(self.ips) - 1:
                self.idx += 1
                self.stats["switch"] += 1
                return self.ips[self.idx]
        self.refill()
        with self._lock:
            return self.ips[0] if self.ips else None

    def record_dead(self, node, until_ts=None):
        """403/连接失败 → 丢弃这个出口（它的 60/h 已光或链路死）。"""
        with self._lock:
            if node in self.ips:
                self.ips.remove(node)
                self.stats["dead"] += 1
                self.idx = min(self.idx, max(0, len(self.ips) - 1))

    def record_ok(self, node):
        self.stats["ok"] += 1

    def restore(self):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="deep", choices=sorted(PT.TIERS))
    ap.add_argument("--from-file", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out-suffix", default="")
    ap.add_argument("--ip-num", type=int, default=3, help="每个线程一次的出口数")
    ap.add_argument("--pool", choices=("kdl", "clash", "tunnel"), default="kdl",
                    help="kdl=快代理私密 IP（默认）；clash=Clash 专用池的 98 个节点"
                         "（每 IP 独立 60/h，实测约 38 个可用、合计 ~1,900 次）")
    ap.add_argument("--clash-budget", type=int, default=45,
                    help="clash 模式下每个节点最多用几次（默认 45，留余量）")
    ap.add_argument("--force", action="store_true", help="忽略状态集合强制重采")
    ap.add_argument("--attempts", type=int, default=_ATTEMPTS,
                    help="每人最多换几个出口（默认 5；调大更抗抖动但更费配额）")
    ap.add_argument("--d2-pages", type=int, default=1)
    ap.add_argument("--d8-pages", type=int, default=3)
    ap.add_argument("--d4l-pages", type=int, default=1)
    args = ap.parse_args()

    PT.PAGES_OPT["d2"] = args.d2_pages
    PT.PAGES_OPT["d8"] = args.d8_pages
    PT.PAGES_OPT["d4l"] = args.d4l_pages
    _install_attempt_cap(args.attempts)

    # clash 模式下**全进程共用一个池**（节点切换是全局的，不能让每个线程各切）
    shared_pool = None
    if args.pool == "clash":
        # Clash 已于 2026-11-13 全面禁用（用户订阅账号被判滥用），见 AGENTS.md 第〇节
        print("✗ Clash 通道已禁用：请改用 --pool tunnel", flush=True)
        raise SystemExit(2)

    out_path = os.path.join(RAW, f"profile_tier_{args.tier}{args.out_suffix}.jsonl")
    # 启动时清掉自己的"主动暂停"标记，否则巡检会一直把它显示成 PAUSED
    _pause = os.path.join(BASE, "data", "state", "paused.json")
    try:
        if os.path.exists(_pause):
            _names = set(json.load(open(_pause, encoding="utf-8")))
            _names.discard(os.path.basename(out_path))
            json.dump(sorted(_names), open(_pause, "w", encoding="utf-8"))
    except Exception:
        pass
    logins = [l.strip() for l in open(args.from_file, encoding="utf-8")
              if l.strip() and not l.startswith("#")]
    seen = set()
    uniq = []
    for lg in logins:
        k = lg.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(lg)
    logins = uniq

    stem = os.path.splitext(os.path.basename(out_path))[0]
    state = CollectState(stem, args.tier, units=PT.TIERS[args.tier], quiet=True)
    state.warm_from_jsonl(out_path)
    terminal = set() if args.force else state.terminal_set()
    todo = [u for u in logins if u.lower() not in terminal]
    if args.limit:
        todo = todo[:args.limit]
    st = state.stats()
    print(f"档位 {args.tier} = {'+'.join(PT.TIERS[args.tier])} | 名单 {len(logins)} | "
          f"done={st['done']} partial={st['partial']} gone={st['gone']} | 待采 {len(todo)} | "
          f"线程 {args.workers}", flush=True)

    d1_cache = PT.load_d1_cache()
    print(f"D1 缓存 {len(d1_cache)} 条", flush=True)

    q = queue.Queue()
    for lg in todo:
        q.put(lg)
    lock = threading.Lock()
    out = open(out_path, "a", encoding="utf-8")
    t0 = time.time()
    done_n = [0, 0, 0]      # ok / 404 / err
    stop = threading.Event()
    tries = {}                       # login -> 已失败次数（代理不可用时不烧掉账号）
    last_write = [time.time()]       # 看门狗用
    WATCHDOG_MIN = 20

    def watchdog():
        """长时间没有任何写入 → 直接退出，让外层重启；静默卡死比崩溃更糟。"""
        while not stop.is_set():
            time.sleep(60)
            idle = (time.time() - last_write[0]) / 60
            if idle > WATCHDOG_MIN:
                print(f"[watchdog] {idle:.0f} 分钟无写入，进程判定卡死，主动退出", flush=True)
                os._exit(3)

    def worker():
        pool = shared_pool if shared_pool is not None else (TunnelPool() if args.pool == "tunnel" else KdlPool(ip_num=args.ip_num))
        while not stop.is_set():
            try:
                login = q.get_nowait()
            except queue.Empty:
                return
            try:
                rec = PT.collect_user(pool, login, args.tier, d1_cache)
            except Exception as e:
                rec = {"login": login, "tier": args.tier, "http": 0,
                       "collected_at": None, "error": f"{type(e).__name__}: {e}"}
            # 代理不可用导致的失败：退回队列重试，别浪费账号（最多 3 次）
            if rec.get("http") == 0 and not pool.ips and tries.get(login, 0) < 3:
                tries[login] = tries.get(login, 0) + 1
                q.put(login)
                time.sleep(3)
                continue
            with lock:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                state.mark(login, rec)
                last_write[0] = time.time()
                if rec.get("http") == 200:
                    done_n[0] += 1
                elif rec.get("http") == 404:
                    done_n[1] += 1
                else:
                    done_n[2] += 1
                n = done_n[0] + done_n[1] + done_n[2]
                if n % 50 == 0:
                    rate = n / max(time.time() - t0, 1e-6) * 60
                    eta = (len(todo) - n) / max(rate, 1e-6)
                    print(f"[{n}/{len(todo)}] ok={done_n[0]} 404={done_n[1]} err={done_n[2]} | "
                          f"{rate:.0f} 人/分钟 | ETA {eta:.0f} 分钟", flush=True)
            q.task_done()

    threading.Thread(target=watchdog, daemon=True).start()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop.set()
        print("\n手动中断：已写盘的部分可续采", flush=True)
    finally:
        out.close()
        state.close()
        el = (time.time() - t0) / 60
        print(f"收尾 ok={done_n[0]} 404={done_n[1]} err={done_n[2]} | {el:.1f} 分钟 "
              f"| {done_n[0] / max(el, 1e-6):.1f} 人/分钟 → {os.path.basename(out_path)}")


if __name__ == "__main__":
    sys.exit(main())
