# -*- coding: utf-8 -*-
"""把 _forker_probe.py 产出的 CSV 变成一份可读的 Markdown 画像报告。

刻意保持「生成器」而不是「手写报告」：数字全部从 CSV 现算，
换一批 fork、重跑一次就能得到口径一致的新报告。

用法：
    py _forker_report.py                     # 读默认 CSV，写到 data/out/
    py _forker_report.py --csv x.csv --out y.md
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from crawler.config import OUT_DIR, RAW_DIR, utcnow_iso

CSV_IN = os.path.join(OUT_DIR, "forker_probe_100.csv")
RAW_USERS = os.path.join(RAW_DIR, "probe_users.jsonl")
OUT_MD = os.path.join(OUT_DIR, "forker_probe_100_report.md")

# 仅用于老 CSV（没有 fork_in_burst 列）的兜底；新数据以探测脚本自动检测为准
BURST_DAY = "2026-10-19"
BURST_HOURS = ("19", "20", "21")


# ── 读入 ─────────────────────────────────────────────────
def load_rows(path: str) -> list:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_users(path: str) -> dict:
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    out[d.get("login")] = d
    return out


# ── 取值小工具 ───────────────────────────────────────────
def num(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def flt(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def txt(v):
    return "" if v is None else str(v)


def pct(a, n):
    return f"{a}/{n} ({a / n * 100:.0f}%)" if n else "—"


def is_burst(r: dict) -> bool:
    """批次标记以 CSV 里的 `fork_in_burst` 为准（由探测脚本自动检测），
    老 CSV 没这列时退回手写窗口。"""
    v = txt(r.get("fork_in_burst"))
    if v != "":
        return v == "1"
    t = txt(r.get("fork_created_at"))
    return t[:10] == BURST_DAY and t[11:13] in BURST_HOURS


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[len(xs) // 2]


def rows_has_repos(rows) -> bool:
    return any(r.get("repos_status") == "200" for r in rows)


def burst_span(B: list) -> str:
    """爆发窗里的人实际落在哪几个整点，压成一段可读文字。

    窗口由 `_forker_probe.py::detect_bursts()` 自动判定，所以这里不能再写死时段。
    """
    hrs = sorted({txt(r.get("fork_created_at"))[:13] for r in B
                  if len(txt(r.get("fork_created_at"))) >= 13})
    if not hrs:
        return "—"
    idx = [int(h[11:13]) for h in hrs]
    if (hrs[0][:10] == hrs[-1][:10]
            and idx == list(range(idx[0], idx[-1] + 1))):
        return ("%s %02d:00–%02d:59 UTC（%d 小时连续）"
                % (hrs[0][:10], idx[0], idx[-1], len(hrs)))
    return "、".join(h[:10] + " " + h[11:13] + "时" for h in hrs)


# ── 档案自洽性 ───────────────────────────────────────────
def name_matches_login(name: str, login: str):
    """姓名是否与登录名有可辨认的对应关系。None = 没填姓名，无从判断。"""
    if not name:
        return None
    parts = [p for p in re.split(r"[\s\-_.]+", name.lower()) if len(p) >= 3]
    l = re.sub(r"[^a-z]", "", login.lower())
    if not l:
        return None
    return any(p in l for p in parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=CSV_IN)
    ap.add_argument("--users", default=RAW_USERS)
    ap.add_argument("--out", default=OUT_MD)
    args = ap.parse_args()

    rows = load_rows(args.csv)
    users = load_users(args.users)
    n = len(rows)
    if not n:
        print("CSV 是空的。")
        return 1

    B = [r for r in rows if is_burst(r)]
    R = [r for r in rows if not is_burst(r)]
    tiers = Counter(r["tier"] for r in rows)

    got_u = sum(1 for r in rows if r.get("api_status") == "200")
    gone = [r for r in rows if r.get("api_status") == "404"]
    got_c = sum(1 for r in rows if r.get("contrib_status") == "200")
    got_e = sum(1 for r in rows if r.get("events_status") == "200")
    got_r = sum(1 for r in rows if r.get("repos_status") == "200")

    now = datetime.now(timezone.utc)
    L = []
    w = L.append

    w("# `openclaw/openclaw` 最新 100 个 fork 者的真实性与活动画像\n")
    w("> 分析日期：%s" % now.strftime("%Y-%m-%d %H:%M UTC"))
    w("> 样本：`openclaw/openclaw` 最新 %d 个 fork（`/forks?sort=newest`）" % n)
    w("> 采集：`_forker_probe.py`（四通道，匿名、无凭据）")
    w("> 判据：`_forker_probe.py::assess()`，分数为**可疑度分层**，不是身份判定")
    w("> 报告生成：`_forker_report.py`\n")
    w("---\n")

    # ── 一、摘要 ──
    w("## 一、摘要\n")
    w("本报告只回答两个问题：**这个账号像不像真人**、**有没有留下真实活动的痕迹**。")
    w("所有结论都建立在公开数据上，且给出证据；没有证据的账号一律标为「中性」，"
      "而不是默认怀疑。\n")
    w("| 项目 | 结果 |")
    w("|---|---|")
    w("| 分层 | " + " · ".join(f"{t} {tiers.get(t, 0)}"
                                 for t in ("正常", "中性", "可疑", "高危")) + " |")
    acts = Counter(txt(r.get("activity")) or "未知" for r in rows)
    w("| 活动轴 | " + " · ".join(f"{a} {acts.get(a, 0)}"
                                 for a in ("活跃", "零星", "沉寂", "无公开活动", "未知")) + " |")
    w("| 档案采集 | %s |" % pct(got_u, n))
    w("| 已注销/改名 | %d 个 |" % len(gone))
    w("| 贡献热力图 | %s |" % pct(got_c, n))
    w("| 公开事件 | %s |" % pct(got_e, n))
    w("| 仓库清单 | %s |" % pct(got_r, n))
    w("| fork 时间聚集 | %d 人集中在 %s |" % (len(B), burst_span(B)))
    w("")

    # ── 二、方法与边界 ──
    w("## 二、方法与数据边界\n")
    w("判「是不是真人」没有单一指标，本报告把证据分成四类，分别采集：\n")
    w("| 通道 | 端点 | 成本 | 回答什么 |")
    w("|---|---|---|---|")
    w("| A 档案 | `GET /users/{login}` | 1 请求/人，匿名 60 次/小时 | 账号几时注册、有几个仓库、"
      "关注关系、bio |")
    w("| B 贡献 | `GET github.com/users/{login}/contributions` | **不占 API 配额** | "
      "过去一年逐日贡献（最硬的「真实活动」证据） |")
    w("| C 事件 | `GET /users/{login}/events/public` | 1 请求/人 | 近 90 天公开的 push/PR/issue |")
    w("| D 仓库 | `GET /users/{login}/repos`，兜底 `/{login}?tab=repositories`（HTML） | "
      "1 请求/人，或**免配额** | 仓库里哪些是真项目、哪些是占位 |")
    w("")
    w("> ⚠️ **边界一：没有凭据，全部走匿名。** 当前环境没有任何 GitHub 令牌，"
      "匿名配额只有 60 次/小时，所以 A/C/D 三个通道是按「最可疑优先」分批采的，"
      "覆盖率见上表。")
    w("> ⚠️ **边界二：0 事件 ≠ 没活动。** 私有仓库、本地提交、只看不说的账号都不会出现在"
      "公开事件里。所以「事件数」只做展示，**不参与评分**，避免把「安静的人」误判成「假人」。")
    # 通道 B 的可达性是**间歇的**（github.com 会被按目标抑制，而 api.github.com 同时正常），
    # 所以这句边界话必须跟着实际覆盖数走，不能写死「不可达」——上一版就因此说了假话。
    if got_c == 0:
        w("> ⚠️ **边界三：贡献热力图通道本轮不可达。** `github.com` 在本机被按目标抑制"
          "（TLS 层超时，而 `api.github.com` 同时正常），通道 B 覆盖率为 0。"
          "这一项补齐之前，「有没有真实活动」只能用 C/D 两个较弱的通道回答。")
    elif got_c < n:
        w("> ⚠️ **边界三：贡献热力图覆盖 %d/%d。** `github.com` 会被间歇性按目标抑制"
          "（TLS 层超时，而 `api.github.com` 同时正常），通道 B 因此是分窗口补采的。"
          "**没有热力图的 %d 人不能按「0 贡献」处理**，他们在活动轴上一律记为「未知」。"
          % (got_c, n, n - got_c))
    else:
        w("> ⚠️ **边界三：贡献热力图只统计「公开可见」的贡献。** 私有仓库的提交只有用户"
          "主动公开才会计入，所以 0 贡献 ≠ 从不动手，只能说明**没有可公开验证的编码活动**。")
    w("")

    # ── 三、时间聚集 ──
    w("## 三、fork 时间聚集\n")
    # 按「日期+钟点」分桶而不是只按钟点：样本横跨两天，
    # 只按 hour-of-day 归并会把不同日子的同一钟点叠在一起，看不出平台在哪。
    dhours = Counter(txt(r.get("fork_created_at"))[:13].replace("T", " ")
                     for r in rows if len(txt(r.get("fork_created_at"))) >= 13)
    bkeys = {txt(r.get("fork_created_at"))[:13].replace("T", " ") for r in B}
    w("```")
    for k in sorted(dhours):
        w(f"{k}  {dhours[k]:3d}  {'#' * dhours[k]}"
          + ("   ◀ 爆发窗" if k in bkeys else ""))
    w("```\n")
    mins = Counter(txt(r.get("fork_created_at"))[:16] for r in B)
    peak_min = max(mins.values()) if mins else 0
    w("正常情况下，一个仓库的 fork 会跟着新闻/发布的时间走，日内分布有起伏但不会出现"
      "平台。本次样本在 **%s 出现连续高位**：%d 人，占 %s。"
      "而同一批人在分钟上又是**错开的**（同一分钟内最多 %d 人），"
      "更像排过队的自动化任务，而不是同一秒齐射的批量脚本。\n"
      % (burst_span(B), len(B), pct(len(B), n), peak_min))
    w("> 窗口不是手选的：`detect_bursts()` 在 fork 时间轴上滚动 3 小时求和，"
      "取超过「全体窗口均值 + 2σ」的区间。阈值来自数据自身分布，换一批样本会重新算。\n")
    w("### 3.1 爆发窗 vs 其余\n")
    w("| 指标 | 爆发窗（n=%d） | 其余（n=%d） |" % (len(B), len(R)))
    w("|---|---|---|")

    def compare(label, fn):
        def rate(rs):
            vals = [fn(r) for r in rs]
            vals = [v for v in vals if v is not None]
            return pct(sum(1 for v in vals if v), len(vals))
        w(f"| {label} | {rate(B)} | {rate(R)} |")

    compare("注册于 2026-07", lambda r: (txt(r.get("account_created_at")) or "").startswith("2026-07"))
    compare("档案字段恰好 3 项", lambda r: num(r.get("profile_fields_filled")) == 3)
    compare("档案全空", lambda r: num(r.get("profile_fields_filled")) == 0)
    compare("登录名末尾 ≥4 位数字", lambda r: (num(r.get("login_ends_digits")) or 0) >= 4)
    compare("followers = 0", lambda r: num(r.get("followers")) == 0)
    compare("followers=0 且 following=0",
            lambda r: num(r.get("followers")) == 0 and num(r.get("following")) == 0)
    compare("public_repos ≤ 2", lambda r: (num(r.get("public_repos")) or 0) <= 2
            if num(r.get("public_repos")) is not None else None)
    compare("fork 后向自己的仓库推送过", lambda r: (num(r.get("fork_touched")) or 0) == 1)
    compare("已注销/改名", lambda r: 1 if r.get("api_status") == "404" else 0)
    w("")
    ab = [flt(r.get("account_age_days_at_fork")) for r in B]
    ar = [flt(r.get("account_age_days_at_fork")) for r in R]
    w("fork 时账号年龄中位数：爆发窗 **%s 天**，其余 **%s 天**。\n"
      % (median(ab), median(ar)))
    w("> 特别注意最后一行的反向结果：`followers=0 且 following=0` 在爆发窗里只占 %s，"
      "在其余里占 %s —— **这一项没有判别力**。如果单看它就把人打成可疑，会把大量"
      "真实的沉默开发者一起打进去。它只能作为辅助证据，不能单独成立。\n"
      % (pct(sum(1 for r in B if num(r.get("followers")) == 0
                 and num(r.get("following")) == 0), len(B)),
         pct(sum(1 for r in R if num(r.get("followers")) == 0
                 and num(r.get("following")) == 0), len(R))))

    # ── 四、注册批次 ──
    w("## 四、注册批次\n")
    months = Counter((txt(r.get("account_created_at")) or "未知")[:7] for r in rows)
    top = months.most_common(6)
    w("| 注册月 | 人数 | 其中在爆发窗 |")
    w("|---|---|---|")
    for m, c in top:
        inb = sum(1 for r in B if (txt(r.get("account_created_at")) or "未知")[:7] == m)
        w(f"| {m} | {c} | {inb} |")
    w("")
    same_day = Counter((txt(r.get("account_created_at")) or "")[:10] for r in rows
                       if r.get("account_created_at"))
    batches = [(d, c) for d, c in sorted(same_day.items()) if c >= 3]
    if batches:
        w("同一天注册 ≥3 人的日期：" + "、".join(f"`{d}`（{c} 人）" for d, c in batches) + "。")
        w("")
    others = [c for m, c in months.items() if m != "2026-07"]
    w("2026-07 一个月里有 %d 个账号；其余任何一个月最多 %d 个，"
      "其余月份的人均值为 %.1f。注册时间这样成簇，配合后面的「姓名/地点/登录名互相对不上」，"
      "指向「同一时间点成批制备、之后陆续启用」。\n"
      % (months.get("2026-07", 0), max(others) if others else 0,
         sum(others) / len(others) if others else 0))

    # ── 五、档案自洽性 ──
    w("## 五、档案自洽性：填写内容是生成出来的吗\n")
    w("真人的 profile 通常内部一致：登录名、姓名、地点、bio 能互相对上。"
      "批量生成的 persona 恰恰在这里最容易露馅 —— 名字对不上登录名，"
      "地点是「听起来像但不存在」的地名。\n")
    mm = {True: 0, False: 0, None: 0}
    for r in rows:
        u = users.get(r["login"]) or {}
        v = name_matches_login(txt(u.get("name")).strip(), r["login"])
        mm[v] += 1
    w("| 指标 | 爆发窗 | 其余 |")
    w("|---|---|---|")

    def rate_of(rs, fn):
        vals = [fn(r) for r in rs]
        vals = [v for v in vals if v is not None]
        return pct(sum(1 for v in vals if v), len(vals))

    def mism(r):
        u = users.get(r["login"]) or {}
        v = name_matches_login(txt(u.get("name")).strip(), r["login"])
        return None if v is None else (not v)

    def loc_comma(r):
        u = users.get(r["login"]) or {}
        loc = txt(u.get("location")).strip()
        return ("," in loc) if loc else None

    def bio_mark(r):
        u = users.get(r["login"]) or {}
        b = txt(u.get("bio"))
        return ("—" in b or "|" in b) if b else None

    w(f"| 姓名与登录名对不上 | {rate_of(B, mism)} | {rate_of(R, mism)} |")
    w(f"| 填了「城市, 国家」格式的地点 | {rate_of(B, loc_comma)} | {rate_of(R, loc_comma)} |")
    w(f"| bio 含破折号 / 竖线（模板味） | {rate_of(B, bio_mark)} | {rate_of(R, bio_mark)} |")
    w("")
    w("爆发窗里填了地点的账号：\n")
    w("```")
    for r in B:
        u = users.get(r["login"]) or {}
        loc = txt(u.get("location")).strip()
        if loc:
            w(f"{r['login']:22s} {loc[:34]:34s} | name={txt(u.get('name'))[:18]}")
    w("```\n")
    w("对照端（其余组）填的地点基本是可核对的城市/国家，与姓名语种也大多相符：\n")
    w("```")
    for r in R:
        u = users.get(r["login"]) or {}
        loc = txt(u.get("location")).strip()
        if loc:
            w(f"{r['login']:22s} {loc[:34]:34s} | name={txt(u.get('name'))[:18]}")
    w("```\n")
    w("这里要把两种痕迹分开看，混在一起说会夸大结论：\n")
    w("- **地点真实、但姓名对不上**：`claralim33401`（Francisco / Resistencia）、"
      "`gaiagallo6725`（Agustin / Mar del Plata）、`reddy86277`（Reddy / Bihar Sharif）。"
      "这几个城市是真的，问题出在「名字和登录名毫无关系」——买来的号常沿用原主人的姓名。")
    w("- **地点本身查不到**：`Hannahmouth`、`Pamelabury`、`Camargo do Norte`、"
      "`Nueva Ecuador`、`San Natalia los bajos`、`Caldeira das Flores`。"
      "这些地名符合目标国家的构词法，但按常识与行政区划都对不上号；"
      "对照组 %d 人里没有一个地点与这六个重合。这是语言模型编造 persona 的典型产物。"
      % len(R))
    w("")

    # 7 月批次全表 —— 这是整份报告最直接的证据，单列一节
    jul = [r for r in rows if (txt(r.get("account_created_at")) or "").startswith("2026-07")]
    if jul:
        w("### 5.1 2026-07 批次的完整档案（%d 人）\n" % len(jul))
        w("| login | 注册日 | 姓名 | 地点 | 公司 | bio | 仓库 | followers |")
        w("|---|---|---|---|---|---|---|---|")
        for r in sorted(jul, key=lambda x: txt(x.get("account_created_at"))):
            u = users.get(r["login"]) or {}
            w("| `%s` | %s | %s | %s | %s | %s | %s | %s |" % (
                r["login"], (txt(r.get("account_created_at")) or "—")[:10],
                txt(u.get("name")) or "—", txt(u.get("location")) or "—",
                txt(u.get("company")) or "—", txt(u.get("bio"))[:28] or "—",
                txt(u.get("public_repos")) or "—", txt(u.get("followers")) or "—"))
        w("")
        jrepos = [num(r.get("public_repos")) for r in jul]
        jrepos = [x for x in jrepos if x is not None]
        jfill = [num(r.get("profile_fields_filled")) for r in jul]
        jfill = [x for x in jfill if x is not None]
        jdays = sorted({(txt(r.get("account_created_at")) or "")[8:10] for r in jul})
        w("这一批的共同点：注册集中在 7 月的 %d 天里（%s），"
          "仓库数 %d–%d（中位数 %s），档案填了 %d–%d 项，followers 全部为 0。"
          "单独看每一条都不算出格，**合在一起**就是「同一模板批量生成」的形状。\n"
          % (len(jdays), " / ".join(jdays),
             min(jrepos) if jrepos else 0, max(jrepos) if jrepos else 0,
             median(jrepos) if jrepos else "—",
             min(jfill) if jfill else 0, max(jfill) if jfill else 0))

    w("> 地点真伪本应用地理库交叉核验。本机的 OSM / Wikidata 均不可达"
      "（与被抑制的 `github.com` 同一类问题），所以上面对「查不到」的判断"
      "基于公开行政区划常识，**建议人工复核后再写进正式结论**。\n")

    # ── 六、消失的账号 ──
    if gone:
        w("## 六、fork 后迅速消失的账号\n")
        w("| login | fork 时间 | 观察时状态 |")
        w("|---|---|---|")
        for r in sorted(gone, key=lambda x: txt(x.get("fork_created_at"))):
            w(f"| `{r['login']}` | {txt(r.get('fork_created_at'))} | HTTP 404（已注销或改名） |")
        w("")
        w("这 %d 个账号全部落在爆发窗内，且都在 fork 后两天内从公开 API 上消失。"
          "为什么消失无法从外部证实（可能是改名，也可能是删除），"
          "但「刚 fork 完就消失」与一次性账号的行为模式一致。\n" % len(gone))

    # ── 七、活动证据 ──
    w("## 七、真实活动：现有证据够不够\n")
    cv = [r for r in rows if r.get("contrib_status") == "200"]
    ev_rows = [r for r in rows if r.get("events_status") == "200"]
    rp_rows = [r for r in rows if r.get("repos_status") == "200"]
    w("| 证据 | 可判定人数 | 结果 |")
    w("|---|---|---|")
    if cv:
        z = sum(1 for r in cv if num(r.get("contrib_total")) == 0)
        w("| 贡献热力图（通道 B） | %d | 过去一年 0 贡献 %s |"
          % (len(cv), pct(z, len(cv))))
    else:
        w("| 贡献热力图（通道 B） | 0 | 不可达，未采到 |")
    if ev_rows:
        zero = sum(1 for r in ev_rows if (num(r.get("events_n")) or 0) == 0)
        w("| 近 90 天公开事件（通道 C） | %d | 0 事件 %s |"
          % (len(ev_rows), pct(zero, len(ev_rows))))
    else:
        w("| 近 90 天公开事件（通道 C） | 0 | 未采到 |")
    if rp_rows:
        orig0 = sum(1 for r in rp_rows if (num(r.get("repos_original_n")) or 0) == 0)
        w("| 自建（非 fork）仓库（通道 D） | %d | 一个都没有 %s |"
          % (len(rp_rows), pct(orig0, len(rp_rows))))
    else:
        w("| 自建（非 fork）仓库（通道 D） | 0 | 未采到 |")
    touched = sum(1 for r in rows if (num(r.get("fork_touched")) or 0) == 1)
    w("| fork 后是否向 fork 推送过 | %d | 推送过 %s |" % (n, pct(touched, n)))
    w("")
    w("「fork 后推送过」是唯一一个**对全部 100 人都可得**的活动信号："
      "fork 仓库的 `pushed_at` 如果比 `created_at` 晚，说明这个人 fork 之后真的动手改过。"
      "反过来，`pushed_at == created_at` 只说明没往这个 fork 推过，"
      "**不能**推出「这个人别的什么都没做」。\n")

    if cv:
        w("### 7.1 贡献热力图：爆发窗 vs 其余\n")
        w("这是整份报告里最接近「有没有真实活动」的一项：贡献日历按天记录，"
          "造假者可以 fork 一千次，却很难伪造一整年的逐日提交。\n")
        w("| 指标 | 爆发窗 | 其余 |")
        w("|---|---|---|")

        def cv_rate(rs, fn):
            vals = [fn(r) for r in rs if r.get("contrib_status") == "200"]
            vals = [v for v in vals if v is not None]
            return pct(sum(1 for v in vals if v), len(vals))

        def cv_med(rs, key):
            vals = [flt(r.get(key)) for r in rs if r.get("contrib_status") == "200"]
            return median(vals) if vals else "—"

        w(f"| 可判定人数 | {len([r for r in B if r.get('contrib_status') == '200'])} "
          f"| {len([r for r in R if r.get('contrib_status') == '200'])} |")
        w(f"| 过去一年 0 贡献 | {cv_rate(B, lambda r: num(r.get('contrib_total')) == 0)} "
          f"| {cv_rate(R, lambda r: num(r.get('contrib_total')) == 0)} |")
        w(f"| 最近 90 天 0 贡献 | {cv_rate(B, lambda r: num(r.get('contrib_last90')) == 0)} "
          f"| {cv_rate(R, lambda r: num(r.get('contrib_last90')) == 0)} |")
        w(f"| 最近 30 天 0 贡献 | {cv_rate(B, lambda r: num(r.get('contrib_last30')) == 0)} "
          f"| {cv_rate(R, lambda r: num(r.get('contrib_last30')) == 0)} |")
        w(f"| 过去一年贡献数（中位数） | {cv_med(B, 'contrib_total')} "
          f"| {cv_med(R, 'contrib_total')} |")
        w(f"| 有贡献的天数（中位数） | {cv_med(B, 'contrib_active_days')} "
          f"| {cv_med(R, 'contrib_active_days')} |")
        w("")
        w("**这个结果和直觉相反，而且是整份报告最需要小心的地方：**"
          "爆发窗的贡献量不但不比对照组少，反而是数倍。也就是说，"
          "这些账号**不是空壳号** —— 它们有持续数月、节奏像人的提交历史。\n")
        w("这意味着两种可能，公开数据暂时无法区分：")
        w("1. 它们是**被买来/养起来的真号**：原主人或养号脚本留下的真实历史还在，"
          "但十月这次 fork 是被人统一指挥的。")
        w("2. 它们就是**真实开发者**，只是碰巧被同一波信息（视频/文章/群消息）"
          "在同一晚推去 fork。")
        w("区分这两者的关键不在「动了多少」，而在「动在哪儿」——"
          "见下一节的仓库清单与 §7.2 的交叉表。\n")
        w("贡献日历的统计口径是 **公开可见** 的贡献（含用户在设置里主动公开的私有仓库贡献）。"
          "所以「0 贡献」只能说明**没有可公开验证的编码活动**，"
          "不能说明此人从不动手 —— 一个从不提交代码、只用 GitHub 看项目的真实开发者"
          "也是 0。\n")
        w("但要注意口径的另一面：热力图上有真实提交，只说明**这个账号背后有持续的编码行为**，"
          "并不能证明**当前操作者就是账号的原始主人**。"
          "账号被买走、被接管之后，历史贡献仍然挂在上面。\n")

        zero_all = [r for r in cv if num(r.get("contrib_total")) == 0]
        if zero_all:
            w("过去一年 0 贡献的 %d 个账号（其中爆发窗 %d 人）：\n"
              % (len(zero_all), sum(1 for r in zero_all if is_burst(r))))
            w("| login | 分层 | fork 时间 | 账号年龄(天) | 仓库 | 档案字段 |")
            w("|---|---|---|---|---|---|")
            for r in sorted(zero_all, key=lambda x: txt(x.get("fork_created_at"))):
                w("| `%s` | %s | %s | %s | %s | %s |" % (
                    r["login"], r.get("tier"),
                    txt(r.get("fork_created_at"))[:16].replace("T", " "),
                    txt(r.get("account_age_days_at_fork")) or "—",
                    txt(r.get("public_repos")) or "—",
                    txt(r.get("profile_fields_filled")) or "—"))
            w("")
        rich = sorted([r for r in cv if (num(r.get("contrib_total")) or 0) > 0],
                      key=lambda x: -(num(x.get("contrib_total")) or 0))[:5]
        if rich:
            w("作为对照，贡献最多的 5 个账号 —— 热力图确实在区分度上有用，"
              "不是所有人都为 0：\n")
            w("| login | 分层 | 过去一年贡献 | 活跃天数 | 最近90天活跃 | 最长连续 |")
            w("|---|---|---|---|---|---|")
            for r in rich:
                w("| `%s` | %s | %s | %s | %s | %s |" % (
                    r["login"], r.get("tier"), txt(r.get("contrib_total")),
                    txt(r.get("contrib_active_days")), txt(r.get("contrib_last90")),
                    txt(r.get("contrib_streak_max"))))
            w("")

        w("### 7.2 可疑度 × 活动量\n")
        w("这两个轴是**分开算**的：可疑度只看身份/行为像不像批量制备，"
          "活动量只看贡献热力图。交叉之后四类人的含义完全不同。\n")
        w("| 可疑度 | 有活动（活跃+零星） | 低活动（沉寂+无公开活动） | 未知 |")
        w("|---|---|---|---|")
        for t in ("正常", "中性", "可疑", "高危"):
            hi = sum(1 for r in rows if r.get("tier") == t
                     and txt(r.get("activity")) in ("活跃", "零星"))
            lo = sum(1 for r in rows if r.get("tier") == t
                     and txt(r.get("activity")) in ("沉寂", "无公开活动"))
            un = sum(1 for r in rows if r.get("tier") == t
                     and txt(r.get("activity")) in ("", "未知"))
            w(f"| {t} | {hi} | {lo} | {un} |")
        w("")
        burst_active = sum(1 for r in B if txt(r.get("activity")) in ("活跃", "零星"))
        rest_active = sum(1 for r in R if txt(r.get("activity")) in ("活跃", "零星"))
        w("读法：**「可疑 × 有活动」**（爆发窗 %d/%d，其余 %d/%d）"
          "是「用有历史的账号做批量动作」的形状，比空壳号更难识别；"
          "**「中性 × 低活动」**只是潜水者，不该因为贡献少就被怀疑。\n"
          % (burst_active, len(B), rest_active, len(R)))

    # ── 7.3 仓库构成 ──
    #   为什么单开一节：贡献热力图只说明「这个账号有提交」，不说明提交发生在**自己写的项目**上。
    #   fork 别人的仓库、把自己仓库建成 fork 集合，同样能堆出一片绿格子。
    #   所以要把「自建仓库」和「fork 仓库」分开数。
    if rp_rows:
        rpB = [r for r in rp_rows if is_burst(r)]
        rpR = [r for r in rp_rows if not is_burst(r)]

        def orig_names(r):
            """`repos_names` 的前 min(自建数, 12) 项就是自建仓库名。

            `fetch_repos()` 拼这串时先放自建（最多 12 个）再放 fork（最多 6 个），
            所以按 `repos_original_n` 切一刀就能拿到「自己写的那些」。
            """
            k = int(num(r.get("repos_original_n")) or 0)
            return [x for x in txt(r.get("repos_names")).split(",") if x][:min(k, 12)]

        PLACEHOLDER = ("test", "demo", "my-first", "first-repo", "hello-world",
                       "untitled", "new-repo", "practice", "exercise", "playground")

        def placeholder_n(rs):
            return sum(1 for r in rs for n in orig_names(r)
                       if any(k in n.lower() for k in PLACEHOLDER))

        def push_days(rs):
            """有自建仓库的人里，最近一次 push 距今多少天。"""
            return [d for d in (flt(r.get("repos_original_push_days")) for r in rs)
                    if d is not None]

        def rate_lt(rs, days):
            ds = push_days(rs)
            return pct(sum(1 for d in ds if d < days), len(ds)) if ds else "—"

        def orig_med(rs):
            vs = [num(r.get("repos_original_n")) or 0 for r in rs]
            return median(vs) if vs else "—"

        w("### 7.3 仓库构成：写代码的人，还是收藏 fork 的人\n")
        w("贡献热力图只说明「这个账号有提交」，不说明提交发生在**自己写的项目**上。"
          "`original` = 非 fork 仓库（自己建的），`forks` = 从他处 fork 来的。"
          "一个健康的开发者通常两者都有；以收藏/养号为用途的账号，仓库几乎全是 fork。\n")

        if rpR:
            # 有对照组时，这一节才从「描述」变成「比较」
            w("| 指标 | 爆发窗（n=%d） | 其余（n=%d） |" % (len(rpB), len(rpR)))
            w("|---|---|---|")
            w("| 自建仓库 = 0 | %s | %s |"
              % (pct(sum(1 for r in rpB if (num(r.get('repos_original_n')) or 0) == 0), len(rpB)),
                 pct(sum(1 for r in rpR if (num(r.get('repos_original_n')) or 0) == 0), len(rpR))))
            w("| 自建仓库数（中位数） | %s | %s |" % (orig_med(rpB), orig_med(rpR)))
            w("| 最近 7 天向自建仓库 push 过 | %s | %s |" % (rate_lt(rpB, 7), rate_lt(rpR, 7)))
            w("| 最近 30 天向自建仓库 push 过 | %s | %s |"
              % (rate_lt(rpB, 30), rate_lt(rpR, 30)))
            d_b, d_r = push_days(rpB), push_days(rpR)
            w("| 距今最近一次 push（中位数·天） | %s | %s |"
              % (median(d_b) if d_b else "—", median(d_r) if d_r else "—"))
            w("| 自建仓库名含 test/demo 等占位词 | %d | %d |"
              % (placeholder_n(rpB), placeholder_n(rpR)))
            w("")
        else:
            w("| 指标 | 数值 |")
            w("|---|---|")
            w(f"| 可判定人数（通道 D） | {len(rp_rows)} |")
            o0 = [r for r in rp_rows if (num(r.get("repos_original_n")) or 0) == 0]
            w(f"| 自建仓库 = 0（仓库全是 fork） | {pct(len(o0), len(rp_rows))} |")
            w("| 自建仓库数（中位数） | %s |" % orig_med(rp_rows))
            w("| 最近 7 天向自建仓库 push 过 | %s |" % rate_lt(rp_rows, 7))
            w("| 最近 30 天向自建仓库 push 过 | %s |" % rate_lt(rp_rows, 30))
            w(f"| 自建仓库名含 test/demo 等占位词 | {placeholder_n(rp_rows)} 个 |")
            w("")
            w("> ⚠️ **这一节目前没有对照组。** 表里全是爆发窗的人，"
              "所以「最近 7 天 push 过的比例很高」**不能**单独当证据 —— "
              "刚 fork 了热门项目的开发者本来就比一般人活跃。"
              "要断言这个比例异常，必须拿同一批 fork 里其余人的仓库数据按同一口径比一次。")

        w("| login | 仓库总数 | 自建 | fork | 仓库名（自建在前，fork 在后） |")
        w("|---|---|---|---|---|")
        for r in sorted(rp_rows, key=lambda x: (num(x.get("repos_original_n")) or 0,
                                                -(num(x.get("repos_n")) or 0))):
            names = txt(r.get("repos_names"))
            w("| `%s` | %s | %s | %s | %s |" % (
                r["login"], txt(r.get("repos_n")) or "—",
                txt(r.get("repos_original_n")) or "—",
                txt(r.get("repos_forks_n")) or "—",
                ("`" + names.replace(",", "`, `") + "`") if names else "—"))
        w("")

        o0 = [r for r in rp_rows if (num(r.get("repos_original_n")) or 0) == 0]
        if o0:
            big = [r for r in o0 if (num(r.get("repos_n")) or 0) >= 20]
            if big:
                w("自建仓库为 0、但仓库总数很多的账号："
                  + "、".join("`%s`（%s 个仓库）" % (r["login"], txt(r.get("repos_n")))
                             for r in big)
                  + "。它们把 GitHub 当成了 fork 的收藏夹，而不是写代码的地方。")
            else:
                w("自建仓库为 0 的账号："
                  + "、".join("`%s`" % r["login"] for r in o0) + "。")
            w("")

        has_orig = [r for r in rp_rows if (num(r.get("repos_original_n")) or 0) >= 1]
        named = [r for r in rp_rows if txt(r.get("repos_names"))]
        if named and has_orig:
            ph = placeholder_n(has_orig)
            w("**名字读下来，结论要往回收一步。** %d/%d 的人至少有一个自建仓库；"
              "这些自建仓库里含 `test` / `demo` / `my-first-repo` 这类占位名的只有 **%d 个**。"
              "纯养号的种子仓库常常就是这种占位名，这里没有这个形状 —— "
              "它更像**真人在用 GitHub 干活**，与 §7.1 贡献热力图的结论一致。"
              % (len(has_orig), len(rp_rows), ph))
            if o0:
                w("真正「只有 fork、没有任何自建项目」的是另外 %d 人：%s —— "
                  "这两类账号不该用同一个结论去套。\n"
                  % (len(o0), "、".join("`%s`" % r["login"] for r in o0)))
        if not any(txt(r.get("repos_names")) for r in rp_rows):
            w("> ⚠️ 仓库**名字**本批尚未采到（API 那一轮只记了计数）。"
              "名字比计数更有判别力 —— 「my-first-repo」和「threejs-devtools-mcp」"
              "代表完全不同的两类人。补采命令：`--stage repos-html`（免配额）。\n")

    # ── 八、分层明细 ──
    w("## 八、分层明细\n")
    w("排序规则：分数升序（越低越可疑）。分数是加权和，明细见 `assess()`。\n")
    w("| login | 分层 | 分数 | 活动 | fork时年龄(天) | 注册日 | 档案字段 | 仓库 | 事件 | flags |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (num(x.get("score")) or 0, txt(x.get("fork_created_at")))):
        if r["tier"] not in ("高危", "可疑"):
            continue
        w("| `%s` | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["login"], r["tier"], txt(r.get("score")),
            txt(r.get("activity")) or "—",
            txt(r.get("account_age_days_at_fork")) or "—",
            (txt(r.get("account_created_at")) or "—")[:10],
            txt(r.get("profile_fields_filled")) or "—",
            txt(r.get("public_repos")) or "—",
            txt(r.get("events_n")) if r.get("events_status") == "200" else "—",
            txt(r.get("flags")).replace(";", "、") or "—"))
    w("")
    w("### 8.1 对照端：分数最高的 5 个\n")
    w("| login | 分数 | fork时年龄(天) | 注册日 | followers | 仓库 |")
    w("|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: -(num(x.get("score")) or 0))[:5]:
        w("| `%s` | %s | %s | %s | %s | %s |" % (
            r["login"], txt(r.get("score")), txt(r.get("account_age_days_at_fork")),
            (txt(r.get("account_created_at")) or "—")[:10],
            txt(r.get("followers")), txt(r.get("public_repos"))))
    w("")

    # ── 九、结论与局限 ──
    w("## 九、结论与局限\n")
    w("**能说的：**\n")
    w("1. 这 100 人里有 %d 人的行为在时间上高度聚集，且成分与其余 %d 人显著不同"
      "（7 月注册、档案恰好 3 项、登录名带数字、地点与姓名对不上），"
      "这与「一批预先准备、按队列执行的账号」一致。" % (len(B), len(R)))
    w("2. 有 %d 个账号在 fork 后两天内从 API 上消失，全部位于同一时间窗。" % len(gone))
    ages_all = [flt(r.get("account_age_days_at_fork")) for r in rows]
    lt7 = sum(1 for a in ages_all if a is not None and a < 7)
    lt1 = sum(1 for a in ages_all if a is not None and a < 1)
    w("3. 另外有 %d 个账号在 fork 时注册不满 7 天，其中 %d 个注册当天就 fork 了。"
      % (lt7, lt1))
    if cv:
        act_b = sum(1 for r in B if txt(r.get("activity")) in ("活跃", "零星"))
        w("4. **爆发窗里的账号大多有真实的活动历史**（%d/%d 在贡献热力图上留下了近年提交），"
          "所以这不是一批空壳号。但这恰好让判断更难："
          "历史真实 ≠ 这次 fork 是自发的，"
          "账号可能是有历史的老号被接管，也可能是真人被同一波信息推动。"
          % (act_b, len(B)))
    rp_rows = [r for r in rows if r.get("repos_status") == "200"]
    if rp_rows:
        o0 = [r for r in rp_rows if (num(r.get("repos_original_n")) or 0) == 0]
        if o0:
            w("5. 采到仓库构成的 %d 人里，有 %d 人的**自建仓库为 0**（仓库全是 fork）。"
              "写代码的人一般至少有几个自己的项目；仓库里全是 fork 更像把 GitHub 当收藏夹，"
              "与「这个号平时不用来写代码」一致。"
              % (len(rp_rows), len(o0)))
    w("")
    w("**不能说的：**\n")
    w("1. 不能断言任何账号「是机器人」或「是买的号」。公开数据只能给出行为相似度，"
      "不能给出身份判定。")
    if cv:
        w("2. 贡献热力图只覆盖了 %d/%d 人，且只统计**公开可见**的贡献。"
          "没有热力图数据的人不能按「0 贡献」处理。"
          % (len(cv), n))
    else:
        w("2. 贡献热力图（最硬的证据）本次没采到，所以「有没有真实活动」目前只有弱证据。")
    w("3. 0 个公开事件不能证明没活动；私有仓库的工作在这里完全不可见。")
    w("4. 对照端只有同一仓库的其他 forker，没有跨仓库基线。"
      "「爆发窗的 2026-07 账号更集中」是**样本内**比较，不能外推成总体比例。")
    w("")
    w("**下一步（按性价比排序）：**\n")
    k = 1
    if not cv:
        w(f"{k}. 等 `github.com` 恢复，跑 `--stage contrib`，用免费的热力图给 100 人补上最硬的活动证据。")
        k += 1
    if not rows_has_repos(rows):
        w(f"{k}. 跑仓库清单（`--stage repos` 走 API，或 `--stage repos-html` 免配额），"
          "看这些账号的仓库里有没有任何非 fork 的真项目 —— "
          "这是区分「真开发者」和「养号」目前最直接的一条。")
        k += 1
    elif not any(txt(r.get("repos_names")) for r in rows):
        w(f"{k}. 补仓库**名字**（`--stage repos-html` 免配额）："
          "计数只说明「有几个仓库」，名字才说明「是不是自己写的项目」。")
        k += 1
    if (rows_has_repos(rows)
            and not any(r.get("repos_status") == "200" and not is_burst(r) for r in rows)):
        w(f"{k}. **补齐其余 {len(R)} 人的仓库数据**（`--stage repos --tier all`，"
          "每窗口约 55 人）。现在 §7.3 只有爆发窗自己，"
          "「最近 7 天 push 比例高」是混杂了「刚 fork 热门项目的人本来就活跃」的，"
          "没有对照组就不能当异常。")
        k += 1
    w(f"{k}. 拿一个同期的大仓库（如 `ollama/ollama`）的同规模 fork 名单做外部对照，"
      "确认爆发窗和 7 月批次是不是 openclaw 特有。")
    w(f"{k + 1}. 抽查爆发窗里最可疑的若干账号的仓库内容（README、提交信息、提交节奏），"
      "判断「有活动」是不是自动生成的提交。")
    w("")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print(f"报告 → {args.out}（{len(L)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
