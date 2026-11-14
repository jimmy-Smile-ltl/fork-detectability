# -*- coding: utf-8 -*-
"""信号优化三轮（对应 repo_metrics_compare.md §七 遗留问题）：
S1 对照组扩充：vscode/react/linux/dify/browser-use —— watch/星比 + punch_card 夜间占比 + 近周星速
   → 把"弱信号"的基线从 2 个同类仓扩到 5+ 巨型仓
S2 夜间提交作者归因：CH 10月 PushEvent 按 actor_login × 小时分布
   → 判定 35% 夜间提交是 bot 军团还是 steipete 本人（结论性质完全不同）
S3 issue 区 CONTRIBUTOR 作者画像：openclaw 最新500条 issue 中 CONTRIBUTOR 身份作者逐个采
   → 判定 38% 占比是真人深度参与还是账号刷存在感

每阶段独立落盘，已存在则跳过（可断点续跑）。
输出:
  data/raw/control_group_metrics.json    (S1)
  data/raw/ch_push_hours.json            (S2)
  data/raw/issue_contrib_authors.jsonl   (S3)
"""
import datetime
import json
import os
import time

from curl_cffi import requests as cffi

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
      "Accept": "application/vnd.github+json"}
PROFILES = ["chrome", "firefox133", "safari17_0", "edge101"]
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "raw")

CONTROLS = [
    "microsoft/vscode",
    "facebook/react",
    "torvalds/linux",
    "langgenius/dify",
    "browser-use/browser-use",
]


def get(url, params=None, wait=None):
    for attempt in range(16):
        for prof in PROFILES:
            try:
                time.sleep(wait if wait else 1.3)
                r = cffi.get(url, params=params, headers=UA, timeout=30, impersonate=prof)
                if r.status_code == 202:  # stats 计算中，稍后重试
                    time.sleep(3)
                    continue
                return r
            except Exception:
                time.sleep(2.5)
        attempt += 1
        print(f"    [suppression] 退避 {20+attempt*10}s (轮次{attempt})...")
        time.sleep(20 + attempt * 10)
    raise RuntimeError(f"all profiles failed: {url}")


def night_share(punch):
    """punch_card: [[dow, hour, count], ...] → 夜间(<6 或 >=22, UTC)占比"""
    if not isinstance(punch, list) or not punch:
        return None
    total = sum(c for _, _, c in punch)
    night = sum(c for _, h, c in punch if h < 6 or h >= 22)
    work = sum(c for _, h, c in punch if 9 <= h < 19)
    return {"night_pct": round(night / total * 100, 1),
            "work_pct": round(work / total * 100, 1), "total": total}


