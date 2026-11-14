# -*- coding: utf-8 -*-
"""仓库详情采集：openclaw + 对照组，只采指标不采内容。

数据面：
  R1  元数据（含 subscribers/星比）
  R2  issues/PR 总量与节奏（search total_count + 抽样页时间分布）
  R3  commits 总量与作者结构（Link 尾页 + 首页样本）
  R4  contributors 全名单（Top500 + 尾页计数）
  R5  releases 节奏（首页 100 条的时间序列）
  R6  周统计（commit_activity / participation / punch_card）
  R7  社区健康（issues 的 author_association 分布抽样）
  R8  事件流近 90 天类型分布

输出: data/raw/repo_metrics_{owner}__{repo}.jsonl（每源一批）
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

REPOS = [
    ("openclaw", "openclaw"),      # 样本仓
    ("ollama", "ollama"),          # 同类对照（最关键）
    ("n8n-io", "n8n"),             # 同类对照
    ("666ghj", "BettaFish"),       # 新增高疑似样本
    ("morluto", "rea"),            # 新增高疑似样本（原 storytold/rea，疑似仓库转移）
]

REQ_T0 = time.time()
REQ_N = 0
SEARCH_COOLDOWN = 0  # search 独立桶 10/min，串行自然低于限


def get(url, params=None, wait=None):
    global REQ_N
    attempt = 0
    while attempt < 24:  # 全指纹×2轮 = 16次约40s；抑制窗口通常几十秒~几分钟，拉长总重试
        for prof in PROFILES:
            try:
                time.sleep(wait if wait else 1.3)
                r = cffi.get(url, params=params, headers=UA, timeout=30,
                             impersonate=prof)
                REQ_N += 1
                return r
            except Exception:
                time.sleep(2.5)
        attempt += 1
        print(f"    [suppression] 全指纹一轮失败，退避 {20+attempt*10}s 后重试 (轮次{attempt})...")
        time.sleep(20 + attempt * 10)
    raise RuntimeError(f"all profiles failed after {attempt} rounds: {url}")


def last_page(r):
    link = r.headers.get("link", "")
    for part in link.split(","):
        if 'rel="last"' in part:
            try:
                return int(part.split("&page=")[1].split(">")[0])
            except (IndexError, ValueError):
                pass
    return None


def collect_repo(owner, repo, skip_kinds=None):
    full = f"{owner}/{repo}"
    skip_kinds = skip_kinds or set()
    out_path = os.path.join(BASE, "data", "raw", f"repo_metrics_{owner}__{repo}.jsonl")
    done_kinds = set()
    if os.path.exists(out_path):  # 断点续采：已落盘的 kind 跳过
        with open(out_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    done_kinds.add(json.loads(line).get("kind"))
                except json.JSONDecodeError:
                    pass
    out = open(out_path, "a", encoding="utf-8")

    def emit(kind, data):
        out.write(json.dumps({"kind": kind, "repo": full,
                              "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                              **data}, ensure_ascii=False) + "\n")
        out.flush()

    def p(msg):
        print(f"[{full}] {msg}")

    # R1 元数据
    if "meta" in done_kinds:
        p("R1 已采，跳过")
    else:
        r = get(f"https://api.github.com/repos/{full}")
        d = r.json()
        emit("meta", {"stars": d.get("stargazers_count"),
                      "forks": d.get("forks_count"),
                      "subscribers": d.get("subscribers_count"),
                      "open_issues": d.get("open_issues_count"),
                      "network_forks": d.get("forks_count"),
                      "created_at": d.get("created_at"),
                      "pushed_at": d.get("pushed_at"),
                      "size_kb": d.get("size"),
                      "language": d.get("language"),
                      "license": (d.get("license") or {}).get("spdx_id"),
                      "topics": d.get("topics")})
        sub_star = (d.get("subscribers_count", 0) / d.get("stargazers_count", 1)) * 100
        p(f"R1 元数据: {d.get('stargazers_count')}★ / {d.get('forks_count')}fork / "
          f"{d.get('subscribers_count')}watch (watch/星={sub_star:.2f}%)")

    # R2 issue/PR 总量（search，独立限流桶，wait=7s 保 <10/min）
    if "search" in done_kinds:
        p("R2 search 已采，跳过")
    else:
        for kind, q in [("issue_count", f"repo:{full} is:issue"),
                        ("pr_count", f"repo:{full} is:pr"),
                        ("issue_open", f"repo:{full} is:issue is:open"),
                        ("pr_open", f"repo:{full} is:pr is:open")]:
            r = get("https://api.github.com/search/issues", params={"q": q, "per_page": 1}, wait=7)
            if r.status_code == 200:
                emit("search", {"metric": kind, "total": r.json().get("total_count")})
                p(f"R2 {kind}: {r.json().get('total_count')}")
            else:
                emit("search", {"metric": kind, "error": r.status_code})

    # issues 时间节奏：state=all 前 5 页 × 100（最新500条的时间戳与作者关联）
    ts, assoc = [], {}
    # issues 时间节奏：state=all 前 5 页 × 100（最新500条的时间戳与作者关联）
    if any(k.startswith("issues_page") for k in done_kinds) or "issues_page" in done_kinds:
        p("R2 issues 节奏已采，跳过")
    else:
        ts, assoc = [], {}
        for pg in range(1, 6):
            r = get(f"https://api.github.com/repos/{full}/issues",
                    params={"state": "all", "per_page": 100, "page": pg, "sort": "created", "direction": "desc"})
            if r.status_code != 200:
                break
            batch = r.json()
            if not batch:
                break
            for it in batch:
                ts.append(it.get("created_at"))
                a = it.get("author_association")
                assoc[a] = assoc.get(a, 0) + 1
            emit("issues_page", {"page": pg, "count": len(batch),
                                 "first_created": batch[0].get("created_at"),
                                 "last_created": batch[-1].get("created_at"),
                                 "author_association": assoc})
            p(f"R2 issues 第{pg}页: {len(batch)}条 → {batch[-1].get('created_at','')[:10]}")
            if len(batch) < 100:
                break

    # R3 commits：尾页总量 + 首页作者分布
    if "commits_total" in done_kinds:
        p("R3 已采，跳过")
    else:
        r = get(f"https://api.github.com/repos/{full}/commits", params={"per_page": 1})
        cp = last_page(r)
        emit("commits_total", {"last_page": cp})
        authors, dates = {}, []
        r = get(f"https://api.github.com/repos/{full}/commits", params={"per_page": 100})
        if r.status_code == 200:
            for c in r.json():
                a = (c.get("author") or {}).get("login") or (c.get("commit", {}).get("author", {}) or {}).get("name", "?")
                authors[a] = authors.get(a, 0) + 1
                dates.append((c.get("commit", {}).get("author", {}) or {}).get("date"))
        emit("commits_sample", {"total_est": cp, "sample_size": len(dates),
                                "top_authors": dict(sorted(authors.items(), key=lambda x: -x[1])[:10]),
                                "newest": dates[0] if dates else None,
                                "oldest": dates[-1] if dates else None})
        p(f"R3 commits: ~{cp}页×100 | 近100条 top作者 {sorted(authors.items(), key=lambda x:-x[1])[:3]}")

    # R4 contributors：前 500 + 尾页
    if "contributors" in done_kinds:
        p("R4 已采，跳过")
    else:
        r = get(f"https://api.github.com/repos/{full}/contributors", params={"per_page": 1, "anon": "false"})
        contrib_total = last_page(r)
        contribs = []
        r = get(f"https://api.github.com/repos/{full}/contributors", params={"per_page": 100})
        if r.status_code == 200:
            contribs = [{"login": c.get("login"), "type": c.get("type"),
                         "contributions": c.get("contributions")} for c in r.json()]
        emit("contributors", {"total_pages": contrib_total, "top100": contribs})
        top1 = contribs[0] if contribs else {}
        total_top100 = sum(c["contributions"] for c in contribs)
        p(f"R4 contributors: ~{contrib_total}页 | Top1 {top1.get('login')} "
          f"{top1.get('contributions')} ({top1.get('contributions',0)/max(total_top100,1)*100:.0f}% of top100)")

    # R5 releases：首页100条时间序列（失败跳过，不中断整仓）
    if "releases" in done_kinds:
        p("R5 已采，跳过")
    else:
        rel = []
        try:
            r = get(f"https://api.github.com/repos/{full}/releases", params={"per_page": 100})
            if r.status_code == 200:
                rel = [{"tag": x.get("tag_name"), "published_at": x.get("published_at")} for x in (r.json() or [])]
        except Exception:
            pass
        emit("releases", {"count": len(rel), "items": rel})
        p(f"R5 releases: {len(rel)} 条" + (f" 最新 {rel[0]['tag']} @ {rel[0]['published_at'][:10]}" if rel else ""))

    # R6 周统计（单端点失败跳过）
    for stat in ["commit_activity", "participation", "punch_card"]:
        kind = f"stats_{stat}"
        if kind in done_kinds:
            p(f"R6 {stat} 已采，跳过")
            continue
        try:
            r = get(f"https://api.github.com/repos/{full}/stats/{stat}")
            if r.status_code == 200:
                emit(kind, {"data": r.json()})
                p(f"R6 stats/{stat}: ok")
            else:
                emit(kind, {"http": r.status_code})
                p(f"R6 stats/{stat}: HTTP {r.status_code}")
        except Exception:
            emit(kind, {"error": "all_profiles_failed"})
            p(f"R6 stats/{stat}: 全指纹失败，跳过")

    # R8 事件流（失败跳过）
    if "events_recent" in done_kinds:
        p("R8 已采，跳过")
    else:
        etypes = {}
        try:
            r = get(f"https://api.github.com/repos/{full}/events", params={"per_page": 100})
            if r.status_code == 200:
                for e in r.json():
                    etypes[e["type"]] = etypes.get(e["type"], 0) + 1
        except Exception:
            pass
        emit("events_recent", {"types": etypes})
        p(f"R8 近期事件: {etypes}")

    out.close()
    return out_path


def main():
    import argparse
    ap = argparse.ArgumentParser(description="仓库指标采集（R1-R8，只采指标不采内容）")
    ap.add_argument("--repos", default="",
                    help="o/r 形式逗号分隔，如 storytold/craft-launcher,morluto/rea；"
                         "默认跑 REPOS 列表（openclaw + 对照组）")
    ap.add_argument("--skip-kinds", default="",
                    help="跳过某些指标（逗号分隔 kind 名），省配额用")
    args = ap.parse_args()

    if args.repos:
        repos = []
        for item in args.repos.split(","):
            item = item.strip()
            if "/" in item:
                o, r = item.split("/", 1)
                repos.append((o, r))
    else:
        repos = REPOS
    skip = {k.strip() for k in args.skip_kinds.split(",") if k.strip()}

    paths = []
    for owner, repo in repos:
        paths.append(collect_repo(owner, repo, skip_kinds=skip))
        print()
    print(f"共 {REQ_N} 请求，{(time.time()-REQ_T0)/60:.1f} 分钟")
    for p in paths:
        print(" →", p)


if __name__ == "__main__":
    main()
