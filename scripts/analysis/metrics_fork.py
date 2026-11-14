# -*- coding: utf-8 -*-
"""fork 名单形态指标（L2 层）—— 0 请求、纯本地重算、确定性。

设计原则（与 analysis/metrics.py 同源）：
  * 每个指标返回**过程量**（分子/分母/样本量/覆盖率），不只返回结果；
  * 只读 data/raw/forks_all_*.jsonl，不联网、不依赖采集时点；
  * 阈值参数（爆发窗 3h / 2σ）写进报告头，改参数必须改版本号；
  * 数据不足就报「数据不足」，不填 0。

指标：
  N / 唯一 login / 时间跨度
  B2  爆发窗占比       滚动 3h 窗口计数 > μ+2σ 的那些小时里的 fork 数 ÷ N
  B1  同秒碰撞富集     λ=N/W，E[S]=W(1-e^-λ-λe^-λ)，富集=实测同秒数/E[S]（底数用**全窗口 W**）
  TS%  10 位时间戳形态登录名占比（最长连续数字串）
  PHONE% 11 位手机号形态占比
  TS-dist  上述 TS 样本解码年份直方图
  TS-cluster  TS 样本中解码时间戳互差 <60s 的账号对数（脚本循环硬指纹）

用法：
  py metrics_fork.py --repo openclaw/openclaw
  py metrics_fork.py --all                     # 所有已采名单横向对比
  py metrics_fork.py --repo morluto/rea --min-n 500   # 样本不足则拒绝出结论
"""
import argparse
import datetime
import json
import math
import os
import re
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")

SPEC_VERSION = "v1"
BURST_WINDOW_H = 3          # 爆发窗宽度（小时）
BURST_Z = 2.0               # 阈值 = μ + zσ，数据自标定
TS_MIN, TS_MAX = 10 ** 9, 2 * 10 ** 9   # 合法 Unix 秒区间（2001-09 ~ 2033-05）
CLUSTER_GAP_S = 60          # TS-cluster：互差 <60s 视为同批命名


