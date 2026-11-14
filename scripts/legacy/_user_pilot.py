# -*- coding: utf-8 -*-
"""S5 试采：10 个 fork 用户的主页信息全维度探测。

从全量 fork 数据抽 10 个有代表性的 login，每人采：
  D1 /users/{login}            档案
  D2 /users/{login}/events     90 天活动（类型分布）
  D3 /users/{login}/starred    star 的仓库（取第1页+尾页计数）
  D4 /users/{login}/orgs       组织
  D5 /users/{login}/repos      创建的仓库（取第1页+尾页计数）
  D6 /users/{login}/received_events  可选，跳过（与 D2 信息重叠）
  D7 /users/{login}/subscriptions    watch 的仓库
  issue 活动不单独采（无聚合端点，需逐仓翻——成本高，用 events 里的
  IssuesEvent/IssueCommentEvent 做替代指标）

输出: data/raw/user_profiles_pilot.jsonl + 控制台摘要
"""
import json
import os
import random
import re
import time
import datetime

from curl_cffi import requests as cffi

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
      "Accept": "application/vnd.github+json"}
PROFILES = ["chrome", "firefox133", "safari17_0", "edge101"]
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "raw", "user_profiles_pilot.jsonl")

REQ_T0 = time.time()
REQ_N = 0


def get(url, params=None):
    """指纹轮换 + 每指纹重试2次 + 节流（匿名 60/h 纪律）。"""
    global REQ_N
    for prof in PROFILES + PROFILES:
        for _ in range(2):
            try:
                time.sleep(1.3)  # 节流
                r = cffi.get(url, params=params, headers=UA, timeout=25,
                             impersonate=prof)
                REQ_N += 1
                return r
            except Exception:
                time.sleep(1.5)
    raise RuntimeError(f"all profiles failed: {url}")


def last_page(r):
    link = r.headers.get("link", "")
    for part in link.split(","):
        if 'rel="last"' in part:
            try:
                return int(part.split("&page=")[1].split(">")[0])
            except (IndexError, ValueError):
                pass
    return None


