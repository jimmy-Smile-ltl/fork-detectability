# -*- coding: utf-8 -*-
"""Forker 真实性探针 —— 一页 100 个 fork 者的「是真人吗 / 有真实活动吗」双通道采集。

━━━ 为什么是两个通道 ━━━

问「这个账号是不是真人、有没有真实活动」，能回答的数据分成两半，而它们的**成本差 60 倍**：

    通道 A  API  /users/{login}              1 请求/人，匿名配额 60 次/**小时**
    通道 B  HTML /users/{login}/contributions 1 请求/人，**不占 API 配额**

通道 A 给的是档案字段（注册时间 / followers / public_repos / bio …），
是一个人「**说自己是谁**」。通道 B 给的是过去 365 天**逐日贡献热力图**
（372 个 `<td data-date data-level>`），是一个人「**实际做了什么**」。

后者才是判「有没有真实活动」的关键，而且它不消耗那 60 次/小时的匿名配额。
实测判别力：torvalds 3,762 次贡献 vs techwithdb 10 次。

━━━ 实测边界（2026-10-21）━━━

* `/repos/{o}/{r}/forks?per_page=100&sort=newest` → 匿名 200，一页就是 100 人。
  fork 条目里**没有** bio/created_at 这类档案字段，但**有** fork 仓库自己的
  `created_at` / `pushed_at` —— 两者相等 = fork 完没动过，是**免费**的活动信号。
* `/users/{login}` → 匿名 200，但只有 **60 次/小时**。100 人要 ~2 小时。
* `github.com/{login}` 首屏 HTML 只有 name / bio / avatar，
  **followers / following 不在里面**（前端异步加载），所以不能替代通道 A。
* `github.com/users/{login}/contributions` → 200，含总数 + 逐日热力图。

━━━ 断点续采 ━━━

三个阶段各自追加写 JSONL，重跑时已采过的 login 直接跳过。
所以匿名配额用完后，隔一小时再跑一次就能补齐剩下的人（`--offline` 只重算不联网）。

用法：
    py _forker_probe.py                # 采集（配额用完自动停）+ 出表
    py _forker_probe.py --offline      # 不联网，用已落盘的数据重算 + 出表
    py _forker_probe.py --max-users 55 # 限制通道 A 的人数
    py _forker_probe.py --stage forks-all           # 全量 fork 名单（约 822 页，匿名 ~13.7 小时，可断点续跑）
    py _forker_probe.py --stage forks-all --proxy   # 同上，但走 KDL 代理出口（用户决策，见 README）
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

# Windows 控制台默认 GBK，表里的中文/符号会炸
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from curl_cffi import requests as cffi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler.config import GH_API, OUT_DIR, RAW_DIR, iso_to_ts, ts_to_iso, utcnow_iso
from crawler.http_client import GhClient, RateLimited, SuppressWindow
from crawler.sources.users import _row

REPO = "openclaw/openclaw"
PER_PAGE = 100
SORT = "newest"

RAW_FORKS = os.path.join(RAW_DIR, "probe_forks.jsonl")
RAW_USERS = os.path.join(RAW_DIR, "probe_users.jsonl")
RAW_CONTRIB = os.path.join(RAW_DIR, "probe_contrib.jsonl")
RAW_EVENTS = os.path.join(RAW_DIR, "probe_events.jsonl")
RAW_REPOS = os.path.join(RAW_DIR, "probe_repos.jsonl")
RAW_REPOS_HTML = os.path.join(RAW_DIR, "probe_repo_names.jsonl")
RAW_FORKS_ALL = os.path.join(RAW_DIR, "probe_forks_all.jsonl")
STATE_FORKS_ALL = os.path.join(RAW_DIR, "probe_forks_all.page")
PROXY_CFG = os.path.join(os.path.dirname(RAW_DIR), "proxy_kdl.json")
OUT_CSV = os.path.join(OUT_DIR, "forker_probe_100.csv")

UA_HTML = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
HTML_PROFILES = ["firefox133", "chrome142", "safari184", "edge101", "chrome145", "firefox144"]


# ══ JSONL 小工具 ════════════════════════════════════════
def load_jsonl(path: str) -> dict:
    """按主键去重读回。文件不存在 = 空字典（正常状态，不是错误）。"""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue          # 上次被打断写了半行
            key = o.get("login")
            if key:
                out[key] = o
    return out


def append_jsonl(path: str, obj: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ══ 通道 A：API（占配额）══════════════════════════════════
def fetch_forks(gh: GhClient) -> list:
    """一页 fork 名单。返回 100 条原始对象。"""
    r = gh.request(f"{GH_API}/repos/{REPO}/forks",
                   params={"per_page": PER_PAGE, "sort": SORT})
    if r.status_code != 200:
        raise RuntimeError(f"forks 页面 HTTP {r.status_code}: {r.text[:300]}")
    items = r.json()
    rem = r.headers.get("x-ratelimit-remaining")
    print(f"[forks] 拿到 {len(items)} 条，本小时剩余配额 {rem}")
    return items


def fork_row(f: dict) -> dict:
    """fork 条目 → 探针行。`pushed_at == created_at` 就是「fork 完没动过」。"""
    owner = f.get("owner") or {}
    created, pushed = f.get("created_at"), f.get("pushed_at")
    c_ts, p_ts = iso_to_ts(created), iso_to_ts(pushed)
    lag_h = ((p_ts - c_ts) / 3600.0) if (c_ts and p_ts) else None
    return {
        "login": (owner.get("login") or "").lower(),
        "fork_full_name": f.get("full_name"),
        "fork_created_at": created,
        "fork_pushed_at": pushed,
        "fork_pushed_lag_hours": round(lag_h, 2) if lag_h is not None else None,
        "fork_touched": int(lag_h is not None and lag_h > (1 / 60.0)),   # >60 秒才算真动过
        "fork_size_kb": f.get("size"),
        "fork_stars": f.get("stargazers_count"),
        "fork_open_issues": f.get("open_issues_count"),
        "fork_language": f.get("language"),
        "owner_id": owner.get("id"),
        "owner_type": owner.get("type"),
    }


# ══ 全量 fork 名单（可断点续爬）══════════════════════════
def _load_fork_all_ids() -> set:
    """已落盘的全量 fork 主键（fork 仓库 id）。文件不存在 = 空集合。"""
    ids = set()
    if not os.path.exists(RAW_FORKS_ALL):
        return ids
    with open(RAW_FORKS_ALL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue            # 上次被打断写了半行
            if o.get("fork_id") is not None:
                ids.add(o["fork_id"])
    return ids


def _read_page_cursor() -> int:
    try:
        with open(STATE_FORKS_ALL, "r", encoding="utf-8") as f:
            return max(1, int((f.read() or "1").strip()))
    except (OSError, ValueError):
        return 1


def _write_page_cursor(page: int) -> None:
    with open(STATE_FORKS_ALL, "w", encoding="utf-8") as f:
        f.write(str(page))


def _plain_get(url: str, params: dict):
    """curl_cffi 全部档位被抑制时的兜底直连。

    ★ 实测（2026-10-24）：curl_cffi 的 9 个 impersonate 档位同时 SSL 失败时，
      python-requests 的默认 TLS 栈仍能 200 —— 抑制是按指纹选的，不是按 IP。
      全量 fork 要跑十几个小时，抑制窗口是常态；没有这层兜底，
      整夜会耗在 GhClient 的 120→1800s 退避里，一页都拿不到。
    匿名请求，不带任何凭据。
    """
    import requests
    return requests.get(url, params=params, timeout=30,
                        headers={"User-Agent": UA_HTML,
                                 "Accept": "application/vnd.github+json"})


# ══ 代理路径（用户 2026-10-24 决策：822 页走 KDL 私密代理）═════════════
# 决策记录见 README「请求配额与令牌」。要点：
#   * 这是用户对自己账号安全的取舍（不想动用任何 GitHub 账号），
#     且量级很小（822 次请求 ≈ 十几个出口 IP，非大规模采集）；
#   * 凭据只从 data/proxy_kdl.json（gitignored）读，绝不硬编码进源码；
#   * 走代理时完全绕开 GhClient（它的抑制窗口/指纹轮换是按直连设计的）。
class ProxyPool:
    """快代理私密代理的极简封装：按需取 IP、失败即弃、统计成功率。"""

    def __init__(self, cfg_path: str = PROXY_CFG):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.api_url = cfg["api_url"]          # 含 {num} 占位符
        self.user = cfg["username"]
        self.pwd = cfg["password"]
        self.ok = self.dead = 0

    def fetch_ips(self, num: int = 5) -> list:
        """从 KDL API 提取 num 个 IP。失败返回空列表（调用方降级直连）。

        ★ 本机对 python-requests 的 TLS 指纹有选择性抑制（直连 KDL API 被 RST），
          curl_cffi 的浏览器指纹能过 —— 与 api.github.com 的抑制是同一类现象，
          所以这里用 curl_cffi 而不是 requests。
        """
        try:
            r = cffi.get(self.api_url.format(num=num), timeout=15,
                         impersonate="firefox133")
            if r.status_code != 200:
                print(f"[proxy] KDL API HTTP {r.status_code}: {r.text[:120]}")
                return []
            ips = [x.strip() for x in r.text.splitlines() if x.strip()]
            return ips[:num]
        except Exception as e:
            print(f"[proxy] KDL API 请求失败：{type(e).__name__}: {e}")
            return []

    def build_proxies(self, ip: str) -> dict:
        auth = f"http://{self.user}:{self.pwd}@{ip}/"
        return {"http": auth, "https": auth}

    def get_page(self, url: str, params: dict, ips: list):
        """依次尝试 ips 里的出口，返回第一个 HTTP 200 的响应；全失败返回 None。

        非 200（403/429 等）也视为该出口失败 —— 换 IP 重试比解析限流语义便宜。
        """
        import requests
        for ip in ips:
            try:
                r = requests.get(url, params=params, timeout=25,
                                 proxies=self.build_proxies(ip),
                                 headers={"User-Agent": UA_HTML,
                                          "Accept": "application/vnd.github+json"})
                if r.status_code == 200:
                    self.ok += 1
                    return r
                print(f"[proxy] {ip} → HTTP {r.status_code}，换下一个")
            except Exception as e:
                print(f"[proxy] {ip} 连接失败（{type(e).__name__}），换下一个")
            self.dead += 1
        return None


def fetch_forks_all_proxy() -> dict:
    """全量 fork 名单的代理版：每批 5 个出口 IP 轮着用，失败降级直连。

    与 fetch_forks_all 同一套游标/去重/落盘逻辑（文件互通，可随时来回切），
    区别只在传输层：匿名直连 60 页/小时的配额墙在这里被多个出口分摊。
    """
    pool = ProxyPool()
    seen = _load_fork_all_ids()
    total0 = len(seen)
    page = _read_page_cursor()
    if total0:
        print(f"[forks-all-proxy] 已有 {total0:,} 条，从第 {page} 页继续（sort=oldest）")
    else:
        print("[forks-all-proxy] 全量采集开始（sort=oldest，代理出口）")

    ips = pool.fetch_ips(5)
    last_swap = 0
    if not ips:
        print("[forks-all-proxy] ⚠ KDL 没给出 IP，降级直连模式（60 页/小时）")

    while True:
        url = f"{GH_API}/repos/{REPO}/forks"
        params = {"per_page": PER_PAGE, "sort": "oldest", "page": page}

        r = None
        if ips:
            r = pool.get_page(url, params, ips)
        if r is None:
            # 代理失败 → 直连兜底（可能撞配额墙，睡到重置）
            try:
                r = _plain_get(url, params)
            except Exception as e:
                print(f"[forks-all-proxy] 直连也失败：{type(e).__name__}: {e}；60s 后重试")
                time.sleep(60)
                continue
            rem = r.headers.get("x-ratelimit-remaining")
            if r.status_code == 403 and rem == "0":
                reset_at = r.headers.get("x-ratelimit-reset")
                wait = max(30.0, float(reset_at) - time.time() + 5) if reset_at else 3600.0
                print(f"[forks-all-proxy] ⏸ 直连配额也耗尽，睡 {wait/60:.1f} 分钟并换一批 IP ...",
                      flush=True)
                time.sleep(min(wait, 60))       # 只睡一小段，主要靠换 IP
                ips = pool.fetch_ips(5) or ips
                continue
            if r.status_code != 200:
                print(f"[forks-all-proxy] 直连 HTTP {r.status_code}，120s 后重试")
                time.sleep(120)
                continue

        items = r.json()
        if not items:
            print(f"[forks-all-proxy] 第 {page} 页为空 → 全量采集完成")
            break

        new = 0
        for f in items:
            fid = f.get("id")
            row = fork_row(f)
            if not row["login"] or fid in seen:
                continue
            row["fork_id"] = fid
            append_jsonl(RAW_FORKS_ALL, row)
            seen.add(fid)
            new += 1

        page += 1
        _write_page_cursor(page)
        time.sleep(1.2)   # 节流：稳低于 GitHub 二级限流 1 req/s 红线（按单出口计）

        # 每 25 页换一批 IP（KDL 私密代理按时间计费，长跑要轮换；
        # 实测每个出口自带一份独立 60/h 配额，但共享池可能有少量预消耗）。
        # ★ 出口失败计数达 10 也提前换批：单批 5 个出口里若有坏 IP 留在池中，
        #   会被每页反复试到 25 页轮换点为止，浪费 20s+ 超时 × 几十次。
        if page % 25 == 0 or pool.dead - last_swap >= 10:
            fresh = pool.fetch_ips(5)
            if fresh:
                ips = fresh
            last_swap = pool.dead
            time.sleep(1.0)
        print(f"[forks-all-proxy] 第 {page-1} 页 +{new}，累计 {len(seen):,} 条 "
              f"（出口 ok={pool.ok} dead={pool.dead}）", flush=True)

    return {"rows": len(seen), "new": len(seen) - total0, "next_page": page}


def fetch_forks_all(gh: GhClient) -> dict:
    """把整个仓库的 fork 名单翻完（82k 条 ≈ 822 页），逐页追加落盘。

    ★ 为什么是 `sort=oldest` 而不是 `newest`：

      `newest` 排序下，每新增一个 fork，所有已有内容在页码上**整体后移一位**。
      这次全量要跑十几个小时，期间随时有人 fork，翻到后面就会重复页、漏页。
      `oldest` 排序只在**末尾追加**，已翻过的页号永远稳定，断点续爬天然成立。

    ★ 匿名配额 60 次/小时 → 每页 1 次请求 → 约 13.7 小时。
      配额耗尽时睡到重置（`GhClient` 只自动等 60 秒以内的，更长的抛给这里处理）；
      交叉指纹被抑制时 `GhClient` 自己轮换，连续失败则整页重试。

    实测边界（2026-10-24）：`forks_count` = 82,583，可列出 82,174 条
    （末页 = 第 822 页，74 条），第 823 页起为空 —— 差额 ~409 条是已删除/
    不可列出的 fork，任何翻页方式都拿不到。API 本身**没有** 100 页上限。
    """
    seen = _load_fork_all_ids()
    total0 = len(seen)
    if seen and not os.path.exists(STATE_FORKS_ALL):
        # 游标文件丢了：每页 100 条，按条数推算下一页（末页不满 100 也能算对）
        page = len(seen) // PER_PAGE + 1
    else:
        page = _read_page_cursor()
    if total0:
        print(f"[forks-all] 已有 {total0:,} 条，从第 {page} 页继续（sort=oldest）")
    else:
        print("[forks-all] 全量采集开始（sort=oldest，约 822 页 / 匿名 ~13.7 小时）")

    while True:
        url = f"{GH_API}/repos/{REPO}/forks"
        params = {"per_page": PER_PAGE, "sort": "oldest", "page": page}
        try:
            # max_attempts=3：撞上抑制窗口就尽快交给下面的直连兜底，
            # 不要在 GhClient 里耗完整条 120→1800s 的退避阶梯。
            r = gh.request(url, params=params, max_attempts=3)
        except RateLimited as e:
            wait = max(30.0, (e.reset_at or 0) - time.time() + 5)
            print(f"[forks-all] ⏸ {e}；睡 {wait/60:.1f} 分钟 ...", flush=True)
            time.sleep(wait)
            continue
        except SuppressWindow as e:
            print(f"[forks-all] curl_cffi 全档位失败（{e}），改用直连兜底 ...", flush=True)
            try:
                r = _plain_get(url, params)
            except Exception as e2:
                print(f"[forks-all] 直连也失败：{type(e2).__name__}: {e2}；"
                      f"120s 后重试第 {page} 页", flush=True)
                time.sleep(120)
                continue
            # 直连成功 → 清掉 GhClient 刚开的抑制窗口，否则下一页会先白等 120s
            gh._reset_window("api.github.com")

        if r.status_code == 403:
            rem = r.headers.get("x-ratelimit-remaining")
            reset_at = r.headers.get("x-ratelimit-reset")
            if rem == "0" and reset_at:
                wait = max(30.0, float(reset_at) - time.time() + 5)
                print(f"[forks-all] ⏸ 配额耗尽，睡 {wait/60:.1f} 分钟 ...", flush=True)
                time.sleep(wait)
                continue

        if r.status_code != 200:
            print(f"[forks-all] 第 {page} 页 HTTP {r.status_code}，停止：{r.text[:200]}")
            break

        items = r.json()
        if not items:
            print(f"[forks-all] 第 {page} 页为空 → 全量采集完成")
            break

        new = 0
        for f in items:
            fid = f.get("id")
            row = fork_row(f)
            if not row["login"] or fid in seen:
                continue
            row["fork_id"] = fid
            append_jsonl(RAW_FORKS_ALL, row)
            seen.add(fid)
            new += 1

        page += 1
        _write_page_cursor(page)
        print(f"[forks-all] 第 {page-1} 页 +{new}，累计 {len(seen):,} 条，"
              f"配额 {r.headers.get('x-ratelimit-remaining')}", flush=True)

    return {"rows": len(seen), "new": len(seen) - total0, "next_page": page}


def fetch_user(gh: GhClient, login: str) -> dict:
    """通道 A。配额耗尽抛 RateLimited，由调用方优雅收尾。"""
    try:
        data = gh.get_json(f"{GH_API}/users/{login}")
    except RateLimited:
        raise
    except Exception as e:
        return {"login": login, "api_status": -1, "api_error": f"{type(e).__name__}: {e}"}

    if data is None:                       # 404 = 已注销/改名，本身就是信号
        return {"login": login, "api_status": 404}

    row = _row(login, data)
    row["api_status"] = 200
    row["login"] = login
    # 热力图节点数 0 不代表假号，但 null 会让下游区分不了「没采」和「真是 0」
    return row


# ══ 通道 B：HTML 贡献热力图（不占配额）═══════════════════
def html_get(url: str, tries: int = 6):
    """HTML 请求。失败换 TLS 档位重试 —— 与 API 侧同一个抑制窗口问题。"""
    last = ""
    for i in range(tries):
        prof = HTML_PROFILES[i % len(HTML_PROFILES)]
        try:
            r = cffi.get(url, impersonate=prof, timeout=45,
                         headers={"User-Agent": UA_HTML,
                                  "Accept": "text/html,application/xhtml+xml"})
            if r.status_code == 200:
                return r
            if r.status_code in (404, 429):       # 不重试，语义明确
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.2 * (i + 1))
    print(f"    [html] {url} 放弃：{last}")
    return None


def parse_contrib(text: str) -> dict:
    """把贡献热力图解析成可算的数列。

    `<h2>… 3,762 contributions in the last year` 给总数；
    372 个 `<td data-date="2026-10-21" data-level="3">` 给逐日强度 0–4。
    """
    m = re.search(r"([0-9,]+)\s+contributions?\s+in\s+the\s+last\s+year", text)
    total = int(m.group(1).replace(",", "")) if m else None

    cells = []
    for tm in re.finditer(r"<td\b[^>]*>", text):
        tag = tm.group(0)
        d = re.search(r'data-date="([0-9]{4}-[0-9]{2}-[0-9]{2})"', tag)
        l = re.search(r'data-level="([0-9])"', tag)
        if d and l:
            cells.append((d.group(1), int(l.group(1))))
    cells.sort()

    levels = [lv for _, lv in cells]
    active = sum(1 for lv in levels if lv > 0)

    def window(n):
        return sum(1 for lv in levels[-n:] if lv > 0) if levels else None

    # 最长连续有贡献天数
    streak = best = 0
    for lv in levels:
        streak = streak + 1 if lv > 0 else 0
        best = max(best, streak)

    return {
        "contrib_total": total,
        "contrib_cells": len(cells),
        "contrib_active_days": active,
        "contrib_active_pct": round(active / len(levels) * 100, 1) if levels else None,
        "contrib_last30": window(30),
        "contrib_last90": window(90),
        "contrib_streak_max": best,
        "contrib_level4_days": sum(1 for lv in levels if lv == 4),
        "contrib_first_day": cells[0][0] if cells else None,
        "contrib_last_day": cells[-1][0] if cells else None,
        "contrib_levels": "".join(str(x) for x in levels),   # 原始序列，留作复核
    }


def fetch_contrib(login: str, pace: float) -> dict:
    url = f"https://github.com/users/{login}/contributions"
    r = html_get(url)
    if r is None:
        return {"login": login, "contrib_status": -1}
    if r.status_code != 200:
        return {"login": login, "contrib_status": r.status_code}
    out = {"login": login, "contrib_status": 200, "fetched_at": utcnow_iso()}
    out.update(parse_contrib(r.text))
    time.sleep(pace)
    return out


# ══ 通道 C：API 事件流（占配额，3 小时窗口）════════════════
def fetch_events(gh: GhClient, login: str) -> dict:
    """近 90 天公开事件。Retina 窗口只有 300 条 / 90 天，但足够判「有没有在动」。

    ⚠️ 0 事件**不等于**没活动 —— 私有仓库的活动不会出现在这里。
       所以这个量只做展示，不参与扣分。
    """
    data = gh.get_json(f"{GH_API}/users/{login}/events/public",
                       params={"per_page": 100})
    if data is None:
        return {"login": login, "events_status": 404}
    data = data or []
    types = Counter(e.get("type") for e in data if e.get("type"))
    times = sorted(e.get("created_at") or "" for e in data)
    repos = {((e.get("repo") or {}).get("name")) for e in data}
    repos.discard(None)
    return {
        "login": login,
        "events_status": 200,
        "events_n": len(data),
        "events_last_at": times[-1] if times else None,
        "events_first_at": times[0] if times else None,
        "events_distinct_repos": len(repos),
        "events_push": types.get("PushEvent", 0),
        "events_types": ",".join(f"{k}:{v}" for k, v in types.most_common(6)),
        "fetched_at": utcnow_iso(),
    }


def fetch_repos(gh: GhClient, login: str) -> dict:
    """该账号的公开仓库清单 —— 回答「这些 repos 是真项目还是摆设」。

    ★ 数量本身不说明问题（买的号常带 3~19 个种子仓库）。真正有判别力的是：
      - **非 fork 仓库有几个** —— fork 是零成本的，非 fork 才要动手建
      - **非 fork 仓库最近一次 push 距今多久** —— fork 的 pushed_at 只是创建时间，
        混进去算会把「没动过」误读成「刚动过」
      - **空仓库有几个** —— size=0 且无描述无语言 = 建了就没用过的占位
    """
    data = gh.get_json(f"{GH_API}/users/{login}/repos",
                       params={"per_page": 100, "sort": "pushed", "direction": "desc"})
    if data is None:
        return {"login": login, "repos_status": 404}
    data = data or []
    forks = [r for r in data if r.get("fork")]
    orig = [r for r in data if not r.get("fork")]

    def latest(rs):
        ts = [iso_to_ts(r.get("pushed_at") or "") for r in rs]
        ts = [t for t in ts if t]
        return max(ts) if ts else None

    lo, lall = latest(orig), latest(data)
    empty = [r for r in data
             if (r.get("size") or 0) == 0 and not r.get("description") and not r.get("language")]
    langs = Counter(r.get("language") for r in data if r.get("language"))
    return {
        "login": login,
        "repos_status": 200,
        "repos_n": len(data),
        "repos_forks_n": len(forks),
        "repos_original_n": len(orig),
        # 名字是最有信息量的一列：「my-first-repo」「test」和
        # 「threejs-devtools-mcp」完全是两种人。原仓库排前面。
        "repos_names": ",".join([r["name"] for r in orig[:12]]
                                + [r["name"] for r in forks[:6]]),
        "repos_orig_stars": sum(r.get("stargazers_count") or 0 for r in orig),
        "repos_desc_n": sum(1 for r in data if r.get("description")),
        "repos_empty_n": len(empty),
        "repos_stars_sum": sum(r.get("stargazers_count") or 0 for r in data),
        "repos_first_created_at": min((r.get("created_at") for r in data
                                       if r.get("created_at")), default=None),
        "repos_last_push_at": ts_to_iso(lall) if lall else None,
        "repos_original_push_days": (round((time.time() - lo) / 86400.0, 1)
                                     if lo else None),
        "repos_langs": ",".join(f"{k}:{v}" for k, v in langs.most_common(4)),
        "fetched_at": utcnow_iso(),
    }


def fetch_repos_html(login: str, pace: float = 0.8) -> dict:
    """通道 D 的**免配额**备份：直接解析 `/{login}?tab=repositories` 的 HTML。

    为什么需要它：仓库清单要问 95 个人，匿名 API 配额 60/小时，一轮跑不完。
    而 HTML 页面不占配额，只是少了 star 数、描述、创建时间。

    ★ 真正有判别力的东西 HTML 一样能给：**仓库叫什么、是不是 fork**。
      「有三个仓库」毫无意义，「有三个自己写的、带语言标签的仓库」才有意义。
    """
    r = html_get(f"https://github.com/{login}?tab=repositories")
    if r is None:
        return {"login": login, "repo_html_status": -1}
    if r.status_code != 200:
        return {"login": login, "repo_html_status": r.status_code}
    time.sleep(pace)

    items = re.findall(r'<li[^>]*itemprop="owns"[^>]*>(.*?)</li>', r.text, re.S)
    names, orig_names, forks, langs, stamps = [], [], 0, Counter(), []
    for it in items:
        m = re.search(r'itemprop="name codeRepository"[^>]*>\s*([^<]+?)\s*<', it)
        if not m:
            continue
        name = m.group(1).strip()
        names.append(name)
        if "Forked from" in it:
            forks += 1
        else:
            orig_names.append(name)
        lm = re.search(r'itemprop="programmingLanguage">([^<]+)<', it)
        if lm:
            langs[lm.group(1).strip()] += 1
        tm = re.search(r'datetime="([0-9T:\-Z]+)"', it)
        if tm:
            stamps.append(tm.group(1))

    return {
        "login": login,
        "repo_html_status": 200,
        "repo_html_n": len(names),
        "repo_html_forks_n": forks,
        "repo_html_original_n": len(orig_names),
        # 名字本身就是要看的东西，原样留下（原仓库排前面）
        "repos_names": ",".join(orig_names[:12] + [n for n in names if n not in orig_names][:6]),
        "repo_html_langs": ",".join(f"{k}:{v}" for k, v in langs.most_common(4)),
        "repo_html_last_at": max(stamps) if stamps else None,
        "fetched_at": utcnow_iso(),
    }


# ══ 指标合成 ════════════════════════════════════════════
HEXISH = re.compile(r"^[a-f0-9]{8,}$")
ENDS_DIGITS = re.compile(r"(\d+)$")


def login_shape(login: str) -> dict:
    """登录名形态。批量注册的号常见「无意义串 / 词+4~8 位数字」。"""
    tail = ENDS_DIGITS.search(login)
    return {
        "login_len": len(login),
        "login_ends_digits": len(tail.group(1)) if tail else 0,
        "login_hexish": int(bool(HEXISH.match(login))),
        "login_has_sep": int(("-" in login) or ("_" in login)),
    }


def detect_bursts(fork_times: list, window_h: int = 3, z: float = 2.0) -> set:
    """在 fork 时间轴上找异常聚集的窗口，返回落在窗口里的「整点小时」集合。

    为什么用**绝对小时**而不是「一天中的第几小时」：
      样本可能横跨两天，按 hour-of-day 归并会把不同日子的同一钟点混在一起。

    为什么用**滚动窗口**而不是单小时：
      刷量不是同一秒齐射。实测这 100 人里每分钟最多 1 个，
      单小时看只是「略高」，三小时连成一片才看出来。
      窗口阈值 = 全体窗口的 均值 + z × 标准差 —— 数据自己说话，不手调。
    """
    hours = []
    for t in fork_times:
        ts = iso_to_ts(t or "")
        if ts:
            hours.append(int(ts // 3600))
    if len(hours) < 10:
        return set()
    lo, hi = min(hours), max(hours)
    dense = [0] * (hi - lo + 1)
    for h in hours:
        dense[h - lo] += 1

    n = len(dense)
    w = min(window_h, n)
    sums = [sum(dense[i:i + w]) for i in range(n - w + 1)]
    mean = sum(sums) / len(sums)
    var = sum((x - mean) ** 2 for x in sums) / len(sums)
    thr = mean + z * (var ** 0.5)

    flagged = set()
    for i, s in enumerate(sums):
        if s > thr:
            for h in range(lo + i, lo + i + w):
                flagged.add(h)
    return flagged


def assess(r: dict) -> tuple:
    """把一条记录压成 (score, tier, flags[])。

    ★ 这是**可疑度分层**，不是判定。所有阈值都是先验的、可调的，
      最终应与对照组分布一起看（本探针只跑 openclaw，没有对照组）。

    ★ 2026-10-21 修订：实测有两条直觉信号**方向是反的**，已从计分里剔除 ——
        · 「档案全空」在爆发窗占 16%、在对照组占 50%
        · 「followers=0 且 following=0」在爆发窗占 34%、对照组占 47%
      真实的沉默开发者远比批量号更可能不填档案、不关注任何人。
      把它们当可疑信号会把最无辜的人先抓出来。

      反过来，批量制备的号往往**有一份填得挺整齐的最小档案**
      （姓名 + 地点，没有公司/博客/bio），这一条才进计分。

    ★ 贡献热力图**不在这里计分**，它走 activity_level() 单算一个活动轴。
      理由见那个函数的说明：活动量和身份可疑度是两个问题。
    """
    flags = []
    score = 0

    # ── 1. fork 那一刻的账号年龄（最硬的一条）──
    age = r.get("account_age_days_at_fork")
    if age is None:
        pass
    elif age < 1:
        flags.append(f"注册当天就 fork（{age * 24:.1f} 小时）"); score -= 4
    elif age < 7:
        flags.append(f"fork时账号仅{age:.0f}天"); score -= 3
    elif age < 30:
        flags.append(f"fork时账号{age:.0f}天"); score -= 2
    elif age < 180:
        pass
    elif age < 365:
        score += 1
    else:
        score += 2

    # ── 2. 档案形态 ──
    #   惩罚只给「最小模板形」；填得比较全算正向（真人通常愿意留下公司/博客）。
    #   注意：**档案全空不扣分**（见上面的修订说明）。
    filled = r.get("profile_fields_filled")
    if filled is not None:
        if r.get("profile_template_shape"):
            flags.append("档案为最小模板形（姓名+地点，无公司/博客/bio）"); score -= 1
        elif filled >= 4:
            score += 1

    # ── 3. 社交关系：只保留正向（有 follower 的人不太可能是新号）──
    f = r.get("followers")
    if f is not None:
        if f >= 100:
            score += 2
        elif f >= 10:
            score += 1

    # ── 4. 公开仓库 ──
    pr = r.get("public_repos")
    if pr is not None:
        if pr == 0:
            flags.append("无公开仓库"); score -= 1
        elif pr >= 5:
            score += 1

    # ── 5. 登录名形态 ──
    if r.get("login_hexish"):
        flags.append("登录名疑似随机串"); score -= 1
    if (r.get("login_ends_digits") or 0) >= 4:
        flags.append("登录名末尾4+数字"); score -= 1

    # ── 6. 是否落在批量 fork 的时间窗里（批次信号，不是个人信号）──
    if r.get("fork_in_burst"):
        flags.append("fork时间落在聚集窗"); score -= 1

    if r.get("api_status") == 404:
        flags.append("账号已注销/改名"); score -= 2

    tier = ("正常" if score >= 5 else
            "中性" if score >= 0 else
            "可疑" if score >= -3 else "高危")
    return score, tier, flags


def activity_level(r: dict) -> str:
    """活动轴 —— 与「可疑度」**分开算**。

    为什么必须分开（2026-10-21 实测）：
      贡献热力图在爆发窗里反而更高（中位数 99 次 vs 对照组 11 次）。
      如果把「贡献少」直接扣进可疑度，等于拿活动量去猜身份，
      方向还是反的 —— 会和 followers=0 一样把最无辜的人先抓出来。

    两个轴组合起来才有意义：
      · 可疑 × 活跃   → 像买来的老号（有真实历史，但行为是批量的）
      · 可疑 × 无活动 → 像一次性小号
      · 正常 × 无活动 → 只看不说的潜水者，不该被怀疑
    """
    ct = r.get("contrib_total")
    if ct is None:
        return "未知"                       # 采不到就说采不到，不要填 0
    l90 = r.get("contrib_last90") or 0
    if l90 >= 10:                           # 90 天里至少 10 天在提交 ≈ 每周
        return "活跃"
    if l90 > 0:                             # 动过，但不密
        return "零星"
    if ct > 0:                              # 近 90 天完全没动，但一年内有历史
        return "沉寂"
    return "无公开活动"


def merge_rows(forks: dict, users: dict, contribs: dict, events: dict = None,
               repos: dict = None, names: dict = None) -> list:
    events = events or {}
    repos = repos or {}
    names = names or {}
    rows = []
    for login, f in forks.items():
        r = dict(f)
        u = users.get(login) or {}
        c = contribs.get(login) or {}
        ev = events.get(login) or {}
        rp = repos.get(login) or {}
        nm = names.get(login) or {}

        r["api_status"] = u.get("api_status")
        r["account_created_at"] = u.get("created_at")
        r["account_type"] = u.get("type")
        for k in ("name", "bio", "company", "blog", "location", "email", "twitter",
                  "public_repos", "public_gists", "followers", "following",
                  "profile_fields_filled", "is_identicon", "updated_at"):
            r[k] = u.get(k)

        # 账号年龄是整份分析的核心量：用 fork 时间做基准（不是今天）
        c_ts = iso_to_ts(u.get("created_at") or "")
        f_ts = iso_to_ts(f.get("fork_created_at") or "")
        r["account_age_days_at_fork"] = (round((f_ts - c_ts) / 86400.0, 2)
                                         if (c_ts and f_ts) else None)

        for k in ("contrib_total", "contrib_active_days", "contrib_active_pct",
                  "contrib_last30", "contrib_last90", "contrib_streak_max",
                  "contrib_level4_days", "contrib_first_day", "contrib_last_day",
                  "contrib_status"):
            r[k] = c.get(k)

        for k in ("events_status", "events_n", "events_last_at", "events_first_at",
                  "events_distinct_repos", "events_push", "events_types"):
            r[k] = ev.get(k)

        for k in ("repos_status", "repos_n", "repos_forks_n", "repos_original_n",
                  "repos_desc_n", "repos_empty_n", "repos_stars_sum",
                  "repos_first_created_at", "repos_last_push_at",
                  "repos_original_push_days", "repos_langs", "repos_names"):
            r[k] = rp.get(k)

        # HTML 通道（免配额）单列自己的字段，**不冒充 API 结果**：
        #   repos_status 只代表 API 通道，缺了就是缺了，不能拿 HTML 补上，
        #   否则下游会以为拿到了 star 数/描述数（那些 HTML 根本没有）。
        for k in ("repo_html_status", "repo_html_n", "repo_html_forks_n",
                  "repo_html_original_n", "repo_html_langs", "repo_html_last_at"):
            r[k] = nm.get(k)

        # 名单是两路可以互相补的：API 更全，HTML 覆盖面更广。
        # 老数据（补名单字段之前采的 API 行）没有名字，用 HTML 的补上。
        if rp:
            r["repos_source"] = "api"
        if not r.get("repos_names") and nm.get("repos_names"):
            r["repos_names"] = nm["repos_names"]
            r["repos_source"] = "api+html" if rp else "html"

        r.update(login_shape(login))

        # 最小模板形档案：有姓名 + 有地点，但公司/博客/bio/邮箱/推特全空。
        # 正常人要么什么都不填，要么填得较全；「只填姓名和地点」更像按模板生成的 persona。
        name = (u.get("name") or "").strip()
        loc = (u.get("location") or "").strip()
        r["profile_template_shape"] = int(
            bool(name and loc)
            and not any((u.get(k) or "").strip()
                        for k in ("company", "blog", "bio", "email", "twitter")))

        rows.append(r)

    # 批次信号要看到全样本才能算，所以 assess 放在第二遍
    burst = detect_bursts([r.get("fork_created_at") for r in rows])
    for r in rows:
        ts = iso_to_ts(r.get("fork_created_at") or "")
        r["fork_in_burst"] = int(bool(ts) and int(ts // 3600) in burst)
        score, tier, flags = assess(r)
        r["score"] = score
        r["tier"] = tier
        r["flags"] = ";".join(flags)
        r["activity"] = activity_level(r)

    rows.sort(key=lambda x: (x["score"], x.get("fork_created_at") or ""))
    return rows


# ══ 输出 ════════════════════════════════════════════════
COLS = [
    "login", "tier", "score", "activity", "flags",
    "account_created_at", "account_age_days_at_fork", "account_type",
    "fork_in_burst", "profile_template_shape",
    "contrib_total", "contrib_active_days", "contrib_last30", "contrib_last90",
    "contrib_streak_max", "contrib_level4_days", "contrib_first_day", "contrib_last_day",
    "events_n", "events_last_at", "events_distinct_repos", "events_push",
    "repos_n", "repos_forks_n", "repos_original_n", "repos_desc_n", "repos_empty_n",
    "repos_original_push_days", "repos_last_push_at", "repos_stars_sum", "repos_langs",
    "repos_names", "repos_source",
    "repo_html_n", "repo_html_forks_n", "repo_html_original_n", "repo_html_langs",
    "repo_html_last_at", "repo_html_status",
    "followers", "following", "public_repos", "public_gists",
    "profile_fields_filled", "login_ends_digits", "login_hexish",
    "fork_touched", "fork_pushed_lag_hours",
    "fork_created_at", "api_status", "contrib_status", "events_status", "repos_status",
]


def write_csv(rows: list) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:   # BOM 让 Excel 认中文
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def fmt(v, width):
    s = "" if v is None else str(v)
    if len(s) > width:
        s = s[: width - 1] + "…"
    return s.ljust(width)


def print_report(rows: list) -> None:
    n = len(rows)
    print("\n" + "=" * 108)
    print(f"forker 探针 —— {REPO} 最新 {n} 个 fork")
    print("=" * 108)

    tiers = Counter(r["tier"] for r in rows)
    print("\n【分层】" + "  ".join(f"{t}={tiers.get(t,0)}" for t in ("正常", "中性", "可疑", "高危")))

    got_u = sum(1 for r in rows if r.get("api_status") == 200)
    gone = sum(1 for r in rows if r.get("api_status") == 404)
    got_c = sum(1 for r in rows if r.get("contrib_status") == 200)
    got_e = sum(1 for r in rows if r.get("events_status") == 200)
    got_r = sum(1 for r in rows if r.get("repos_status") == 200)
    print(f"【覆盖】通道A /users 成功 {got_u}/{n}（404={gone}，未采={n-got_u-gone}）  "
          f"通道B 贡献图成功 {got_c}/{n}  通道C 事件流成功 {got_e}/{n}  通道D 仓库清单成功 {got_r}/{n}")

    # ── 分布 ──
    ct = [r["contrib_total"] for r in rows if r.get("contrib_total") is not None]
    if ct:
        ct.sort()
        q = lambda p: ct[min(len(ct) - 1, int(len(ct) * p))]
        print(f"\n【过去一年贡献数】n={len(ct)}  0次={sum(1 for x in ct if x==0)}  "
              f"<10次={sum(1 for x in ct if x<10)}  中位数={q(.5)}  p90={q(.9)}  max={ct[-1]}")

    ev = [r for r in rows if r.get("events_status") == 200]
    if ev:
        zero = sum(1 for r in ev if (r.get("events_n") or 0) == 0)
        print(f"【近90天公开事件】n={len(ev)}  0 事件={zero}  有事件={len(ev)-zero}"
              f"   （0 事件 ≠ 没活动：私有仓库的活动这里看不到，故不参与评分）")

    rp = [r for r in rows if r.get("repos_status") == 200]
    if rp:
        orig = [r["repos_original_n"] for r in rp if r.get("repos_original_n") is not None]
        if orig:
            print(f"【非 fork 仓库】n={len(orig)}  一个都没有={sum(1 for x in orig if x==0)}  "
                  f"中位数={sorted(orig)[len(orig)//2]}  最多={max(orig)}")
        pd = [r["repos_original_push_days"] for r in rp
              if r.get("repos_original_push_days") is not None]
        if pd:
            pd.sort()
            print(f"【自有仓库最近 push 距今(天)】n={len(pd)}  <30天={sum(1 for x in pd if x<30)}  "
                  f"<90天={sum(1 for x in pd if x<90)}  中位数={pd[len(pd)//2]:.0f}")

    age = [r["account_age_days_at_fork"] for r in rows
           if r.get("account_age_days_at_fork") is not None]
    if age:
        age.sort()
        print(f"【fork时账号年龄(天)】n={len(age)}  <7天={sum(1 for x in age if x<7)}  "
              f"<30天={sum(1 for x in age if x<30)}  中位数={age[len(age)//2]:.0f}")

    # ── 最可疑 15 个 ──
    print("\n" + "-" * 108)
    print("最可疑 15 个")
    print("-" * 108)
    print(fmt("login", 24) + fmt("tier", 6) + fmt("score", 6) + fmt("年龄d", 7)
          + fmt("贡献/年", 9) + fmt("贡献/90d", 9) + fmt("事件", 6) + fmt("followers", 10) + "flags")
    for r in rows[:15]:
        print(fmt(r["login"], 24) + fmt(r["tier"], 6) + fmt(r["score"], 6)
              + fmt(r["account_age_days_at_fork"], 7)
              + fmt(r.get("contrib_total"), 9) + fmt(r.get("contrib_last90"), 9)
              + fmt(r.get("events_n"), 6)
              + fmt(r.get("followers"), 10) + (r["flags"] or ""))

    # ── 最正常 5 个（做对照，证明分数不是全压在负数上）──
    print("\n" + "-" * 108)
    print("最正常 5 个（同一套指标的对照端）")
    print("-" * 108)
    for r in rows[-5:]:
        print(fmt(r["login"], 24) + fmt(r["tier"], 6) + fmt(r["score"], 6)
              + fmt(r["account_age_days_at_fork"], 7)
              + fmt(r.get("contrib_total"), 9) + fmt(r.get("contrib_last90"), 9)
              + fmt(r.get("events_n"), 6)
              + fmt(r.get("followers"), 10) + (r["flags"] or ""))

    # ── flags 频次 ──
    fc = Counter()
    for r in rows:
        for x in (r["flags"] or "").split(";"):
            if x:
                fc[x] += 1
    if fc:
        print("\n" + "-" * 108)
        print("旗标频次")
        print("-" * 108)
        for k, v in fc.most_common():
            print(f"  {v:4d}  {v/n*100:5.1f}%  {k}")

    print(f"\nCSV → {OUT_CSV}")


# ══ 主流程 ══════════════════════════════════════════════
def github_reachable() -> bool:
    """通道 B 可达性预检。

    ★ 为什么要先探一下：github.com 会被**按目标**抑制（实测 2026-10-21：
      整站 10 个 TLS 档位全部 12s 超时，而 api.github.com 同一时刻 1.2s 正常）。
      不预检的话，100 个人 × 6 次重试 × 25s ≈ 42 分钟的纯空转。
    """
    try:
        r = cffi.get("https://github.com/", impersonate=HTML_PROFILES[0],
                     timeout=12, headers={"User-Agent": UA_HTML})
        return r.status_code < 500
    except Exception:
        return False


def wait_for_quota(gh: GhClient, need: int, max_wait: float) -> bool:
    """等匿名配额回到 `need` 以上。/rate_limit 本身不消耗配额。"""
    t0 = time.time()
    while True:
        r = gh.request(f"{GH_API}/rate_limit")
        core = (r.json().get("resources") or {}).get("core") or {}
        rem, reset = core.get("remaining", 0), core.get("reset", 0)
        if rem >= need:
            print(f"[quota] 剩余 {rem}，继续")
            return True
        wait = max(30.0, reset - time.time() + 5)
        if time.time() - t0 + wait > max_wait:
            print(f"[quota] 还差 {need - rem} 次，需再等 {wait/60:.0f} 分钟，超上限 → 放弃")
            return False
        print(f"[quota] 剩余 {rem}（需要 {need}），{wait/60:.1f} 分钟后重置，等待中 ...",
              flush=True)
        time.sleep(wait)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="不联网，只用已落盘数据重算")
    ap.add_argument("--stage", default="all",
                    choices=["all", "forks", "contrib", "users", "events", "repos",
                             "repos-html", "forks-all"],
                    help="只跑某一阶段（report 永远在最后自动出）")
    ap.add_argument("--tier", default="高危,可疑",
                    help="events/repos 阶段只打哪些分层（逗号分隔；all=全量）。按分数从高到低消耗配额")
    ap.add_argument("--burst", action="store_true",
                    help="events/repos 阶段只打 fork 时间落在聚集窗口的账号（与 --tier 取交集）")
    ap.add_argument("--max-users", type=int, default=0, help="通道A最多采多少人（0=按配额）")
    ap.add_argument("--pace", type=float, default=0.8, help="HTML 请求间隔秒")
    ap.add_argument("--wait-reset", action="store_true",
                    help="配额不足时睡到重置再采（最长 70 分钟）")
    ap.add_argument("--skip-contrib", action="store_true")
    ap.add_argument("--proxy", action="store_true",
                    help="forks-all 阶段走 KDL 代理出口（凭据读 data/proxy_kdl.json，gitignored）")
    args = ap.parse_args()

    forks = load_jsonl(RAW_FORKS)
    users = load_jsonl(RAW_USERS)
    contribs = load_jsonl(RAW_CONTRIB)
    events = load_jsonl(RAW_EVENTS)
    repos = load_jsonl(RAW_REPOS)
    repo_names = load_jsonl(RAW_REPOS_HTML)

    gh = None
    if not args.offline:
        gh = GhClient(conn=None, pool=None, task="probe", rate=1.0, verbose=True)
        want_forks = args.stage in ("all", "forks")

        # ── 阶段 0：全量 fork 名单（长跑，独立出口）──
        #    它跟下面所有阶段不在同一个时间尺度上（十几小时 vs 几分钟），
        #    跑完直接返回，不在这里顺带出报告。
        if args.stage == "forks-all":
            if args.proxy:
                res = fetch_forks_all_proxy()
            else:
                res = fetch_forks_all(gh)
            print(f"[forks-all] 结束：累计 {res['rows']:,} 条"
                  f"（本次新增 {res['new']:,}）→ {RAW_FORKS_ALL}")
            return 0
        want_contrib = (args.stage in ("all", "contrib")) and not args.skip_contrib
        want_users = args.stage in ("all", "users")
        want_events = args.stage in ("all", "events")
        want_repos = args.stage in ("all", "repos")

        # ── 阶段 1：fork 名单 ──
        if want_forks and not forks:
            items = fetch_forks(gh)
            for f in items:
                row = fork_row(f)
                if row["login"]:
                    append_jsonl(RAW_FORKS, row)
                    forks[row["login"]] = row
            print(f"[forks] 落盘 {len(forks)} 人 → {RAW_FORKS}")
        elif forks:
            print(f"[forks] 已有 {len(forks)} 人，跳过")

        logins = list(forks)

        # ── 阶段 2：HTML 贡献热力图（不占 API 配额）──
        if want_contrib:
            todo = [lg for lg in logins if lg not in contribs]
            if not todo:
                print("[contrib] 已全部采完")
            elif not github_reachable():
                print("[contrib] ⚠ github.com 当前不可达（TLS/SNI 层被抑制），跳过本阶段。\n"
                      "         通道 B 不占 API 配额，网络恢复后重跑 --stage contrib 即可。")
            else:
                print(f"[contrib] 待采 {len(todo)}/{len(logins)}")
                for i, lg in enumerate(todo, 1):
                    row = fetch_contrib(lg, args.pace)
                    append_jsonl(RAW_CONTRIB, row)
                    contribs[lg] = row
                    if i % 20 == 0:
                        print(f"    {i}/{len(todo)} ...", flush=True)
                ok = sum(1 for r in contribs.values() if r.get("contrib_status") == 200)
                print(f"[contrib] 完成，成功 {ok}/{len(logins)}")

        # ── 阶段 2b：仓库名单（HTML，免配额）──
        #   单独存一个文件：它只补「仓库叫什么」，不要覆盖 API 版的计数与 star。
        #   所以这里**不按 repos 是否存在来跳过**，只看 names 自己采过没有。
        if args.stage == "repos-html":
            # 上次失败的（非 200）要重试一次；成功过的才是真的不用再问。
            todo = [lg for lg in logins
                    if (repo_names.get(lg) or {}).get("repo_html_status") != 200]
            if not todo:
                print("[repos-html] 已全部采完")
            elif not github_reachable():
                print("[repos-html] ⚠ github.com 当前不可达，跳过。")
            else:
                print(f"[repos-html] 待采 {len(todo)}/{len(logins)}")
                for i, lg in enumerate(todo, 1):
                    row = fetch_repos_html(lg, args.pace)
                    append_jsonl(RAW_REPOS_HTML, row)
                    repo_names[lg] = row
                    if i % 20 == 0:
                        print(f"    {i}/{len(todo)} ...", flush=True)
                ok = sum(1 for r in repo_names.values() if r.get("repo_html_status") == 200)
                print(f"[repos-html] 完成，成功 {ok}/{len(logins)}")

        # ── 阶段 3：API 画像（占配额，用完即停）──
        if want_users:
            todo = [lg for lg in logins if lg not in users]
            if args.max_users:
                todo = todo[: args.max_users]
            print(f"\n[users] 待采 {len(todo)}/{len(logins)}（匿名配额 60/小时）")

            if todo and args.wait_reset and not wait_for_quota(gh, min(len(todo), 55), 4200):
                todo = todo[:0]

            for i, lg in enumerate(todo, 1):
                try:
                    row = fetch_user(gh, lg)
                except RateLimited as e:
                    print(f"\n[users] ⏸ 配额耗尽：{e}")
                    print(f"[users] 已完成 {i-1} 个；剩 {len(todo)-i+1} 个留到下一小时窗口重跑")
                    break
                except SuppressWindow as e:
                    print(f"\n[users] ⏸ 网络抑制窗口：{e}")
                    break
                append_jsonl(RAW_USERS, row)
                users[lg] = row
                if i % 10 == 0:
                    print(f"    {i}/{len(todo)}  当前 {lg}", flush=True)

            ok = sum(1 for r in users.values() if r.get("api_status") == 200)
            print(f"[users] 已落盘 {len(users)}，其中成功 {ok}")

        # ── 阶段 4：公开事件流（API 侧「还在不在动」的证据，只打重点人群）──
        # 配额只有 60/小时，全量 100 人要跨两个窗口；按分数从高到低打，先保高危。
        def api_stage(name: str, store: dict, out_path: str, fetch) -> None:
            """按「最可疑优先」消耗配额跑一个逐人 API 阶段。

            events / repos 两个阶段形状完全一样：1 请求/人、会撞匿名配额、
            要能中途停下等下个窗口。所以共用这一个循环，不写两遍。
            """
            graded = merge_rows(forks, users, contribs, events, repos, repo_names)
            tier_arg = (args.tier or "").strip()
            want_tiers = None if tier_arg.lower() == "all" else {
                t.strip() for t in tier_arg.split(",") if t.strip()
            }
            def needs(lg: str) -> bool:
                """这条记录是不是还得再问一次。

                repos 阶段多一个条件：早先采的行只有计数、没有**仓库名**。
                名字是这条通道里最有信息量的一列，缺了就该重采一次
                （load_jsonl 后写覆盖前写，重采不会丢原有字段，因为新行字段更全）。
                """
                row = store.get(lg)
                if row is None:
                    return True
                return name == "repos" and not row.get("repos_names")

            todo = [r["login"] for r in graded                    # 已按 score 升序 = 最可疑在前
                    if needs(r["login"])
                    and r.get("api_status") == 200                 # 404 的号不用再问
                    and (want_tiers is None or r.get("tier") in want_tiers)
                    and (not args.burst or r.get("fork_in_burst") == 1)]
            scope = f"筛选 {tier_arg}" + ("；仅爆发窗" if args.burst else "")
            print(f"\n[{name}] 待采 {len(todo)} 人（{scope}；匿名配额 60/小时）")

            if todo and args.wait_reset and not wait_for_quota(gh, min(len(todo), 55), 4200):
                todo = []

            done = 0
            for i, lg in enumerate(todo, 1):
                try:
                    row = fetch(gh, lg)
                except RateLimited as e:
                    print(f"\n[{name}] ⏸ 配额耗尽：{e}")
                    print(f"[{name}] 已完成 {done} 个；剩 {len(todo)-done} 个留到下一窗口")
                    break
                except SuppressWindow as e:
                    print(f"\n[{name}] ⏸ 网络抑制窗口：{e}")
                    break
                except Exception as e:
                    print(f"    [{name}] {lg} 失败：{type(e).__name__}: {e}")
                    row = {"login": lg, f"{name}_status": -1}
                append_jsonl(out_path, row)
                store[lg] = row
                done += 1
                if i % 10 == 0:
                    print(f"    {i}/{len(todo)}  当前 {lg}", flush=True)

            ok = sum(1 for r in store.values() if r.get(f"{name}_status") == 200)
            print(f"[{name}] 已落盘 {len(store)}，其中成功 {ok}")

        # ── 阶段 4：公开事件流（API 侧「还在不在动」的证据，只打重点人群）──
        # 配额只有 60/小时，全量 100 人要跨两个窗口；按分数从高到低打，先保高危。
        if want_events:
            api_stage("events", events, RAW_EVENTS, fetch_events)

        # ── 阶段 5：仓库清单（repos 是真项目还是占位摆设）──
        if want_repos:
            api_stage("repos", repos, RAW_REPOS, fetch_repos)

    # ── 合成 + 报告 ──
    rows = merge_rows(forks, users, contribs, events, repos, repo_names)
    if not rows:
        print("没有数据。先联网跑一次。")
        return 1
    write_csv(rows)
    print_report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
