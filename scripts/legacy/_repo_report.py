# -*- coding: utf-8 -*-
"""单仓完整分析报告生成器：聚合已采集数据 → 中文 Markdown 报告。

输入一个仓库名，自动拼装以下数据源（缺哪个就如实标注「未采集/数据不足」，
绝不静默扣减）：
  repo_metrics_{key}.jsonl        R1-R8 指标（_repo_detail.py）
  forks_all_{key}.jsonl          fork 名单全量（_small_repo_collect.py）
  star_history_{key}.jsonl       周桶 star 曲线
  ch_star_events/{key}.jsonl     CH star 身份（衰减抽样）
  ch_star_seconds_{key}.jsonl    CH 同秒聚簇
  profile_tier_full_{key}.jsonl  fork owner 画像 full 档（_profile_tiers.py --out-suffix）
  其余 repo_metrics_*.jsonl      对照组（glob 自动发现）
  forker_profiles_all.jsonl      openclaw forker 池 D1（画像基线参照，可选）

用法：
  py _repo_report.py --repo storytold/craft-repo-status-app
  py _repo_report.py --repo ... --note "生态位备注文字"   # 进报告第 §三

输出：data/out/report_{key}.md

措辞纪律：只输出「异常信号」，不下「造假结论」；对照组 < 2 个 → uncalibrated；
CH 覆盖率 < 5% → 该节数据不足，不出身份级结论。
"""
import argparse
import datetime
import glob
import json
import os
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")
os.makedirs(OUT, exist_ok=True)

MIN_CH_COVERAGE = 5.0     # 铁律：CH 抽样率 < 5% 的身份级数据不出结论
TZ = datetime.timezone.utc


# ── 小工具 ──────────────────────────────────────────
def _load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _med(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _pctl(vals, p):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    i = min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))
    return v[i]


def _share(vals, cond):
    v = [x for x in vals if x is not None]
    return (sum(1 for x in v if cond(x)) / len(v) * 100) if v else None


def _fmt(x, nd=1, suffix=""):
    if x is None:
        return "—"
    return f"{x:.{nd}f}{suffix}"


def _fi(x):
    """None 安全的千分位整数（meta 字段缺值时不断言崩溃）。"""
    return f"{x:,}" if isinstance(x, (int, float)) else "—"


def _mtime(path):
    """§二 数据新鲜度：优先行内 collected_at，缺则退回文件 mtime。"""
    if not os.path.exists(path):
        return "—"
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path), tz=TZ).strftime("%m-%d %H:%M")
    except OSError:
        return "—"


def _iso_date(ts_or_iso):
    try:
        return datetime.datetime.fromisoformat(str(ts_or_iso).replace("Z", "+00:00")).astimezone(TZ).date().isoformat()
    except (ValueError, TypeError):
        return str(ts_or_iso)[:10] if ts_or_iso else "—"


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None


# ── 数据源装载 ────────────────────────────────────────
def load_all(repo, suffix):
    key = repo.replace("/", "__")
    files = {
        "metrics": os.path.join(RAW, f"repo_metrics_{key}.jsonl"),
        "forks": os.path.join(RAW, f"forks_all_{key}.jsonl"),
        "history": os.path.join(RAW, f"star_history_{key}.jsonl"),
        "ch": os.path.join(RAW, "ch_star_events", f"{key}.jsonl"),
        "ch_sec": os.path.join(RAW, f"ch_star_seconds_{key}.jsonl"),
        "tiers": os.path.join(RAW, f"profile_tier_full{suffix or '_' + key}.jsonl"),
        "openclaw_d1": os.path.join(RAW, "forker_profiles_all.jsonl"),
    }
    d = {}
    for name, path in files.items():
        rows = _load_jsonl(path)
        d[name] = {"rows": rows, "path": path, "exists": os.path.exists(path)}
    # 对照组：其它 repo_metrics 文件（一次读盘，meta/contrib/search 三用）
    controls = {}
    for path in glob.glob(os.path.join(RAW, "repo_metrics_*.jsonl")):
        name = os.path.basename(path)[len("repo_metrics_"):-len(".jsonl")]
        if name == key:
            continue
        rows = _load_jsonl(path)
        meta = next((r for r in rows if r.get("kind") == "meta"), None)
        contrib = next((r for r in rows if r.get("kind") == "contributors"), None)
        search = {r.get("metric"): r.get("total") for r in rows if r.get("kind") == "search"}
        if meta:
            controls[name] = {"meta": meta, "contrib": contrib, "search": search,
                              "path": path}
    d["controls"] = controls
    return key, d


