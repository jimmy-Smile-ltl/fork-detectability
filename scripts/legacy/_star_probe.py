# -*- coding: utf-8 -*-
"""star 名单获取通道实测：官方 REST / HTML 页 / 本地已有数据盘点。

用法: py -X utf8 _star_probe.py
"""
import json
import os
import sqlite3

from curl_cffi import requests as cffi

REPO = "openclaw/openclaw"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
      "Accept": "application/vnd.github+json"}

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def t_rest():
    print("== A. 官方 REST /stargazers（匿名）==")
    try:
        r = cffi.get(f"https://api.github.com/repos/{REPO}/stargazers",
                     params={"per_page": 1}, headers=UA, timeout=20,
                     impersonate="firefox133")
        print(f"   HTTP {r.status_code}  ratelimit-remaining={r.headers.get('x-ratelimit-remaining')}")
        print(f"   body[:180]: {r.text[:180]!r}")
    except Exception as e:
        print(f"   异常: {e}")


def t_rest_subscribers():
    print("== B. 官方 REST /subscribers（watchers 名单，对照用）==")
    try:
        r = cffi.get(f"https://api.github.com/repos/{REPO}/subscribers",
                     params={"per_page": 1}, headers=UA, timeout=20,
                     impersonate="firefox133")
        print(f"   HTTP {r.status_code}  ratelimit-remaining={r.headers.get('x-ratelimit-remaining')}")
        if r.status_code == 200:
            print(f"   首页条数={len(r.json())}  body[:120]: {r.text[:120]!r}")
        else:
            print(f"   body[:180]: {r.text[:180]!r}")
    except Exception as e:
        print(f"   异常: {e}")


def t_html():
    print("== C. HTML github.com/{o}/{r}/stargazers 页面 ==")
    for label, url in [
        ("第1页", f"https://github.com/{REPO}/stargazers"),
        ("?page=2", f"https://github.com/{REPO}/stargazers?page=2"),
        ("?page=100", f"https://github.com/{REPO}/stargazers?page=100"),
    ]:
        try:
            r = cffi.get(url, headers={"User-Agent": UA["User-Agent"]}, timeout=25,
                         impersonate="firefox133")
            n = r.text.count('data-view-component="true" class="Link--secondary"') if r.status_code == 200 else -1
            has_after = 'rel="next"' in r.text
            print(f"   [{label}] HTTP {r.status_code}  用户条目≈{n}  有下一页={has_after}  len={len(r.text)}")
        except Exception as e:
            print(f"   [{label}] 异常: {e}")


def t_local():
    print("== D. 本地已有数据盘点 ==")
    db = os.path.join(BASE, "data", "gh.db")
    if not os.path.exists(db):
        print("   无 gh.db")
        return
    con = sqlite3.connect(db)
    cur = con.cursor()
    try:
        rows = cur.execute(
            "SELECT count(*), count(DISTINCT actor_login), min(created_at), max(created_at) "
            "FROM star_events").fetchone()
        print(f"   star_events(ClickHouse导出): {rows[0]} 行 / {rows[1]} 去重账号 / {rows[2]} → {rows[3]}")
        by_repo = cur.execute(
            "SELECT repo_name_at_event, count(*) FROM star_events "
            "GROUP BY repo_name_at_event ORDER BY 2 DESC").fetchall()
        for name, c in by_repo:
            print(f"     - {name}: {c}")
    except sqlite3.OperationalError as e:
        print(f"   查询失败: {e}")
    con.close()
    # 原始 JSONL
    raw = os.path.join(BASE, "data", "raw")
    if os.path.isdir(raw):
        for fn in sorted(os.listdir(raw)):
            if "star" in fn.lower() or "clickhouse" in fn.lower() or "history" in fn.lower():
                p = os.path.join(raw, fn)
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    n = sum(1 for _ in f)
                print(f"   raw/{fn}: {n} 行  {os.path.getsize(p)//1024} KB")


if __name__ == "__main__":
    t_rest()
    t_rest_subscribers()
    t_html()
    t_local()