# ── 基础 ────────────────────────────────────────────────
def load_forks(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("login") and r.get("fork_created_at"):
            rows.append(r)
    return rows


def iso_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def longest_digits(login):
    runs = re.findall(r"\d+", login or "")
    return max(runs, key=len) if runs else ""


def looks_sequential(run):
    """排除"1234567890"这类键盘序列/低熵串——它们恰好落在 1e9~2e9，会被误判成时间戳。

    实测（2026-11-05, openclaw）：`pop1234567889` / `zzq1234567890` 两个账号的最长数字串
    互差 1 秒，看起来像"同批脚本命名"，实际只是用户敲了一串连号。
    """
    seq = "0123456789" * 2
    if run in seq or run in seq[::-1]:
        return True
    return len(set(run)) <= 2


def classify_login(login):
    """→ (kind, digit_run, decoded_ts|None)。kind ∈ TS / PHONE / NUM / OTHER。

    ⚠️ 「能解码成时间戳」本身零信息量——任何 10 位、首位为 1 的数字都落在 2001–2033。
    判别力来自解码时间的**分布**（TS-dist）与**批次聚集**（TS-cluster）。
    """
    run = longest_digits(login)
    n = len(run)
    if n == 10 and TS_MIN <= int(run) < TS_MAX and not looks_sequential(run):
        return "TS", run, int(run)
    if n == 11 and run[0] == "1" and run[1] in "3456789":
        return "PHONE", run, None
    if 5 <= n <= 11:
        return "NUM", run, None
    return "OTHER", run, None


# ── 指标 ────────────────────────────────────────────────
def burst_metrics(ts_list):
    """滚动 3h 窗口：返回 (flagged_hours, B 占比, μ, σ, top_windows)。"""
    hours = [int(t // 3600) for t in ts_list]
    if len(hours) < 10:
        return set(), None, None, None, []
    lo, hi = min(hours), max(hours)
    dense = [0] * (hi - lo + 1)
    for h in hours:
        dense[h - lo] += 1
    w = min(BURST_WINDOW_H, len(dense))
    sums = [sum(dense[i:i + w]) for i in range(len(dense) - w + 1)]
    mean = sum(sums) / len(sums)
    var = sum((x - mean) ** 2 for x in sums) / len(sums)
    sd = math.sqrt(var)
    thr = mean + BURST_Z * sd
    flagged = set()
    top = []
    for i, s in enumerate(sums):
        if s > thr:
            top.append((lo + i, s))
            for h in range(lo + i, lo + i + w):
                flagged.add(h)
    in_burst = sum(1 for h in hours if h in flagged)
    top.sort(key=lambda x: -x[1])
    return flagged, (in_burst / len(hours) if hours else None), mean, sd, top[:10]


def collision_metrics(ts_list):
    """同秒碰撞：Poisson 零模型，底数 = 全窗口 W（不是活跃秒数）。"""
    n = len(ts_list)
    if n < 2:
        return None
    secs = Counter(int(t) for t in ts_list)
    w = max(ts_list) - min(ts_list)
    if w <= 0:
        return None
    lam = n / w
    expected = w * (1 - math.exp(-lam) - lam * math.exp(-lam))
    observed = sum(1 for v in secs.values() if v >= 2)
    pairs = sum(v * (v - 1) // 2 for v in secs.values())

    # ★ 局部速率零模型（2026-11-09 修正）
    # 全局 λ 会把「爆发期流量大 → 同秒自然多」误判成「协调」，实测 rea：
    #   按全局 λ 富集 9.03×，按**小时局部速率**只有 1.04× —— 差异全部来自速率异质性。
    # 正确做法：在每个小时内部按该小时的速率算期望，再加总。
    hour = Counter(int(t) // 3600 for t in ts_list)
    exp_pairs_local = sum((c * (c - 1) / 2) / 3600.0 for c in hour.values())
    exp_secs_local = sum(3600.0 * (1 - math.exp(-c / 3600.0) * (1 + c / 3600.0))
                         for c in hour.values())
    return {"n": n, "window_s": w, "lambda": lam, "expected_collision_secs": expected,
            "observed_collision_secs": observed,
            "enrichment": (observed / expected) if expected > 0 else None,
            "enrichment_local": (observed / exp_secs_local) if exp_secs_local > 0 else None,
            "pairs_local_enrichment": (pairs / exp_pairs_local) if exp_pairs_local > 0 else None,
            "expected_pairs_local": exp_pairs_local,
            "max_batch": max(secs.values()), "same_sec_pairs": pairs}


def login_shape_metrics(logins):
    kinds = Counter()
    decoded = []
    for lg in logins:
        k, run, ts = classify_login(lg)
        kinds[k] += 1
        if ts:
            decoded.append(ts)
    decoded.sort()
    # 同一数字串被两个 login 共用（如 `z1141922290` 与 `z1141922290-arch`）不是"批次命名"，
    # 是同一个人/同基名双号 —— 先按解码值去重，再算间隔，否则会凭空造出 0 秒对。
    uniq = sorted(set(decoded))
    pairs_60 = sum(1 for a, b in zip(uniq, uniq[1:]) if b - a < CLUSTER_GAP_S)
    min_gap = min((b - a for a, b in zip(uniq, uniq[1:])), default=None)
    years = Counter(datetime.datetime.fromtimestamp(t, datetime.UTC).year for t in decoded)
    return {"kinds": kinds, "n": len(logins), "decoded": decoded,
            "cluster_pairs_60s": pairs_60, "min_gap_s": min_gap, "years": years}


def _date_ts(s, end=False):
    """YYYY-MM-DD → 时间戳；end=True 表示含当日（取次日 0 点作开区间上界）。"""
    d = datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    if end:
        d = d + datetime.timedelta(days=1)
    return d.timestamp()


def compute(path, until=None, since=None):
    rows = load_forks(path)
    if until is not None or since is not None:
        # 时间截断对齐：两仓名单的采集时点不同（openclaw 2026-10-24 / rea 2026-11-06），
        # 直接比爆发窗与同秒富集会被"多出来的十来天"污染，必须截到同一窗口。
        kept = []
        for r in rows:
            t = iso_ts(r["fork_created_at"])
            if t is None:
                continue
            if until is not None and t > until:
                continue
            if since is not None and t < since:
                continue
            kept.append(r)
        rows = kept
    logins = [r["login"].lower() for r in rows]
    ts_list = [t for t in (iso_ts(r["fork_created_at"]) for r in rows) if t]
    unique = set(logins)
    m = {"n_rows": len(rows), "n_unique": len(unique), "n_ts": len(ts_list),
         "span": (min(ts_list), max(ts_list)) if ts_list else (None, None),
         "filter": (since, until)}
    flagged, burst_share, mean, sd, top = burst_metrics(ts_list)
    m.update({"burst_share": burst_share, "burst_mean": mean, "burst_sd": sd,
              "burst_top": top, "n_burst_hours": len(flagged)})
    m["collision"] = collision_metrics(ts_list)
    m["login"] = login_shape_metrics(list(unique))
    touched = [r.get("fork_touched") for r in rows if r.get("fork_touched") is not None]
    m["touched_share"] = (sum(1 for x in touched if x in (1, True)) / len(touched)) if touched else None
    return m


# ── 渲染 ────────────────────────────────────────────────
def _pct(a, b):
    return f"{a / b * 100:.2f}%" if b else "—"


def render(repo, m, min_n):
    L = m["login"]
    c = m["collision"]
    lo, hi = m["span"]
    verdicts = []
    lines = [f"# fork 名单形态指标 · `{repo}`", "",
             f"> 口径：`metrics_fork.py` spec {SPEC_VERSION} · 爆发窗 {BURST_WINDOW_H}h / μ+{BURST_Z}σ · "
             f"TS-cluster 阈值 {CLUSTER_GAP_S}s", ""]
    _f = m.get("filter") or (None, None)
    if _f[0] or _f[1]:
        _fmt = lambda t: datetime.datetime.fromtimestamp(t, datetime.UTC).date().isoformat() if t else "…"
        # until 存的是「次日 0 点」的开区间上界，显示时减 1 秒才是含当日的日期
        _end = lambda t: datetime.datetime.fromtimestamp(t - 1, datetime.UTC).date().isoformat() if t else "…"
        lines += [f"> ⏱ **时间截断**：{_fmt(_f[0])} → {_end(_f[1])}（两仓对比必须同窗口）", ""]

    if m["n_rows"] < min_n:
        lines += [f"> ⚠️ **样本不足**：{m['n_rows']} 条 < 阈值 {min_n}，以下数字只作形态参考，"
                  f"**不得作为总体比例引用**。", ""]

    lines += ["## 一、规模与时间跨度", "", "| 指标 | 值 |", "|---|---|",
              f"| 名单条数 | {m['n_rows']:,} |",
              f"| 唯一 login | {m['n_unique']:,} |",
              f"| 时间跨度 | {datetime.datetime.fromtimestamp(lo, datetime.UTC).date() if lo else '—'} → "
              f"{datetime.datetime.fromtimestamp(hi, datetime.UTC).date() if hi else '—'}"
              f"（{(hi - lo) / 86400:.0f} 天）|" if lo else "| 时间跨度 | — |",
              ""]

    lines += ["## 二、时间聚集（链 B）", "", "| 指标 | 值 | 判定 |", "|---|---|---|",
              f"| 爆发窗占比 B | {_pct(m['burst_share'] or 0, 1) if m['burst_share'] is not None else '—'} | "
              f"{'🔴 高' if (m['burst_share'] or 0) > 0.30 else '🟡 中' if (m['burst_share'] or 0) > 0.15 else '🟢 低'} |",
              f"| 窗口 μ / σ | {m['burst_mean']:.1f} / {m['burst_sd']:.1f} | — |"
              if m["burst_mean"] is not None else "| 窗口 μ / σ | — | — |"]
    if c:
        if c["expected_collision_secs"] < 1:
            # 期望碰撞数 <1 时，富集倍数没有统计意义（0/0.02 会显得像"负异常"）
            lines += [f"| 同秒碰撞富集 | 样本不足以检验（期望仅 {c['expected_collision_secs']:.2f} 次） | — |",
                      f"| 最大同秒批次 | {c['max_batch']} 人 | — |"]
        else:
            lines += [f"| 同秒碰撞富集 | **{c['enrichment']:.2f}×**（实测 {c['observed_collision_secs']} / 期望 "
                      f"{c['expected_collision_secs']:.1f}）← 全局 λ，**会高估** | ⚠️ 仅供参考 |",
                      f"| ↳ **按小时局部速率校正** | **{c['enrichment_local']:.2f}×**（同秒**对** {c['pairs_local_enrichment']:.2f}×）"
                      f" → {'🔴 仍显著' if c['enrichment_local'] > 2 else '🟢 **不显著（可由爆发期流量解释）**'} | — |",
                      f"| 最大同秒批次 | {c['max_batch']} 人 | — |",
                      f"| λ（事件/秒，全局） | {c['lambda']:.2e} | — |"]
    lines.append("")
    if m["burst_top"]:
        lines += ["Top 爆发窗：", "", "| 窗口起点 (UTC) | 3h fork 数 |", "|---|---:|"]
        for h, s in m["burst_top"]:
            lines.append(f"| {datetime.datetime.fromtimestamp(h * 3600, datetime.UTC).strftime('%Y-%m-%d %H:00')} | {s} |")
        lines.append("")

    lines += ["## 三、登录名形态（链 C）", "", "| 形态 | 人数 | 占比 |", "|---|---:|---|"]
    for k, label in (("TS", "10 位时间戳（Unix 秒）"), ("PHONE", "11 位手机号"),
                     ("NUM", "其他 5–11 位纯数字"), ("OTHER", "非上述形态")):
        lines.append(f"| {label} | {L['kinds'].get(k, 0):,} | {_pct(L['kinds'].get(k, 0), L['n'])} |")
    gap = L["min_gap_s"]
    if gap is None:
        gap_txt = "—"
    elif gap > 86400:
        gap_txt = f"{gap / 86400:.1f} 天"
    else:
        gap_txt = f"{gap:.0f} 秒"
    lines += ["",
              f"- **TS-cluster**：解码时间戳互差 <{CLUSTER_GAP_S}s 的账号对 = "
              f"**{L['cluster_pairs_60s']}**；最小间隔 {gap_txt}",
              f"- **TS-dist**（解码年份）：" + ("、".join(f"{y}:{n}" for y, n in sorted(L["years"].items())) or "—"),
              ""]

    lines += ["## 四、判定", ""]
    lines += ["- **本节阈值只作粗筛**：openclaw（被视为有机爆红）实测 B=39.2%、同秒富集 4.26×，"
              "绝对值本身**不能**用来定性；判定必须用 `--all` 的横比 lift。", ""]
    if m["n_rows"] < min_n:
        verdicts.append("insufficient_data（样本不足，不出结论）")
    else:
        if (m["burst_share"] or 0) > 0.30:
            verdicts.append("B2 爆发窗占比高")
        if c and c["enrichment"] and c["enrichment"] > 3:
            verdicts.append("B1 同秒富集高")
        if L["n"] and L["kinds"].get("TS", 0) / L["n"] > 0.05:
            verdicts.append("TS% > 5%（时间戳形态异常）")
        if L["n"] and L["kinds"].get("PHONE", 0) / L["n"] > 0.05:
            verdicts.append("PHONE% > 5%（手机号形态异常）")
        verdicts = verdicts or ["本仓未见形态异常"]
    lines += [f"- {'；'.join(verdicts)}",
              "- 以上全部为**形态指标**，不是造假认定；须与对照组同口径并排后才可定性。", ""]

    extra = []
    if m["touched_share"] is not None:
        extra.append(f"| fork 后有推送（fork_touched） | {_pct(m['touched_share'], 1)} |")
    if extra:
        lines += ["## 五、补充（可选字段）", "", "| 指标 | 值 |", "|---|---|"] + extra + [""]
    return "\n".join(lines)


def table_row(name, m):
    L = m["login"]
    c = m["collision"]
    burst = _pct(m["burst_share"], 1) if m["burst_share"] is not None else "—"
    enr = f"{c['enrichment']:.2f}×" if (c and c.get("enrichment")) else "—"
    loc = f"{c['enrichment_local']:.2f}×" if (c and c.get("enrichment_local")) else "—"
    return (f"| {name} | {m['n_rows']:,} | {burst} | {enr} | **{loc}** | "
            f"{_pct(L['kinds'].get('TS', 0), L['n'])} | {_pct(L['kinds'].get('PHONE', 0), L['n'])} | "
            f"{L['cluster_pairs_60s']} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", help="owner/repo")
    ap.add_argument("--path", help="直接指定 JSONL（覆盖默认命名）")
    ap.add_argument("--all", action="store_true", help="所有已采名单横向对比")
    ap.add_argument("--min-n", type=int, default=1000, help="低于此条数只作形态参考，不出比例结论")
    ap.add_argument("--until", help="时间截断上界 YYYY-MM-DD（两仓对比必须同窗口）")
    ap.add_argument("--since", help="时间截断下界 YYYY-MM-DD")
    args = ap.parse_args()
    until = _date_ts(args.until, end=True) if args.until else None
    since = _date_ts(args.since) if args.since else None

    if args.all:
        names = sorted(f[10:-6] for f in os.listdir(RAW)
                       if f.startswith("forks_all_") and f.endswith(".jsonl"))
        items = [(n.replace("__", "/"), os.path.join(RAW, f"forks_all_{n}.jsonl")) for n in names]
        legacy = os.path.join(RAW, "probe_forks_all.jsonl")
        if os.path.exists(legacy):                      # openclaw 的 82k 旧命名归档
            items.append(("openclaw/openclaw", legacy))
        if not items:
            raise SystemExit("data/raw/ 下没有 forks_all_*.jsonl")
        print("| 仓库 | 名单条数 | B 爆发窗 | 同秒富集(全局λ) | **同秒富集(小时局部)** | TS% | PHONE% | TS-cluster |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|")
        for name, path in items:
            m = compute(path, until=until, since=since)
            if not m["n_rows"]:
                continue
            print(table_row(name, m))
        return

    if not args.repo:
        raise SystemExit("必须指定 --repo owner/repo 或 --all")
    key = args.repo.replace("/", "__")
    path = args.path or os.path.join(RAW, f"forks_all_{key}.jsonl")
    if not args.path and not os.path.exists(path):
        # 旧命名：openclaw 的 82k 名单由 _forker_probe.py 产出，落在 probe_forks_all.jsonl
        legacy = os.path.join(RAW, "probe_forks_all.jsonl")
        if args.repo.lower() == "openclaw/openclaw" and os.path.exists(legacy):
            path = legacy
            print(f"[warn] 使用旧命名归档 {os.path.basename(legacy)}（_forker_probe 产出，字段略多）")
    m = compute(path, until=until, since=since)
    if not m["n_rows"]:
        raise SystemExit(f"没有数据：{path}（先跑 _small_repo_collect.py --repo {args.repo} --only forks）")
    md = render(args.repo, m, args.min_n)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"fork_metrics_{key}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(md.split("## 四、判定")[0])
    print(f"→ {out}")


if __name__ == "__main__":
    main()