# ───────────────────────── S1 对照组扩充 ─────────────────────────
def s1_control_group():
    path = os.path.join(OUT, "control_group_metrics.json")
    if os.path.exists(path):
        print("S1 已采，跳过")
        return json.load(open(path, encoding="utf-8"))
    result = {}
    for full in CONTROLS:
        print(f"[S1] {full} ...")
        try:
            r = get(f"https://api.github.com/repos/{full}")
            d = r.json()
            rec = {"stars": d.get("stargazers_count"),
                   "forks": d.get("forks_count"),
                   "watch": d.get("subscribers_count"),
                   "created": d.get("created_at", "")[:10],
                   "watch_star_pct": round(d.get("subscribers_count", 0) / max(d.get("stargazers_count", 1), 1) * 100, 3)}
            # punch_card（202 自动重试已在 get 内处理）
            r2 = get(f"https://api.github.com/repos/{full}/stats/punch_card")
            if r2.status_code == 200:
                rec["punch"] = night_share(r2.json())
            # 近周星速
            r3 = get(f"https://api.github.com/repos/{full}/stargazers/history")
            if r3.status_code == 200 and r3.json():
                last = r3.json()[-1]
                rec["stars_last_week"] = last.get("total")
                rec["stars_per_day"] = round(last.get("total", 0) / 7)
            result[full] = rec
            print(f"    {rec['stars']}★ watch/星={rec['watch_star_pct']}% "
                  f"夜={(rec.get('punch') or {}).get('night_pct')}% 周★={rec.get('stars_last_week')}")
        except Exception as e:
            result[full] = {"error": str(e)}
            print(f"    失败: {e}")
    json.dump(result, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return result


# ───────────────────────── S2 夜间提交作者归因 ─────────────────────────
def s2_push_hours():
    path = os.path.join(OUT, "ch_push_hours.json")
    if os.path.exists(path):
        print("S2 已采，跳过")
        return json.load(open(path, encoding="utf-8"))
    q = """
SELECT repo_name, actor_login, toHour(created_at) h, count() c
FROM github_events
WHERE repo_name IN ('openclaw/openclaw','ollama/ollama','n8n-io/n8n')
  AND event_type = 'PushEvent'
  AND created_at >= '2026-10-01'
GROUP BY repo_name, actor_login, h
ORDER BY repo_name, actor_login, h
FORMAT JSONCompact
"""
    r = cffi.post("https://play.clickhouse.com/", data=q.encode(),
                  params={"user": "explorer", "database": "default"},
                  headers={"User-Agent": "research-probe"}, timeout=60, impersonate="chrome")
    rows = r.json()["data"]  # JSONCompact: {meta, data, rows, statistics}
    # repo -> actor -> {hour: count}
    data = {}
    for repo, actor, h, c in rows:
        data.setdefault(repo, {}).setdefault(actor, {})[h] = c
    result = {}
    for repo, actors in data.items():
        tot_n = tot_w = tot = 0
        per_actor = {}
        for actor, hours in actors.items():
            a_tot = sum(hours.values())
            a_night = sum(c for h, c in hours.items() if h < 6 or h >= 22)
            a_work = sum(c for h, c in hours.items() if 9 <= h < 19)
            tot += a_tot
            tot_n += a_night
            tot_w += a_work
            per_actor[actor] = {"total": a_tot, "night_pct": round(a_night / a_tot * 100, 1),
                                "work_pct": round(a_work / a_tot * 100, 1)}
        top = dict(sorted(per_actor.items(), key=lambda x: -x[1]["total"])[:8])
        result[repo] = {"repo_night_pct": round(tot_n / max(tot, 1) * 100, 1),
                        "repo_work_pct": round(tot_w / max(tot, 1) * 100, 1),
                        "n_actors": len(actors), "n_push_events": tot,
                        "top_actors": top}
    json.dump(result, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for repo, v in result.items():
        print(f"[S2] {repo}: push事件 {v['n_push_events']} | 夜间 {v['repo_night_pct']}% | {v['n_actors']} 推送者")
        for a, st in v["top_actors"].items():
            print(f"      {a:28s} {st['total']:5d} 次 | 夜 {st['night_pct']:5.1f}% | 白 {st['work_pct']:5.1f}%")
    return result


# ───────────────────────── S3 CONTRIBUTOR 作者画像 ─────────────────────────
def s3_issue_contrib_authors():
    path = os.path.join(OUT, "issue_contrib_authors.jsonl")
    done = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                done.add(json.loads(line).get("login"))
            except json.JSONDecodeError:
                pass
        print(f"S3 已采 {len(done)} 人，续采")
    # 先收作者名单（issue 分页缓存到内存，不落盘）
    authors = []
    seen = set()
    for pg in range(1, 6):
        r = get("https://api.github.com/repos/openclaw/openclaw/issues",
                params={"state": "all", "per_page": 100, "page": pg,
                        "sort": "created", "direction": "desc"})
        if r.status_code != 200:
            break
        batch = [x for x in r.json() if "pull_request" not in x]
        if not batch:
            continue
        for it in batch:
            if it.get("author_association") == "CONTRIBUTOR":
                u = (it.get("user") or {}).get("login")
                if u and u not in seen:
                    seen.add(u)
                    authors.append(u)
    print(f"[S3] 最新500条 issue 中 CONTRIBUTOR 去重作者 {len(authors)} 人")
    with open(path, "a", encoding="utf-8") as out:
        for login in authors:
            if login in done:
                continue
            try:
                r = get(f"https://api.github.com/users/{login}")
                d = r.json()
                rec = {"login": login, "type": d.get("type"),
                       "created_at": d.get("created_at"),
                       "age_days": (datetime.date(2026, 10, 25) - datetime.date.fromisoformat(
                           (d.get("created_at") or "2026-10-25")[:10])).days if d.get("created_at") else None,
                       "followers": d.get("followers"), "following": d.get("following"),
                       "public_repos": d.get("public_repos"),
                       "hireable": d.get("hireable"), "name": d.get("name"),
                       "company": d.get("company"), "location": d.get("location"),
                       "bio": (d.get("bio") or "")[:80]}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                print(f"    {login}: {rec['age_days']}天龄 {d.get('followers')}粉 {d.get('public_repos')}仓")
            except Exception as e:
                print(f"    {login}: 失败 {e}")
    # 简易汇总
    recs = [json.loads(l) for l in open(path, encoding="utf-8")]
    ages = [x["age_days"] for x in recs if x.get("age_days") is not None]
    repos = [x["public_repos"] for x in recs if x.get("public_repos") is not None]
    fols = [x["followers"] for x in recs if x.get("followers") is not None]
    if ages:
        ages.sort(); repos.sort(); fols.sort()
        n = len(ages)
        print(f"[S3] 汇总: n={n} | 年龄中位 {ages[n//2]}天 <90天 {sum(1 for a in ages if a<90)}人"
              f" | 仓库中位 {repos[len(repos)//2]} | 粉丝中位 {fols[len(fols)//2]}")
    return recs


if __name__ == "__main__":
    print("── S1 对照组扩充 ──")
    s1_control_group()
    print()
    print("── S2 夜间提交作者归因（CH） ──")
    s2_push_hours()
    print()
    print("── S3 CONTRIBUTOR 作者画像 ──")
    s3_issue_contrib_authors()
