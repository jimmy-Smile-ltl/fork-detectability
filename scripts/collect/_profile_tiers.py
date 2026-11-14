# -*- coding: utf-8 -*-
"""用户画像档位采集器：D1-D8 基础采集单元 → 档位排列组合。

设计（用户 2026-10-31 决策）：先实现基础采集单元（每个 D 项一个函数，
独立可测），再用档位组合成整人采集——新增档位只改一张表，不动采集逻辑。

┌─────────┬──────────────────────────────┬────────────┬──────────────────────────┐
│ 档位     │ 组合                          │ 请求数/人   │ 用途                       │
├─────────┼──────────────────────────────┼────────────┼──────────────────────────┤
│ minimal │ D1 + D2×1页 + D3 + D4        │ 4          │ S5 最小指标包：号龄+活跃+  │
│         │                              │            │ 仓库数+star 宽度           │
│ standard│ D1 + D2×3页 + D3 + D4 + D6   │ 5-7        │ + 组织归属（真人常挂 org） │
│ full    │ D1 + D2×3页 + D3 + D4 + D6   │ 6-10       │ + D8 fork/自建结构（批量号 │
│         │ + D8×3页                     │            │ 核心特征：全 fork 无自建）  │
└─────────┴──────────────────────────────┴────────────┴──────────────────────────┘

各基础单元（详见 数据采集方案.md §D）：
  D1 /users/{login}             档案：注册时间/仓库数/粉丝/画像完整度/has_email(布尔)
  D2 /users/{login}/events      近 90 天活动：事件类型分布（Push=开发 vs 纯 Watch=挂机）
  D3 /users/{login}/repos       Link 尾页=公开仓库数（per_page=1，1 req 拿计数）
  D4 /users/{login}/starred     Link 尾页=star 总数（行为宽度：真人几十~几千）
  D5                            0 req——D1 响应已含 followers/following
  D6 /users/{login}/orgs        所属组织
  D7 HTML 主页                  未启用（Accept 406 待修，全档位都不含）
  D8 /users/{login}/repos       fork vs 自建结构（sort=pushed，采样 N 页聚合）

断点续采：每档位独立 JSONL；done = 该档位文件里 http∈(200,404) 的 login。
D1 复用：--reuse-d1（默认开）从既有画像文件（forker_profiles_all 等）读缓存，
  省掉 D1 请求——任务 #14 已在全量采 D1，同账号不重复花钱。

与任务 #14 并发注意：两脚本共用 Clash 策略组，会互相切走对方节点。
冒烟（几个人）无妨；整档大批量建议等 #14 跑完或错峰。

用法：
  py _profile_tiers.py --tier full --logins mneves75,steipete          # 冒烟
  py _profile_tiers.py --tier minimal --from-file 可疑名单.txt --limit 600
  py _profile_tiers.py --tier full --from-forks --limit 100            # forker 池抽样
"""
import argparse
import datetime
import glob
import json
import os
import time

from _profile_lib import NodePool, quota_get, link_last_page
from collect_state import CollectState

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
API = "https://api.github.com"

# D1 落盘字段（与 _forker_profiles_all.FIELDS 一致，含 has_email 布尔化）
D1_FIELDS = ("user_id", "type", "name", "company", "blog", "location", "hireable",
             "bio", "twitter_username", "public_repos", "public_gists",
             "followers", "following", "created_at", "updated_at")
# D1 缓存来源（--reuse-d1 读这些文件，都是本项目已采的公开档案）
D1_CACHE_FILES = ("forker_profiles_all.jsonl", "probe_users.jsonl",
                  "user_profiles_pilot.jsonl", "user_profiles_newrepos.jsonl",
                  "issue_contrib_authors.jsonl")