def ch_actor_rows(ch_rows):
    """CH 导出 → 统一的 (actor, ts) 行（兼容分页包装 / 裸 dict 行 / 裸数组行）。"""

    def _push(r, out):
        if isinstance(r, dict) and r.get("actor_login"):
            out.append((r.get("actor_login"), int(r.get("starred_ts") or 0)))
        elif isinstance(r, list) and len(r) >= 2 and isinstance(r[0], str):
            out.append((r[0], int(r[1])))

    rows = []
    for rec in ch_rows:
        if isinstance(rec, dict):
            body = rec.get("body")
            if isinstance(body, list):
                for r in body:
                    _push(r, rows)
            else:
                _push(rec, rows)          # 顶层裸 dict 行（无 body 包装）
        elif isinstance(rec, list):
            # 顶层裸数组：单行 [login, ts] 或行列表 [[login, ts], ...]
            items = [rec] if (len(rec) >= 2 and isinstance(rec[0], str)) else rec
            for r in items:
                _push(r, rows)
    return rows


# ── 报告段落 ──────────────────────────────────────────
def sec_borderline(d, repo, stars_now):
    """§二 数据来源与边界（含每源的采集新鲜度，mtime 兜底）。"""
    lines = ["| 数据源 | 文件 | 规模 | 采集于 | 说明 |", "|---|---|---|---|---|"]
    src = d["metrics"]
    if src["exists"]:
        lines.append(f"| R1-R8 指标 | `repo_metrics_*` | {len(src['rows'])} 批 | {_mtime(src['path'])} | "
                     "meta/search/issues/commits/contributors/releases/stats/events |")
    else:
        lines.append("| R1-R8 指标 | 未采集 | — | — | `_repo_detail.py --repos` 补 |")
    fk = d["forks"]
    if fk["exists"]:
        lines.append(f"| fork 名单（全量） | `forks_all_*` | {len(fk['rows'])} 条 | {_mtime(fk['path'])} | "
                     "sort=oldest 全页翻完，断点续采 |")
    else:
        lines.append("| fork 名单 | 未采集 | — | — | `_small_repo_collect.py --only forks` |")
    hs = d["history"]
    n_buckets = sum(r.get("bucket_count", 0) for r in hs["rows"])
    if hs["exists"]:
        lines.append(f"| star 周曲线 | `star_history_*` | {n_buckets} 桶 | {_mtime(hs['path'])} | "
                     "`/stargazers/history` 翻到底 |")
    else:
        lines.append("| star 曲线 | 未采集 | — | — | `--only history` |")
    ch = d["ch"]
    actors = ch_actor_rows(ch["rows"])
    cov = (len({a for a, _ in actors}) / stars_now * 100) if (stars_now and ch["exists"]) else 0
    if ch["exists"]:
        lines.append(f"| CH star 身份 | `ch_star_events/*` | {len(actors)} 事件 | {_mtime(ch['path'])} | "
                     f"衰减抽样，覆盖率 {cov:.1f}%（{'⚠️ <5%：身份级数据不出结论' if cov < MIN_CH_COVERAGE else '≥5%，可做样本内相对统计'}） |")
    else:
        lines.append("| CH star 身份 | 未采集 | — | — | `--only ch` |")
    tp = d["tiers"]
    if tp["exists"]:
        n_ok = sum(1 for r in tp["rows"] if r.get("http") == 200)
        n_404 = sum(1 for r in tp["rows"] if r.get("http") == 404)
        lines.append(f"| fork owner 画像 | `profile_tier_full_*` | {n_ok} 成功 / {n_404} 删号(404) | "
                     f"{_mtime(tp['path'])} | full 档 D1-D8 |")
    else:
        lines.append("| fork owner 画像 | 未采集 | — | — | `_profile_tiers.py --from-file fork_owners_*` |")
    coop = d["openclaw_d1"]
    if coop["exists"]:
        lines.append(f"| openclaw forker 池 D1 | `forker_profiles_all.jsonl` | {len(coop['rows'])} 人 | "
                     f"{_mtime(coop['path'])} | 画像基线参照（采集中时点） |")
    lines.append("")
    lines.append(f"> **star 身份通道说明**：GitHub 自 2026-06-30 起 `/stargazers` 收归 admin-only（token 路线已实测死亡），"
                 f"ClickHouse `github_events` 是唯一身份来源但为衰减抽样——本报告身份级结论均受上表覆盖率约束。")
    lines.append("")
    return "\n".join(lines), cov


