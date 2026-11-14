# -*- coding: utf-8 -*-
"""L4 形态层指标：把登录名里的时间戳解码，找"批次"。

为什么单独一个脚本：
  `_compute_all_features.py` 是**个人级**特征；这里是**群体级**指标
  （同一批注册/同一批生成才看得出来），必须两仓横向对比。

算三样：
  TSD  时间戳解码后的年份分布（批量生成的年份会聚集）
  TSC  解码值互差 <60 秒的账号对（同一脚本连续生成）
  BATCH 同一天注册的账号数超额比（vs 泊松期望）

数据源：fork 名单（login）+ D1（created_at），纯本地。

用法：py _form_layer_metrics.py
"""
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")

SPECS = {
    "rea": ("forks_all_morluto__rea.jsonl", "profile_tier_screen_rea_all.jsonl"),
    "openclaw": ("forks_all_openclaw__openclaw.jsonl", "profile_tier_screen_openclaw_all.jsonl"),
}


def decode_ts(login):
    """从登录名里找 10 位 Unix 秒时间戳（排除连号）。"""
    digs = re.findall(r"\d+", login)
    for d in sorted(digs, key=len, reverse=True):
        if 10 <= len(d) <= 11:
            v = int(d[:10])
            if 10 ** 9 <= v < 2 * 10 ** 9:
                # 排除 1111111111 / 1234567890 这类连号/序号
                if len(set(d)) <= 2:
                    continue
                try:
                    return v, datetime.fromtimestamp(v, tz=timezone.utc)
                except Exception:
                    continue
    return None, None


def main():
    report = []
    for tag, (fork_f, d1_f) in SPECS.items():
        logins = []
        for line in open(os.path.join(RAW, fork_f), encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            lg = (r.get("login") or "").strip()
            if lg:
                logins.append(lg)

        # ---- TSD / TSC ----
        decoded = {}
        for lg in logins:
            v, dt = decode_ts(lg)
            if v:
                decoded[lg] = (v, dt)
        years = Counter(dt.year for _, dt in decoded.values())
        vals = sorted(v for v, _ in decoded.values())
        pairs = 0
        for i in range(1, len(vals)):
            if vals[i] - vals[i - 1] < 60:
                pairs += 1
        min_gap = min((vals[i] - vals[i - 1] for i in range(1, len(vals))), default=None) / 3600 if len(vals) > 1 else None

        # ---- BATCH：同一天注册 ----
        reg = {}
        for line in open(os.path.join(RAW, d1_f), encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            lg = (r.get("login") or "").lower()
            d = ((r.get("d1") or {}).get("created_at") or "")[:10]
            if lg and d:
                reg[lg] = d
        day_cnt = Counter(reg.values())
        n = sum(day_cnt.values())
        days = len(day_cnt) if day_cnt else 1
        lam = n / days
        # 泊松尾部期望：P(X>=3) 的天数
        # P(X>=3) 必须用补集 1-P0-P1-P2 算，不能把 k 截断到 11
        # （λ=17 时泊松众数在 k=17，截断会把期望缩小几十倍——2026-11-14 实测踩过）
        p_le2 = math.exp(-lam) * (1 + lam + lam * lam / 2)
        exp_ge3 = days * max(0.0, 1.0 - p_le2)
        obs_ge3 = sum(1 for c in day_cnt.values() if c >= 3)
        top_days = day_cnt.most_common(5)

        L = [f"### {tag}", "",
             f"- 登录名可解码为时间戳：**{len(decoded):,}** / {len(logins):,} = {len(decoded)/len(logins)*100:.2f}%",
             f"- **TSC 批次聚集**：解码值互差 <60s 的相邻对 = **{pairs}**（最小间隔 "
             f"{f'{min_gap:.1f} 小时' if min_gap is not None else '—'}）",
             f"- **TSD 解码年份分布**：{dict(sorted(years.items()))}",
             f"- **BATCH 注册批次**：{n:,} 人分布在 {days:,} 天，日均 {lam:.1f} 人",
             f"  - 同一天注册 ≥3 人的天数：实测 **{obs_ge3:,}** vs 泊松期望 **{exp_ge3:,.1f}** "
             f"→ **{obs_ge3/max(exp_ge3,1e-9):.1f}×**",
             f"  - 注册最集中的 5 天：{[(d, c) for d, c in top_days]}", ""]
        report.append("\n".join(L))

    md = ("# L4 形态层指标\n\n> 纯字符串 + 注册时间，0 请求。\n\n" + "\n".join(report))
    print(md)
    open(os.path.join(BASE, "data", "out", "form_layer.md"), "w", encoding="utf-8").write(md)


if __name__ == "__main__":
    main()