# 档位组合表：新增档位只改这里
TIERS = {
    # 全量筛查档：只采 D1（1 req/人）——用于十几万 forker 的第一遍粗筛
    "screen":   ("d1",),
    "minimal":  ("d1", "d2", "d3", "d4"),
    "standard": ("d1", "d2", "d3", "d4", "d6"),
    "full":     ("d1", "d2", "d3", "d4", "d6", "d8"),
    # 演练档：只采最能分开「真人 / 批量号」的三项（档案形状 + star 宽度 + 自建结构），3 req/人。
    # 用于 50 人规模的判据验证，比 minimal 更省、比 full 更聚焦。
    "rehearsal": ("d1", "d4", "d8"),
    # 深挖档：在 full 之上把「star 了哪些仓」也留下（1 页 = 100 条，+1 req）
    "deep":     ("d1", "d2", "d3", "d4", "d4l", "d6", "d8"),
    # 行为档：只回答一个问题——「这个账号还活着吗、有没有自己的作品」。
    # d1 多半命中缓存（0 req），d2 看 90 天活动，d8 看自建仓。约 2–3 req/人。
    "behavior": ("d1", "d2", "d8"),
    # 仓库结构档：只补 D8，回答「有没有自建仓、fork 占比多少」。
    # d1 命中缓存后 ≈1 req/人（--d8-pages 1 时）。19 万人约 58 小时。
    "repos":    ("d1", "d8"),
    # ---- 单指标补充档（用户 2026-11-11 指定的后续队列）----
    # star 宽度：50 人样本里 star=0 有 12pp 判别力，是唯一还有信号的空白单元。
    "stars":    ("d1", "d4"),
    # 近 90 天事件流：注意 D13 已覆盖"全年活跃"，D2 只多出"事件类型构成"。
    "events":   ("d1", "d2"),
    # 仓库计数：⚠️ 与 D1 的 public_repos 重复，只在需要 newest_push 时才有额外价值。
    "repocount": ("d1", "d3"),
    # 所属组织：实测判别力仅 4pp，优先级最低。
    "orgs":     ("d1", "d6"),
    # watch 总数：与 star 宽度对比（只看不 star = 潜水）。
    "watch":    ("d1", "d5"),
    # star 明细（含 starred_at）：算 SAAR、看"fork 当天是否同时点星"。1 页/100 星，按需 cap。
    "starlist": ("d1", "d4l"),
}


# ============================ 基础采集单元 ============================

def c_d1(pool, login, d1_cache):
    """D1 档案。缓存命中（#14 等已采）则 0 req 直接复用。"""
    if login in d1_cache:
        return 200, dict(d1_cache[login])
    st, r = quota_get(pool, f"{API}/users/{login}")
    if st != 200:
        return st, {}
    d = r.json()
    out = {k: d.get("id" if k == "user_id" else k) for k in D1_FIELDS}
    out["has_email"] = bool(d.get("email"))   # 项目纪律：邮箱只落布尔
    return 200, out


def c_d2(pool, login, pages=1):
    """D2 近 90 天活动。只聚合事件类型/时间，不落 payload（最小化+省空间）。"""
    by_type, last_at, first_at, repos = {}, None, None, set()
    total = 0
    for page in range(1, pages + 1):
        st, r = quota_get(pool, f"{API}/users/{login}/events?per_page=100&page={page}")
        if st != 200:
            return st, {}
        evs = r.json()
        if not evs:
            break
        total += len(evs)
        for e in evs:
            t = e.get("type", "?")
            by_type[t] = by_type.get(t, 0) + 1
            at = e.get("created_at")
            if at:
                last_at = max(last_at or at, at)
                first_at = min(first_at or at, at)
            rn = e.get("repo", {}).get("name")
            if rn:
                repos.add(rn)
        if len(evs) < 100:
            break
    dev = sum(v for k, v in by_type.items()
              if k in ("PushEvent", "PullRequestEvent", "IssuesEvent",
                       "IssueCommentEvent", "CreateEvent", "ReleaseEvent"))
    return 200, {
        "events_total": total, "by_type": by_type,
        "dev_events": dev, "watch_events": by_type.get("WatchEvent", 0),
        "distinct_repos": len(repos),
        "first_at": first_at, "last_at": last_at,
    }


def c_d3(pool, login):
    """D3 公开仓库数：per_page=1 + Link 尾页 = 1 req 拿计数；
    顺带看 sort=pushed 的第 1 条（最近有 push 的仓）。"""
    st, r = quota_get(pool, f"{API}/users/{login}/repos?per_page=1&sort=pushed")
    if st != 200:
        return st, {}
    items = r.json()
    last = link_last_page(r)
    return 200, {
        "repos_total": last if last is not None else len(items),
        "newest_push": items[0].get("pushed_at") if items else None,
        "newest_push_repo": items[0].get("full_name") if items else None,
    }


