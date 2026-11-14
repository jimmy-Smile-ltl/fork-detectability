# -*- coding: utf-8 -*-
"""S5 分层抽样：600 人 full 档名单（用户 2026-10-31 拍板：600 人 × full）。

分层设计（数据采集方案.md §四 S5；主战场 = openclaw star 群体，两极参照 + 对照）：

  P0a openclaw 同秒批量账号      200  最可疑（同秒 ≥2 actor 批次内账号，确定性抽 200）
  P0b rea 10月点星者              80  可疑参照（rea = 强异常仓，42.8% 单-star）
  P0c BettaFish 25-11 峰值月      80  健康参照（BettaFish = 初判有机爆红）
  P2  openclaw 爆发周点星者      120  主战场代表样本（CH 事件最大周）
  P3  早期 forker（25-11/12）    100  对照（fork 群体试点偏真人）
  P4  点名账号（owner/名人）        6  锚点：rea/BettaFish owner + 名人对照
  合计                            586

确定性：md5(login) 排序后取前 N（可复现，不依赖 CH 并行顺序）。
输出：data/raw/user_sample_s5_600.txt（采集器输入）+ .meta.jsonl（cohort 标签，
  分析时按 login join 回来——采集器只认纯名单，cohort 信息不进采集链路）。
"""
import glob
import hashlib
import json
import os
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT_TXT = os.path.join(RAW, "user_sample_s5_600.txt")
OUT_META = os.path.join(RAW, "user_sample_s5_600.meta.jsonl")

N_P0A, N_P0B, N_P0C, N_P2, N_P3 = 200, 80, 80, 120, 100
# 锚点账号：仓库作者 / 名人对照（公开）。实证小号等私有锚点放
# data/named_anchors.txt（gitignored，每行一个 login），存在则自动并入——
# 涉及对第三方账号定性的名单不入公开仓库。
P4_NAMED = ["storytold", "morluto", "666ghj",           # rea 原/现 owner + BettaFish owner
            "steipete", "mneves75", "OptionalCoin"]     # 名人/首个 CH actor 锚点
_ANCHOR_FILE = os.path.join(RAW, "named_anchors.txt")
if os.path.exists(_ANCHOR_FILE):
    P4_NAMED += [l.strip() for l in open(_ANCHOR_FILE, encoding="utf-8")
                 if l.strip() and not l.startswith("#")]


def load_ch(path):
    """CH 导出文件 → 行列表（分页包装 {task, page, body}；body 行有两种形态：
    openclaw 早期导出 = dict{actor_login, starred_ts, ...}；rea/BettaFish 扩样
    导出 = 裸数组 [actor_login, starred_ts, repo_name_observed]——统一成 dict）。"""
    rows = []
    for line in open(path, encoding="utf-8"):
        body = json.loads(line).get("body")
        if not isinstance(body, list):
            continue
        for r in body:
            if isinstance(r, dict):
                rows.append(r)
            elif isinstance(r, list) and len(r) >= 2:
                rows.append({"actor_login": r[0], "starred_ts": int(r[1]),
                             "repo_name_observed": r[2] if len(r) > 2 else None})
    return rows


def det_sample(logins, n, exclude):
    """确定性抽样：md5(login) 排序取前 N（排除已选 + 已删号 404 由采集器落墓碑）。"""
    pool = sorted(set(logins) - exclude, key=lambda x: hashlib.md5(x.encode()).hexdigest())
    return pool[:n]


def main():
    # ---- P0a：openclaw 同秒批量（≥2 actor/秒）内的账号 ----
    sec_rows = load_ch(os.path.join(RAW, "ch_star_seconds", "openclaw__openclaw.jsonl"))
    batch_actors = [a for r in sec_rows if r.get("n", 1) >= 2 for a in r.get("actors", [])]
    # ---- P2：openclaw 爆发周（CH 事件最大周）点星者 ----
    ev_rows = load_ch(os.path.join(RAW, "ch_star_events", "openclaw__openclaw.jsonl"))
    weeks = {}
    for r in ev_rows:
        wk = datetime.fromtimestamp(r["starred_ts"], tz=timezone.utc).strftime("%G-W%V")
        weeks.setdefault(wk, set()).add(r["actor_login"])
    burst_week = max(weeks, key=lambda k: len(weeks[k]))
    burst_actors = weeks[burst_week]
    # ---- P0b/P0c：rea / BettaFish 参照池 ----
    rea_actors = {r["actor_login"] for r in load_ch(os.path.join(RAW, "ch_star_events", "morluto__rea.jsonl"))}
    bf_rows = load_ch(os.path.join(RAW, "ch_star_events", "666ghj__BettaFish.jsonl"))
    bf_peak = {r["actor_login"] for r in bf_rows
               if datetime.fromtimestamp(r["starred_ts"], tz=timezone.utc).strftime("%Y-%m") == "2025-11"}
    # ---- P3：早期 forker（2025-11/12 fork openclaw）----
    early_forkers = []
    for line in open(os.path.join(RAW, "probe_forks_all.jsonl"), encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        ca = r.get("fork_created_at") or r.get("created_at") or ""
        if ca[:7] in ("2025-11", "2025-12"):
            early_forkers.append(r["login"])

    print(f"池规模：同秒批量 {len(set(batch_actors))} | 爆发周 {burst_week} {len(burst_actors)} | "
          f"rea {len(rea_actors)} | BettaFish 峰值月 {len(bf_peak)} | 早期 forker {len(set(early_forkers))}")

    picked, meta = [], []
    chosen = set()

    def take(cohort, logins, n):
        sel = det_sample(logins, n, chosen)
        chosen.update(sel)
        picked.extend(sel)
        meta.extend({"login": l, "cohort": cohort} for l in sel)
        print(f"  {cohort}: 取 {len(sel)}")

    take("P0a_same_second_openclaw", batch_actors, N_P0A)
    take("P0b_rea_oct", rea_actors, N_P0B)
    take("P0c_bettafish_peak", bf_peak, N_P0C)
    take("P2_burst_week_openclaw", burst_actors, N_P2)
    take("P3_early_forker", early_forkers, N_P3)
    take("P4_named", P4_NAMED, len(P4_NAMED))

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(picked) + "\n")
    with open(OUT_META, "w", encoding="utf-8") as f:
        for m in meta:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"合计 {len(picked)} 人（去重后 {len(chosen)}）→ {OUT_TXT}")


if __name__ == "__main__":
    main()
