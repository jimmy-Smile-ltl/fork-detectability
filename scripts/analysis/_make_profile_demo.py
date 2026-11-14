# -*- coding: utf-8 -*-
"""账户完整画像 demo：把一个人的 8 个维度拼成一份完整档案。

    python scripts/analysis/_make_profile_demo.py [--tag bettafish] [--login xxx]

输出 docs/14_账户画像demo.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "collect"))

from collect_core import DIMS, dim_path  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
DOCS = os.path.join(BASE, "docs")


def load_dim(dim: str, tag: str) -> dict:
    p = dim_path(dim, tag, RAW)
    out = {}
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        lg = (r.get("login") or "").lower()
        if not lg:
            continue
        oc = r.get("outcome") or ("OK" if r.get("http") == 200 else "?")
        if lg not in out or oc == "OK":
            out[lg] = (oc, r)
    return out


def payload_of(r: dict) -> dict:
    if isinstance(r.get("payload"), dict) and r["payload"]:
        return r["payload"]
    return {k: v for k, v in r.items()
            if k not in ("login", "outcome", "http", "ts", "attempt", "note")}


def pick_rich(tag: str, forks: str) -> str:
    """挑一个"维度齐全且内容丰富"的账号做样例。"""
    want = ["D1", "D2B", "D4L", "D8H", "D9", "D14", "D2"]
    maps = {d: load_dim(d, tag) for d in want}
    best, score = None, -1
    for lg in maps.get("D1", {}):
        if any(lg not in maps[d] for d in want):
            continue
        s = sum(len(json.dumps(payload_of(maps[d][lg][1]), default=str)) for d in want)
        if s > score:
            best, score = lg, s
    return best or next(iter(maps.get("D1", {})), "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="bettafish")
    ap.add_argument("--login", default="")
    ap.add_argument("--repo", default="666ghj/BettaFish")
    args = ap.parse_args()

    tag = args.tag
    login = args.login or pick_rich(tag, "")
    if not login:
        print("找不到合适的样例账号")
        return 1

    acc = {d: load_dim(d, tag).get(login) for d in DIMS}
    # fork 记录
    fork = None
    fp = os.path.join(RAW, f"forks_all_{args.repo.replace('/', '__')}.jsonl")
    if os.path.exists(fp):
        for line in open(fp, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if (r.get("login") or "").lower() == login:
                fork = r
                break

    d1 = payload_of(acc["D1"][1]) if acc.get("D1") else {}
    d8 = payload_of(acc["D8H"][1]) if acc.get("D8H") else {}
    d9 = payload_of(acc["D9"][1]) if acc.get("D9") else {}
    d2b = payload_of(acc["D2B"][1]) if acc.get("D2B") else {}
    d14 = payload_of(acc["D14"][1]) if acc.get("D14") else {}
    d4 = payload_of(acc["D4L"][1]) if acc.get("D4L") else {}
    d15 = payload_of(acc["D15"][1]) if acc.get("D15") else {}
    d13 = payload_of(acc["D13"][1]) if acc.get("D13") else {}

    # 列表文件
    starred = []
    sp = os.path.join(RAW, f"starred_all_{tag}.jsonl")
    if os.path.exists(sp):
        for line in open(sp, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if (r.get("login") or "").lower() == login:
                starred = r.get("items") or []
                break
    repos = []
    rp = os.path.join(RAW, f"repos_all_{tag}.jsonl")
    if os.path.exists(rp):
        for line in open(rp, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if (r.get("login") or "").lower() == login:
                repos = r.get("items") or []
                break

    def m(v, n=3):
        return (v[:n] + "***") if isinstance(v, str) and len(v) > n else v

    L = [f"# 账户完整画像 Demo", "",
         f"> 账号：`{m(login, 3)}`（脱敏）· 仓库：`{args.repo}` · 采集日：2026-11-15", "",
         "一个账号在 8 个维度上的**全部数据**，可直接看出哪些指标能算、哪些拿不到。", ""]

    L += ["## 一、时间线（fork 名单，0 请求）", "",
          "| 字段 | 值 |", "|---|---|"]
    if fork:
        for k in ("fork_created_at", "fork_pushed_at", "size_kb", "default_branch",
                  "fork_language", "fork_stars", "owner_type"):
            L.append(f"| `{k}` | {fork.get(k)} |")
        touched = "✅ 动过" if (fork.get("fork_pushed_at") or "") > (fork.get("fork_created_at") or "") else "❌ 未动"
        L.append(f"| **`fork_touched`** | **{touched}** |")
    L += ["", "## 二、档案（D1）", "", "| 字段 | 值 |", "|---|---|"]
    for k in ("created_at", "public_repos", "public_gists", "followers", "following",
              "name", "bio", "location", "company", "twitter_username", "type"):
        v = d1.get(k)
        if k in ("name", "bio"):
            v = m(v) if v else None
        L.append(f"| `{k}` | {v} |")

    L += ["", "## 三、仓库（D8H + repo 明细）", "",
          f"- 自建仓 **{d8.get('own_count')}** 个 / fork **{d8.get('fork_count')}** 个",
          f"- 仓库明细共 **{len(repos)}** 条", "",
          "| 仓名 | fork | 语言 | ★ | 最后 push |", "|---|---|---|---:|---|"]
    for x in repos[:10]:
        L.append(f"| `{m(x.get('name') or '', 40)}` | {'是' if x.get('fork') else '否'} | "
                 f"{x.get('lang')} | {x.get('stars')} | {str(x.get('pushed_at'))[:10]} |")
    if len(repos) > 10:
        L.append(f"| … | | | | （共 {len(repos)} 条） |")

    L += ["", "## 四、提交（D9）", "",
          f"- 提交总数 **{d9.get('commit_total')}**",
          f"- 检查了 {d9.get('repos_checked')} 个仓", "",
          "| 仓 | 提交数 | 备注 |", "|---|---:|---|"]
    for x in (d9.get("per_repo") or [])[:5]:
        L.append(f"| `{m(x.get('repo') or '', 30)}` | {x.get('commits')} | {x.get('why') or ''} |")

    L += ["", "## 五、活动（D2B 90 天）", "",
          f"- 事件总数 **{d2b.get('events_n_90d')}**",
          f"- 活跃天数 **{d2b.get('active_days_90d')}**",
          f"- 开发事件占比 **{d2b.get('dev_event_ratio')}**", "",
          "事件类型分布：" + " ".join(f"`{k}`:{v}" for k, v in
                                     (d2b.get("event_types") or {}).items())]

    L += ["", "## 六、star 行为（D4L + star 明细）", "",
          f"- star 总数 **{d4.get('starred_count')}**",
          f"- 明细条数 **{len(starred)}**", "",
          "最近 star 的 10 个仓：", "",
          "| 仓库 | 点星时间 | ★ | 语言 |", "|---|---|---:|---|"]
    for x in starred[:10]:
        L.append(f"| `{m(x.get('full_name') or '', 40)}` | {str(x.get('at'))[:19]} | "
                 f"{x.get('stars')} | {x.get('lang')} |")

    L += ["", "## 七、头像（D14）", "",
          f"- 类型：**{d14.get('type')}**（{d14.get('bytes')} 字节）",
          "", "## 八、主页画像（D15）", ""]
    if d15:
        L += [f"- star 数：{d15.get('starred_count')}",
              f"- 成就徽章：{d15.get('achievement_n')} 个",
              f"- Pull Shark：{d15.get('has_pull_shark')}｜协作徽章：{d15.get('has_collab')}",
              f"- pinned：{len(d15.get('pinned') or [])} 个",
              f"- 组织：{len(d15.get('orgs') or [])} 个"]
    else:
        L += ["⚠️ 本账号 D15 未采集（HTML 通道受限）"]
    if d13:
        L += ["", "## 九、全年贡献（D13）", "",
              f"- 活跃天数：{d13.get('active_days')}｜总贡献：{d13.get('total_contribs')}"]
    else:
        L += ["", "## 九、全年贡献（D13）", "", "⚠️ 本账号 D13 未采集（HTML 通道受限）"]

    # ---- 指标计算 ----
    L += ["", "## 十、从这个账号能算出的指标", "",
          "| 指标 | 值 | 计算依据 |", "|---|---:|---|"]
    if fork:
        L.append(f"| `fork_touched` | "
                 f"{1 if (fork.get('fork_pushed_at') or '') > (fork.get('fork_created_at') or '') else 0} | "
                 f"pushed_at > created_at |")
    L.append(f"| `followers_zero` | {1 if d1.get('followers') == 0 else 0} | followers == 0 |")
    L.append(f"| `has_own` | {1 if (d8.get('own_count') or 0) > 0 else 0} | own_count > 0 |")
    L.append(f"| `commit_total` | {d9.get('commit_total')} | 各仓提交数之和 |")
    L.append(f"| `commit_zero` | {1 if d9.get('commit_total') == 0 else 0} | commit_total == 0 |")
    L.append(f"| `avatar_photo` | {1 if d14.get('type') == 'photo' else 0} | 头像字节 > 6000 |")
    L.append(f"| `dev_event_ratio` | {d2b.get('dev_event_ratio')} | 开发事件 / 总事件 |")
    L.append(f"| `repo_named_target` | "
             f"{1 if any(args.repo.split('/')[1].lower() in (x.get('name') or '').lower() for x in repos) else 0} "
             f"| 仓名含目标仓 |")
    if d1.get("created_at") and fork:
        from datetime import datetime, timezone
        try:
            c = datetime.strptime(d1["created_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            f = datetime.strptime(fork["fork_created_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            L.append(f"| `acct_age_at_fork_days` | **{(f-c).days:,}** | fork 时间 − 注册时间 |")
        except Exception:
            pass
    L.append("")

    md = "\n".join(L) + "\n"
    os.makedirs(DOCS, exist_ok=True)
    dst = os.path.join(DOCS, "14_账户画像demo.md")
    open(dst, "w", encoding="utf-8").write(md)
    print(f"→ {dst}（{len(md.splitlines())} 行）  样例账号：{m(login)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