def sec_repo_profile(d, repo, note):
    """§三 仓库画像。"""
    if not d["metrics"]["exists"]:
        return "**未采集 R1-R8 指标，本节缺数据。**\n"
    m = next((r for r in d["metrics"]["rows"] if r.get("kind") == "meta"), None)
    if not m:
        return "**meta 行缺失。**\n"
    search = {r.get("metric"): r.get("total") for r in d["metrics"]["rows"] if r.get("kind") == "search"}
    ct = next((r for r in d["metrics"]["rows"] if r.get("kind") == "commits_total"), {})
    cs = next((r for r in d["metrics"]["rows"] if r.get("kind") == "commits_sample"), {})
    contrib = next((r for r in d["metrics"]["rows"] if r.get("kind") == "contributors"), None)
    ev = next((r for r in d["metrics"]["rows"] if r.get("kind") == "events_recent"), {})

    stars, forks, subs = m.get("stars") or 0, m.get("forks") or 0, m.get("subscribers") or 0
    lines = ["| 指标 | 值 |", "|---|---|",
             f"| 星 / fork / watch | **{stars:,}** / **{forks:,}** / {subs:,} |",
             f"| **fork ÷ star** | **{(forks / max(stars, 1) * 100):.1f}%** |",
             f"| watch ÷ star | {(subs / max(stars, 1) * 100):.2f}% |",
             f"| open issues / PR | {m.get('open_issues') if isinstance(m.get('open_issues'), int) else '—'} |",
             f"| 建仓 → 最近 push | `{(m.get('created_at') or '')[:10]}` → `{(m.get('pushed_at') or '')[:10]}` |",
             f"| 语言 / 规模 | {m.get('language') or '—'} / {_fi(m.get('size_kb'))} KB |",
             f"| issue 总数（search） | {search.get('issue_count') if isinstance(search.get('issue_count'), int) else '—'} |",
             f"| commits | ~{(ct.get('last_page') or 0) * 100} |"]
    if contrib and contrib.get("top100"):
        top = contrib["top100"]
        csum = sum(c.get("contributions") or 0 for c in top)
        top1 = top[0]
        lines.append(f"| 贡献者集中度 | Top1 `{top1.get('login')}` 占 top100 的 **{top1.get('contributions', 0) / max(csum, 1) * 100:.0f}%** |")
    if ev.get("types"):
        lines.append(f"| 近期事件类型分布 | {ev['types']} |")
    if note:
        lines.append("")
        lines.append(f"> 生态位备注（人工提供）：{note}")
    lines.append("")
    return "\n".join(lines)


def sec_curve(d):
    """§四 star 周曲线。"""
    if not d["history"]["exists"]:
        return "**未采集 star 曲线。**\n"
    buckets = []
    for rec in d["history"]["rows"]:
        for b in rec.get("buckets") or []:
            buckets.append(b)
    if not buckets:
        seen_rows = d["history"]["rows"]
        if not any(r.get("buckets") for r in seen_rows):
            return ("**star 曲线文件是旧版摘要格式（无周桶明细）**——删掉重跑 "
                    "`_small_repo_collect.py --only history` 后可出表。\n")
    buckets.sort(key=lambda b: b.get("week") or 0)
    total = sum(b.get("total", 0) for b in buckets)
    if not total:
        return "**曲线为空。**\n"
    lines = ["| 周（起始日） | 新增★ | 占比 |", "|---|---|---|"]
    for b in buckets:
        wk = datetime.datetime.fromtimestamp(b.get("week") or 0, tz=TZ).date().isoformat()
        lines.append(f"| {wk} | {b.get('total', 0)} | {b.get('total', 0) / total * 100:.1f}% |")
    peak = max(buckets, key=lambda b: b.get("total", 0))
    peak_wk = datetime.datetime.fromtimestamp(peak.get("week") or 0, tz=TZ).date().isoformat()
    top2 = sum(sorted((b.get("total", 0) for b in buckets), reverse=True)[:2])
    lines += ["",
              f"- 全周期 {total}★ 分布在 {len(buckets)} 周；峰值周 `{peak_wk}`（{peak.get('total', 0)}★）",
              f"- **前两周占比 {top2 / total * 100:.0f}%**；最后一桶 {buckets[-1].get('total', 0)}★",
              ""]
    return "\n".join(lines)


