# -*- coding: utf-8 -*-
"""账户采集状态集合：记录「哪些 fork 账号已经完整采集」。

设计三条原则（对应本项目的既有纪律）：

1. **JSONL 是唯一事实来源，Redis 只是索引**
   Redis 挂了/被清了，能从 JSONL 全量重建；重建是幂等的。
   写入顺序永远是「先落 JSONL，再 SADD」——反过来会在崩溃时丢账号。

2. **「完整」必须显式定义，且按档位区分**
   同一个人采了 `minimal` 不等于采了 `full`；D8 采样没覆盖全部仓库的，
   只能算 `partial`，不能进 `done`（否则分析层会把"没采到"当成"没有"）。

3. **三类状态分开存，不能合并**
   `done`   所有单元 200 且覆盖率完整
   `gone`   404（删号/改名）—— 永久墓碑，不再请求
   `partial` 采到了但覆盖不全（如 300/1564 仓）—— 将来放开上限要重采

Redis 键（v1，前缀可改，避免和同机其它项目串键）：
    grf:v1:{key}:done:{tier}      SET
    grf:v1:{key}:gone:{tier}      SET
    grf:v1:{key}:partial:{tier}   SET
    grf:v1:{key}:meta             HASH（last_run / counts / spec）

后端自动降级：Redis 不可达 → 同一套 API 落到 SQLite（data/state/collect_state.db）。
调用方完全不用关心走的是哪个后端。

用法：
    from collect_state import CollectState
    st = CollectState("profile_tier_rehearsal_rea_p98", tier="rehearsal",
                      units=("d1", "d4", "d8"))
    st.warm_from_jsonl("data/raw/profile_tier_rehearsal_rea_p98.jsonl")
    if st.is_done(login): ...
    st.mark(login, rec)          # 自动判定 done / partial / gone / 未完成

    # 命令行自检
    py collect_state.py --file data/raw/profile_tier_full.jsonl --tier full --units d1,d2,d3,d4,d6,d8
"""
import argparse
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_DIR = os.path.join(BASE, "data", "state")
SQLITE_PATH = os.path.join(STATE_DIR, "collect_state.db")

KINDS = ("done", "gone", "partial")


# ── 判定：一条记录属于哪一类 ────────────────────────────
def classify(rec, units):
    """→ 'done' | 'partial' | 'gone' | None（未完成，下轮要重采）。

    ★ 这里的规则是**唯一的真相定义**，别处不要另写一套。
    """
    if not rec:
        return None
    http = rec.get("http")
    if http == 404:
        return "gone"                      # 永久墓碑
    if http != 200:
        return None                        # 0 = 临时失败，必须重试
    for u in units:
        if (rec.get(u) or {}).get("http") != 200:
            return None                    # 某个必需单元没成功 → 整人未完成
    # D8 采样覆盖率：用 D1 的 public_repos（总数）校验，缺一个都算 partial
    if "d8" in units:
        d8 = rec.get("d8") or {}
        d1 = rec.get("d1") or {}
        sampled = d8.get("sampled_repos")
        total = d1.get("public_repos")
        if isinstance(sampled, int) and isinstance(total, int) and sampled < total:
            return "partial"
    return "done"


# ── 后端 ────────────────────────────────────────────────
class _RedisBackend:
    name = "redis"

    def __init__(self, db=0, prefix="grf:v1", host="127.0.0.1", port=6379):
        import redis  # 惰性导入：没装也不影响 SQLite 路线
        self.prefix = prefix
        self.r = redis.StrictRedis(host=host, port=port, db=db, decode_responses=True,
                                   socket_timeout=3, socket_connect_timeout=3)
        self.r.ping()                      # 连不上直接抛，由上层降级

    def key(self, name, tier, kind):
        # kind 必须进键名：done / partial / gone 是三个互斥集合，
        # 早先漏掉 kind 会把三类混进同一个 SET（实测 done 与 gone 同时为 True）。
        return f"{self.prefix}:{name}:{tier}:{kind}"

    def add(self, name, tier, kind, logins):
        if not logins:
            return
        k = self.key(name, tier, kind)
        pipe = self.r.pipeline()
        for lg in logins:
            pipe.sadd(k, lg)
        pipe.execute()

    def members(self, name, tier, kind):
        return set(self.r.smembers(self.key(name, tier, kind)))

    def contains(self, name, tier, kind, login):
        return bool(self.r.sismember(self.key(name, tier, kind), login))

    def remove(self, name, tier, kind, logins):
        if logins:
            self.r.srem(self.key(name, tier, kind), *list(logins))

    def meta_set(self, name, **kw):
        self.r.hset(f"{self.prefix}:{name}:meta", mapping={k: str(v) for k, v in kw.items()})

    def close(self):
        try:
            self.r.close()
        except Exception:
            pass


