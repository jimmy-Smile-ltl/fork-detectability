# -*- coding: utf-8 -*-
"""全量特征计算：把九套数据并成一人一行，算 70+ 个特征。

设计原则：
  1. **纯本地计算**，不再发任何请求
  2. **缺失 ≠ 0**：采不到的特征留 None，不填 0（否则会污染均值）
  3. CSV 列取**所有行的并集**（不能只看第一行，否则会丢列）
  4. `star_fork_delay_h` 只对 **star 过目标仓** 的人计算（否则分母无意义）

用法：py _compute_all_features.py
"""
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
OUT = os.path.join(BASE, "data", "out")
os.makedirs(OUT, exist_ok=True)
NOW = datetime.now(timezone.utc).timestamp()

CODE_LANGS = {"Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C", "C++",
              "C#", "Ruby", "PHP", "Swift", "Kotlin", "Scala", "Shell", "Lua",
              "Dart", "Objective-C", "Haskell", "Elixir", "Erlang", "Clojure",
              "Perl", "R", "MATLAB", "Julia", "Zig", "Nim", "V", "Solidity"}


def ts(s):
    if not s:
        return None
    try:
        return datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def load(path, key="login"):
    """成功优先去重 → {login: rec}"""
    best = {}
    if not os.path.exists(path):
        return best
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        lg = (r.get(key) or "").lower()
        if not lg:
            continue
        good = (r.get("http") == 200) or (r.get("status") == "ok") or bool(r.get("type"))
        prev = best.get(lg)
        if prev is None or (good and not prev[0]):
            best[lg] = (good, r)
    return {k: v[1] for k, v in best.items()}


def login_type(lg):
    d = re.sub(r"[-_]", "", lg)
    if not d.isdigit():
        return "numeric" if lg.isdigit() else "other"
    if len(d) == 11 and d[0] == "1" and d[1] in "3456789":
        return "phone_cn"
    if len(d) == 10 and 10 ** 9 <= int(d) < 2 * 10 ** 9 and len(set(d)) > 2:
        return "ts_or_phone"
    return "numeric"


