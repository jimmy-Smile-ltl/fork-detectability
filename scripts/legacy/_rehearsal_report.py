# -*- coding: utf-8 -*-
"""演练/指标分析报告：用 rea forks 第 98 页的 active / inactive 两组，检验判据的分辨力。

数据：
    data/raw/profile_tier_deep_rea_p98.jsonl     画像（deep 档：d1+d2+d3+d4+d4l+d6+d8）
    data/raw/avatar_*deep*.jsonl                 头像类型（可选）
    data/raw/rehearsal_rea_forks_p98.txt         分组名单

输出：每条判据在两组的取值差（pp）、方向、以及按差值排序的分辨力榜。

用法：
    py _rehearsal_report.py
    py _rehearsal_report.py --tier-file ... --cohort-file ... --avatar-file ...
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")


# ── 读入 ────────────────────────────────────────────────
def load_cohort(path):
    groups, cur = defaultdict(list), "ungrouped"
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("# ----") and s.endswith("----"):
            cur = s.strip("# -").strip() or "ungrouped"
            continue
        if s.startswith("#"):
            continue
        groups[cur].append(s)
    return dict(groups)


def load_last(path):
    """同名 login 多条记录时以最后一条为准（重采覆盖）。"""
    out = {}
    if not path or not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("login"):
            out[r["login"].lower()] = r
    return out


# ── 取值 ────────────────────────────────────────────────
def d1(r, k):
    return (r.get("d1") or {}).get(k)


def unit(r, name, k):
    return (r.get(name) or {}).get(k)


def profile_shape(r1):
    fields = ("name", "company", "blog", "location", "bio", "twitter_username")
    filled = [k for k in fields if (r1.get(k) or "").strip()]
    p = len(filled)
    name, loc = (r1.get("name") or "").strip(), (r1.get("location") or "").strip()
    if p == 0:
        return p, "EMPTY"
    if name and loc and p == 2:
        return p, "TEMPLATE"
    if p >= 4:
        return p, "RICH"
    return p, "PARTIAL"


def login_kind(login):
    runs = re.findall(r"\d+", login or "")
    run = max(runs, key=len) if runs else ""
    n = len(run)
    if n == 10 and 10 ** 9 <= int(run) < 2 * 10 ** 9:
        seq = "0123456789" * 2
        if run in seq or run in seq[::-1] or len(set(run)) <= 2:
            return "NUM"
        return "TS"
    if n == 11 and run[0] == "1" and run[1] in "3456789":
        return "PHONE"
    if 5 <= n <= 11:
        return "NUM"
    return "OTHER"


def med(xs):
    v = sorted(x for x in xs if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def share(vals, cond):
    v = [x for x in vals if x is not None]
    return (sum(1 for x in v if cond(x)) / len(v) * 100) if v else None


# ── 分组统计 ────────────────────────────────────────────
def group_stats(logins, rows, avatars):
    ok = [rows[l.lower()] for l in logins if l.lower() in rows and rows[l.lower()].get("http") == 200]
    gone = [l for l in logins if l.lower() in rows and rows[l.lower()].get("http") == 404]
    pending = [l for l in logins if l.lower() not in rows or rows[l.lower()].get("http") not in (200, 404)]
    now = datetime.datetime.now(datetime.timezone.utc)

    ages, pvals, shapes, shapes_tpl = [], [], [], 0
    starw, own, ownlive, star_sampled, dev_ratio, orgs_n = [], [], [], [], [], []
    fork_total = fork_moved = 0
    for r in ok:
        c = d1(r, "created_at")
        if c:
            try:
                ages.append((now - datetime.datetime.fromisoformat(c.replace("Z", "+00:00"))).days)
            except ValueError:
                pass
        p, sh = profile_shape(r.get("d1") or {})
        pvals.append(p)
        shapes.append(sh)
        if sh == "TEMPLATE":
            shapes_tpl += 1
        starw.append(unit(r, "d4", "starred_total"))
        oc = unit(r, "d8", "own_count")
        own.append(oc)
        if oc:
            ownlive.append(unit(r, "d8", "own_touched") or 0)
        star_sampled.append(unit(r, "d4l", "sampled"))
        et, dv = unit(r, "d2", "events_total"), unit(r, "d2", "dev_events")
        if et:                                     # et=0 是"无公开活动"，不参与比例
            dev_ratio.append(dv / et * 100)
        orgs_n.append(unit(r, "d6", "orgs"))
        for x in ((r.get("d8") or {}).get("fork_details") or []):
            fork_total += 1
            if x.get("moved"):
                fork_moved += 1

    kinds = Counter(login_kind(l) for l in logins)
    av_photo = sum(1 for l in logins if (avatars.get(l.lower()) or {}).get("type") == "photo")
    av_known = sum(1 for l in logins if (avatars.get(l.lower()) or {}).get("type") in ("photo", "identicon"))
    return {
        "n": len(logins), "ok": len(ok), "gone": len(gone), "pending": len(pending),
        "p_med": med(pvals), "empty": sum(1 for s in shapes if s == "EMPTY"),
        "tpl": shapes_tpl, "rich": sum(1 for s in shapes if s == "RICH"),
        "starw_med": med(starw), "star0": sum(1 for x in starw if x == 0),
        "star_le3": sum(1 for x in starw if isinstance(x, int) and 0 <= x <= 3),
        "own0": sum(1 for x in own if x == 0), "ownlive_any": sum(1 for x in ownlive if x > 0),
        "star_sampled_med": med([x for x in star_sampled if isinstance(x, int) and x > 0]),
        "dev_med": med(dev_ratio), "org_any": sum(1 for o in orgs_n if isinstance(o, list) and o),
        "fork_total": fork_total, "fork_moved": fork_moved,
        "age_med": med(ages), "age_lt30": sum(1 for a in ages if a < 30),
        "ts_phone": kinds.get("TS", 0) + kinds.get("PHONE", 0),
        "av_photo": av_photo, "av_known": av_known,
        "reqs": len(ok) * 8,
    }


# ── 渲染 ────────────────────────────────────────────────
def render(groups, rows, avatars, tier_file, avatar_file):
    keys = list(groups)
    S = {k: group_stats(groups[k], rows, avatars) for k in keys}
    a, b = keys[0], (keys[1] if len(keys) > 1 else keys[0])
    sa, sb = S[a], S[b]

    def pct(n, d):
        return (n / d * 100) if d else 0.0

    # (名称, a 值, b 值, 单位, 方向说明)
    def pv(n, d):
        return pct(n, d)

    M = [
        ("登录名 TS+PHONE 占比", pv(sa["ts_phone"], sa["n"]), pv(sb["ts_phone"], sb["n"]), "pp", "越高越批量"),
        ("fork 后动过率（按仓）", pv(sa["fork_moved"], sa["fork_total"]), pv(sb["fork_moved"], sb["fork_total"]), "pp", "越高越真"),
        ("自建仓 = 0 占比", pv(sa["own0"], sa["ok"]), pv(sb["own0"], sb["ok"]), "pp", "越低越真"),
        ("自建仓有 push 占比", pv(sa["ownlive_any"], sa["ok"]), pv(sb["ownlive_any"], sb["ok"]), "pp", "越高越真"),
        ("档案 EMPTY 占比", pv(sa["empty"], sa["ok"]), pv(sb["empty"], sb["ok"]), "pp", "越低越真"),
        ("档案 RICH 占比", pv(sa["rich"], sa["ok"]), pv(sb["rich"], sb["ok"]), "pp", "越高越真"),
        ("档案 TEMPLATE 占比", pv(sa["tpl"], sa["ok"]), pv(sb["tpl"], sb["ok"]), "pp", "越高越可疑"),
        ("star = 0 占比", pv(sa["star0"], sa["ok"]), pv(sb["star0"], sb["ok"]), "pp", "越高越可疑"),
        ("star ≤3 占比", pv(sa["star_le3"], sa["ok"]), pv(sb["star_le3"], sb["ok"]), "pp", "越高越可疑"),
        ("有组织归属 占比", pv(sa["org_any"], sa["ok"]), pv(sb["org_any"], sb["ok"]), "pp", "越高越真"),
        ("上传头像 占比", pv(sa["av_photo"], sa["av_known"] or 1), pv(sb["av_photo"], sb["av_known"] or 1), "pp", "越高越真（弱）"),
        ("年龄 <30 天 占比", pv(sa["age_lt30"], sa["ok"]), pv(sb["age_lt30"], sb["ok"]), "pp", "越高越可疑"),
    ]
    X = [
        ("star 宽度中位", sa["starw_med"], sb["starw_med"], "个", "越高越真"),
        ("star 清单条数中位", sa["star_sampled_med"], sb["star_sampled_med"], "条", "越高越真"),
        ("开发事件占比中位", sa["dev_med"], sb["dev_med"], "%", "越高越真"),
        ("账号年龄中位", sa["age_med"], sb["age_med"], "天", "无方向"),
    ]

    def pfmt(v):
        return "—" if v is None else f"{v:.0f}%"

    def nfmt(v, unit_):
        if v is None:
            return "—"
        if unit_ == "%":
            return f"{v:.1f}%"
        if isinstance(v, float) and abs(v - round(v)) < 1e-9:
            v = int(round(v))
        return f"{v}{unit_}"

    L = ["# 指标分析：rea forks 第 98 页 active vs inactive", "",
         f"> 画像：`{os.path.basename(tier_file)}`（deep 档，8 req/人）"
         + (f" · 头像：`{os.path.basename(avatar_file)}`" if avatar_file else ""),
         "> 名单：`github.com/morluto/rea/forks?include=active|inactive&page=98`（2026-11-05）",
         "", "## 一、采集健康度", "",
         "| 组 | 名单 | 存活 | 404 | 未定论 | 估算请求 |", "|---|---:|---:|---:|---:|---:|"]
    for k in keys:
        s = S[k]
        L.append(f"| {k} | {s['n']} | {s['ok']} | {s['gone']} | {s['pending']} | ~{s['reqs']} |")
    L.append("")

    L += ["## 二、判据分辨力榜（按两组差值降序）", "",
          "| # | 判据 | " + " | ".join(keys) + " | 差值 | 方向 |", "|---|---|" + "---:|" * len(keys) + "---:|---|"]
    ranked = []
    for name, va, vb, unit_, direction in M:
        d = abs(vb - va)
        ranked.append((d, name, va, vb, unit_, direction))
    for i, (d, name, va, vb, unit_, direction) in enumerate(sorted(ranked, reverse=True), 1):
        L.append(f"| {i} | {name} | {pfmt(va)} | {pfmt(vb)} | **{d:.0f}pp** | {direction} |")
    L.append("")
    L += ["## 三、连续型指标", "", "| 指标 | " + " | ".join(keys) + " |", "|---|" + "---:|" * len(keys)]
    for name, va, vb, unit_, direction in X:
        L.append(f"| {name} | {nfmt(va, unit_)} | {nfmt(vb, unit_)} |")
    L.append("")

    # 两轴分布
    L += ["## 四、两轴分布（每人落一格）", "",
          "判定规则（写死，可复算）：",
          "- **自证高**：`own_count>0 且有 push` 或 `star 宽度≥10`",
          "- **自证低**：`own_count=0` 且 `star 宽度=0`",
          "- **其余 = 无信息**",
          "- **批量性高**：登录名为 TS/PHONE 形态", ""]
    quad = {k: Counter() for k in keys}
    for k in keys:
        for lg in groups[k]:
            r = rows.get(lg.lower())
            if not r or r.get("http") != 200:
                continue
            own = unit(r, "d8", "own_count") or 0
            live = unit(r, "d8", "own_touched") or 0
            sw = unit(r, "d4", "starred_total")
            ident = "自证高" if (own > 0 and live > 0) or (isinstance(sw, int) and sw >= 10) else \
                    ("自证低" if own == 0 and sw == 0 else "无信息")
            batch = "批量高" if login_kind(lg) in ("TS", "PHONE") else "批量低"
            quad[k][(ident, batch)] += 1
    L += ["| 组 | 自证高·批量低 | 自证高·批量高 | 自证低·批量低 | 自证低·批量高 | 无信息 |",
          "|---|---:|---:|---:|---:|---:|"]
    for k in keys:
        c = quad[k]
        L.append(f"| {k} | {c[('自证高','批量低')]} | {c[('自证高','批量高')]} | "
                 f"{c[('自证低','批量低')]} | {c[('自证低','批量高')]} | "
                 f"{c[('无信息','批量低')] + c[('无信息','批量高')]} |")
    L.append("")

    # 结论
    L += ["## 五、结论", ""]
    top = sorted(ranked, reverse=True)[:4]
    L.append("- 分辨力最强的四条：" + "、".join(f"{n}（{d:.0f}pp）" for d, n, *_ in top))
    L.append(f"- 采集完成度：{sa['ok']}/{sa['n']} 与 {sb['ok']}/{sb['n']}；未定论的账号不参与任何比例")
    L += ["", "> 措辞纪律：本报告检验**判据的分辨力**，不对个体账号定性。",
          "> `inactive` 是 GitHub forks 页的过滤口径（fork 后无提交），不等于「假号」。", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier-file", default=os.path.join(RAW, "profile_tier_deep_rea_p98.jsonl"))
    ap.add_argument("--cohort-file", default=os.path.join(RAW, "rehearsal_rea_forks_p98.txt"))
    ap.add_argument("--avatar-file", default=os.path.join(RAW, "avatar_profile_tier_deep_rea_p98.jsonl"))
    args = ap.parse_args()
    rows = load_last(args.tier_file)
    if not rows:
        raise SystemExit(f"没有画像数据：{args.tier_file}")
    avatars = load_last(args.avatar_file) if os.path.exists(args.avatar_file) else {}
    groups = load_cohort(args.cohort_file)
    md = render(groups, rows, avatars, args.tier_file,
                args.avatar_file if os.path.exists(args.avatar_file) else None)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, "metrics_analysis_rea_p98.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