class _SqliteBackend:
    name = "sqlite"

    def __init__(self, path=SQLITE_PATH):
        import sqlite3
        import threading
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # check_same_thread=False：采集是**多线程**的，同一个状态对象会被各个 worker 调用；
        # 不加这个参数会在写入时抛 "SQLite objects created in a thread can only be used
        # in that same thread"（2026-11-08 实测）。
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._lock = threading.Lock()
        self.conn.execute("""CREATE TABLE IF NOT EXISTS collected (
            name TEXT NOT NULL, tier TEXT NOT NULL, login TEXT NOT NULL,
            kind TEXT NOT NULL, updated_at REAL, PRIMARY KEY (name, tier, login))""")
        self.conn.commit()

    def add(self, name, tier, kind, logins):
        if not logins:
            return
        now = time.time()
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO collected(name,tier,login,kind,updated_at) VALUES(?,?,?,?,?)",
                [(name, tier, lg, kind, now) for lg in logins])
            self.conn.commit()

    def members(self, name, tier, kind):
        with self._lock:
            cur = self.conn.execute(
                "SELECT login FROM collected WHERE name=? AND tier=? AND kind=?", (name, tier, kind))
            return {r[0] for r in cur}

    def contains(self, name, tier, kind, login):
        with self._lock:
            cur = self.conn.execute(
                "SELECT 1 FROM collected WHERE name=? AND tier=? AND login=? AND kind=?",
                (name, tier, login, kind))
            return cur.fetchone() is not None

    def remove(self, name, tier, kind, logins):
        if logins:
            with self._lock:
                self.conn.executemany(
                    "DELETE FROM collected WHERE name=? AND tier=? AND login=?",
                    [(name, tier, lg) for lg in logins])
                self.conn.commit()

    def meta_set(self, name, **kw):
        pass                               # 元信息对 SQLite 路线不重要

    def close(self):
        try:
            with self._lock:
                self.conn.close()
        except Exception:
            pass


