# -*- coding: utf-8 -*-
"""采集巡检：一次看清「在采什么 / 采到哪 / 数据健康不健康」。

回答三个问题：
  1. **进程还在写吗**——文件多久没更新了（STALL 判定）
  2. **采到的数据干净吗**——坏行、重复主键、http 分布、各单元成功率
  3. **离目标还有多远**——覆盖数 vs 期望总量、采样完整性、状态集合一致性

用法：
    py _patrol.py                # 一次巡检
    py _patrol.py --watch 300    # 每 5 分钟一次（Ctrl-C 停）

输出：控制台摘要 + data/out/patrol.md（覆盖写，适合反复看）
"""
import argparse
import datetime
import json
import os
import sys
import time
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")

# 每个数据集的期望规模（用于算覆盖率；None = 不按规模判定）
DATASETS = [
    {"name": "rea fork 名单", "path": "forks_all_morluto__rea.jsonl", "kind": "forks",
     "expect": 107836, "unit": "条"},
    {"name": "openclaw fork 名单", "path": "forks_all_openclaw__openclaw.jsonl", "kind": "forks",
     "expect": 82790, "unit": "条"},
    {"name": "rea 全量 screen", "path": "profile_tier_screen_rea_all.jsonl", "kind": "profile",
     "expect": 108340, "unit": "人", "units": ("d1",)},
    {"name": "openclaw 全量 screen", "path": "profile_tier_screen_openclaw_all.jsonl", "kind": "profile",
     "expect": 82574, "unit": "人", "units": ("d1",)},
    {"name": "rea 50 人 deep", "path": "profile_tier_deep_rea_p98.jsonl", "kind": "profile",
     "expect": 50, "unit": "人", "units": ("d1", "d2", "d3", "d4", "d4l", "d6", "d8")},
]

STALE_MIN = 15          # 超过这么多分钟没写入 = STALL
ERR_WARN = 0.05         # 错误率告警线


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def scan(ds):
    path = os.path.join(RAW, ds["path"])
    info = {"name": ds["name"], "path": ds["path"], "expect": ds.get("expect"),
            "unit": ds.get("unit", ""), "exists": os.path.exists(path)}
    if not info["exists"]:
        info["verdict"] = "MISSING"
        return info

    st = os.stat(path)
    info["mb"] = st.st_size / 1024 / 1024
    info["mtime_age_min"] = (time.time() - st.st_mtime) / 60

    rows = bad = 0
    keys = set()
    dup = 0
    http = Counter()
    units = Counter()
    recent = 0
    now = utcnow()
    last_collected = None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        rows += 1
        key = r.get("fork_id") or (r.get("login") or "").lower()
        if key in keys:
            dup += 1
        keys.add(key)
        if ds["kind"] == "profile":
            http[r.get("http")] += 1
            for u in ds.get("units", ()):
                v = r.get(u)
                if isinstance(v, dict) and v.get("http") == 200:
                    units[u] += 1
        c = parse_ts(r.get("collected_at"))
        if c:
            last_collected = max(last_collected or c, c)
            if (now - c).total_seconds() <= 600:
                recent += 1

    info.update({"rows": rows, "bad": bad, "dup": dup, "uniq": len(keys),
                 "recent_10min": recent,
                 "last_collected_age_min": ((now - last_collected).total_seconds() / 60
                                            if last_collected else None)})
    if ds["kind"] == "profile":
        info["http"] = dict(http)
        info["units"] = dict(units)
        ok = http.get(200, 0)
        err = http.get(0, 0)
        info["err_rate"] = err / max(ok + err + http.get(404, 0), 1)
    if info["expect"]:
        info["coverage"] = rows / info["expect"]

    # 判定顺序：坏行 > 主动暂停 > 停滞 > 错误率 > 覆盖率
    # 主动暂停（我们手动 kill 的采集器）不能报成 STALL，否则每次都要人去分辨误报
    paused = set()
    pause_file = os.path.join(BASE, "data", "state", "paused.json")
    if os.path.exists(pause_file):
        try:
            paused = set(json.load(open(pause_file, encoding="utf-8")))
        except (OSError, ValueError):
            paused = set()

    if bad:
        info["verdict"] = "BAD_LINES"
    elif os.path.basename(ds["path"]) in paused:
        info["verdict"] = "PAUSED"
    elif info["mtime_age_min"] > STALE_MIN and (info.get("coverage") or 0) < 0.999:
        info["verdict"] = "STALL"
    elif ds["kind"] == "profile" and info.get("err_rate", 0) > ERR_WARN:
        info["verdict"] = "HIGH_ERROR"
    elif info["expect"] and info["rows"] >= info["expect"]:
        info["verdict"] = "DONE"
    else:
        info["verdict"] = "RUNNING" if info["mtime_age_min"] <= STALE_MIN else "IDLE"
    return info