def sec_forks(d):
    """§五 fork 结构（全量）。"""
    if not d["forks"]["exists"]:
        return "**未采集 fork 名单。**\n"
    rows = d["forks"]["rows"]
    if not rows:
        return "**fork 名单为空。**\n"
    by_day = Counter((r.get("fork_created_at") or "")[:10] for r in rows)
    peak_day, peak_n = by_day.most_common(1)[0]
    span_days = len(by_day)

    # pushed_at 语义注意：fork 的 pushed_at 会继承上游最后提交时间，不能当「fork 后有活动」
    # 的证据（README 已记录此坑）。lag 仅作参考。
    lags = []
    for r in rows:
        c, p = _parse_iso(r.get("fork_created_at")), _parse_iso(r.get("fork_pushed_at"))
        if c and p:
            lags.append((p - c).total_seconds() / 3600)

    lines = [f"全量 fork **{len(rows)}** 条（按日分布跨 {span_days} 天）：", "",
             "| 指标 | 值 |", "|---|---|",
             f"| 峰值日 | `{peak_day}`（{peak_n} 条，占 {peak_n / len(rows) * 100:.1f}%） |",
             f"| 集中度：前 3 日合计 | {sum(n for _, n in by_day.most_common(3))} 条（{sum(n for _, n in by_day.most_common(3)) / len(rows) * 100:.1f}%） |",
             f"| pushed−created 中位滞后 | {_fmt(_med(lags), 1, ' 小时')}（仅供参考；fork 的 pushed_at 继承上游，**不构成**「fork 后有活动」的证据） |",
             "",
             "按日 Top 10：",
             "", "| 日期 | fork 数 |", "|---|---|"]
    for day, n in by_day.most_common(10):
        lines.append(f"| {day} | {n} |")
    lines.append("")
    return "\n".join(lines)


