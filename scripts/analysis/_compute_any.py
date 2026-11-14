# -*- coding: utf-8 -*-
"""通用特征表：任意 tag（用统一信封构建）。

    TAG=shardeum FORKS=forks_all_shardeum__shardeum.jsonl TARGET=shardeum \
        python scripts/analysis/_compute_any.py


    python scripts/analysis/_compute_bettafish.py

输出：
    data/out/features_bettafish.csv     一人一行
    data/out/bettafish_judges.md        12 判据实测

与 `_compute_all_features.py` 的区别：
  后者读的是旧格式（http 整数 / 嵌套字段），本脚本读新信封（outcome/payload），
  但**特征名保持一致**，便于三方对照。
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "collect"))

from collect_core import DIMS, Outcome, dim_path, load_account  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")
TAG = os.environ.get("TAG", "bettafish")
TARGET = os.environ.get("TARGET", "bettafish")
NOW = datetime.now(timezone.utc).timestamp()

CODE_LANGS = {"Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C",
              "C++", "C#", "Ruby", "PHP", "Swift", "Kotlin", "Scala", "Shell",
              "Lua", "Dart", "Objective-C", "Haskell", "Elixir", "R", "MATLAB"}


def ts(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s).replace("Z", "")[:19],
                                 "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def load_forks() -> dict:
    """fork 名单：一人一条（最后一条胜出）。"""
    p = os.path.join(RAW, os.environ.get("FORKS", "forks_all_666ghj__BettaFish.jsonl"))
    out = {}
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if lg:
            out[lg] = r
    return out


_CACHE: dict = {}


def _load_dim(dim: str) -> dict:
    """一次性把某维度读进内存（成功优先）。**不要每人重读文件。**"""
    if dim in _CACHE:
        return _CACHE[dim]
    from collect_core import _payload_of
    path = dim_path(dim, TAG, RAW)
    m: dict = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            lg = (r.get("login") or "").lower()
            if not lg:
                continue
            oc = r.get("outcome") or "BAD_RESPONSE"
            prev = m.get(lg)
            # 成功优先：OK 覆盖一切，其余只在不重复时保留
            if prev is None or oc == "OK" or (prev[0] != "OK" and oc == "EMPTY"):
                m[lg] = (oc, _payload_of(r, dim))
    _CACHE[dim] = m
    return m


def _acc(lg: str) -> dict:
    """把某人的各维度从内存索引里拼出来。"""
    out = {}
    for dim in DIMS:
        m = _load_dim(dim)
        if lg in m:
            oc, payload = m[lg]
            out[f"{dim}_outcome"] = oc
            out[f"{dim}_data"] = payload if oc in ("OK", "EMPTY") else None
    return out


def build(lg: str, f: dict) -> dict:
    acc = _acc(lg)
    r: dict = {"login": lg}

    # ---- 时间轴（fork 名单自带，0 请求）----
    ft = ts(f.get("fork_created_at"))
    pt = ts(f.get("fork_pushed_at"))
    r["fork_ts"] = ft
    r["fork_age_days"] = round((NOW - ft) / 86400, 1) if ft else None
    r["fork_touched"] = (1 if (ft and pt and pt > ft) else
                         (0 if (ft and pt) else None))
    r["size_kb"] = f.get("size_kb")
    r["branch_other"] = 1 if (f.get("default_branch") or "").lower() not in (
        "main", "master", "") else 0
    r["fork_language"] = f.get("fork_language")
    r["fork_stars"] = f.get("fork_stars")
    r["owner_type"] = f.get("owner_type")

    # ---- D1 档案 ----
    d1 = acc.get("D1_data") or {}
    r["D1_outcome"] = acc.get("D1_outcome")
    ut = ts(d1.get("created_at"))
    r["acct_age_days"] = round((NOW - ut) / 86400, 1) if ut else None
    r["acct_age_at_fork_days"] = round((ft - ut) / 86400, 1) if (ut and ft) else None
    r["public_repos"] = d1.get("public_repos")
    r["public_gists"] = d1.get("public_gists")
    r["followers"] = d1.get("followers")
    r["following"] = d1.get("following")
    if d1.get("followers") is not None and d1.get("following") is not None:
        r["follower_ratio"] = round(d1["followers"] / max(d1["following"], 1), 2)
    r["followers_zero"] = (1 if d1.get("followers") == 0 else
                           (0 if d1 else None))
    r["following_zero"] = (1 if d1.get("following") == 0 else
                           (0 if d1 else None))
    r["user_type"] = d1.get("type")
    r["user_id"] = d1.get("user_id")
    filled = sum(1 for k in ("name", "bio", "blog", "location", "company",
                             "twitter_username") if (d1.get(k) or "").strip())
    r["filled_n"] = filled if d1 else None
    if d1:
        nm = (d1.get("name") or "").strip()
        loc = (d1.get("location") or "").strip()
        others = any((d1.get(k) or "").strip() for k in
                     ("company", "blog", "bio", "twitter_username"))
        r["shape"] = ("EMPTY" if filled == 0 else
                      "TEMPLATE" if (nm and loc and not others) else
                      "RICH" if filled >= 4 else "PARTIAL")
        if nm:
            toks = [t.lower() for t in re.split(r"[\s._-]+", nm) if len(t) >= 3]
            r["namematch"] = 1 if any(t in lg for t in toks) else 0

    # ---- D8H 仓库结构 ----
    d8 = acc.get("D8H_data") or {}
    r["D8H_outcome"] = acc.get("D8H_outcome")
    r["own_count"] = d8.get("own_count")
    r["fork_count"] = d8.get("fork_count")
    r["has_own"] = (1 if (d8.get("own_count") or 0) > 0 else
                    (0 if d8 else None))
    langs = {(x.get("lang") or "") for x in (d8.get("own_details") or [])}
    langs.discard("")
    r["own_langs_n"] = len(langs) if d8 else None
    r["own_has_code_lang"] = (1 if (langs & CODE_LANGS) else
                              (0 if d8 else None))
    names = [(x.get("name") or "").lower() for x in (d8.get("own_details") or [])]
    r["repo_named_target"] = 1 if any(TARGET in n for n in names) else 0
    r["has_profile_repo"] = d8.get("has_profile_repo")

    # ---- D14 头像 ----
    d14 = acc.get("D14_data") or {}
    r["D14_outcome"] = acc.get("D14_outcome")
    r["avatar_type"] = d14.get("type")
    r["avatar_photo"] = (1 if d14.get("type") == "photo" else
                         (0 if d14.get("type") else None))

    # ---- D15 主页（成就）----
    d15 = acc.get("D15_data") or {}
    r["D15_outcome"] = acc.get("D15_outcome")
    r["starred_count"] = d15.get("starred_count")
    r["achievement_n"] = d15.get("achievement_n")
    r["has_pull_shark"] = d15.get("has_pull_shark")
    r["has_collab"] = d15.get("has_collab")
    r["pinned_n"] = len(d15.get("pinned") or []) if d15 else None
    r["org_n"] = len(d15.get("orgs") or []) if d15 else None

    # ---- D2 事件 ----
    d2 = acc.get("D2_data") or {}
    r["D2_outcome"] = acc.get("D2_outcome")
    r["events_n"] = d2.get("events_n")
    r["dev_events"] = d2.get("dev_events")
    r["dev_event_ratio"] = d2.get("dev_event_ratio")
    r["has_push_ev"] = 1 if (d2.get("event_types") or {}).get("push") else (
        0 if d2 else None)

    # ---- D4L star ----
    d4 = acc.get("D4L_data") or {}
    r["D4L_outcome"] = acc.get("D4L_outcome")
    r["starred_count_api"] = d4.get("starred_count")
    stars = d4.get("stars") or []
    star_ts = [x for x in (ts(y.get("at")) for y in stars) if x]
    if ft and star_ts:
        near = min(star_ts, key=lambda x: abs(x - ft))
        r["star_fork_delay_h"] = round((ft - near) / 3600, 2)
        r["star_same_hour"] = 1 if abs(ft - near) <= 3600 else 0
        r["star_before_fork"] = 1 if near < ft else 0
    if star_ts:
        days = [int(x // 86400) for x in star_ts]
        r["star_active_days"] = len(set(days))
        r["star_max_same_day"] = max(Counter(days).values())

    # ---- D9 提交 ----
    d9 = acc.get("D9_data") or {}
    r["D9_outcome"] = acc.get("D9_outcome")
    r["commit_total"] = d9.get("commit_total")
    r["commit_zero"] = (1 if d9.get("commit_total") == 0 else
                        (0 if d9.get("commit_total") is not None else None))

    # ---- D13 日历 ----
    d13 = acc.get("D13_data") or {}
    r["D13_outcome"] = acc.get("D13_outcome")
    r["active_days"] = d13.get("active_days")
    r["total_contribs"] = d13.get("total_contribs")
    r["max_streak"] = d13.get("max_streak")
    if d13.get("active_days") is not None:
        r["active_le5"] = 1 if d13["active_days"] <= 5 else 0

    # 统一把布尔量转成 1/0 —— 否则 CSV 里是 True/False，比率会算成 0
    for k, v in list(r.items()):
        if isinstance(v, bool):
            r[k] = int(v)
    return r


def main() -> int:
    forks = load_forks()
    print(f"fork 名单 {len(forks):,} 人")
    rows = []
    for i, (lg, f) in enumerate(forks.items(), 1):
        rows.append(build(lg, f))
        if i % 1000 == 0:
            print(f"  {i:,}/{len(forks):,}", flush=True)

    keys = ["login"]
    seen = {"login"}
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, f"features_{TAG}.csv")
    with open(dst, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"→ {dst}  ({len(rows):,} 人 × {len(keys)} 列)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