def c_d9(pool, login, own_repos):
    """D9 本人在某仓的提交数 —— **当前不启用**（2026-11-09 用户决定搁置）。

    为什么搁置：成本 = Σ(每个人的自建仓数)。实测 101 人样本自建仓中位 4、均值 9.9，
    19 万人总计约 189 万请求 = 24 天，超出代理期；即便只抽查 3 个仓也要 115 小时。

    为什么"只对自建仓有意义"：fork 仓里的提交是上游作者写的，查本人的 `author` 只会得到 0
    或少量的上游贡献，不能说明这个人有没有生产力。所以调用前**必须**先用 D8 拿到
    `own_details`，并且只对 `own_count > 0` 的人调用。

    替代方案（0 额外请求）：D8 已经落盘 `own_touched` / `own_newest_push`，
    对单人小仓来说等价于"这个人有没有 push 过"。

    ``own_repos`` 传 D8 的 ``own_details`` 列表；为空时直接跳过，不发任何请求。
    """
    if not own_repos:
        return 200, {"commit_total": None, "skipped": "no_own_repo"}
    st, r = quota_get(pool, f"{API}/repos/{own_repos[0]['full_name']}/commits"
                            f"?author={login}&per_page=1")
    if st != 200:
        return st, {}
    last = link_last_page(r)
    return 200, {"commit_total": last if last is not None else 0,
                 "repo": own_repos[0]["full_name"]}


def c_d4(pool, login):
    """D4 star 总数：per_page=1 + Link 尾页。行为宽度（真人几十~几千，批量号 0-个位）。"""
    st, r = quota_get(pool, f"{API}/users/{login}/starred?per_page=1")
    if st != 200:
        return st, {}
    items = r.json()
    last = link_last_page(r)
    return 200, {"starred_total": last if last is not None else len(items)}


STAR_ACCEPT = {"Accept": "application/vnd.github.star+json"}


def c_d4l(pool, login, pages=1):
    """D4L star 仓库清单 —— 与 D4（只取计数）互补，把"star 了什么"也留下来。

    D4 用 per_page=1 拿精确总数（1 req）；这里用 100/页 拿明细（pages req）。
    `starred_at` 必须带 `Accept: application/vnd.github.star+json`，否则只有 repo 对象。
    只落精简字段（全名/时间/语言/star 数），不存整个 repo 对象。
    """
    items = []
    for page in range(1, pages + 1):
        st, r = quota_get(pool, f"{API}/users/{login}/starred?per_page=100&page={page}",
                          headers=STAR_ACCEPT)
        if st != 200:
            return st, {}
        raw = r.json()
        if not raw:
            break
        for it in raw:
            repo = it.get("repo") or {}
            items.append({"repo": repo.get("full_name"), "at": it.get("starred_at"),
                          "lang": repo.get("language"), "stars": repo.get("stargazers_count")})
        if len(raw) < 100:
            break
    return 200, {"sampled": len(items), "items": items}


def c_d6(pool, login):
    """D6 所属组织：只落 org login 列表（真人常挂 org；空号多为 []）。

    ⚠️ 必须显式传 per_page=100：GitHub 默认每页 30，>30 个 org 会被静默截断。
    """
    st, r = quota_get(pool, f"{API}/users/{login}/orgs?per_page=100")
    if st != 200:
        return st, {}
    return 200, {"orgs": [o.get("login") for o in r.json()]}


D8_MAX_PAGES = 10          # 1000 仓上限：再多也不逐个翻（38k 仓的极端账号不值得 388 个请求）