# ── 对外接口 ────────────────────────────────────────────
class CollectState:
    """已采集账号集合。Redis 优先，不可用则自动降级 SQLite。"""

    def __init__(self, name, tier, units=(), backend="auto", db=0, prefix="grf:v1", quiet=False):
        self.name, self.tier, self.units = name, tier, tuple(units)
        self.quiet = quiet
        self.backend = None
        if backend in ("auto", "redis"):
            try:
                self.backend = _RedisBackend(db=db, prefix=prefix)
            except Exception as e:
                if backend == "redis":
                    raise
                if not quiet:
                    print(f"[state] Redis 不可用（{type(e).__name__}），降级 SQLite", flush=True)
        if self.backend is None:
            self.backend = _SqliteBackend()
        if not quiet:
            print(f"[state] {name} / {tier} → 后端 {self.backend.name}", flush=True)

    # ---- 后端容错 ----
    def _call(self, method, *a, **kw):
        """调用后端；Redis 挂掉时**自动降级到 SQLite** 并重试一次。

        2026-11-08 实测：本地 Redis 进程挂掉后，6 个采集线程全部在 `state.mark()`
        抛异常而死，整轮采集提前结束（还留下 3 万个没采的账号）。
        状态集合是**辅助索引**，绝不该让它拖死采集主流程。
        """
        try:
            return getattr(self.backend, method)(*a, **kw)
        except Exception as e:
            if self.backend.name != "redis":
                raise
            if not self.quiet:
                print(f"[state] Redis 不可用（{type(e).__name__}），切换到 SQLite 继续", flush=True)
            try:
                self.backend.close()
            except Exception:
                pass
            self.backend = _SqliteBackend()
            return getattr(self.backend, method)(*a, **kw)

    # ---- 查询 ----
    def is_done(self, login):
        return self._call("contains", self.name, self.tier, "done", login.lower())

    def is_gone(self, login):
        return self._call("contains", self.name, self.tier, "gone", login.lower())

    def needs(self, login):
        """三类终态之外才需要采：done / gone(404) / partial(采过但覆盖不全)。"""
        lg = login.lower()
        return not any(self._call("contains", self.name, self.tier, k, lg) for k in KINDS)

    def done_set(self):
        return self._call("members", self.name, self.tier, "done")

    def gone_set(self):
        return self._call("members", self.name, self.tier, "gone")

    def partial_set(self):
        return self._call("members", self.name, self.tier, "partial")

    def terminal_set(self):
        """一次性取出三类终态，用于批量过滤（避免逐个 sismember 的往返）。"""
        return self.done_set() | self.gone_set() | self.partial_set()

    # ---- 写入 ----
    def mark(self, login, rec=None, kind=None):
        """记录一个账号的终态。rec 给了就自动判定，否则用显式 kind。"""
        k = kind or classify(rec, self.units)
        if not k:
            return None                    # 未完成：什么都不写，下轮重采
        self._call("add", self.name, self.tier, k, [login.lower()])
        return k

    def mark_many(self, items):
        """items: [(login, rec)] 或 [(login, kind)]，按类批量写（一次 pipeline）。"""
        buckets = {k: [] for k in KINDS}
        for item in items:
            lg = (item[0] or "").lower()
            if not lg:
                continue
            rec = item[1]
            k = rec if isinstance(rec, str) else classify(rec, self.units)
            if k in buckets:
                buckets[k].append(lg)
        for k, lgs in buckets.items():
            self._call("add", self.name, self.tier, k, lgs)
        return {k: len(v) for k, v in buckets.items()}

    # ---- 重建 ----
    def warm_from_jsonl(self, path):
        """从 JSONL 重建索引（幂等）。返回各类数量。

        ⚠️ 去重规则是「**成功优先**」，不是"最后一条覆盖"。
        账号被重试时，一次失败的 `http=0` 会写在成功记录之后；按 last-wins 去重
        会把已经采到的 200 覆盖成失败。2026-11-10 实测：openclaw D1 有 3,373 个
        账号因此从"成功"退回"待采"，等于白采。
        正确规则：同名多条里只要出现过 200 就是 done。
        """
        if not os.path.exists(path):
            return {k: 0 for k in KINDS}
        rank_of = {"gone": 0, "partial": 1, "none": 2, "done": 3}
        best = {}                          # login → (rank, rec)
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            lg = (rec.get("login") or "").lower()
            if not lg:
                continue
            rk = rank_of.get(classify(rec, self.units) or "none", 0)
            prev = best.get(lg)
            if prev is None or rk >= prev[0]:
                best[lg] = (rk, rec)
        latest = {lg: rec for lg, (_, rec) in best.items()}
        # 先把旧的索引清干净，再按 JSONL 重建（否则改判后会有残留）
        for k in KINDS:
            old = self._call("members", self.name, self.tier, k)
            if old:
                self._call("remove", self.name, self.tier, k, old)
        counts = self.mark_many(list(latest.items()))
        if not self.quiet:
            print(f"[state] 重建：done={counts['done']} partial={counts['partial']} "
                  f"gone={counts['gone']}（{len(latest)} 个 login）", flush=True)
        return counts

    def stats(self):
        d = {k: len(self._call("members", self.name, self.tier, k)) for k in KINDS}
        d["backend"] = self.backend.name
        return d

    def close(self):
        self.backend.close()


# ── 命令行自检 ──────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="采集状态集合自检 / 重建")
    ap.add_argument("--file", required=True, help="profile_tier_*.jsonl")
    ap.add_argument("--tier", default="full")
    ap.add_argument("--units", default="d1,d2,d3,d4,d6,d8")
    ap.add_argument("--backend", default="auto", choices=("auto", "redis", "sqlite"))
    ap.add_argument("--name", help="集合名（默认取文件名去后缀）")
    ap.add_argument("--check", help="查询某个 login 的状态")
    args = ap.parse_args()

    units = tuple(u.strip() for u in args.units.split(",") if u.strip())
    name = args.name or os.path.splitext(os.path.basename(args.file))[0]
    st = CollectState(name, args.tier, units, backend=args.backend)
    counts = st.warm_from_jsonl(args.file)
    print(f"  分类结果：{counts}")
    if args.check:
        lg = args.check.lower()
        print(f"  {lg}: done={st.is_done(lg)} gone={st.is_gone(lg)} needs={st.needs(lg)}")
    st.close()


if __name__ == "__main__":
    main()
