# -*- coding: utf-8 -*-
"""watch 通道判定：`/subscribers`（仓→人）与 `/users/{login}/subscriptions`（人→仓）到底活没活。

为什么必须等配额：403 + `X-RateLimit-Remaining: 0` 是**配额问题**，不是权限问题。
在配额耗尽时测，会把"限流"误读成"接口死了"。所以本脚本先轮询 `/rate_limit`
（该端点不消耗配额），等窗口重置后再发测试请求。

测试矩阵（3 个请求）：
    T1  /repos/{repo}/subscribers?per_page=1            仓→人（2026-06-30 起据称 admin-only）
    T2  /users/{login}/subscriptions?per_page=1         人→仓（计数：Link 尾页）
    T3  /users/{login}/subscriptions?per_page=100&page=1 人→仓（列表 + 是否有 Link）

判定：
    T1 200 → 仓维度仍开放（推翻 admin-only 结论，必须更新文档）
    T1 401/403/404 且 T2/T3 200 → 只能做反向与抽样估计
    三个全 401/403/404 → watch 数据彻底不可得，只剩 subscribers_count
"""
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from curl_cffi import requests as cffi

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "out")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
      "Accept": "application/vnd.github+json"}
PROFILES = ["chrome", "firefox133", "safari17_0", "edge101"]

REPO = "morluto/rea"
LOGIN = "mneves75"        # 公开、订阅数可观、无隐私顾虑的样本账号


def get(url, tries=4):
    for _ in range(tries):
        for prof in PROFILES:
            try:
                r = cffi.get(url, headers=UA, timeout=20, impersonate=prof)
            except Exception:
                time.sleep(1.0)
                continue
            if r.status_code in (200, 401, 403, 404):
                return r
            time.sleep(1.0)
    return None


def wait_for_quota(min_remaining=5, max_wait_min=45):
    t0 = time.time()
    while time.time() - t0 < max_wait_min * 60:
        r = get("https://api.github.com/rate_limit")     # 不消耗配额
        if r is None:
            time.sleep(30)
            continue
        core = r.json()["resources"]["core"]
        rem, reset = core["remaining"], core["reset"]
        print(f"[quota] 剩余 {rem}/{core['limit']} | 重置还有 {max(0, reset - time.time()) / 60:.1f} 分钟",
              flush=True)
        if rem >= min_remaining:
            return True
        wait = max(10, min(reset - time.time() + 15, 1800))
        time.sleep(wait)
    return False


def probe(url):
    r = get(url)
    if r is None:
        return {"http": 0}
    out = {"http": r.status_code, "remaining": r.headers.get("x-ratelimit-remaining")}
    try:
        d = r.json()
    except Exception:
        d = None
    if isinstance(d, list):
        out["items"] = len(d)
        out["has_link"] = bool(r.headers.get("Link"))
        if d and isinstance(d[0], dict):
            out["first_keys"] = sorted(d[0].keys())[:8]
            if d[0].get("repo"):
                out["first_repo"] = (d[0]["repo"] or {}).get("full_name")
            if d[0].get("login"):
                out["first_login"] = d[0]["login"]
    elif isinstance(d, dict):
        out["keys"] = sorted(d.keys())[:8]
        out["message"] = str(d.get("message"))[:120]
    return out


def main():
    print(f"目标：{REPO} / 样本账号 {LOGIN}", flush=True)
    if not wait_for_quota():
        print("等不到配额窗口，放弃（避免把 403 误读成权限）")
        return 1
    tests = [
        ("T0 对照 /starred（已知可用）", f"https://api.github.com/users/{LOGIN}/starred?per_page=1"),
        ("T1 仓→人 /subscribers", f"https://api.github.com/repos/{REPO}/subscribers?per_page=1"),
        ("T2 人→仓 /subscriptions(计数)", f"https://api.github.com/users/{LOGIN}/subscriptions?per_page=1"),
        ("T3 人→仓 /subscriptions(列表)", f"https://api.github.com/users/{LOGIN}/subscriptions?per_page=100&page=1"),
    ]
    results = {}
    for name, url in tests:
        res = probe(url)
        results[name] = res
        print(f"{name}: {json.dumps(res, ensure_ascii=False)}", flush=True)
        time.sleep(1.5)

    t0 = results["T0 对照 /starred（已知可用）"]["http"]
    t1 = results["T1 仓→人 /subscribers"]["http"]
    t2 = results["T2 人→仓 /subscriptions(计数)"]["http"]
    t3 = results["T3 人→仓 /subscriptions(列表)"]["http"]

    # 判定必须区分三件事：配额/网络失败(0、403+rem0)、权限拒绝(401/403/404)、成功(200)
    # 没有 T0 对照就下结论是本项目踩过的坑（把限流读成"接口死了"）。
    if t0 != 200:
        verdict = ("本次测试无效（对照 /starred 也失败 = 配额或网络问题），T1/T2/T3 结论未知"
                   if t0 == 0 else
                   "本次测试无效（对照 /starred 返回 %s，说明配额/限流干扰），结论未知" % t0)
    elif t1 == 200:
        verdict = "T1 开放（推翻 admin-only 结论）"
    elif t2 == 200 and t3 == 200:
        verdict = "仓→人 已关闭（T1=%s）；人→仓 可用 → 只能做反向 + 抽样估计" % t1
    elif t2 == 0 or t3 == 0:
        verdict = "T1 已关闭（%s）；T2/T3 网络失败，人→仓 结论未知" % t1
    else:
        verdict = "watch 数据彻底不可得（T1/T2/T3 全部拒绝：%s/%s/%s）" % (t1, t2, t3)
    print(f"\n结论：{verdict}")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "watch_channel_test.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# watch 通道判定（实测）\n\n")
        f.write(f"> 时间：{time.strftime('%Y-%m-%d %H:%M:%S')} · 仓库 {REPO} · 样本 {LOGIN}\n\n")
        f.write("| 测试 | 结果 |\n|---|---|\n")
        for name, res in results.items():
            f.write(f"| {name} | `{json.dumps(res, ensure_ascii=False)}` |\n")
        f.write(f"\n**结论：{verdict}**\n")
    print(f"→ {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