def sec_owners(d, repo):
    """§六 fork owner 画像（full 档 + join fork 时间）。"""
    if not d["tiers"]["exists"]:
        return "**未采集 fork owner 画像。**\n"
    recs = [r for r in d["tiers"]["rows"] if r.get("http") in (200, 404)]
    if not recs:
        return "**owner 画像文件为空。**\n"
    fork_at = {r.get("login"): r.get("fork_created_at") for r in d["forks"]["rows"]} if d["forks"]["exists"] else {}

    n_all = len(recs)
    ok = [r for r in recs if r.get("http") == 200]
    gone = [r for r in recs if r.get("http") == 404]

    def d1(r, k):
        v = (r.get("d1") or {})
        return v.get(k)

    def unit(r, name, k):
        v = (r.get(name) or {})
        return v.get(k)

    # 账号年龄@fork（login join：fork 名单缺失 / fork 后改名 → join 不上，量化损失而非静默丢）
    ages = []
    join_miss = []
    for r in ok:
        cu, fk = _parse_iso(d1(r, "created_at")), _parse_iso(fork_at.get(r.get("login")))
        if cu and fk:
            ages.append((fk - cu).days)
        else:
            join_miss.append(r.get("login"))

    followers = [d1(r, "followers") for r in ok]
    repos_n = [d1(r, "public_repos") for r in ok]
    empty_profile = [r for r in ok if not any([d1(r, "name"), d1(r, "bio"), d1(r, "blog"), d1(r, "location")])]
    has_email = [r for r in ok if d1(r, "has_email")]
    starred = [unit(r, "d4", "starred_total") for r in ok]
    # 纯 fork 结构：自建仓为 0
    pure_fork = [r for r in ok if (r.get("d8") or {}).get("http") == 200 and (r.get("d8") or {}).get("own_count") == 0]
    d8_sampled = [r for r in ok if (r.get("d8") or {}).get("http") == 200]
    zero_dev = [r for r in ok if (r.get("d2") or {}).get("http") == 200 and (r.get("d2") or {}).get("events_total") == 0]
    d2_ok = [r for r in ok if (r.get("d2") or {}).get("http") == 200]
    no_org = [r for r in ok if (r.get("d6") or {}).get("http") == 200 and not (r.get("d6") or {}).get("orgs")]
    d6_ok = [r for r in ok if (r.get("d6") or {}).get("http") == 200]

    lines = [f"名单 {n_all} 人：**{len(ok)} 存活 / {len(gone)} 已删号(404)**。删号率 "
             f"{len(gone) / n_all * 100:.1f}%（对照组 fork 群体常见 <3%）。", "",
             "| 画像指标 | 本仓 fork owner | 说明 |", "|---|---|---|",
             f"| 账号年龄@fork 中位 | {_fmt(_med(ages), 0, ' 天')} | p25 {_fmt(_pctl(ages, 25), 0)} / p75 {_fmt(_pctl(ages, 75), 0)} 天 |",
             f"| **@fork < 30 天** | {_fmt(_share(ages, lambda a: a < 30), 1, '%')} | 新号点 fork 信号 |",
             f"| @fork < 7 天 | {_fmt(_share(ages, lambda a: a < 7), 1, '%')} | 极新号 |",
             f"| @fork > 365 天 | {_fmt(_share(ages, lambda a: a > 365), 1, '%')} | 老号占比 |",
             f"| **0 粉丝** | {_fmt(_share(followers, lambda f: f == 0), 1, '%')} | 中位 {_fmt(_med(followers), 0)} |",
             f"| 公开仓中位 | {_fmt(_med(repos_n), 0)} | |",
             f"| **空档案率**（无名/bio/blog/location） | {len(empty_profile) / max(len(ok), 1) * 100:.1f}% | |",
             f"| 留邮箱比例 | {len(has_email) / max(len(ok), 1) * 100:.1f}% | 公开 email 布尔，不落原文 |",
             f"| **star 宽度中位**（公开 starred 总数） | {_fmt(_med(starred), 0)} | ≤3 占 {_fmt(_share(starred, lambda s: s <= 3), 1, '%')} |",
             f"| **纯 fork 无自建仓** | {len(pure_fork)}/{len(d8_sampled)} = {len(pure_fork) / max(len(d8_sampled), 1) * 100:.1f}% | D8 采样 {len(d8_sampled)} 人 |",
             f"| **近 90 天零事件** | {len(zero_dev)}/{len(d2_ok)} = {len(zero_dev) / max(len(d2_ok), 1) * 100:.1f}% | 挂机/代办号特征 |",
             f"| 无组织归属 | {len(no_org)}/{len(d6_ok)} = {len(no_org) / max(len(d6_ok), 1) * 100:.1f}% | |",
             ""]
    if join_miss:
        n_fk = len(d["forks"]["rows"]) if d["forks"]["exists"] else 0
        lines.append(f"> ⚠️ **join 损失**：{len(join_miss)}/{len(ok)} 名存活 owner 的 fork 时间对不上"
                     f"（fork 名单缺失或 fork 后改名），年龄@fork 统计不含这些人"
                     f"{'（fork 名单未采集）' if not n_fk else ''}。")
        lines.append("")

    # openclaw forker 池参照
    if d["openclaw_d1"]["exists"]:
        oc = [r for r in d["openclaw_d1"]["rows"] if r.get("http") == 200]
        if oc:
            def age_now(r):
                c = _parse_iso(r.get("created_at"))
                return (datetime.datetime.now(TZ) - c).days if c else None
            oc_ages = [age_now(r) for r in oc]
            oc_zero_f = _share([r.get("followers") for r in oc], lambda f: f == 0)
            lines += ["**参照基线**（openclaw forker 池 D1，采集中时点的账号年龄，口径为「至今」而非「@fork」，方向参考）：",
                      "",
                      "| 指标 | openclaw forkers | 本仓 |", "|---|---|---|",
                      f"| 账号年龄中位 | {_fmt(_med(oc_ages), 0, ' 天')} | {_fmt(_med(ages), 0, ' 天')} |",
                      f"| 0 粉丝率 | {_fmt(oc_zero_f, 1, '%')} | {_fmt(_share(followers, lambda f: f == 0), 1, '%')} |",
                      f"| 样本量 | {len(oc)} | {len(ok)} |",
                      "> ⚠️ 两个群体的年龄口径不同（至今 vs @fork 时点），只做方向性参考。", ""]
    return "\n".join(lines)


def sec_ch(d, cov, stars_now):
    """§七 CH 身份样本 + 同秒聚簇。"""
    actors = ch_actor_rows(d["ch"]["rows"])
    uniq = {a for a, _ in actors}
    lines = []
    if not d["ch"]["exists"] or not uniq:
        lines.append("**未采集 CH star 身份**（`_small_repo_collect.py --only ch`）。")
    elif cov < MIN_CH_COVERAGE:
        lines.append(f"CH star 身份 {len(uniq)} 人 ÷ API 星数 {stars_now or '—'} = 覆盖率 "
                     f"**{cov:.1f}% < {MIN_CH_COVERAGE}%**。按铁律本节**不出身份级结论**。")
    else:
        lines.append(f"CH star 身份 {len(uniq)} 人 ÷ API 星数 {stars_now or '—'} = 覆盖率 "
                     f"**{cov:.1f}% ≥ {MIN_CH_COVERAGE}%：可做样本内相对统计（绝不做绝对计数）。")
    lines.append("")
    sec = d["ch_sec"]
    if sec["exists"]:
        body = sec["rows"][0].get("body") if sec["rows"] else []
        if isinstance(body, list):
            n_sec = len(body)
            batch_secs = [b for b in body if b.get("n", 0) >= 2]
            actors_in_batch = sum(b.get("n", 0) for b in batch_secs)
            lines.append(f"同秒聚簇：{n_sec} 个有事件秒，其中 **≥2 actor 的秒数 {len(batch_secs)}**"
                         f"（涉及 {actors_in_batch} 人）。")
            if cov < MIN_CH_COVERAGE:
                lines.append("> 同上：样本覆盖率不足，该分布**不可**与全周期「16 倍富集」等基线并排比较。")
    lines.append("")
    return "\n".join(lines)