def render(items):
    L = ["# 采集巡检", "",
         f"> 时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
         f"停滞阈值 {STALE_MIN} 分钟 · 错误率告警线 {ERR_WARN:.0%}", "",
         "## 一、总览", "",
         "| 数据集 | 状态 | 已采 | 目标 | 覆盖 | 最近写入 | 近10分钟 |",
         "|---|---|---:|---:|---:|---:|---:|"]
    icon = {"DONE": "✅ 完成", "RUNNING": "🔄 运行中", "IDLE": "⏸ 空闲",
            "PAUSED": "⏸ 已暂停（主动）", "STALL": "🛑 **停滞**",
            "HIGH_ERROR": "⚠️ 错误率高", "BAD_LINES": "❌ 坏行", "MISSING": "— 未开始"}
    for it in items:
        if not it["exists"]:
            L.append(f"| {it['name']} | {icon['MISSING']} | 0 | — | — | — | — |")
            continue
        cov = f"{it['coverage'] * 100:.1f}%" if it.get("coverage") else "—"
        L.append(f"| {it['name']} | {icon[it['verdict']]} | {it['rows']:,} | "
                 f"{it['expect']:,} | {cov} | {it['mtime_age_min']:.1f} 分钟前 | "
                 f"{it['recent_10min']:,} |")
    L.append("")

    L += ["## 二、数据质量", "", "| 数据集 | 坏行 | 重复键 | 唯一键 | 说明 |", "|---|---:|---:|---:|---|"]
    for it in items:
        if not it["exists"]:
            continue
        note = ""
        if it["dup"]:
            note = "重复键会让统计偏高"
        if it["bad"]:
            note = "有截断/半行，需重跑该段"
        L.append(f"| {it['name']} | {it['bad']} | {it['dup']} | {it['uniq']:,} | {note or '—'} |")
    L.append("")

    prof = [it for it in items if it.get("http")]
    if prof:
        L += ["## 三、账户采集的 http / 单元成功率", "",
              "| 数据集 | 200 | 404 | 0(失败) | 错误率 | 单元成功率 |",
              "|---|---:|---:|---:|---:|---|"]
        for it in prof:
            h = it["http"]
            units = " · ".join(f"{u}={it['units'].get(u, 0):,}" for u in sorted(it["units"])) or "—"
            L.append(f"| {it['name']} | {h.get(200, 0):,} | {h.get(404, 0):,} | {h.get(0, 0):,} | "
                     f"{it.get('err_rate', 0):.1%} | {units} |")
        L.append("")

    L += ["## 四、告警", ""]
    alerts = []
    for it in items:
        if not it["exists"]:
            continue
        if it["verdict"] == "BAD_LINES":
            alerts.append(f"- ❌ **{it['name']}** 有 {it['bad']} 坏行")
        if it["verdict"] == "STALL":
            alerts.append(f"- 🛑 **{it['name']}** 已 {it['mtime_age_min']:.0f} 分钟无写入（可能进程已死）")
        if it["verdict"] == "HIGH_ERROR":
            alerts.append(f"- ⚠️ **{it['name']}** 失败率 {it['err_rate']:.1%}（>5%），建议重跑失败墓碑")
        if it["dup"]:
            alerts.append(f"- ⚠️ **{it['name']}** 重复键 {it['dup']} 条")
    L += alerts or ["- 无告警"]
    L.append("")
    L += ["> 巡检只看**数据健康度**，不做判据分析。判据见 `_rehearsal_report.py` / `metrics_fork.py`。"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="每 N 秒重复一次")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "patrol.md")
    while True:
        items = [scan(ds) for ds in DATASETS]
        md = render(items)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(md)
        print(f"→ {out_path}")
        if not args.watch:
            break
        time.sleep(args.watch)
        print("\n" + "=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
