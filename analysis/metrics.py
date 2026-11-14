"""指标计算 —— 从 SQLite 读出**可审计**的数字。

设计原则：每个函数返回的字典里，除了 `value` 之外必须带上足够的过程量，
使得报告里的每一个数字都能被独立复算。**不接受「算出来就是这样」的中间结论**
——本项目在纯算法攻坚阶段已经吃过一次亏（自洽回环骗过了自己）。

━━━ 三个容易算错的地方（都在本文件里显式处理）━━━

1. **Poisson 零模型的底数**
   同秒碰撞的期望值不能用「活跃秒数 T」当分母 —— T 按定义就是
   「至少 1 个事件的秒」，拿它当底会系统性低估期望碰撞数。
   正确底数是**整个时间窗 W**（首事件 → 末事件）。见 `clustering_metrics`。

2. **ClickHouse 是抽样，不是全量**
   所有基于 `star_events` 的比例都只能当「样本内相对比例」用。
   任何绝对计数都必须来自 `repos` / `star_history`。见 `ch_coverage`。

3. **`star_history.stars` 是当日增量，`star_history_ext.stars_cum` 是累计值**
   两者语义相反，混用会让曲线整个翻掉。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from crawler.config import iso_to_ts, resolve_canonical

# ClickHouse 各月抽样率（实测，见 crawler/sources/clickhouse.py）。
# 低于 MIN_USABLE_COVERAGE 的月份不出结论 —— 报告里显式标注「数据不足」。
MIN_USABLE_COVERAGE = 0.05


# ══ 基础读取 ═════════════════════════════════════════════
def repo_row(conn, full_name: str) -> Optional[dict]:
    canon = resolve_canonical(conn, full_name)
    r = conn.execute("SELECT * FROM repos WHERE full_name=?", (canon,)).fetchone()
    return dict(r) if r else None


def star_daily(conn, full_name: str) -> list:
    """[(day, stars)] 按日期升序。stars 是**当日新增**。"""
    canon = resolve_canonical(conn, full_name)
    return [(r["day"], r["stars"]) for r in conn.execute(
        "SELECT day, stars FROM star_history WHERE canonical_full_name=? ORDER BY day",
        (canon,))]


def star_cum_ext(conn, full_name: str) -> list:
    """star-history.com 反解出的**累计**曲线 [(day, stars_cum)]。"""
    canon = resolve_canonical(conn, full_name)
    return [(r["day"], r["stars_cum"]) for r in conn.execute(
        "SELECT day, stars_cum FROM star_history_ext WHERE canonical_full_name=? "
        "ORDER BY day", (canon,))]


def has_users(conn) -> int:
    """画像表里成功采到的账号数（0 表示还没跑 users 阶段）。"""
    return conn.execute("SELECT COUNT(*) n FROM users WHERE http_status=200").fetchone()["n"]


# ══ 信号 #1：增长曲线形态 ═══════════════════════════════
def growth_metrics(conn, full_name: str) -> dict:
    """峰值、集中度、变点、衰减半衰期。

    有机增长 vs 刷量的形态差别：
        有机 —— 媒体事件驱动，**单尖峰 + 平滑衰减**，尖峰日附近有可解释的事件
        刷量 —— **阶梯状**（某日突然平台化）、**多脉冲**、或无事件对应的孤立尖峰
    """
    rows = star_daily(conn, full_name)
    if not rows:
        return {"n": 0}

    days = [d for d, _ in rows]
    v = np.array([s for _, s in rows], dtype=float)
    n = len(v)
    total = float(v.sum())
    if total <= 0:
        return {"n": n, "total": 0.0}

    peak_i = int(np.argmax(v))
    mean, std = float(v.mean()), float(v.std())
    z = (v - mean) / std if std > 0 else np.zeros(n)

    # 集中度：Top-1 / Top-7 日占全周期新增的比例
    srt = np.sort(v)[::-1]
    top1_pct = float(srt[0] / total * 100)
    top7_pct = float(srt[:7].sum() / total * 100)

    # 变点检测（CUSUM 二分段）：在「均值发生持久性位移」处切分。
    # 用二分段而非 PELT 是有意的 —— 47 周的数据量下，多分段极易过拟合噪声。
    cp_i, cp_gain = _best_split(v)

    # 衰减半衰期：从峰值日起，增量回落到峰值一半所需天数
    half_life = None
    half = v[peak_i] / 2
    for j in range(peak_i, n):
        if v[j] <= half:
            half_life = j - peak_i
            break

    # 阶梯检测：连续 k 天的增量中位数显著高于全期中位数（平台化特征）
    plateau = _plateau_days(v)

    return {
        "n": n,
        "total": total,
        "first_day": days[0], "last_day": days[-1],
        "mean": mean, "std": std, "median": float(np.median(v)),
        "peak_day": days[peak_i], "peak_value": float(v[peak_i]),
        "peak_z": float(z[peak_i]),
        "peak_pct_of_total": top1_pct,
        "top7_pct_of_total": top7_pct,
        "z_gt3_days": int((z > 3).sum()),
        "changepoint_day": days[cp_i] if cp_i is not None else None,
        "changepoint_gain": cp_gain,
        "half_life_days": half_life,
        "plateau_days": plateau,
        "daily": list(zip(days, [int(x) for x in v])),
    }


def _best_split(v: np.ndarray):
    """二分段 CUSUM：找使「段内平方和」最小的切分点。

    返回 (切分下标, 相对增益)。增益 = 1 - SS_best/SS_total，
    接近 0 说明序列本身没有结构性位移。
    """
    n = len(v)
    if n < 8:
        return None, None
    cs = np.cumsum(v)
    tot = cs[-1]
    ss_total = float(((v - v.mean()) ** 2).sum())
    if ss_total <= 0:
        return None, None

    best_i, best_ss = None, float("inf")
    for i in range(3, n - 3):                      # 两侧各留 3 天，避免边界假变点
        n1, n2 = i, n - i
        s1, s2 = cs[i - 1], tot - cs[i - 1]
        ss = (float(((v[:i] - s1 / n1) ** 2).sum())
              + float(((v[i:] - s2 / n2) ** 2).sum()))
        if ss < best_ss:
            best_ss, best_i = ss, i
    return best_i, 1 - best_ss / ss_total


def _plateau_days(v: np.ndarray, k: int = 7, mult: float = 2.0) -> list:
    """滑动窗口找「持续高位」的平台段 —— 阶梯状刷量的核心特征。

    判据：连续 k 天的**中位数** > 全期中位数 × mult。
    单日尖峰不算（那是媒体事件）；连续高位才算。
    """
    n = len(v)
    if n < k:
        return []
    med = float(np.median(v))
    if med <= 0:
        return []
    out, i = [], 0
    while i <= n - k:
        if float(np.median(v[i:i + k])) > med * mult:
            j = i
            while j <= n - k and float(np.median(v[j:j + k])) > med * mult:
                j += 1
            out.append({"start_i": i, "end_i": j + k - 1, "len": j + k - i,
                        "median": float(np.median(v[i:j + k]))})
            i = j + k
        else:
            i += 1
    return out


# ══ 信号 #2：周内分布 ═══════════════════════════════════
def weekday_metrics(conn, full_name: str) -> dict:
    """按星期几分桶。

    真实开发者行为有工作日/周末节律。**批量刷量常呈均匀分布**
    ——但注意：均匀分布也可能来自「定时任务每周跑一次」这类正常运维行为，
    所以这条信号只做辅助，不能单独定罪。
    """
    rows = star_daily(conn, full_name)
    if not rows:
        return {"n": 0}
    buckets = [0] * 7
    for d, s in rows:
        try:
            wd = datetime.strptime(d, "%Y-%m-%d").weekday()   # 0=周一
        except ValueError:
            continue
        buckets[wd] += s
    tot = sum(buckets)
    if not tot:
        return {"n": 0}
    shares = [b / tot * 100 for b in buckets]
    # 均匀度：与完全均匀(1/7)的偏离。0 = 完全均匀
    unif = float(np.std(shares))
    return {
        "n": len(rows), "total": tot,
        "buckets": buckets,                      # 周一..周日
        "shares_pct": shares,
        "weekend_pct": (buckets[5] + buckets[6]) / tot * 100,
        "std_pct": unif,
        "max_share_pct": max(shares),
        "min_share_pct": min(shares),
    }


# ══ 信号 #5：时间聚集（同秒碰撞）═════════════════════════
def clustering_metrics(conn, full_name: str) -> dict:
    """同秒 ≥2 人的秒数占比 + **Poisson 零模型校正**。

    ━━━ ⚠️ 底数不能取错（踩过）━━━
    零模型的底数是**整个时间窗 W**，不是「活跃秒数 T」。
    T 按定义就是「至少 1 个事件的秒」—— 拿它当分母等于假设
    「每个活跃秒都恰好装 1 个事件」，会系统性低估期望碰撞数，
    算出「比随机还少」的荒谬结论（实测 λ=1.03 → 0.11×，方向都反了）。

    正确算法：
        W  = last_ts - first_ts          （秒）
        λ  = n_events / W                （每秒钟的平均事件数）
        期望碰撞秒 = W · (1 − e^−λ − λ·e^−λ)
    """
    canon = resolve_canonical(conn, full_name)
    agg = conn.execute("""
        SELECT COUNT(*) AS n_sec,
               COALESCE(SUM(n_actors),0) AS n_ev,
               COALESCE(SUM(CASE WHEN n_actors>=2 THEN 1 ELSE 0 END),0) AS n_col,
               COALESCE(MAX(n_actors),0) AS max_batch,
               MIN(starred_ts) AS t0, MAX(starred_ts) AS t1
        FROM star_seconds WHERE canonical_full_name=?
    """, (canon,)).fetchone()

    n_sec = agg["n_sec"] or 0
    if not n_sec:
        return {"n": 0}

    n_ev = agg["n_ev"]
    n_col = agg["n_col"]
    W = max(1, (agg["t1"] or 0) - (agg["t0"] or 0))

    lam = n_ev / W
    # P(X>=2) for X~Poisson(lam)
    p_col = 1.0 - np.exp(-lam) - lam * np.exp(-lam)
    exp_col = W * p_col

    # 批次规模分布（max 只有 4 → 是「大量成对/成三」的分散式刷量，不是粗暴大批量）
    dist = [dict(r) for r in conn.execute("""
        SELECT n_actors, COUNT(*) c FROM star_seconds
        WHERE canonical_full_name=? GROUP BY n_actors ORDER BY n_actors
    """, (canon,))]

    return {
        "n": n_sec,
        "n_events": n_ev,
        "n_collision_seconds": n_col,
        "collision_pct": n_col / n_sec * 100,
        "max_batch": agg["max_batch"],
        "window_seconds": W,
        "window_days": W / 86400,
        "lambda_per_sec": lam,
        "expected_collision_seconds": float(exp_col),
        "enrichment": (n_col / exp_col) if exp_col > 0 else None,
        "batch_dist": dist,
        "first_ts": agg["t0"], "last_ts": agg["t1"],
    }


def top_collision_seconds(conn, full_name: str, limit: int = 30) -> list:
    """碰撞最密集的秒 —— 报告附录用，人工核对是否真的是机器人。"""
    canon = resolve_canonical(conn, full_name)
    return [dict(r) for r in conn.execute("""
        SELECT starred_ts, n_actors, actors_json FROM star_seconds
        WHERE canonical_full_name=? AND n_actors>=2
        ORDER BY n_actors DESC, starred_ts LIMIT ?
    """, (canon, limit))]


# ══ 信号 #5b：订阅者 / 星数比（头号结构性信号）══════════
def subscriber_ratio(conn, full_name: str) -> dict:
    """`subscribers_count / stargazers_count`。

    ★ 用 subscribers_count，**不是 watchers_count** —— 后者是历史遗留字段，
      在 GitHub API 里恒等于 stargazers_count，拿它算比永远是 1.0。

    刷星服务通常不刷 Watch（成本高、不直观），所以真实社区订阅会
    被稀释掉。对照组间此指标高度稳定，故判别力强、成本极低。
    """
    r = repo_row(conn, full_name)
    if not r or not r.get("stars"):
        return {"n": 0}
    return {
        "n": 1,
        "stars": r["stars"], "subscribers": r["subscribers"],
        "ratio_pct": (r["subscribers"] or 0) / r["stars"] * 100,
        "forks": r["forks"],
        "fork_star_pct": (r["forks"] or 0) / r["stars"] * 100,
        "open_issues": r["open_issues"],
        "age_days": _age_days(r.get("created_at")),
    }


def _age_days(created_at: Optional[str]) -> Optional[float]:
    if not created_at:
        return None
    ts = iso_to_ts(created_at)
    if not ts:
        return None
    return (datetime.now(timezone.utc).timestamp() - ts) / 86400


# ══ 信号 #8：贡献者集中度 ═══════════════════════════════
def contributor_metrics(conn, full_name: str) -> dict:
    """基尼系数 / bus factor / Top-N 占比。

    ★ 单人主导**不是**造假信号 —— 它反映开发模式（个人项目 vs 公司项目）。
      必须和对照组同口径比较才有意义。
    """
    canon = resolve_canonical(conn, full_name)
    rows = conn.execute(
        "SELECT contributor_key, login, contributions FROM contributors "
        "WHERE canonical_full_name=? ORDER BY contributions DESC", (canon,)).fetchall()
    if not rows:
        return {"n": 0}

    counts = np.array([r["contributions"] for r in rows], dtype=float)
    n = len(counts)
    total = float(counts.sum())
    if total <= 0:
        return {"n": n, "total": 0.0}

    srt = np.sort(counts)
    cum = float((np.arange(1, n + 1) * srt).sum())
    gini = (2 * cum) / (n * total) - (n + 1) / n

    acc, bus = 0.0, 0
    for c in counts:                       # counts 已按降序
        acc += c
        bus += 1
        if acc >= total * 0.5:
            break

    return {
        "n": n, "total": total,
        "gini": float(gini),
        "bus_factor_50pct": bus,
        "top1_pct": float(counts[0] / total * 100),
        "top5_pct": float(counts[:5].sum() / total * 100),
        "top10_pct": float(counts[:10].sum() / total * 100),
        "single_commit_pct": float((counts == 1).sum() / n * 100),
        "anon_pct": float(sum(1 for r in rows if not r["login"]) / n * 100),
        "top": [{"login": r["login"] or r["contributor_key"],
                 "contributions": r["contributions"]} for r in rows[:15]],
        # ★ 完整分布的采样点（最多 60 个）。图表要画的是**全部 2,898 人**的
        #   累计曲线，不是 top-15 —— 只画 top-15 会让读者以为前 15 名
        #   就占了全部，那是把结论画歪。
        "cumulative": _cumulative_points(counts, total),
    }


def _cumulative_points(counts: np.ndarray, total: float, max_pts: int = 60) -> list:
    """[(rank, cum_pct)] —— 在完整降序序列上等距取点。"""
    n = len(counts)
    if n == 0 or total <= 0:
        return []
    cum = np.cumsum(counts) / total * 100
    idx = sorted({0, n - 1} | {int(round(i * (n - 1) / (max_pts - 1)))
                               for i in range(max_pts)})
    return [[int(i) + 1, float(cum[i])] for i in idx]


# ══ 信号 #9：改名史 ═════════════════════════════════════
def rename_metrics(conn, full_name: str) -> dict:
    """改名链本身**不是**造假证据，但会干扰朴素统计。

    同一个项目被计为三个仓库 → 第三方榜单/趋势工具会把它的星数拆散或重复计。
    报告必须说明这一点，否则读者会拿某个别名下的数字当全量。
    """
    canon = resolve_canonical(conn, full_name)
    aliases = [dict(r) for r in conn.execute(
        "SELECT * FROM repo_aliases WHERE canonical_full_name=? ORDER BY name_from", (canon,))]

    by_name = [dict(r) for r in conn.execute("""
        SELECT repo_name_observed AS name, COUNT(*) n,
               MIN(starred_at) t0, MAX(starred_at) t1
        FROM star_events WHERE canonical_full_name=?
        GROUP BY repo_name_observed ORDER BY t0
    """, (canon,))]

    return {"aliases": aliases, "by_name": by_name,
            "n_aliases": len(aliases),
            "n_events": sum(b["n"] for b in by_name)}


# ══ 信号 #10：生态协同 ══════════════════════════════════
def leaderboard_metrics(conn, snapshot_at: Optional[str] = None) -> dict:
    """趋势榜快照里的 owner 聚集度。

    **上榜 ≠ 造假** —— 一个 owner 多个仓库同时爆红，可能是产品矩阵
    （正常），也可能是协同刷量。这条信号的正确用法是**提示人工核查**，
    而不是自动定罪。
    """
    if snapshot_at is None:
        r = conn.execute("SELECT MAX(snapshot_at) s FROM starhistory_leaderboard").fetchone()
        snapshot_at = r["s"] if r else None
    if not snapshot_at:
        return {"n": 0}

    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM starhistory_leaderboard WHERE snapshot_at=? ORDER BY rank",
        (snapshot_at,))]
    if not rows:
        return {"n": 0}

    by_owner = {}
    for r in rows:
        o = r.get("owner") or (r["repo"].split("/")[0] if "/" in r["repo"] else "?")
        d = by_owner.setdefault(o, {"owner": o, "n": 0, "repos": [], "gain": 0})
        d["n"] += 1
        d["repos"].append(r["repo"])
        d["gain"] += r.get("gain_exact") or 0

    clusters = sorted([d for d in by_owner.values() if d["n"] >= 2],
                      key=lambda d: -d["gain"])
    return {
        "n": len(rows), "snapshot_at": snapshot_at,
        "n_owners": len(by_owner),
        "clusters": clusters,
        "cluster_seats": sum(c["n"] for c in clusters),
        "cluster_gain": sum(c["gain"] for c in clusters),
        "total_gain": sum(r.get("gain_exact") or 0 for r in rows),
        "board": rows,
    }


# ══ 数据边界：ClickHouse 覆盖率 ═════════════════════════
def ch_coverage(conn, full_name: str) -> dict:
    """逐月抽样率 = CH 事件数 ÷ API 真实星数。

    ★ 这是**报告的数据边界章节**的核心。
      CH 是时间衰减抽样，绝不用它做绝对计数；
      覆盖率 < 5% 的月份不出结论。
    """
    canon = resolve_canonical(conn, full_name)
    ev = {r["m"]: r["n"] for r in conn.execute("""
        SELECT substr(starred_at,1,7) m, COUNT(*) n FROM star_events
        WHERE canonical_full_name=? GROUP BY m ORDER BY m
    """, (canon,))}

    # ★ 按月**求和**，不是取最后一天。
    #   写成 {d[:7]: s for d, s in star_daily(...)} 会静默地只保留每月最后一天，
    #   算出 385%/1223% 这种荒谬的「覆盖率」—— 而它看起来像个正常数字，
    #   会一路混进报告。这类静默错误比抛异常危险得多。
    api = {}
    for d, s in star_daily(conn, canon):
        api[d[:7]] = api.get(d[:7], 0) + s

    out = []
    for m in sorted(set(ev) | set(api)):
        e, a = ev.get(m, 0), api.get(m, 0)
        cov = (e / a) if a else None
        out.append({
            "month": m, "ch_events": e, "api_stars": a,
            "coverage": cov,
            "usable": bool(cov is not None and cov >= MIN_USABLE_COVERAGE),
        })
    tot_ev = sum(ev.values())
    tot_api = sum(api.values())
    return {
        "n": len(out), "months": out,
        "total_ch": tot_ev, "total_api": tot_api,
        "overall_coverage": (tot_ev / tot_api) if tot_api else None,
        "usable_months": [x["month"] for x in out if x["usable"]],
    }


# ══ 聚合入口 ════════════════════════════════════════════
def all_metrics(conn, full_name: str) -> dict:
    """一次性算出所有指标，供 signals / charts / report 共用。"""
    canon = resolve_canonical(conn, full_name)
    return {
        "full_name": canon,
        "repo": repo_row(conn, canon),
        "growth": growth_metrics(conn, canon),
        "weekday": weekday_metrics(conn, canon),
        "clustering": clustering_metrics(conn, canon),
        "subscriber": subscriber_ratio(conn, canon),
        "contributors": contributor_metrics(conn, canon),
        "rename": rename_metrics(conn, canon),
        "coverage": ch_coverage(conn, canon),
    }


# ══ 调试入口 ════════════════════════════════════════════
def main(argv=None) -> int:
    """`py -m analysis.metrics [repo] [--key growth]` —— 看原始指标。

    加这个入口是因为报告里每个数字都要能独立复算，而在此之前复算的唯一途径
    是手写一段 `python -c`。那段命令在 Windows 上还会因为仓库描述里的 emoji
    触发 GBK 编码错误 —— 一个「用来验证数据」的工具不该自己先崩。
    """
    import argparse
    import json
    import sys

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="analysis.metrics",
                                 description="打印原始指标（供人工复算报告数字）")
    ap.add_argument("repo", nargs="?", default=None, help="默认取 targets.json 的目标")
    ap.add_argument("--key", default=None,
                    help="只看某一组：repo/growth/weekday/clustering/subscriber/"
                         "contributors/rename/coverage")
    args = ap.parse_args(argv)

    from crawler.config import load_targets
    from crawler.storage import init_db

    repo = args.repo or load_targets()["target"]["full_name"]
    conn = init_db()
    try:
        m = all_metrics(conn, repo)
    finally:
        conn.close()

    if args.key:
        if args.key not in m:
            print(f"未知 key: {args.key}\n可选: {list(m)}")
            return 2
        m = {args.key: m[args.key]}
    print(json.dumps(m, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