def sec_controls(d, repo, stars_now, forks_now, subs_now):
    """§八 对照组对比。"""
    key = repo.replace("/", "__")
    rows = [("本仓", stars_now, forks_now, subs_now)]
    extra = {}
    for name, c in d["controls"].items():
        m = c["meta"]
        rows.append((name.replace("__", "/"), m.get("stars"), m.get("forks"), m.get("subscribers")))
        if c.get("contrib") and c["contrib"].get("top100"):
            top = c["contrib"]["top100"]
            csum = sum(x.get("contributions") or 0 for x in top) or 1
            extra[name.replace("__", "/")] = top[0].get("contributions", 0) / csum * 100
    # 本仓贡献者集中度
    mrow = next((r for r in d["metrics"]["rows"] if r.get("kind") == "contributors"), None) if d["metrics"]["exists"] else None
    if mrow and mrow.get("top100"):
        top = mrow["top100"]
        csum = sum(x.get("contributions") or 0 for x in top) or 1
        extra["本仓"] = top[0].get("contributions", 0) / csum * 100

    rows = sorted(rows, key=lambda x: -(x[1] or 0))
    lines = ["| 仓库 | 星 | fork | **fork÷star** | watch÷star | 贡献者集中度(Top1) |", "|---|---|---|---|---|---|"]
    for name, s, f, w in rows:
        s_v = s if isinstance(s, int) else (int(s) if isinstance(s, float) else None)
        f_v = f if isinstance(f, int) else (int(f) if isinstance(f, float) else None)
        ratio = f"{f_v / max(s_v, 1) * 100:.1f}%" if (s_v is not None and f_v is not None) else "—"
        wratio = f"{(w or 0) / max(s_v or 1, 1) * 100:.2f}%" if (s_v is not None and w is not None) else "—"
        lines.append(f"| {name} | {_fi(s)} | {_fi(f)} | **{ratio}** | {wratio} | {_fmt(extra.get(name), 0, '%')} |")
    n_ctrl = len(rows) - 1
    lines.append("")
    if n_ctrl < 2:
        lines.append(f"> ⚠️ 对照组仅 {n_ctrl} 个 → 判定档位 **uncalibrated**（无结论）。")
    else:
        others = [(s, f) for name, s, f, _ in rows if name != "本仓" and isinstance(s, int) and isinstance(f, int)]
        best = max((f / max(s, 1) * 100 for s, f in others if s), default=0)
        mine = forks_now / max(stars_now, 1) * 100
        lines.append(f"> 对照组 fork÷star 最高 {best:.1f}%，本仓 **{mine:.1f}%** = 对照组最高值的 "
                     f"**{mine / max(best, 0.1):.1f}×**。")
    lines.append("")
    return "\n".join(lines)


