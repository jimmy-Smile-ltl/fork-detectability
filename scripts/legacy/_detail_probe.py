# -*- coding: utf-8 -*-
"""仓库详情指标 + 用户真人画像通道实测（匿名、只读、指标不采内容）。

用法: py -X utf8 _detail_probe.py
"""
import json
import os
import re
import time

from curl_cffi import requests as cffi

REPO = "openclaw/openclaw"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
      "Accept": "application/vnd.github+json"}
PROFILES = ["chrome", "firefox133", "safari17_0", "edge101"]
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get(url, params=None):
    """带 TLS 指纹轮换的 GET；同一 profile 失败两次才换下一个（抑制窗口抖动）。"""
    for prof in PROFILES + PROFILES:
        for _ in range(2):
            try:
                return cffi.get(url, params=params, headers=UA, timeout=25,
                                impersonate=prof)
            except Exception:
                time.sleep(1.5)
    raise RuntimeError(f"all profiles failed: {url}")


def last_page(r):
    """从 Link 头解析 last 页码（估算总量用）。"""
    link = r.headers.get("link", "")
    for part in link.split(","):
        if 'rel="last"' in part:
            try:
                return int(part.split("&page=")[1].split(">")[0])
            except (IndexError, ValueError):
                pass
    return None


def section(title):
    print(f"\n== {title} ==")


# ---------- 仓库详情通道 ----------

def probe_repo():
    section("R0. 配额余量（不消耗）")
    r = get("https://api.github.com/rate_limit")
    core = r.json()["resources"]["core"]
    print(f"   core: {core['remaining']}/{core['limit']}")

    section("R1. /repos/{o}/{r} 仓库元数据")
    r = get(f"https://api.github.com/repos/{REPO}")
    d = r.json()
    keys = ["stargazers_count", "forks_count", "subscribers_count", "open_issues_count",
            "created_at", "pushed_at", "size", "language", "license"]
    print("   HTTP", r.status_code, {k: d.get(k) for k in keys})

    section("R2. /issues?state=all（含PR） Link 尾页 → 总量")
    r = get(f"https://api.github.com/repos/{REPO}/issues", params={"state": "all", "per_page": 1})
    print(f"   HTTP {r.status_code}  last_page={last_page(r)}  首条has('pull_request')={'pull_request' in (r.json()[0] if r.json() else {})}")

    section("R3. /pulls?state=all Link 尾页")
    r = get(f"https://api.github.com/repos/{REPO}/pulls", params={"state": "all", "per_page": 1})
    print(f"   HTTP {r.status_code}  last_page={last_page(r)}")

    section("R4. /commits Link 尾页 + 首条字段")
    r = get(f"https://api.github.com/repos/{REPO}/commits", params={"per_page": 1})
    c0 = r.json()[0] if r.json() else {}
    print(f"   HTTP {r.status_code}  last_page={last_page(r)}")
    print(f"   首条: author={c0.get('author', {}).get('login')} date={c0.get('commit', {}).get('author', {}).get('date')} verified={c0.get('commit', {}).get('verification', {}).get('verified')}")

    section("R5. /contributors Link 尾页（bot 会标 type:Bot）")
    r = get(f"https://api.github.com/repos/{REPO}/contributors", params={"per_page": 1, "anon": "true"})
    d0 = r.json()[0] if r.json() else {}
    print(f"   HTTP {r.status_code}  last_page={last_page(r)}  首条: {d0.get('login')} type={d0.get('type')} contributions={d0.get('contributions')}")

    section("R6. /languages")
    r = get(f"https://api.github.com/repos/{REPO}/languages")
    print("   HTTP", r.status_code, r.json())

    section("R7. /releases + /tags 尾页")
    for name in ["releases", "tags"]:
        try:
            r = get(f"https://api.github.com/repos/{REPO}/{name}", params={"per_page": 1})
            print(f"   {name}: HTTP {r.status_code} last_page={last_page(r)}")
        except Exception as e:
            print(f"   {name}: 全指纹失败 {type(e).__name__}")

    section("R8. /events（近90天流，匿名可用？）")
    r = get(f"https://api.github.com/repos/{REPO}/events", params={"per_page": 5})
    types = {}
    for e in r.json():
        types[e["type"]] = types.get(e["type"], 0) + 1
    print(f"   HTTP {r.status_code}  本页事件类型分布={types}")

    section("R9. /stats/* 三个统计端点")
    for name in ["commit_activity", "participation", "punch_card"]:
        r = get(f"https://api.github.com/repos/{REPO}/stats/{name}")
        body = r.json() if r.status_code == 200 else None
        if isinstance(body, list):
            preview = f"list[{len(body)}]"
        elif isinstance(body, dict):
            preview = f"dict keys={list(body)[:5]}"
        else:
            preview = str(body)[:80]
        print(f"   {name}: HTTP {r.status_code}  {preview}")

    section("R10. search API total_count（1 请求拿总量，限流 10/min 独立桶）")
    r = get("https://api.github.com/search/issues",
            params={"q": f"repo:{REPO} is:issue", "per_page": 1})
    print(f"   issues total_count={r.json().get('total_count') if r.status_code==200 else r.text[:100]}")
    r = get("https://api.github.com/search/commits",
            params={"q": f"repo:{REPO}", "per_page": 1})
    print(f"   commits total_count={r.json().get('total_count') if r.status_code==200 else r.text[:100]}")


