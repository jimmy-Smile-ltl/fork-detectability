# -*- coding: utf-8 -*-
"""八个维度的真实 fetcher：把 HTTP 细节与解析逻辑收在这里。

对外只暴露一个函数：

    fetch(dim, login, ctx) -> (Outcome, payload, http_code, note)

`ctx` 携带会话、代理、目标年份等上下文，便于两种调度策略共用。

设计约束
--------
* **不在这里做重试**（重试在 collect_core.Collector 里统一做）
* **不在这里写文件**（写文件在 collect_core.Writer）
* 每个 fetcher 只负责"一次请求 → 一个 Outcome"
* 拿不到就是拿不到：`None` 而不是 0
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from curl_cffi import requests as cffi

from collect_core import Outcome, classify_http

API = "https://api.github.com"
GH = "https://github.com"
UA_HTML = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
@dataclass
class Ctx:
    """一次采集运行的上下文。"""
    year: int = 2026
    proxies: dict = field(default_factory=dict)        # API 通道（隧道）
    proxies_html: dict = field(default_factory=dict)   # HTML/CDN 通道（直连为空）
    timeout: int = 12
    deep_star: bool = False          # D4L 是否拉全部时间戳（默认只取总数）
    max_repos: int = 2               # D9 每人查几个仓
    known_user_id: Optional[dict] = None   # login -> user_id（D14 用，省一次请求）


def _session():
    return cffi.Session(impersonate="chrome")


def _api_headers() -> dict:
    return {"Accept": "application/vnd.github+json",
            "User-Agent": "fork-study/1.0", "Connection": "close"}


def _html_headers() -> dict:
    return {"User-Agent": UA_HTML, "Accept-Language": "en-US,en;q=0.9",
            "Connection": "close"}


# ---------------------------------------------------------------------------
# 各维度解析
# ---------------------------------------------------------------------------
_RE_COUNTER = re.compile(r'<span title="([\d,]+)"[^>]*class="Counter"[^>]*>')
_RE_ACHV = re.compile(r'alt="Achievement:\s*([^"]+)"')
_RE_PINNED = re.compile(r'class="pinned-item-list-item-content"[\s\S]{0,400}?href="/([^/"]+/[^/"]+)"')
_RE_DISPLAY = re.compile(r'itemprop="name"[^>]*>\s*([^<]+)')
_RE_ORG = re.compile(r'alt="@([A-Za-z0-9-]+)"')


def parse_profile_html(html: str) -> dict:
    """D15：个人主页一次拿 7 个字段。"""
    counters = [int(x.replace(",", "")) for x in _RE_COUNTER.findall(html)]
    achv = _RE_ACHV.findall(html)
    pinned = list(dict.fromkeys(_RE_PINNED.findall(html)))[:6]
    disp = _RE_DISPLAY.search(html)
    orgs = _RE_ORG.findall(html)
    return {
        "counters": counters[:6],
        "repos_count": counters[0] if len(counters) > 0 else None,
        "starred_count": counters[3] if len(counters) > 3 else None,
        "achievements": achv,
        "achievement_n": len(achv),
        "has_pull_shark": any("Pull Shark" in a for a in achv),
        "has_collab": any("Pair Extraordinaire" in a for a in achv),
        "pinned": pinned,
        "display_name": disp.group(1).strip() if disp else None,
        "orgs": orgs,
    }


_RE_REPO_ROW = re.compile(r'<li[^>]*itemprop="owns"[^>]*>(.*?)</li>', re.S)
_RE_REPO_NAME = re.compile(r'itemprop="name codeRepository"[^>]*>\s*([^<]+)')
_RE_REPO_HREF = re.compile(r'href="/([^/"?]+)/([^/"?]+)"')
_RE_REPO_LANG = re.compile(r'itemprop="programmingLanguage">\s*([^<]+)')
_RE_REPO_UPDATED = re.compile(r'<relative-time[^>]*datetime="([^"]+)"')
_RE_REPO_FORK = re.compile(r'\bForked from\b|forked from', re.S)


def parse_repos_html(html: str, login: str = "") -> dict:
    """D8H：仓库清单（自建 vs fork）。

    注意 `itemprop="name codeRepository"` 标签里**只有仓名、没有 owner**，
    owner 要从同一个 <li> 里的 href 取。
    """
    rows = _RE_REPO_ROW.findall(html)
    own, forks = [], []
    for blk in rows:
        nm = _RE_REPO_NAME.search(blk)
        href = _RE_REPO_HREF.search(blk)
        if not nm:
            continue
        owner = href.group(1) if href else login
        name = nm.group(1).strip()
        lang = _RE_REPO_LANG.search(blk)
        upd = _RE_REPO_UPDATED.search(blk)
        rec = {"name": name, "owner": owner,
               "lang": lang.group(1).strip() if lang else None,
               "updated": upd.group(1) if upd else None}
        (forks if _RE_REPO_FORK.search(blk) else own).append(rec)
    low = (login or "").lower()
    return {"own_count": len(own), "fork_count": len(forks),
            "own_details": own[:12], "fork_details": forks[:12],
            "sampled_repos": len(rows),
            "has_profile_repo": any(r["name"].lower() == low for r in own)}


_RE_CELLCOUNT = re.compile(r'data-date="([\d-]+)"[^>]*data-count="(\d+)"')


def parse_contrib_html(html: str) -> dict:
    """D13：全年逐日贡献。兼容两种页面结构（data-count / data-level+tooltip）。"""
    # 结构 A：直接带 data-count
    by_day = {d: int(c) for d, c in _RE_CELLCOUNT.findall(html)}
    if not by_day:
        # 结构 B：data-level + <tool-tip>N contributions</tool-tip>
        for d, lv, tip in _RE_CELL.findall(html):
            m = _RE_COUNT.search(tip or "")
            cnt = int(m.group(1).replace(",", "")) if (m and m.group(1)) else 0
            by_day[d] = cnt
    if not by_day:
        return {"active_days": None, "total_contribs": None}
    active = [d for d, c in by_day.items() if c > 0]
    total = sum(by_day.values())
    # 最长连续
    streak = best = 0
    prev = None
    from datetime import date, timedelta
    for d in sorted(by_day):
        cur = date.fromisoformat(d)
        if by_day[d] > 0:
            streak = streak + 1 if prev and (cur - prev).days == 1 else 1
            best = max(best, streak)
        else:
            streak = 0
        prev = cur
    by_month: dict[str, int] = {}
    for d, c in by_day.items():
        by_month[d[:7]] = by_month.get(d[:7], 0) + c
    return {"seasons": len(by_day), "active_days": len(active),
            "total_contribs": total, "max_streak": best,
            "first_active": min(active) if active else None,
            "last_active": max(active) if active else None,
            "by_month": by_month}


_RE_ATOM_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_RE_ATOM_TYPE = re.compile(r'<id>tag:github\.com,2008:GollumEvent/(\d+)', re.S)
_RE_ATOM_TITLE = re.compile(r"<title>(.*?)</title>", re.S)


def parse_atom(xml: str) -> dict:
    """D2：近 30 条事件类型。

    Atom 的 event id 形如 `tag:github.com,2008:PullRequestEvent/123`，
    这里按稳定前缀匹配，避免依赖非标准字段。
    """
    entries = _RE_ATOM_ENTRY.findall(xml)
    types: dict[str, int] = {}
    kinds = {
        "PushEvent": "push", "WatchEvent": "star", "ForkEvent": "forkevent",
        "PullRequestEvent": "pr", "IssuesEvent": "issues",
        "CreateEvent": "create", "ReleaseEvent": "release",
        "IssueCommentEvent": "issue_comment", "PullRequestReviewEvent": "pr_review",
        "DeleteEvent": "delete", "PublicEvent": "public",
    }
    for e in entries:
        hit = "other"
        for k, v in kinds.items():
            if k in e:
                hit = v
                break
        types[hit] = types.get(hit, 0) + 1
    dev = types.get("push", 0) + types.get("pr", 0) + types.get("pr_review", 0)
    return {"events_n": len(entries), "event_types": types,
            "dev_events": dev,
            "dev_event_ratio": round(dev / len(entries), 3) if entries else None}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def fetch(dim: str, login: str, ctx: Ctx) -> tuple[Outcome, dict, Optional[int], str]:
    """一次请求。返回 (Outcome, payload, http_code, note)。"""
    fn = _HANDLERS.get(dim)
    if fn is None:
        return Outcome.BAD_RESPONSE, {}, None, f"未知维度 {dim}"
    try:
        return fn(login, ctx)
    except Exception as e:                       # 网络层异常统一归 NETWORK
        return Outcome.NETWORK, {}, None, f"{type(e).__name__}"


# -- D1 账户档案 -------------------------------------------------------------
def _d1(login: str, ctx: Ctx):
    r = cffi.get(f"{API}/users/{login}", headers=_api_headers(),
                 proxies=ctx.proxies, timeout=ctx.timeout, impersonate="chrome")
    if r.status_code == 404:
        return Outcome.DELETED, {}, 404, "账号不存在或已注销"
    if r.status_code != 200:
        oc = classify_http(r.status_code, None, r.text[:80])
        return oc, {}, r.status_code, r.text[:60]
    d = r.json()
    payload = {
        "user_id": d.get("id"), "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"), "public_repos": d.get("public_repos"),
        "public_gists": d.get("public_gists"), "followers": d.get("followers"),
        "following": d.get("following"), "name": d.get("name"),
        "bio": d.get("bio"), "location": d.get("location"),
        "company": d.get("company"), "blog": d.get("blog"),
        "twitter_username": d.get("twitter_username"),
        "hireable": d.get("hireable"), "type": d.get("type"),
        "avatar_url": d.get("avatar_url"),
    }
    return Outcome.OK, payload, 200, ""


# -- D2 事件流 ---------------------------------------------------------------
def _d2(login: str, ctx: Ctx):
    r = cffi.get(f"{GH}/{login}.atom", headers=_html_headers(),
                 proxies=ctx.proxies_html, timeout=ctx.timeout, impersonate="chrome")
    if r.status_code == 404:
        return Outcome.DELETED, {}, 404, "atom 404"
    if r.status_code != 200:
        return classify_http(r.status_code), {}, r.status_code, ""
    p = parse_atom(r.text)
    return (Outcome.OK if p["events_n"] else Outcome.EMPTY), p, 200, \
        ("" if p["events_n"] else "无公开事件")


# -- D4L star 总数（可选深采时间戳）------------------------------------------
def _d4l(login: str, ctx: Ctx):
    r = cffi.get(f"{API}/users/{login}/starred",
                 params={"per_page": 1, "page": 1},
                 headers={**_api_headers(), "Accept": "application/vnd.github.star+json"},
                 proxies=ctx.proxies, timeout=ctx.timeout, impersonate="chrome")
    if r.status_code == 404:
        return Outcome.DELETED, {}, 404, "starred 404"
    if r.status_code != 200:
        return classify_http(r.status_code), {}, r.status_code, ""
    total = None
    link = r.headers.get("Link", "")
    m = re.search(r'[?&]page=(\d+)>; rel="last"', link)
    if m:
        total = int(m.group(1))
    else:
        total = len(r.json() or [])
    payload = {"starred_count": total, "stars": []}
    if ctx.deep_star and total and total <= 3000:
        stars = []
        for page in range(1, min((total + 99) // 100, 30) + 1):
            rr = cffi.get(f"{API}/users/{login}/starred",
                          params={"per_page": 100, "page": page},
                          headers={**_api_headers(),
                                   "Accept": "application/vnd.github.star+json"},
                          proxies=ctx.proxies, timeout=ctx.timeout,
                          impersonate="chrome")
            if rr.status_code != 200:
                break
            for it in rr.json() or []:
                if isinstance(it, dict) and it.get("repo"):
                    stars.append({"at": it.get("starred_at"),
                                  "repo": it["repo"].get("full_name")})
            time.sleep(0.05)
        payload["stars"] = stars
    return (Outcome.OK if total is not None else Outcome.EMPTY), payload, 200, ""


# -- D8H 仓库结构 ------------------------------------------------------------
def _d8h(login: str, ctx: Ctx):
    """仓库结构：**优先用 API /repos（62KB）而不是 HTML（223KB）**。

    两者信息等价，但 API 体积小 3.6 倍、成功率 84% vs 0%（高并发下）。
    """
    r = cffi.get(f"{API}/users/{login}/repos",
                 params={"per_page": 100, "sort": "pushed", "page": 1},
                 headers=_api_headers(), proxies=ctx.proxies,
                 timeout=ctx.timeout, impersonate="chrome")
    if r.status_code == 404:
        return Outcome.DELETED, {}, 404, "repos 404"
    if r.status_code != 200:
        return classify_http(r.status_code), {}, r.status_code, ""
    arr = r.json() or []
    own, forks = [], []
    for it in arr:
        if not isinstance(it, dict):
            continue
        rec = {"name": it.get("name"), "owner": (it.get("owner") or {}).get("login"),
               "lang": it.get("language"), "updated": it.get("pushed_at"),
               "size_kb": it.get("size")}
        (forks if it.get("fork") else own).append(rec)
    low = login.lower()
    p = {"own_count": len(own), "fork_count": len(forks),
         "own_details": own[:12], "fork_details": forks[:12],
         "sampled_repos": len(arr),
         "has_profile_repo": any((x["name"] or "").lower() == low for x in own)}
    if not arr:
        return Outcome.EMPTY, p, 200, "无公开仓库"
    return Outcome.OK, p, 200, ""


# -- D9 自建仓提交数（依赖 D8H 的结果）--------------------------------------
_REPO_CACHE: dict[str, list] = {}


def _d9(login: str, ctx: Ctx):
    repos = _REPO_CACHE.get(login.lower())
    if repos is None:
        # 没缓存就现场取一次仓库页（per_account 模式下 D8H 通常已先跑）
        oc, p, code, note = _d8h(login, ctx)
        if oc not in (Outcome.OK, Outcome.EMPTY):
            return oc, {}, code, f"取仓库列表失败：{note}"
        repos = [x["name"] for x in (p.get("own_details") or [])][: ctx.max_repos]
        _REPO_CACHE[login.lower()] = repos
    if not repos:
        return Outcome.EMPTY, {"commit_total": 0, "repos_checked": 0,
                               "per_repo": []}, 200, "无自建仓"
    per, total, ok_any = [], 0, False
    for name in repos:
        r = cffi.get(f"{API}/repos/{login}/{name}/commits",
                     params={"author": login, "per_page": 1},
                     headers=_api_headers(), proxies=ctx.proxies,
                     timeout=ctx.timeout, impersonate="chrome")
        if r.status_code in (409, 422):
            per.append({"repo": name, "commits": 0, "why": "empty repo"})
            ok_any = True
            continue
        if r.status_code == 404:
            per.append({"repo": name, "commits": None, "why": "404"})
            continue
        if r.status_code != 200:
            per.append({"repo": name, "commits": None,
                        "why": f"http {r.status_code}"})
            continue
        m = re.search(r'[?&]page=(\d+)>; rel="last"', r.headers.get("Link", ""))
        n = int(m.group(1)) if m else len(r.json() or [])
        per.append({"repo": name, "commits": n})
        total += n
        ok_any = True
        time.sleep(0.05)
    if not ok_any:
        return Outcome.NOT_FOUND, {"commit_total": None, "repos_checked": 0,
                                   "per_repo": per}, 404, "所有仓库均不可读"
    return Outcome.OK, {"commit_total": total, "repos_checked": len(per),
                        "per_repo": per}, 200, ""


# -- D13 全年贡献 ------------------------------------------------------------
def _d13(login: str, ctx: Ctx):
    y = ctx.year
    r = cffi.get(f"{GH}/users/{login}/contributions",
                 params={"from": f"{y}-01-01", "to": f"{y}-12-31"},
                 headers=_html_headers(), proxies=ctx.proxies_html,
                 timeout=ctx.timeout, impersonate="chrome")
    if r.status_code == 404:
        return Outcome.DELETED, {}, 404, "贡献页 404"
    if r.status_code != 200:
        return classify_http(r.status_code), {}, r.status_code, ""
    p = parse_contrib_html(r.text)
    if p.get("active_days") is None:
        return Outcome.BAD_RESPONSE, p, 200, "未解析到贡献格"
    if p["active_days"] == 0:
        return Outcome.EMPTY, p, 200, "全年零贡献"
    return Outcome.OK, p, 200, ""


# -- D14 头像类型 ------------------------------------------------------------
def _d14(login: str, ctx: Ctx):
    """头像类型。**缺 user_id 时自己补一次 /users 请求**，不依赖 D1 成功。

    这是一个真实缺陷的修复：早期版本在 D1 失败时直接返回 BAD_RESPONSE，
    导致 648–810 个头像记录被白白丢弃。
    """
    uid = (ctx.known_user_id or {}).get(login.lower())
    if not uid:
        r0 = cffi.get(f"{API}/users/{login}", headers=_api_headers(),
                      proxies=ctx.proxies, timeout=ctx.timeout,
                      impersonate="chrome")
        if r0.status_code == 404:
            return Outcome.DELETED, {}, 404, "账号不存在"
        if r0.status_code == 200:
            uid = (r0.json() or {}).get("id")
            if uid:
                if ctx.known_user_id is None:
                    ctx.known_user_id = {}
                ctx.known_user_id[login.lower()] = uid
        if not uid:
            return classify_http(r0.status_code), {}, r0.status_code, "取 user_id 失败"
    r = cffi.head(f"https://avatars.githubusercontent.com/u/{uid}",
                  params={"v": "4", "s": "460"},
                  headers={"User-Agent": "fork-study/1.0", "Connection": "close"},
                  proxies=ctx.proxies_html, timeout=ctx.timeout, impersonate="chrome")
    if r.status_code != 200:
        return classify_http(r.status_code), {}, r.status_code, ""
    size = int(r.headers.get("Content-Length") or 0)
    # identicon 是程序生成的对称几何图，压缩后通常 < 6 KB；
    # 真人照片几乎都 > 8 KB。阈值取得保守（见 docs/05 的设计说明）。
    kind = "identicon" if 0 < size < 6000 else "photo"
    return Outcome.OK, {"type": kind, "bytes": size}, 200, ""


# -- D15 主页画像 ------------------------------------------------------------
def _d15(login: str, ctx: Ctx):
    r = cffi.get(f"{GH}/{login}", headers=_html_headers(),
                 proxies=ctx.proxies_html, timeout=ctx.timeout, impersonate="chrome")
    if r.status_code == 404:
        return Outcome.DELETED, {}, 404, "主页 404"
    if r.status_code != 200:
        return classify_http(r.status_code), {}, r.status_code, ""
    p = parse_profile_html(r.text)
    if not p["counters"]:
        return Outcome.BAD_RESPONSE, p, 200, "未解析到 Counter"
    return Outcome.OK, p, 200, ""


def _d6(login: str, ctx: Ctx):
    """D6：组织列表（API）。D15 里 org 字段的可替代方案。"""
    r = cffi.get(f"{API}/users/{login}/orgs",
                 params={"per_page": 100}, headers=_api_headers(),
                 proxies=ctx.proxies, timeout=ctx.timeout, impersonate="chrome")
    if r.status_code == 404:
        return Outcome.DELETED, {}, 404, "orgs 404"
    if r.status_code != 200:
        return classify_http(r.status_code), {}, r.status_code, ""
    arr = r.json() or []
    orgs = [x.get("login") for x in arr if isinstance(x, dict)]
    return (Outcome.OK if orgs else Outcome.EMPTY), \
        {"org_count": len(orgs), "orgs": orgs[:20]}, 200, ""


def _d2b(login: str, ctx: Ctx):
    """D2B：API `/events/public` 取近 90 天事件（D13 的可靠替代）。

    D13 走 github.com HTML，会被软封；本维度纯 API，稳定。
    产出：90 天内的活跃天数、事件总数、开发事件占比、事件类型分布。
    """
    days: dict[str, int] = {}
    types: dict[str, int] = {}
    n = 0
    for page in range(1, 4):                      # 最多 300 条事件
        r = cffi.get(f"{API}/users/{login}/events/public",
                     params={"per_page": 100, "page": page},
                     headers=_api_headers(), proxies=ctx.proxies,
                     timeout=ctx.timeout, impersonate="chrome")
        if r.status_code == 404:
            return Outcome.DELETED, {}, 404, "events 404"
        if r.status_code != 200:
            if page == 1:
                return classify_http(r.status_code), {}, r.status_code, ""
            break
        batch = r.json() or []
        for it in batch:
            if not isinstance(it, dict):
                continue
            n += 1
            d = (it.get("created_at") or "")[:10]
            if d:
                days[d] = days.get(d, 0) + 1
            t = (it.get("type") or "").replace("Event", "").lower()
            types[t] = types.get(t, 0) + 1
        if len(batch) < 100:
            break
        time.sleep(0.05)
    dev = types.get("push", 0) + types.get("pullrequest", 0) +         types.get("pullrequestreview", 0)
    p = {"active_days_90d": len(days), "events_n_90d": n,
         "event_types": types, "dev_events": dev,
         "dev_event_ratio": round(dev / n, 3) if n else None,
         "first_day": min(days) if days else None,
         "last_day": max(days) if days else None}
    return (Outcome.OK if n else Outcome.EMPTY), p, 200, ""


_HANDLERS = {"D1": _d1, "D2": _d2, "D2B": _d2b, "D4L": _d4l, "D8H": _d8h,
             "D9": _d9, "D13": _d13, "D14": _d14, "D15": _d15, "D6": _d6}


#: per_account 模式下的推荐顺序：先 D1（拿 user_id），再 D8H（D9 依赖），最后其余
ORDER_PER_ACCOUNT = ["D1", "D15", "D8H", "D9", "D13", "D14", "D2", "D4L"]