def c_d8(pool, login, pages=3, total_hint=None):
    """D8 fork vs 自建结构。**页数按真实仓数算**，不再写死 3 页。

    total_hint：D1 的 public_repos（总仓数，含 fork）。给了就按 ceil(total/100) 翻，
    上限 D8_MAX_PAGES；并回报 `repos_complete`——只有它为 1 时，
    `own_count == 0` 之类结论才成立（否则是"没采到"，不是"没有"）。
    """
    if total_hint:
        need = max(1, -(-int(total_hint) // 100))      # ceil(total/100)
        cap = max(D8_MAX_PAGES, pages)                 # 调用方显式给更大 pages 时以它为准
        pages = min(need, cap)
    fork_count = own_count = own_empty = own_touched = 0
    own_newest_push = fork_newest_push = None
    own_details = []
    fork_details = []          # fork 仓明细：fork 了谁、动没动过（0 额外请求，原来全被丢弃）
    has_profile_repo = 0       # <login>/<login> 个人主页仓：0 额外请求，原来漏判
    sampled = 0
    for page in range(1, pages + 1):
        st, r = quota_get(pool, f"{API}/users/{login}/repos?per_page=100&sort=pushed&page={page}")
        if st != 200:
            return st, {}
        items = r.json()
        if not items:
            break
        sampled += len(items)
        for it in items:
            if (it.get("name") or "").lower() == login.lower():
                has_profile_repo = 1
            kb = it.get("size", 0)
            push, crea = it.get("pushed_at"), it.get("created_at")
            if it.get("fork"):
                fork_count += 1
                if push:                          # 空仓库偶发 pushed_at=null
                    fork_newest_push = max(fork_newest_push or push, push)
                if len(fork_details) < 20:
                    fork_details.append({"repo": it.get("full_name"), "created_at": crea,
                                         "pushed_at": push, "size_kb": kb,
                                         "lang": it.get("language"),
                                         "moved": bool(push and crea and push > crea)})
            else:
                own_count += 1
                if kb <= 2:
                    own_empty += 1            # 空壳代理：≤2KB ≈ 无内容
                if push and push > crea:
                    own_touched += 1          # 建仓后还有后续 push
                if push:
                    own_newest_push = max(own_newest_push or push, push)
                if len(own_details) < 20:
                    own_details.append({"repo": it.get("full_name"), "created_at": crea,
                                        "pushed_at": push, "size_kb": kb,
                                        "stars": it.get("stargazers_count", 0)})
        if len(items) < 100:
            break
    return 200, {
        "sampled_repos": sampled, "fork_count": fork_count, "own_count": own_count,
        "own_empty": own_empty, "own_touched": own_touched,
        "own_newest_push": own_newest_push, "fork_newest_push": fork_newest_push,
        "own_details": own_details, "fork_details": fork_details,
        "has_profile_repo": has_profile_repo,
        "repos_total_hint": int(total_hint) if total_hint is not None else None,
        # 只有采全时 own_count==0 才是结论；否则是"没采到"
        "repos_complete": int(bool(total_hint is not None and sampled >= int(total_hint))),
        "pages_used": pages,
    }


# 每个单元的页数参数（--d2-pages/--d8-pages 覆盖）
PAGES_OPT = {"d2": 3, "d8": 3, "d4l": 1}


# ============================ 组合与调度 ============================

def collect_user(pool, login, tier, d1_cache):
    """按档位组合采一个人。D1 404（账号已删）→ 整人墓碑，不浪费后续请求。"""
    rec = {"login": login, "tier": tier, "http": 200,
           "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    for sec in TIERS[tier]:
        if sec == "d1":
            st, data = c_d1(pool, login, d1_cache)
        elif sec == "d2":
            st, data = c_d2(pool, login, pages=1 if tier == "minimal" else PAGES_OPT["d2"])
        elif sec == "d3":
            st, data = c_d3(pool, login)
        elif sec == "d4":
            st, data = c_d4(pool, login)
        elif sec == "d4l":
            st, data = c_d4l(pool, login, pages=PAGES_OPT["d4l"])
        elif sec == "d6":
            st, data = c_d6(pool, login)
        elif sec == "d8":
            # 页数按 D1 的 public_repos 算（d1 在档位表里排第一，一定已经采到）
            _hint = (rec.get("d1") or {}).get("public_repos")
            st, data = c_d8(pool, login, pages=PAGES_OPT["d8"], total_hint=_hint)
        rec[sec] = {"http": st, **data} if data else {"http": st}
        if st == 0:                 # 该单元 12 节点仍失败 → 整人失败墓碑，下轮重扫
            rec["http"] = 0
            return rec
        if sec == "d1" and st == 404:
            rec["http"] = 404       # 账号已删 → 永久墓碑
            return rec
    return rec


def load_d1_cache():
    """--reuse-d1：既有画像文件 → login→D1 字段缓存（省 D1 请求）。"""
    cache = {}
    for name in D1_CACHE_FILES:
        p = os.path.join(RAW, name)
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("http") == 200 and r.get("login"):
                cache[r["login"]] = {k: r.get(k) for k in D1_FIELDS}
                cache[r["login"]]["has_email"] = r.get("has_email", False)
    # 再补：任何 profile_tier_*.jsonl 里的嵌套 d1（换档位时不重复花 D1 的请求）。
    # 50 人的演练集实测：不补这一段，deep 档会白花 50 个请求。
    for p in glob.glob(os.path.join(RAW, "profile_tier_*.jsonl")):
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            d1 = r.get("d1") or {}
            lg = r.get("login")
            if d1.get("http") == 200 and lg and lg not in cache:
                cache[lg] = {k: d1.get(k) for k in D1_FIELDS}
                cache[lg]["has_email"] = d1.get("has_email", False)
    return cache


def load_logins(args):
    """名单来源：--logins 显式 > --from-file 文件 > --from-forks forker 池。"""
    if args.logins:
        return [s.strip() for s in args.logins.split(",") if s.strip()]
    if args.from_file:
        p = args.from_file if os.path.isabs(args.from_file) else os.path.join(BASE, args.from_file)
        return [l.strip() for l in open(p, encoding="utf-8") if l.strip() and not l.startswith("#")]
    if args.from_forks:
        logins, seen = [], set()
        for line in open(os.path.join(RAW, "probe_forks_all.jsonl"), encoding="utf-8"):
            try:
                u = json.loads(line).get("login")
            except json.JSONDecodeError:
                continue
            if u and u not in seen:
                seen.add(u)
                logins.append(u)
        return logins
    raise SystemExit("必须指定名单来源之一：--logins / --from-file / --from-forks")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=sorted(TIERS), default="minimal")
    ap.add_argument("--logins", help="显式 login 列表，逗号分隔（冒烟用）")
    ap.add_argument("--from-file", help="login 名单文件（每行一个，# 注释）")
    ap.add_argument("--from-forks", action="store_true", help="forker 池全量名单")
    ap.add_argument("--limit", type=int, default=0, help="只采前 N 个")
    ap.add_argument("--d2-pages", type=int, default=PAGES_OPT["d2"])
    ap.add_argument("--d8-pages", type=int, default=PAGES_OPT["d8"])
    ap.add_argument("--d4l-pages", type=int, default=PAGES_OPT["d4l"],
                    help="star 清单页数（100 条/页，deep 档用）")
    ap.add_argument("--no-reuse-d1", action="store_true", help="不复用既有 D1 缓存（强制重采）")
    ap.add_argument("--force", action="store_true",
                    help="忽略状态集合强制重采（用于修 partial：如放开 D8 页数后重跑）")
    ap.add_argument("--out-suffix", default="",
                    help="输出文件名后缀（如 _storytold__x），按仓库隔离名单的输出文件")
    args = ap.parse_args()
    PAGES_OPT["d2"], PAGES_OPT["d8"] = args.d2_pages, args.d8_pages
    PAGES_OPT["d4l"] = args.d4l_pages

    out_path = os.path.join(RAW, f"profile_tier_{args.tier}{args.out_suffix}.jsonl")
    logins = load_logins(args)

    # 断点续采：状态集合（JSONL 是事实来源，Redis 只是加速索引；Redis 不可用自动降级 SQLite）
    stem = os.path.splitext(os.path.basename(out_path))[0]
    state = CollectState(stem, args.tier, units=TIERS[args.tier])
    state.warm_from_jsonl(out_path)
    terminal = set() if args.force else state.terminal_set()   # done ∪ gone ∪ partial
    todo = [u for u in logins if (u or "").lower() not in terminal]
    st = state.stats()
    d1_cache = {} if args.no_reuse_d1 else load_d1_cache()
    print(f"档位 {args.tier} = {'+'.join(TIERS[args.tier])} | 名单 {len(logins)} | "
          f"done={st['done']} partial={st['partial']} gone={st['gone']} | 待采 {len(todo)} | "
          f"状态后端 {st['backend']} | D1 缓存 {len(d1_cache)} 条", flush=True)
    if args.limit:
        todo = todo[:args.limit]
        print(f"限量模式：只采 {len(todo)} 个", flush=True)

    pool = NodePool()
    print(f"节点池 {len(pool.nodes)} 个（含 DIRECT）| 起始 {pool.current()}", flush=True)

    t0, n_ok, n_404, n_err = time.time(), 0, 0, 0
    out = open(out_path, "a", encoding="utf-8")
    try:
        for i, login in enumerate(todo, 1):
            rec = collect_user(pool, login, args.tier, d1_cache)
            if rec["http"] == 200:
                n_ok += 1
            elif rec["http"] == 404:
                n_404 += 1
            else:
                n_err += 1
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            state.mark(login, rec)                 # 先落 JSONL，再写索引（崩溃可重建）
            if i % 50 == 0:
                rate = i / (time.time() - t0) * 3600
                print(f"[{i}/{len(todo)}] ok={n_ok} 404={n_404} err={n_err} | "
                      f"{rate:.0f} 人/h | 节点 {pool.current()} | 切换 {pool.stats['switch']}", flush=True)
            time.sleep(0.8)
    except KeyboardInterrupt:
        print(f"\n手动中断：ok={n_ok} 404={n_404}，断点续采可恢复", flush=True)
    finally:
        out.close()
        state.close()
        pool.restore()
        print(f"收尾: ok={n_ok} 404={n_404} err={n_err} | 耗时 {(time.time()-t0)/60:.1f} 分钟 "
              f"→ {out_path}", flush=True)


if __name__ == "__main__":
    main()
