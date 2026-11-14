# -*- coding: utf-8 -*-
"""小仓库全量采集器：fork 少的样本仓端到端采集（fork 等信息）。

设计动机：openclaw（82k fork）这类巨型仓的全量要跑数天；挑 fork 少的仓把
「fork 名单全量 → fork owner 画像全量 → 单仓报告」整条管线在几小时内闭环。

三个采集单元（各自断点续采，按仓库名隔离文件）：
  forks    /repos/{r}/forks 全页（sort=oldest）→ forks_all_{key}.jsonl
           + fork owner 名单 fork_owners_{key}.txt（给 _profile_tiers.py --from-file）
  history  /repos/{r}/stargazers/history 翻页（周桶曲线）→ star_history_{key}.jsonl
  ch       ClickHouse star 身份 + 同秒聚簇 → ch_star_events/{key}.jsonl
                                                 ch_star_seconds_{key}.jsonl

用法：
  py _small_repo_collect.py --repo storytold/craft-repo-status-app                # 全部三单元
  py _small_repo_collect.py --repo ... --only forks                              # 只跑一个单元
  py _small_repo_collect.py --repo ... --ch-events                               # ch 单元另加同秒聚合

fork owner 画像（D1-D8 full 档）由 _profile_tiers.py --from-file 接力：
  py _profile_tiers.py --tier full --from-file data/raw/fork_owners_{key}.txt --out-suffix _{key}

纪律：全程匿名只读；ClickHouse 不带任何凭据（GhClient(auth=False) 同理，
本脚本直接 curl_cffi 匿名 POST，不经过代理池——play.clickhouse.com 无配额墙）。
"""
import argparse
import datetime
import json
import os
import time

from curl_cffi import requests as cffi

from _profile_lib import NodePool, quota_get

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")

CH_BASE = "https://play.clickhouse.com/"


def _key(full):
    """仓库名 → 文件后缀键（与 repo_metrics_{owner}__{repo} 命名一致）。"""
    return full.replace("/", "__")