# ---------- 用户画像通道 ----------

def probe_user(login):
    print(f"\n----- 用户 {login} -----")
    r = get(f"https://api.github.com/users/{login}")
    if r.status_code != 200:
        print(f"   profile: HTTP {r.status_code} {r.text[:120]!r}")
        return
    d = r.json()
    fields = ["type", "created_at", "updated_at", "public_repos", "public_gists",
              "followers", "following", "company", "location", "blog", "email",
              "bio", "hireable"]
    print("   profile:", {k: d.get(k) for k in fields})

    r = get(f"https://api.github.com/users/{login}/repos", params={"per_page": 1, "sort": "pushed"})
    print(f"   repos: last_page={last_page(r)} (≈公开仓库数)")
    if r.status_code == 200 and r.json():
        r0 = r.json()[0]
        print(f"     最近push的仓库: {r0.get('full_name')} pushed_at={r0.get('pushed_at')} fork={r0.get('fork')} size={r0.get('size')}")

    r = get(f"https://api.github.com/users/{login}/events", params={"per_page": 100})
    evs = r.json() if r.status_code == 200 else []
    types = {}
    for e in evs:
        types[e["type"]] = types.get(e["type"], 0) + 1
    span = (evs[0]["created_at"][:10], evs[-1]["created_at"][:10]) if evs else None
    print(f"   events: HTTP {r.status_code} 本页{len(evs)}条 跨度={span} 类型={dict(sorted(types.items(), key=lambda x: -x[1])[:6])}")

    r = get(f"https://api.github.com/users/{login}/starred", params={"per_page": 1, "sort": "created"})
    print(f"   starred(个人star表): HTTP {r.status_code} last_page={last_page(r)}")
    if r.status_code == 200 and r.json():
        s0 = r.json()[0]
        print(f"     最近的star: {s0.get('full_name')} stargazers={s0.get('stargazers_count')}")

    r = get(f"https://api.github.com/users/{login}/subscriptions", params={"per_page": 1})
    print(f"   subscriptions(watch表): HTTP {r.status_code} last_page={last_page(r)}")

    r = get(f"https://api.github.com/users/{login}/followers", params={"per_page": 1})
    fl = last_page(r)
    r = get(f"https://api.github.com/users/{login}/following", params={"per_page": 1})
    fg = last_page(r)
    print(f"   followers last_page={fl}  following last_page={fg}")

    r = get(f"https://api.github.com/users/{login}/orgs")
    print(f"   orgs: HTTP {r.status_code} {[o.get('login') for o in (r.json() or [])][:8]}")


def probe_html_profile(login):
    """HTML 主页：成就徽章 + 贡献日历是否可抓。"""
    r = get(f"https://github.com/{login}")
    t = r.text
    if r.status_code != 200:
        print(f"   HTML: HTTP {r.status_code}")
        return
    ach = re.findall(r'aria-label="([A-Z][^"]{2,40}?)(?: achievement|:)"', t)
    has_cal = "contribution" in t.lower()
    n_contri = len(re.findall(r'data-date=', t))
    print(f"   HTML: HTTP 200  成就数≈{len(set(ach))} {sorted(set(ach))[:6]}  含贡献图={has_cal}  日历格子数={n_contri}")


if __name__ == "__main__":
    probe_repo()
    section("U. 用户画像通道（可疑号 + 真人对照 + 两个 forker 样本）")
    users = ["steipete"]  # 仓库作者(真人对照)；实证小号样本从 config/targets.json 配置，实名不入库
    # 从 fork 数据取 2 个样本：早期 fork(更像真人) + 爆发窗 fork(更可疑)
    p = os.path.join(BASE, "data", "raw", "probe_forks_all.jsonl")
    early, burst = None, None
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                body = row.get("body", row)
                if not isinstance(body, list):
                    continue
                for it in body:
                    ts = it.get("created_at", "")
                    if ts < "2025-12" and not early:
                        early = it["full_name"].split("/")[0]
                    if "2026-01-25" <= ts[:10] <= "2026-01-26" and not burst:
                        burst = it["full_name"].split("/")[0]
        if early:
            users.append(early)
        if burst:
            users.append(burst)
    print(f"   样本: {users} (early={early}, burst={burst})")
    for u in users:
        try:
            probe_user(u)
            probe_html_profile(u)
        except Exception as e:
            print(f"   失败: {type(e).__name__} {str(e)[:100]}")