def sec_verdict(d, repo, stars_now, forks_now, issues_total, subs_now):
    """§九 信号评分（措辞纪律：异常 ≠ 造假）。"""
    if not d["metrics"]["exists"]:
        return "**指标缺失，无法评分。**"
    ctrl_r = []
    for name, c in d["controls"].items():
        m = c["meta"]
        if m.get("stars"):
            ctrl_r.append(m.get("forks", 0) / max(m.get("stars"), 1) * 100)
    mine = forks_now / max(stars_now, 1) * 100
    n_ctrl = len(d["controls"])

    sig = []
    sig.append(("fork÷star 异常度", f"{mine:.1f}% vs 对照最高 {max(ctrl_r) if ctrl_r else 0:.1f}%"
                + (f"（{mine / max(max(ctrl_r), 0.1):.1f}×）" if ctrl_r else ""),
                "strong" if ctrl_r and mine > 3 * max(ctrl_r) else ("watch" if ctrl_r and mine > max(ctrl_r) else "normal")))
    # 曲线形态
    if d["history"]["exists"]:
        buckets = [b for rec in d["history"]["rows"] for b in (rec.get("buckets") or [])]
        buckets.sort(key=lambda b: b.get("week") or 0)      # 文件序是新→旧，末桶必须先排序
        total = sum(b.get("total", 0) for b in buckets)
        top2 = sum(sorted((b.get("total", 0) for b in buckets), reverse=True)[:2]) if buckets else 0
        burst_pct = top2 / total * 100 if total else 0
        last_pct = (buckets[-1].get("total", 0) / total * 100) if (buckets and total) else 0
        shape = "单脉冲（爆发后归零）" if (burst_pct > 60 and last_pct < 5) else ("脉冲+持续" if total else "—")
        sig.append(("曲线形态", f"前 2 周占 {burst_pct:.0f}%，末桶占 {last_pct:.1f}% → {shape}",
                    "watch" if shape == "单脉冲（爆发后归零）" else "normal"))
    # issue 参与度
    if issues_total is not None and stars_now:
        per_k = issues_total / max(stars_now, 1) * 1000
        sig.append(("issue/千★", f"{per_k:.1f}（对照组通常 50–160）",
                    "strong" if per_k < 10 else ("watch" if per_k < 30 else "normal")))
    # 贡献者集中度
    mrow = next((r for r in d["metrics"]["rows"] if r.get("kind") == "contributors"), None)
    if mrow and mrow.get("top100"):
        top = mrow["top100"]
        csum = sum(x.get("contributions") or 0 for x in top) or 1
        conc = top[0].get("contributions", 0) / csum * 100
        sig.append(("贡献者集中度", f"Top1 占 {conc:.0f}%（openclaw 61% 也可解释=作者驱动型）",
                    "weak" if conc > 80 else "normal"))
    # owner 画像综合
    if d["tiers"]["exists"]:
        recs = [r for r in d["tiers"]["rows"] if r.get("http") == 200]
        n_all = len([r for r in d["tiers"]["rows"] if r.get("http") in (200, 404)])
        fork_at = {r.get("login"): r.get("fork_created_at") for r in d["forks"]["rows"]} if d["forks"]["exists"] else {}
        ages = []
        for r in recs:
            cu, fk = _parse_iso((r.get("d1") or {}).get("created_at")), _parse_iso(fork_at.get(r.get("login")))
            if cu and fk:
                ages.append((fk - cu).days)
        new_pct = _share(ages, lambda a: a < 30)
        zero_f = _share([(r.get("d1") or {}).get("followers") for r in recs], lambda f: f == 0)
        gone_pct = (n_all - len(recs)) / max(n_all, 1) * 100
        pure = [r for r in recs if (r.get("d8") or {}).get("http") == 200 and (r.get("d8") or {}).get("own_count") == 0]
        d8n = [r for r in recs if (r.get("d8") or {}).get("http") == 200]
        pure_pct = len(pure) / max(len(d8n), 1) * 100
        sig.append(("账号年龄@fork < 30 天", f"{_fmt(new_pct, 1, '%')}" + ("（正常群体 <15%）" if new_pct else ""),
                    "strong" if (new_pct or 0) > 30 else ("watch" if (new_pct or 0) > 15 else "normal")))
        sig.append(("0 粉丝率", f"{_fmt(zero_f, 1, '%')}", "watch" if (zero_f or 0) > 40 else "normal"))
        sig.append(("纯 fork 无自建仓", f"{_fmt(pure_pct, 1, '%')}", "watch" if (pure_pct or 0) > 40 else "normal"))
        sig.append(("删号率(404)", f"{_fmt(gone_pct, 1, '%')}", "strong" if gone_pct > 10 else ("watch" if gone_pct > 3 else "normal")))

    lines = ["| 信号 | 实测 | 判定 |", "|---|---|---|"]
    for name, val, v in sig:
        ico = {"strong": "🔴 strong", "watch": "🟡 watch", "normal": "🟢 normal",
               "weak": "⚪ weak（有混杂）"}.get(v, v)
        lines.append(f"| {name} | {val} | {ico} |")
    n_strong = sum(1 for *_, v in sig if v == "strong")
    n_watch = sum(1 for *_, v in sig if v == "watch")
    grade = ("strong（多信号强异常）" if n_strong >= 2 else
             "suspicious（存在强异常信号）" if n_strong == 1 else
             "watch（存在待观察信号）" if n_watch else "normal")
    if n_ctrl < 2:
        grade = "uncalibrated（对照组不足 2 个，无结论）"
    lines += ["",
              f"- strong ×{n_strong}，watch ×{n_watch} → **档位：{grade}**",
              "",
              "> **措辞纪律**：以上是「异常信号」，不是「造假认定」。fork 数激增可能是"
              "自然传播、教学场景、或 fork 代托管（把 fork 当分支用）等合理解释；"
              "结论必须结合对照组基线与本报告 §十 的局限声明阅读。", ""]
    return "\n".join(lines)