def _append(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _collected_logins(path):
    """断点续采：已落盘的 fork_id / actor 集合。"""
    done = {"fork": set(), "actor": set()}
    if not os.path.exists(path):
        return done
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("fork_id"):
            done["fork"].add(r["fork_id"])
        if r.get("actor_login"):
            done["actor"].add(r["actor_login"])
    return done


# ── 单元 1：fork 名单全页 ────────────────────────────
def collect_forks(full):
    key = _key(full)
    out_path = os.path.join(RAW, f"forks_all_{key}.jsonl")
    owners_path = os.path.join(RAW, f"fork_owners_{key}.txt")
    seen = _collected_logins(out_path)["fork"]
    owners = set(l.strip() for l in open(owners_path, encoding="utf-8")) if os.path.exists(owners_path) else set()

    pool = NodePool()
    page_path = os.path.join(RAW, f"forks_all_{key}.page")
    page = 1
    if os.path.exists(page_path):
        try:
            page = max(1, int(open(page_path, encoding="utf-8").read().strip() or 1))
        except (OSError, ValueError):
            page = 1
    print(f"[forks] {full}：已落盘 {len(seen)} 条，从第 {page} 页开始翻页（sort=oldest，断点安全）")
    n_new = 0
    retry = 0
    while True:
        # 页级重试：节点池被共享租户吃光时，quota_get 会返回 0。
        # 早先「一次失败就 break」在 944 页的长跑里等于一遇到配额谷就整轮报废（2026-11-05 实测第 2 页即止）。
        st, r = quota_get(pool, f"https://api.github.com/repos/{full}/forks"
                               f"?per_page=100&sort=oldest&page={page}",
                          max_attempts=max(12, len(pool.nodes)))
        if st == 409:
            print("[forks] 空仓库（409），无 fork 可采")
            break
        if st != 200:
            retry += 1
            if retry > 3:
                print(f"[forks] 第 {page} 页连续 {retry - 1} 轮拿不到（HTTP {st}），停止；"
                      f"游标已存 {os.path.basename(page_path)}，可续采")
                break
            print(f"[forks] 第 {page} 页 HTTP {st}（{retry}/3 轮），等 10 分钟再试", flush=True)
            time.sleep(600)
            continue
        retry = 0
        items = r.json()
        if not items:
            print(f"[forks] 第 {page} 页为空 → 全量完成")
            break
        for f in items:
            fid = f.get("id")
            if fid in seen:
                continue
            owner = f.get("owner") or {}
            login = (owner.get("login") or "").lower()
            _append(out_path, {
                "fork_id": fid, "login": login,
                "fork_full_name": f.get("full_name"),
                "fork_created_at": f.get("created_at"),
                "fork_pushed_at": f.get("pushed_at"),
                "size_kb": f.get("size"),
                "default_branch": f.get("default_branch"),
                "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })
            seen.add(fid)
            if login and login not in owners:
                owners.add(login)
            n_new += 1
        print(f"[forks] 第 {page} 页 +{len(items)}（新增 {n_new}），累计 {len(seen)}", flush=True)
        with open(page_path, "w", encoding="utf-8") as f:   # 游标 = 下一页
            f.write(str(page + 1))
        if len(items) < 100:
            print(f"[forks] 末页不满 100 → 全量完成，共 {len(seen)} 条")
            break
        page += 1
        time.sleep(0.8)

    with open(owners_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(owners)) + "\n")
    print(f"[forks] fork owner 名单 {len(owners)} 人 → {os.path.basename(owners_path)}")
    pool.restore()
    return len(seen), len(owners)


# ── 单元 2：star 周曲线 ─────────────────────────────
def collect_history(full):
    key = _key(full)
    out_path = os.path.join(RAW, f"star_history_{key}.jsonl")
    done_pages = set()
    if os.path.exists(out_path):
        for line in open(out_path, encoding="utf-8"):
            try:
                done_pages.add(json.loads(line).get("page"))
            except json.JSONDecodeError:
                pass

    pool = NodePool()
    print(f"[history] {full}：已采页 {sorted(p for p in done_pages if p)}")
    page = 1
    total = 0
    while True:
        if page in done_pages:
            page += 1
            continue
        st, r = quota_get(pool, f"https://api.github.com/repos/{full}"
                               f"/stargazers/history?page={page}")
        if st != 200:
            print(f"[history] 第 {page} 页 HTTP {st}")
            if st == 200 or (st in (403, 429)):
                break
            break
        buckets = r.json()
        _append(out_path, {"page": page, "bucket_count": len(buckets),
                           "total_week": sum(b.get("total", 0) for b in buckets),
                           "buckets": buckets})
        for b in buckets:
            total += b.get("total", 0)
        print(f"[history] 第 {page} 页：{len(buckets)} 桶", flush=True)
        if not buckets:
            print(f"[history] 翻到底，累计周新增 {total}")
            break
        page += 1
        time.sleep(0.8)
    pool.restore()
    return total


# ── 单元 3：ClickHouse star 身份 + 同秒聚簇 ─────────
def _ch_post(sql):
    """ClickHouse playground 匿名 POST（不带任何凭据）。"""
    for prof in ("firefox133", "chrome", "edge101"):
        try:
            r = cffi.post(CH_BASE, params={"default_format": "JSONEachRow"},
                          data=sql.encode("utf-8"), impersonate=prof, timeout=120,
                          headers={"User-Agent": "spider-research"})
            if r.status_code < 400:
                return r.text
            raise RuntimeError(f"ClickHouse HTTP {r.status_code}: {r.text[:300]}")
        except RuntimeError:
            raise
        except Exception:
            time.sleep(3)
    raise RuntimeError("ClickHouse 全指纹失败")


def collect_ch(full, with_seconds=True):
    key = _key(full)
    ev_path = os.path.join(RAW, "ch_star_events", f"{key}.jsonl")
    os.makedirs(os.path.dirname(ev_path), exist_ok=True)
    done = _collected_logins(ev_path)["actor"]
    if done:
        print(f"[ch] 已有 {len(done)} actor，跳过（CH 是衰减抽样，不重查）")
        return len(done)

    sql = f"""
SELECT actor_login, toUnixTimestamp(min(created_at)) AS starred_ts,
       argMin(repo_name, created_at) AS repo_name_observed
FROM github_events
WHERE event_type = 'WatchEvent' AND repo_name IN ('{full}')
GROUP BY actor_login
ORDER BY starred_ts, actor_login
FORMAT JSONEachRow
"""
    rows = []
    for line in _ch_post(sql).splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    with open(ev_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"task": "ch_star_events", "scope_key": key, "page": 1,
                            "url": "play.clickhouse.com",
                            "body": [{"actor_login": r.get("actor_login"),
                                      "starred_ts": int(r.get("starred_ts", 0)),
                                      "repo_name_observed": r.get("repo_name_observed")}
                                     for r in rows]}, ensure_ascii=False) + "\n")
    print(f"[ch] star 身份 {len(rows)} actor → ch_star_events/{key}.jsonl")

    if with_seconds and rows:
        sec_sql = f"""
SELECT toUnixTimestamp(created_at) AS sec, count() AS n, groupArray(actor_login) AS actors
FROM github_events
WHERE event_type = 'WatchEvent' AND repo_name IN ('{full}')
GROUP BY sec ORDER BY sec
FORMAT JSONEachRow
"""
        sec_path = os.path.join(RAW, f"ch_star_seconds_{key}.jsonl")
        body = []
        for line in _ch_post(sec_sql).splitlines():
            line = line.strip()
            if line:
                r = json.loads(line)
                body.append({"sec": int(r["sec"]), "n": int(r["n"]),
                             "actors": r.get("actors", [])})
        with open(sec_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"task": "ch_star_seconds", "scope_key": key, "body": body},
                               ensure_ascii=False) + "\n")
        n_batch = sum(1 for b in body if b["n"] >= 2)
        print(f"[ch] 同秒聚簇 {len(body)} 秒（≥2 actor 的 {n_batch} 秒）→ "
              f"ch_star_seconds_{key}.jsonl")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="小仓库全量采集（fork/history/ch）")
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--only", choices=["forks", "history", "ch"], default=None,
                    help="只跑一个单元（默认全部）")
    ap.add_argument("--no-seconds", action="store_true", help="ch 单元跳过同秒聚簇")
    args = ap.parse_args()

    if args.only in (None, "forks"):
        collect_forks(args.repo)
    if args.only in (None, "history"):
        collect_history(args.repo)
    if args.only in (None, "ch"):
        collect_ch(args.repo, with_seconds=not args.no_seconds)
    print("完成。")


if __name__ == "__main__":
    main()
