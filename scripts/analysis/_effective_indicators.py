# -*- coding: utf-8 -*-
"""12 个有效指标的完整数值表（统一口径、带分子分母）。

口径纪律：
  - 所有比例都写成 `X/N`，N 是可追溯的样本量
  - star 相关指标一律限制在 **star 过目标仓** 的子集内（否则分母无意义）
  - fork 后动过一律限制在 **fork 龄 ≤30 天**（时间控制）
  - 爆发窗成员（in_burst）用逐人时间戳重算，与群体级 58.5% 分开

用法：py _effective_indicators.py
"""
import csv
import json
import math
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")

SPECS = {
    "rea": ("forks_all_morluto__rea.jsonl", "features_rea.csv", "morluto/rea"),
    "openclaw": ("forks_all_openclaw__openclaw.jsonl", "features_openclaw.csv", "openclaw/openclaw"),
}


def load_csv(tag):
    with open(os.path.join(OUT, f"features_{tag}.csv"), encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def burst_hours(fork_file):
    """逐小时分桶 → 滚动 3h > μ+2σ 的小时集合（与 metrics_fork.py 同参数）。"""
    from datetime import datetime, timezone
    ts = []
    for line in open(os.path.join(RAW, fork_file), encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        s = r.get("fork_created_at") or ""
        if not s:
            continue
        try:
            ts.append(datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
                      .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    hours = [int(t // 3600) for t in ts]
    lo, hi = min(hours), max(hours)
    dense = [0] * (hi - lo + 1)
    for h in hours:
        dense[h - lo] += 1
    w = 3
    sums = [sum(dense[i:i + w]) for i in range(len(dense) - w + 1)]
    mean = sum(sums) / len(sums)
    sd = math.sqrt(sum((x - mean) ** 2 for x in sums) / len(sums))
    thr = mean + 2 * sd
    flagged = set()
    for i, s in enumerate(sums):
        if s > thr:
            for h in range(lo + i, lo + i + w):
                flagged.add(h)
    in_burst = sum(1 for h in hours if h in flagged)
    return flagged, in_burst, len(hours)


def pct(a, b):
    return f"{a/b*100:.1f}%" if b else "—"


def main():
    rows = []
    data = {}
    for tag, (ff, cf, target) in SPECS.items():
        cs = load_csv(tag)
        flagged, ib, tot = burst_hours(ff)
        data[tag] = {"cs": cs, "ib": ib, "tot": tot}
        print(f"{tag}: {len(cs):,} 行 | 爆发窗内 {ib:,}/{tot:,} = {ib/tot*100:.1f}%")

    def stat(tag, key):
        cs = data[tag]["cs"]
        return [r for r in cs if r.get(key) not in (None, "")]

    def rate(tag, key, val="1"):
        v = stat(tag, key)
        hit = sum(1 for r in v if r.get(key) == val)
        return hit, len(v)

    def med(tag, key):
        v = [num(r.get(key)) for r in data[tag]["cs"] if num(r.get(key)) is not None]
        v.sort()
        return (v[len(v) // 2] if v else None), len(v)

    # ---- 逐条计算 12 个指标 ----
    out = []

    # 1 fork 后动过（≤30 天）
    def touched_30(tag):
        cs = [r for r in data[tag]["cs"]
              if num(r.get("fork_age_days")) is not None
              and num(r["fork_age_days"]) <= 30
              and r.get("fork_touched") not in (None, "")]
        return sum(1 for r in cs if r["fork_touched"] == "1"), len(cs)
    a, b = touched_30("rea"), touched_30("openclaw")
    out.append(("1", "fork 后动过率（fork 龄 ≤30 天）", a, b, "高=真"))

    # 2 ≤5 分钟 star+fork（限 star 过目标仓）
    def star5(tag):
        cs = [r for r in data[tag]["cs"]
              if r.get("starred_target") == "1"
              and r.get("star_fork_delay_h") not in (None, "")]
        hit = sum(1 for r in cs if abs(num(r["star_fork_delay_h"])) <= 5 / 60)
        return hit, len(cs)
    out.append(("2", "≤5 分钟 star+fork", star5("rea"), star5("openclaw"), "高=可疑"))

    # 3 先 star 后 fork
    def star_before(tag):
        cs = [r for r in data[tag]["cs"]
              if r.get("starred_target") == "1"
              and r.get("star_before_fork") not in (None, "")]
        return sum(1 for r in cs if r["star_before_fork"] == "1"), len(cs)
    out.append(("3", "先 star 后 fork", star_before("rea"), star_before("openclaw"), "高=可疑"))

    # 4 零粉丝
    out.append(("4", "零粉丝", rate("rea", "followers_zero"), rate("openclaw", "followers_zero"), "高=可疑"))

    # 5 star 过目标仓
    def star_tgt(tag):
        cs = stat(tag, "starred_target")
        return sum(1 for r in cs if r["starred_target"] == "1"), len(cs)
    out.append(("5", "star 过目标仓", star_tgt("rea"), star_tgt("openclaw"), "高=可疑"))

    # 6 协作徽章
    out.append(("6", "协作徽章（Pair Extraordinaire）", rate("rea", "has_collab"),
                rate("openclaw", "has_collab"), "高=真"))

    # 7 Pull Shark
    out.append(("7", "Pull Shark（合并过 PR）", rate("rea", "has_pull_shark"),
                rate("openclaw", "has_pull_shark"), "高=真"))

    # 8 自建仓 fork 后更新过（8–60 天桶内合计）
    def own_upd(tag):
        cs = [r for r in data[tag]["cs"]
              if r.get("own_updated_after_fork") not in (None, "")
              and num(r.get("fork_age_days")) is not None
              and 7 < num(r["fork_age_days"]) <= 60]
        return sum(1 for r in cs if r["own_updated_after_fork"] == "1"), len(cs)
    out.append(("8", "自建仓 fork 后更新过（8–60 天桶）", own_upd("rea"), own_upd("openclaw"), "高=真"))

    # 9 仓名含目标仓
    out.append(("9", "自建仓名含目标仓", rate("rea", "repo_named_target"),
                rate("openclaw", "repo_named_target"), "高=可疑"))

    # 10 活跃 ≤5 天
    out.append(("10", "全年活跃 ≤5 天", rate("rea", "active_le5"), rate("openclaw", "active_le5"), "高=可疑"))

    # 11 爆发窗成员（逐人）
    r11 = (data["rea"]["ib"], data["rea"]["tot"])
    o11 = (data["openclaw"]["ib"], data["openclaw"]["tot"])
    out.append(("11", "fork 落在爆发窗内（逐人）", r11, o11, "高=可疑"))

    # 12 零开发事件（限有事件的人）
    def zerodev(tag):
        cs = [r for r in data[tag]["cs"]
              if num(r.get("events_n")) and num(r["events_n"]) > 0
              and r.get("dev_event_ratio") not in (None, "")]
        return sum(1 for r in cs if (num(r["dev_event_ratio"]) or 0) == 0), len(cs)
    out.append(("12", "近 30 条事件零开发动作", zerodev("rea"), zerodev("openclaw"), "高=可疑"))

    # 13 单点账号：唯一 fork 就是目标仓，且没有任何自建仓
    def single_point(tag):
        tgt = SPECS[tag][2].split("/")[1]
        cs = [r for r in data[tag]["cs"]
              if r.get("own_count") not in (None, "") and r.get("fork_count") not in (None, "")
              and num(r.get("fork_count")) is not None and num(r.get("own_count")) is not None]
        # 只看分页完整的样本（否则 fork_count 可能被截断）
        hit = 0
        for r in cs:
            if num(r["fork_count"]) != 1 or num(r["own_count"]) != 0:
                continue
            hit += 1
        return hit, len(cs)
    a13, b13 = single_point("rea"), single_point("openclaw")
    out.append(("13", "单点账号（唯一 fork 是目标仓 且 无自建仓）", a13, b13, "高=可疑"))

    def one_fork(tag):
        cs = [r for r in data[tag]["cs"] if num(r.get("fork_count")) is not None]
        return sum(1 for r in cs if num(r["fork_count"]) == 1), len(cs)
    out.append(("14", "只有 1 个 fork", one_fork("rea"), one_fork("openclaw"), "高=可疑"))

    # ---- 输出 markdown ----
    L = ["| # | 指标 | rea | openclaw | 差异 | 方向 |", "|--:|---|---:|---:|---:|---|"]
    for no, name, (ra, na), (rb, nb), direction in out:
        pa, pb = ra / na * 100 if na else 0, rb / nb * 100 if nb else 0
        L.append(f"| {no} | **{name}** | {pa:.1f}% ({ra:,}/{na:,}) | "
                 f"{pb:.1f}% ({rb:,}/{nb:,}) | **{pa-pb:+.1f}pp** | {direction} |")
    md = "\n".join(L)
    print("\n" + md)
    open(os.path.join(OUT, "effective_indicators.md"), "w", encoding="utf-8").write(md + "\n")

    # 补充：中位数类
    L2 = ["| 指标 | rea 中位 | openclaw 中位 | 差异 |", "|---|---:|---:|---:|"]
    for key, name in (("active_days", "全年活跃天"), ("total_contribs", "年度贡献"),
                      ("followers", "粉丝数"), ("following", "关注数"),
                      ("follower_ratio", "粉丝/关注比"), ("starred_count", "star 总数"),
                      ("own_count", "自建仓数"), ("commit_total", "自建仓提交数"),
                      ("star_fork_delay_h", "star→fork 延迟(小时)")):
        ma, na = med("rea", key)
        mb, nb = med("openclaw", key)
        if ma is None or mb is None:
            continue
        L2.append(f"| {name} | {ma:,.2f} (n={na:,}) | {mb:,.2f} (n={nb:,}) | {mb-ma:+,.2f} |")
    md2 = "\n".join(L2)
    print("\n" + md2)
    open(os.path.join(OUT, "effective_medians.md"), "w", encoding="utf-8").write(md2 + "\n")


if __name__ == "__main__":
    main()