LIMITS_TMPL = """
## 十、方法论局限

- **star 身份缺口**：官方通道已死（2026-06-30 admin-only），CH 为衰减抽样；本报告身份级
  结论受 §二 覆盖率约束，覆盖率 < 5% 时只标注数据不足。
- **fork pushed_at 语义**：fork 的 `pushed_at` 继承上游最后提交时间，「fork 后零 push」
  不能从 `pushed_at == created_at` 推断（本项目实测过该坑），报告只用时序分布不用该指标定性。
- **账号年龄@fork 依赖 join**：owner 画像与 fork 名单按 login join；2026-10-30 后改名的
  账号会对不上（损失按 404/缺失口径呈现，不静默丢弃）。
- **小样本方差**：仓库星数百级时，percentile 类指标的噪声大；本报告给出样本量供自行判断。
- **对照组口径**：对照仓规模普遍大 2–3 个数量级，ratio 类指标（fork÷star、issue/千★）
  受规模效应影响（大仓社交可见度更高），横向比较只做量级判断。
"""


def main():
    ap = argparse.ArgumentParser(description="单仓完整分析报告")
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--suffix", default="", help="画像文件后缀（默认 _{key}）")
    ap.add_argument("--note", default="", help="生态位备注（进 §三）")
    args = ap.parse_args()

    key, d = load_all(args.repo, args.suffix)
    if not d["metrics"]["exists"]:
        raise SystemExit(f"未找到 repo_metrics_{key}.jsonl —— 先跑 _repo_detail.py --repos {args.repo}")

    m = next((r for r in d["metrics"]["rows"] if r.get("kind") == "meta"), {})
    stars_now, forks_now = m.get("stars") or 0, m.get("forks") or 0
    subs_now = m.get("subscribers") or 0
    search = {r.get("metric"): r.get("total") for r in d["metrics"]["rows"] if r.get("kind") == "search"}
    issues_total = search.get("issue_count")

    border, cov = sec_borderline(d, args.repo, stars_now)
    now = datetime.datetime.now(TZ)

    parts = [
        f"# {args.repo} 仓库数据分析报告",
        "",
        f"> 生成时间：{now.isoformat(timespec='seconds')} · 工具：`_repo_report.py`（本项目）"
        f"<br>样本仓 fork owner 画像：full 档（D1-D8，6–10 req/人，匿名只读）",
        "",
        "> ⚠️ **本报告只输出「异常信号」，不构成「造假认定」**。所有判据须结合对照组基线与 §十 局限声明阅读。",
        "",
        "## 一、结论摘要",
        "",
        f"- **fork ÷ star = {forks_now / max(stars_now, 1) * 100:.1f}%**（{forks_now:,} fork / {stars_now:,}★）——正常开源项目该值多在 2–30% 区间",
        f"- star 曲线：{sum(r.get('bucket_count', 0) for r in d['history']['rows'])} 个观测周",
        f"- 仓库状态：{(m.get('created_at') or '')[:10]} 建仓，最近 push {(m.get('pushed_at') or '')[:10]}",
        f"- issue/PR 总量：issue {issues_total if issues_total is not None else '—'} / PR {search.get('pr_count') if search.get('pr_count') is not None else '—'}",
        "- 逐项信号评分见 §九（含档位判定）",
        "",
        "## 二、数据来源与边界",
        "",
        border,
        "## 三、仓库画像",
        "",
        sec_repo_profile(d, args.repo, args.note),
        "## 四、star 增长曲线",
        "",
        sec_curve(d),
        "## 五、fork 结构分析（全量）",
        "",
        sec_forks(d),
        "## 六、fork owner 画像（full 档全量）",
        "",
        sec_owners(d, args.repo),
        "## 七、star 身份样本（CH）与同秒聚簇",
        "",
        sec_ch(d, cov, stars_now),
        "## 八、对照组对比",
        "",
        sec_controls(d, args.repo, stars_now, forks_now, subs_now),
        "## 九、信号评分与档位",
        "",
        sec_verdict(d, args.repo, stars_now, forks_now, issues_total, subs_now),
        LIMITS_TMPL,
    ]
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"report_{key}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    print(f"报告已生成 → {out}")
    print(f"数据源：metrics={'Y' if d['metrics']['exists'] else 'N'} forks={len(d['forks']['rows'])} "
          f"history={'Y' if d['history']['exists'] else 'N'} ch={'Y' if d['ch']['exists'] else 'N'} "
          f"tiers={'Y' if d['tiers']['exists'] else 'N'} 对照组={len(d['controls'])}")


if __name__ == "__main__":
    main()