def compute(repo_tag, fork_file, files):
    forks = load(fork_file)
    d1 = load(os.path.join(RAW, files["d1"]))
    d13 = load(os.path.join(RAW, files["d13"]))
    d8 = load(os.path.join(RAW, files["d8"]))
    d15 = load(os.path.join(RAW, files["d15"]))
    d2 = load(os.path.join(RAW, files["d2"]))
    d4l = load(os.path.join(RAW, files["d4l"]))
    d9 = load(os.path.join(RAW, files["d9"]))
    av = {}
    avp = os.path.join(RAW, files.get("d14", ""))
    if files.get("d14") and os.path.exists(avp):
        for line in open(avp, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            lg = (r.get("login") or "").lower()
            if lg and r.get("type"):
                av[lg] = r["type"]
    target = files["target"]

    rows = []
    for lg, f in forks.items():
        ft = ts(f.get("fork_created_at"))
        pt = ts(f.get("fork_pushed_at"))
        p = (d1.get(lg) or {}).get("d1") or {}
        cal = d13.get(lg) or {}
        rh = d8.get(lg) or {}
        ph = d15.get(lg) or {}
        at = d2.get(lg) or {}
        sl = d4l.get(lg) or {}
        cm = d9.get(lg) or {}

        r = {"login": lg, "fork_ts": ft}

        # ---------- 时间轴 ----------
        r["fork_touched"] = 1 if (ft and pt and pt > ft) else (0 if (ft and pt) else None)
        r["fork_push_delay_h"] = (round((pt - ft) / 3600, 2)
                                  if (ft and pt and pt > ft) else (0.0 if (ft and pt) else None))
        r["fork_age_days"] = round((NOW - ft) / 86400, 1) if ft else None
        r["size_kb"] = f.get("size_kb")
        r["branch_other"] = 1 if (f.get("default_branch") or "").lower() not in ("main", "master", "") else 0
        r["fork_language"] = f.get("fork_language")
        r["fork_stars"] = f.get("fork_stars")
        r["owner_type"] = f.get("owner_type")
        if ft:
            dt = datetime.fromtimestamp(ft, tz=timezone.utc)
            r["fork_hour"] = dt.hour
            r["fork_weekday"] = dt.weekday()

        # ---------- D1 档案 ----------
        ut = ts(p.get("created_at"))
        r["acct_age_days"] = round((NOW - ut) / 86400, 1) if ut else None
        r["acct_age_at_fork_days"] = round((ft - ut) / 86400, 1) if (ut and ft) else None
        r["filled_n"] = sum(1 for k in ("name", "bio", "blog", "location", "company",
                                        "twitter_username") if (p.get(k) or "").strip())
        r["has_email"] = 1 if p.get("has_email") else (0 if p else None)
        r["public_repos"] = p.get("public_repos")
        r["public_gists"] = p.get("public_gists")
        r["followers"] = p.get("followers")
        r["following"] = p.get("following")
        if p.get("followers") is not None and p.get("following") is not None:
            r["follower_ratio"] = round(p["followers"] / max(p["following"], 1), 2)
        r["followers_zero"] = 1 if p.get("followers") == 0 else (0 if p else None)
        r["following_zero"] = 1 if p.get("following") == 0 else (0 if p else None)
        r["user_type"] = p.get("type")
        r["user_id"] = p.get("user_id")
        r["login_type"] = login_type(lg)
        r["login_len"] = len(lg)
        r["login_has_digit"] = 1 if any(c.isdigit() for c in lg) else 0

        # 档案形状 SHAPE
        nm = (p.get("name") or "").strip()
        loc = (p.get("location") or "").strip()
        co = (p.get("company") or "").strip()
        bl = (p.get("blog") or "").strip()
        bio = (p.get("bio") or "").strip()
        tw = (p.get("twitter_username") or "").strip()
        if p:
            fn = r["filled_n"]
            if fn == 0:
                r["shape"] = "EMPTY"
            elif nm and loc and not (co or bl or bio or tw):
                r["shape"] = "TEMPLATE"
            elif fn >= 4:
                r["shape"] = "RICH"
            else:
                r["shape"] = "PARTIAL"
            if nm:
                toks = [t.lower() for t in nm.replace(".", " ").replace("-", " ").split() if len(t) >= 3]
                r["namematch"] = 1 if any(t in lg for t in toks) else 0
            else:
                r["namematch"] = None

        # 登录名时间戳解码
        digs = re.findall(r"\d+", lg)
        r["login_is_ts"] = 0
        if digs:
            longest = max(digs, key=len)
            if 10 <= len(longest) <= 11:
                v = int(longest[:10])
                if 10 ** 9 <= v < 2 * 10 ** 9 and len(set(longest)) > 2:
                    r["ts_decoded_year"] = datetime.fromtimestamp(v, tz=timezone.utc).year
                    r["login_is_ts"] = 1

        # 注册时间分桶
        if r["acct_age_days"] is not None:
            a = r["acct_age_days"]
            r["acct_age_bucket"] = ("<1年" if a < 365 else "1-3年" if a < 1095
                                    else "3-7年" if a < 2555 else ">7年")

        # ---------- D13 活跃 ----------
        r["active_days"] = cal.get("active_days")
        r["total_contribs"] = cal.get("total_contribs")
        r["max_streak"] = cal.get("max_streak")
        if cal.get("active_days") is not None:
            r["active_le5"] = 1 if cal["active_days"] <= 5 else 0
            r["active_zero"] = 1 if cal["active_days"] == 0 else 0
        la = ts((cal.get("last_active") or "") + "T00:00:00Z") if cal.get("last_active") else None
        r["days_since_last"] = round((NOW - la) / 86400, 1) if la else None

        bm = cal.get("by_month") or {}
        if bm and ft:
            fdt = datetime.fromtimestamp(ft, tz=timezone.utc)
            cur = f"{fdt.year:04d}-{fdt.month:02d}"
            py_, pm_ = (fdt.year, fdt.month - 1) if fdt.month > 1 else (fdt.year - 1, 12)
            ny_, nm_ = (fdt.year, fdt.month + 1) if fdt.month < 12 else (fdt.year + 1, 1)
            prev, nxt = f"{py_:04d}-{pm_:02d}", f"{ny_:04d}-{nm_:02d}"
            r["contrib_fork_month"] = bm.get(cur, 0)
            r["contrib_month_before"] = bm.get(prev, 0)
            r["contrib_month_after"] = bm.get(nxt, 0)
            r["continued_after_fork"] = 1 if (bm.get(nxt, 0) > 0 or bm.get(cur, 0) > 0) else 0
            r["active_before_fork"] = 1 if (bm.get(prev, 0) > 0 or bm.get(cur, 0) > 0) else 0
            r["silent_before_but_active_after"] = 1 if (
                bm.get(prev, 0) == 0 and bm.get(cur, 0) == 0 and bm.get(nxt, 0) > 0) else 0

        # ---------- D8H 仓库 ----------
        r["own_count"] = rh.get("own_count")
        r["fork_count"] = rh.get("fork_count")
        r["repos_complete"] = rh.get("repos_complete")
        r["has_profile_repo"] = rh.get("has_profile_repo")
        if rh.get("own_count") is not None and rh.get("fork_count") is not None:
            tot = rh["own_count"] + rh["fork_count"]
            r["own_ratio"] = round(rh["own_count"] / tot, 3) if tot else None
            r["has_own"] = 1 if rh["own_count"] > 0 else 0
        langs = {(x.get("lang") or "") for x in (rh.get("own_details") or [])}
        langs.discard("")
        r["own_langs_n"] = len(langs)
        r["own_has_code_lang"] = 1 if (langs & CODE_LANGS) else (0 if rh else None)
        if rh.get("own_count"):
            r["ownlive_ratio"] = round((rh.get("own_touched") or 0) / rh["own_count"], 3)
        nt = ts(rh.get("own_newest_push") or "")
        r["own_updated_after_fork"] = (1 if (nt and ft and nt > ft) else (0 if (nt and ft) else None))
        names = [(x.get("name") or "").lower() for x in (rh.get("own_details") or [])]
        r["repo_named_target"] = 1 if any(target.split("/")[1] in n for n in names) else 0

        # ---------- D15 画像 ----------
        r["starred_count"] = ph.get("starred_count")
        r["achievement_n"] = ph.get("achievement_n")
        r["has_pull_shark"] = ph.get("has_pull_shark")
        r["has_collab"] = ph.get("has_collab")
        r["pinned_n"] = len(ph.get("pinned") or []) if ph else None
        r["has_display_name"] = 1 if (ph.get("display_name") or "").strip() else (0 if ph else None)

        # ---------- D14 头像 ----------
        atp = av.get(lg)
        r["avatar_type"] = atp
        r["avatar_photo"] = (1 if atp == "photo" else 0) if atp else None

        # ---------- D2 事件 ----------
        et = at.get("event_types") or {}
        low = {(k or "").lower(): v for k, v in et.items()}
        r["events_n"] = at.get("events_n")
        r["ev_push"] = low.get("push")
        r["ev_star"] = low.get("star") or low.get("watchevent")
        r["ev_fork"] = low.get("forkevent")
        r["ev_pr_created"] = low.get("pr_created") or low.get("pullrequestevent")
        r["ev_pr_merged"] = low.get("pr_merged")
        if r["events_n"] is not None:
            r["has_push_ev"] = 1 if r["ev_push"] else 0
            dev = (r["ev_push"] or 0) + (r["ev_pr_created"] or 0) + (r["ev_pr_merged"] or 0)
            r["dev_event_ratio"] = round(dev / r["events_n"], 3) if r["events_n"] else None
        if r["ev_pr_created"]:
            r["pr_merge_rate"] = round((r["ev_pr_merged"] or 0) / r["ev_pr_created"], 3)

        # ---------- D4L star 明细（只对 star 过目标仓的人算延迟）----------
        r["starred_target"] = sl.get("starred_target")
        stars = sl.get("stars") or []
        star_ts = [x for x in (ts(y.get("at")) for y in stars if y.get("at")) if x]
        if ft and star_ts and sl.get("starred_target"):
            nearest = min(star_ts, key=lambda x: abs(x - ft))
            r["star_fork_delay_h"] = round((ft - nearest) / 3600, 2)
            r["star_same_hour"] = 1 if abs(ft - nearest) <= 3600 else 0
            r["star_before_fork"] = 1 if nearest < ft else 0
            fday = int(ft // 86400)
            r["stars_on_fork_day"] = sum(1 for x in star_ts if int(x // 86400) == fday)
        if star_ts:
            days = [int(x // 86400) for x in star_ts]
            c = Counter(days)
            r["star_max_same_day"] = max(c.values())
            r["star_active_days"] = len(c)

        # ---------- D9 提交 ----------
        r["commit_total"] = cm.get("commit_total")
        r["repos_checked"] = cm.get("repos_checked")
        if cm.get("commit_total") is not None:
            r["commit_zero"] = 1 if cm["commit_total"] == 0 else 0

        rows.append(r)
    return rows


SPECS = {
    "rea": {
        "target": "morluto/rea",
        "fork": "forks_all_morluto__rea.jsonl",
        "d1": "profile_tier_screen_rea_all.jsonl",
        "d13": "heat_rea_all.jsonl",
        "d8": "repos_html_rea.jsonl",
        "d15": "profile_html_rea.jsonl",
        "d2": "atom_rea.jsonl",
        "d4l": "starlist_rea.jsonl",
        "d9": "commits_rea.jsonl",
        "d14": "avatar_rea.jsonl",
    },
    "openclaw": {
        "target": "openclaw/openclaw",
        "fork": "forks_all_openclaw__openclaw.jsonl",
        "d1": "profile_tier_screen_openclaw_all.jsonl",
        "d13": "heat_openclaw_all.jsonl",
        "d8": "repos_html_openclaw.jsonl",
        "d15": "profile_html_openclaw.jsonl",
        "d2": "atom_openclaw.jsonl",
        "d4l": "starlist_openclaw.jsonl",
        "d9": "commits_openclaw.jsonl",
        "d14": "avatar_openclaw.jsonl",
    },
}


def stats(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None, 0
    v.sort()
    return v[len(v) // 2], len(v)


def ratio(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None, 0
    return sum(1 for x in v if x), len(v)


MEDIAN_KEYS = [
    ("acct_age_days", "账号年龄（天）"), ("acct_age_at_fork_days", "fork 时账号年龄（天）"),
    ("fork_age_days", "fork 存活天数"), ("size_kb", "fork 大小 KB"),
    ("filled_n", "档案填充项"), ("public_repos", "公开仓数"),
    ("followers", "粉丝数"), ("following", "关注数"),
    ("follower_ratio", "粉丝/关注比"),
    ("active_days", "全年活跃天"), ("total_contribs", "年度贡献"),
    ("max_streak", "最长连续"), ("days_since_last", "距最后活跃天数"),
    ("contrib_fork_month", "fork 当月贡献"),
    ("contrib_month_before", "fork 前月贡献"), ("contrib_month_after", "fork 次月贡献"),
    ("own_count", "自建仓数"), ("fork_count", "fork 仓数"),
    ("own_ratio", "自建仓占比"), ("own_langs_n", "自建仓语言数"),
    ("starred_count", "star 总数"), ("achievement_n", "成就数"),
    ("events_n", "近 30 条事件数"), ("ev_push", "push 事件数"),
    ("ev_pr_created", "PR 创建数"), ("ev_pr_merged", "PR 合并数"),
    ("pr_merge_rate", "PR 合并率"),
    ("commit_total", "自建仓提交数"),
    ("star_fork_delay_h", "star→fork 延迟(小时)"),
    ("stars_on_fork_day", "fork 当天点星数"), ("star_max_same_day", "单日最多点星数"),
    ("dev_event_ratio", "开发事件占比"), ("ownlive_ratio", "自建仓有 push 占比"),
]

RATIO_KEYS = [
    ("fork_touched", "fork 后动过"), ("branch_other", "分支非 main/master"),
    ("avatar_photo", "**自己上传头像**"),
    ("followers_zero", "零粉丝"), ("following_zero", "零关注"),
    ("active_le5", "活跃≤5天"), ("active_zero", "全年零活跃"),
    ("continued_after_fork", "fork 后仍有贡献"), ("active_before_fork", "fork 前有贡献"),
    ("has_push_ev", "有 push 事件"),
    ("has_own", "有自建仓"), ("own_has_code_lang", "自建仓含代码语言"),
    ("own_updated_after_fork", "自建仓 fork 后更新过"),
    ("has_profile_repo", "**有个人主页仓**"),
    ("repo_named_target", "自建仓名含目标仓"),
    ("has_pull_shark", "有 Pull Shark"), ("has_collab", "有协作徽章"),
    ("has_display_name", "有显示名"), ("has_email", "公开邮箱"),
    ("starred_target", "star 过目标仓"),
    ("star_same_hour", "同小时 star+fork"), ("star_before_fork", "先 star 后 fork"),
    ("commit_zero", "自建仓零提交"),
    ("namematch", "姓名与登录名对应"), ("login_is_ts", "登录名是时间戳"),
]


def main():
    data = {}
    for tag, files in SPECS.items():
        rows = compute(tag, os.path.join(RAW, files["fork"]), files)
        data[tag] = rows
        keys = ["login"]
        seen = {"login"}
        for r in rows:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        dst = os.path.join(OUT, f"features_{tag}.csv")
        with open(dst, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"{tag}: {len(rows):,} 人 × {len(keys)} 特征 → {os.path.basename(dst)}", flush=True)

    L = ["# 全量特征对比报告", "",
         f"> 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
         "> 九套全量数据 · 纯本地计算 · 缺失值不填 0", "",
         "## 一、数值型特征（中位数）", "",
         "| 特征 | rea | openclaw | 差异 |", "|---|---:|---:|---:|"]
    for k, name in MEDIAN_KEYS:
        ma, na = stats([r.get(k) for r in data["rea"]])
        mb, nb = stats([r.get(k) for r in data["openclaw"]])
        if ma is None or mb is None:
            continue
        L.append(f"| {name} | {ma:,.2f} (n={na:,}) | {mb:,.2f} (n={nb:,}) | {mb-ma:+,.2f} |")

    L += ["", "## 二、比例型特征（占比）", "",
          "| 特征 | rea | openclaw | 差异(pp) |", "|---|---:|---:|---:|"]
    for k, name in RATIO_KEYS:
        va, na = ratio([r.get(k) for r in data["rea"]])
        vb, nb = ratio([r.get(k) for r in data["openclaw"]])
        if not na or not nb:
            continue
        pa, pb = va / na * 100, vb / nb * 100
        L.append(f"| {name} | {pa:.1f}% ({va:,}/{na:,}) | {pb:.1f}% ({vb:,}/{nb:,}) | {pa-pb:+.1f} |")

    L += ["", "## 三、档案形状分布（SHAPE）", "", "| 形状 | rea | openclaw |", "|---|---:|---:|"]
    sa = Counter(r.get("shape") for r in data["rea"] if r.get("shape"))
    sb = Counter(r.get("shape") for r in data["openclaw"] if r.get("shape"))
    ua, ub = sum(sa.values()) or 1, sum(sb.values()) or 1
    for shp in ("EMPTY", "TEMPLATE", "PARTIAL", "RICH"):
        L.append(f"| {shp} | {sa[shp]/ua*100:.1f}% ({sa[shp]:,}) | {sb[shp]/ub*100:.1f}% ({sb[shp]:,}) |")

    L += ["", "## 四、账号年龄 × 头像上传率（关键：分桶内两仓几乎相同）", "",
          "| 账号年龄 | rea 上传率 | openclaw 上传率 | 差异 |", "|---|---:|---:|---:|"]
    for bucket in ("<1年", "1-3年", "3-7年", ">7年"):
        vals = []
        for tag in ("rea", "openclaw"):
            sel = [r for r in data[tag]
                   if r.get("acct_age_bucket") == bucket and r.get("avatar_photo") is not None]
            vals.append((sum(1 for r in sel if r["avatar_photo"] == 1), len(sel)))
        (va, na), (vb, nb) = vals
        if not na or not nb:
            continue
        pa, pb = va / na * 100, vb / nb * 100
        L.append(f"| {bucket} | {pa:.1f}% ({va:,}/{na:,}) | {pb:.1f}% ({vb:,}/{nb:,}) | {pa-pb:+.1f}pp |")

    L += ["", "## 五、登录名类型分布", "", "| 类型 | rea | openclaw |", "|---|---:|---:|"]
    ca = Counter(r.get("login_type") for r in data["rea"] if r.get("login_type"))
    cb = Counter(r.get("login_type") for r in data["openclaw"] if r.get("login_type"))
    ta, tb = sum(ca.values()) or 1, sum(cb.values()) or 1
    for typ in sorted(set(ca) | set(cb)):
        L.append(f"| {typ} | {ca[typ]/ta*100:.2f}% ({ca[typ]:,}) | {cb[typ]/tb*100:.2f}% ({cb[typ]:,}) |")

    md = "\n".join(L) + "\n"
    open(os.path.join(OUT, "features_compare.md"), "w", encoding="utf-8").write(md)
    print("\n对比报告 → data/out/features_compare.md")
    print(md)


if __name__ == "__main__":
    main()