def pick_sample(k=10):
    """从 fork 全量数据分层抽 k 个 login。

    分层：早期真人(3) + 爆发窗(3) + 零提交fork(2) + 随机(2)
    """
    rows = []
    with open(os.path.join(BASE, "data", "raw", "probe_forks_all.jsonl"),
              "r", encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    random.seed(42)

    early = [r for r in rows if r["fork_created_at"] < "2025-12-15"]
    burst = [r for r in rows if "2026-01-25" <= r["fork_created_at"][:10] <= "2026-02-03"]
    untouched = [r for r in rows if r.get("fork_touched") == 0]

    sample = []
    sample += random.sample(early, min(3, len(early)))
    sample += random.sample(burst, min(3, len(burst)))
    sample += random.sample(untouched, min(2, len(untouched)))
    sample += random.sample(rows, min(2, len(rows)))
    return sample[:k]


def collect_user(login, fork_ctx=None):
    """采单人全维度。fork_ctx = 该人的 fork 行为数据（免费旁证）。"""
    rec = {"login": login, "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}

    # D1 档案
    r = get(f"https://api.github.com/users/{login}")
    if r.status_code == 404:
        rec["status"] = "gone"  # 蒸发账号
        return rec
    if r.status_code != 200:
        rec["status"] = f"http_{r.status_code}"
        return rec
    p = r.json()
    rec["status"] = "ok"
    rec["type"] = p.get("type")
    rec["created_at"] = p.get("created_at")
    rec["updated_at"] = p.get("updated_at")
    rec["public_repos"] = p.get("public_repos")
    rec["public_gists"] = p.get("public_gists")
    rec["followers"] = p.get("followers")
    rec["following"] = p.get("following")
    rec["company"] = p.get("company")
    rec["location"] = p.get("location")
    rec["blog"] = p.get("blog")
    rec["email"] = p.get("email")
    rec["bio"] = p.get("bio")
    rec["hireable"] = p.get("hireable")
    rec["avatar_url"] = p.get("avatar_url")

    # D2 事件（90天）
    r = get(f"https://api.github.com/users/{login}/events", params={"per_page": 100})
    types, days = {}, set()
    first_ts = last_ts = None
    if r.status_code == 200:
        for e in r.json():
            types[e["type"]] = types.get(e["type"], 0) + 1
            days.add(e["created_at"][:10])
            if first_ts is None:
                first_ts = e["created_at"]
            last_ts = e["created_at"]
    rec["events_count"] = sum(types.values())
    rec["events_types"] = types
    rec["active_days_90d"] = len(days)
    rec["last_event_at"] = last_ts

    # D3 starred：尾页计数 + 第1页前10个仓
    r = get(f"https://api.github.com/users/{login}/starred",
            params={"per_page": 10, "sort": "created"})
    rec["starred_total"] = last_page(r) if r.status_code == 200 else None
    rec["starred_sample"] = ([s["full_name"] for s in r.json()]
                             if r.status_code == 200 else [])

    # D7 subscriptions(watch)
    r = get(f"https://api.github.com/users/{login}/subscriptions",
            params={"per_page": 10})
    rec["watch_total"] = last_page(r) if r.status_code == 200 else None
    rec["watch_sample"] = ([s["full_name"] for s in r.json()]
                           if r.status_code == 200 else [])

    # D5 创建的仓库：尾页 + 第1页明细（sort=created 最早的）
    r = get(f"https://api.github.com/users/{login}/repos",
            params={"per_page": 10, "sort": "created"})
    rec["repos_total"] = last_page(r) if r.status_code == 200 else None
    repos = []
    if r.status_code == 200:
        for it in r.json():
            repos.append({
                "name": it.get("name"), "fork": it.get("fork"),
                "created_at": it.get("created_at"),
                "pushed_at": it.get("pushed_at"),
                "stargazers_count": it.get("stargazers_count"),
                "language": it.get("language"), "size_kb": it.get("size"),
            })
    rec["repos_sample_oldest"] = repos

    # 维护的仓库 = 推送时间排序的第1页（当前活跃维护面）
    r = get(f"https://api.github.com/users/{login}/repos",
            params={"per_page": 10, "sort": "pushed"})
    maintained = []
    if r.status_code == 200:
        for it in r.json():
            maintained.append({
                "name": it.get("name"), "fork": it.get("fork"),
                "pushed_at": it.get("pushed_at"),
                "stargazers_count": it.get("stargazers_count"),
                "language": it.get("language"),
            })
    rec["repos_sample_maintained"] = maintained

    # D4 组织
    r = get(f"https://api.github.com/users/{login}/orgs")
    rec["orgs"] = [o.get("login") for o in (r.json() or [])] if r.status_code == 200 else None

    # issue/PR 活动无聚合端点 → 用 events 里的类型计数替代（已在 D2）

    # 免费旁证：fork 行为
    if fork_ctx:
        rec["fork_ctx"] = fork_ctx
    return rec


def main():
    sample = pick_sample(10)
    print(f"抽取 10 个 fork 用户（分层：早期3/爆发窗3/零提交2/随机2）")
    out = open(OUT, "a", encoding="utf-8")
    for i, row in enumerate(sample, 1):
        login = row["login"]
        ctx = {"fork_created_at": row["fork_created_at"],
               "fork_touched": row.get("fork_touched"),
               "fork_stars": row.get("fork_stars")}
        try:
            rec = collect_user(login, ctx)
        except Exception as e:
            rec = {"login": login, "status": f"error: {type(e).__name__}"}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        # 摘要行
        if rec.get("status") == "gone":
            print(f"[{i}/10] {login}: ☠ 蒸发账号(404)")
            continue
        if rec.get("status") != "ok":
            print(f"[{i}/10] {login}: {rec.get('status')}")
            continue
        age_days = (datetime.datetime.now(datetime.timezone.utc)
                    - datetime.datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00"))).days
        ev = rec.get("events_types", {})
        dev = ev.get("PushEvent", 0) + ev.get("PullRequestEvent", 0)
        print(f"[{i}/10] {login}: {rec['type']} 注册{age_days//365}年 "
              f"repos={rec['public_repos']} starred={rec['starred_total']} "
              f"watch={rec['watch_total']} followers={rec['followers']} "
              f"orgs={len(rec.get('orgs') or [])} "
              f"| 90d事件={rec['events_count']}(开发{dev}) "
              f"活跃{rec['active_days_90d']}天 "
              f"| fork后推送={rec.get('fork_ctx',{}).get('fork_touched')}")
    out.close()
    print(f"\n共 {REQ_N} 请求，{(time.time()-REQ_T0)/60:.1f} 分钟 → {OUT}")


if __name__ == "__main__":
    main()
