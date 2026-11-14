"""造假信号检测框架。

━━━ 本文件最重要的一句话 ━━━

    报告须区分「**异常**」与「**造假**」。

一条信号亮红灯只说明「这个数字偏离了对照分布」，不说明「有人买星」。
偏离可以有完全正常的解释：媒体爆发、改名迁移、领域特性、账号年龄结构。
所以每条信号都强制携带 `caveat`（这条信号在什么情况下会骗人），
报告渲染时必须一起输出 —— 不允许只报数字不报局限。

━━━ 数据不足时怎么办 ━━━

返回 `insufficient_data` / `uncalibrated`，**不猜**。
一个没采到的维度写「数据不足」，比编一个看起来合理的数字诚实得多。
"""
from typing import Callable, Optional

from . import baseline as B
from . import metrics as M

# ══ 信号注册表 ══════════════════════════════════════════
# extract 接收 metrics.all_metrics() 的结果，返回 float 或 None。
# higher_is_worse=False 表示「数值越大越健康」。
SIGNALS = [
    {
        "key": "growth_peak_share",
        "no": 1, "name": "增长峰值集中度",
        "what": "单日新增 star 占全周期新增的比例",
        "how": "max(daily) / sum(daily)",
        "unit": "%", "higher_is_worse": True,
        "extract": lambda m: m["growth"].get("peak_pct_of_total"),
        "caveat": "真实爆红项目（一次 HackerNews 首页）也会产生单日尖峰。"
                  "本信号只说明「增长高度集中」，需结合峰值日附近是否有可解释的"
                  "外部事件（发布/媒体报道）才能下判断。",
    },
    {
        "key": "growth_top7_share",
        "no": 1, "name": "增长 Top-7 日集中度",
        "what": "最高的 7 天贡献了多少比例的新增 star",
        "how": "sum(top7(daily)) / sum(daily)",
        "unit": "%", "higher_is_worse": True,
        "extract": lambda m: m["growth"].get("top7_pct_of_total"),
        "caveat": "与上一条同源，但抗单日噪声。仍然无法区分「一次大爆发」"
                  "与「分批刷量」。",
    },
    {
        "key": "growth_plateau_days",
        "no": 1, "name": "增长平台段长度",
        "what": "连续 7 天中位数 > 全期中位数 2 倍的最长天数",
        "how": "滑动窗口找持续高位段，取最长",
        "unit": "天", "higher_is_worse": True,
        "extract": lambda m: (max((p["len"] for p in m["growth"].get("plateau_days") or []),
                                  default=0) or None),
        "caveat": "★ 这条最像刷量（阶梯状平台），但也最容易被真实事件污染："
                  "项目上线后进入稳定增长期，天然就是一条长平台。"
                  "**必须**对照同类仓库的同期平台长度，单看绝对值会误判。",
    },
    {
        "key": "growth_half_life_days",
        "no": 1, "name": "爆发后衰减半衰期",
        "what": "从峰值日回落到峰值一半所需天数",
        "how": "首个 daily ≤ peak/2 的下标 − 峰值下标",
        "unit": "天", "higher_is_worse": False,
        "extract": lambda m: m["growth"].get("half_life_days"),
        "caveat": "半衰期长既可能是「真实社区黏性」，也可能是「持续刷量」。"
                  "方向不唯一，故判为「越大越好」仅为便于排序，"
                  "**不应作为独立判据**。",
    },
    {
        "key": "weekday_std",
        "no": 2, "name": "周内分布均匀度",
        "what": "按星期几分桶后，各桶占比的标准差",
        "how": "std(share_pct[0..6])",
        "unit": "pp", "higher_is_worse": False,
        "extract": lambda m: m["weekday"].get("std_pct"),
        "caveat": "★ 这条信号**弱**。均匀分布也可能来自「定时任务每周跑一次」"
                  "这类正常运维行为；而真实项目在爆发期也常常因为媒体"
                  "全天候传播而显得均匀。只做辅助，不单独定罪。",
    },
    {
        "key": "weekend_pct",
        "no": 2, "name": "周末点星占比",
        "what": "周六+周日新增占全周期比例（均匀应为 28.6%）",
        "how": "(buckets[5] + buckets[6]) / total",
        "unit": "%", "higher_is_worse": False,
        "extract": lambda m: m["weekday"].get("weekend_pct"),
        "caveat": "开发者群体跨时区，且开源贡献者常有周末集中投入的习惯。"
                  "偏离 28.6% 的幅度需与对照组比才有意义。",
    },
    {
        "key": "clustering_enrichment",
        "no": 5, "name": "同秒碰撞富集倍数",
        "what": "实测「同秒 ≥2 人点星」的秒数 ÷ Poisson 零模型期望值",
        "how": "W·(1−e^−λ−λ·e^−λ) 为零模型期望，λ = n_events/W，W = 首末事件时间窗",
        "unit": "×", "higher_is_worse": True,
        "extract": lambda m: m["clustering"].get("enrichment"),
        "caveat": "★ 底数必须用**整个时间窗 W**，不能用活跃秒数 T —— "
                  "T 按定义就是「至少 1 个事件的秒」，拿它当分母会算出"
                  "「比随机还少」的荒谬结论。另外：真实的病毒式传播"
                  "（一条推文带来同时点星）也会产生同秒碰撞，"
                  "所以富集本身不等于刷量，要看**批次规模分布**："
                  "大量「成对」而无「大批量」更像分散式刷量或小圈子传播。",
    },
    {
        "key": "clustering_max_batch",
        "no": 5, "name": "最大同秒批次",
        "what": "同一秒内点星的最大账号数",
        "how": "max(n_actors) over star_seconds",
        "unit": "人", "higher_is_worse": True,
        "extract": lambda m: m["clustering"].get("max_batch"),
        "caveat": "粗暴刷量会留下几十人的大批次，但**聪明的刷量会刻意压小批次**。"
                  "所以「批次小」不能作为清白证据，只能说「不是粗暴刷量」。",
    },
    {
        "key": "subscriber_ratio_pct",
        "no": 5, "name": "订阅者/星数比",
        "what": "subscribers_count ÷ stargazers_count",
        "how": "repos.subscribers / repos.stars（★ 不是 watchers_count，那个恒等于 star 数）",
        "unit": "%", "higher_is_worse": False,
        "extract": lambda m: m["subscriber"].get("ratio_pct"),
        "caveat": "刷星服务通常不刷 Watch（成本高、不直观），所以该比值被稀释。"
                  "但**大仓库天然偏低**（star 数增长远快于订阅数），"
                  "所以必须与**同量级**对照比，跨量级比较会得出假阳性。"
                  "这是本框架里成本最低、判别力最强的一条，但也最依赖对照质量。",
    },
    {
        "key": "fork_star_pct",
        "no": 7, "name": "Fork / Star 比",
        "what": "fork 数 ÷ star 数",
        "how": "repos.forks / repos.stars",
        "unit": "%", "higher_is_worse": True,
        "extract": lambda m: m["subscriber"].get("fork_star_pct"),
        "caveat": "高 fork 比既可能是刷量，也可能是「工具型项目的正常用法」"
                  "（用户 fork 来改成自己的配置，openclaw 这类工具正是如此）。"
                  "要结合「fork 后是否有提交」才能区分，而那需要全量 fork 数据。",
    },
    {
        "key": "contributor_gini",
        "no": 8, "name": "贡献集中度（基尼）",
        "what": "提交数分布的基尼系数",
        "how": "对 contributions 降序序列算基尼",
        "unit": "", "higher_is_worse": True,
        "extract": lambda m: m["contributors"].get("gini"),
        "caveat": "★ 单人主导**不是**造假信号 —— 个人项目天然如此。"
                  "它反映开发模式（个人 vs 公司），必须同口径比较才有意义。",
    },
    {
        "key": "contributor_single_commit_pct",
        "no": 8, "name": "单次提交贡献者占比",
        "what": "只贡献过 1 次的人占全部贡献者的比例",
        "how": "count(contributions==1) / n",
        "unit": "%", "higher_is_worse": True,
        "extract": lambda m: m["contributors"].get("single_commit_pct"),
        "caveat": "高比例既可能是「刷提交量」，也可能是**完全健康的**"
                  "「修一个 typo 就走的过路贡献者」。知名项目的这个比例"
                  "普遍很高，务必对照。",
    },
    {
        "key": "contributor_anon_pct",
        "no": 8, "name": "匿名提交者占比",
        "what": "没有关联 GitHub 账号的提交者（按邮箱归并）占比",
        "how": "count(login IS NULL) / n",
        "unit": "%", "higher_is_worse": True,
        "extract": lambda m: m["contributors"].get("anon_pct"),
        "caveat": "匿名提交是**正常且常见**的（本地 git 没配 user.email 就会这样），"
                  "尤其是长期维护者用多个邮箱提交。这条信号的判别力弱。",
    },
    {
        "key": "repo_age_days",
        "no": 9, "name": "仓库年龄",
        "what": "建仓至今的天数",
        "how": "now − repos.created_at",
        "unit": "天", "higher_is_worse": False,
        "extract": lambda m: m["subscriber"].get("age_days"),
        "caveat": "这不是造假信号，是**归一化用的分母** —— "
                  "「328 天拿到 39 万 star」和「十年拿到 19 万 star」"
                  "是完全不同的两件事，报告里所有增速指标都要除以它。",
    },
]


# ══ 执行 ════════════════════════════════════════════════
def evaluate_all(conn, target: str, only: Optional[list] = None) -> list:
    """跑全部信号，返回结果列表（已含对照标定）。"""
    from crawler.config import resolve_canonical
    canon = resolve_canonical(conn, target)
    out = []
    for spec in SIGNALS:
        if only and spec["key"] not in only:
            continue
        r = _evaluate_one(conn, canon, spec)
        out.append(r)
    return out


def _evaluate_one(conn, canon: str, spec: dict) -> dict:
    extract = spec["extract"]
    try:
        cal = B.compare(conn, extract, canon,
                        higher_is_worse=spec["higher_is_worse"])
    except Exception as e:
        cal = {"verdict": "insufficient_data", "lift": None, "control_n": 0,
               "control_p50": None, "control_p95": None, "value": None,
               "error": f"{type(e).__name__}: {e}", "controls": {}}

    return {
        "key": spec["key"], "no": spec["no"], "name": spec["name"],
        "what": spec["what"], "how": spec["how"], "unit": spec["unit"],
        "higher_is_worse": spec["higher_is_worse"], "caveat": spec["caveat"],
        "value": cal.get("value"),
        "control_p50": cal.get("control_p50"),
        "control_p95": cal.get("control_p95"),
        "control_n": cal.get("control_n", 0),
        "lift": cal.get("lift"),
        "z": cal.get("z"),
        "verdict": cal.get("verdict", "insufficient_data"),
        "controls": cal.get("controls", {}),
        "error": cal.get("error"),
    }


def store(conn, results: list, target: str) -> int:
    """把信号结果写进 `signals` 表（报告与后续追踪共用）。"""
    from crawler.config import resolve_canonical, utcnow_iso
    from crawler.storage import tx
    canon = resolve_canonical(conn, target)
    now = utcnow_iso()
    n = 0
    with tx(conn):
        for r in results:
            conn.execute("""
                INSERT INTO signals (signal, canonical_full_name, value, unit,
                    control_p50, control_p95, lift, z, verdict, detail_json, computed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(signal, canonical_full_name) DO UPDATE SET
                  value=excluded.value, unit=excluded.unit,
                  control_p50=excluded.control_p50, control_p95=excluded.control_p95,
                  lift=excluded.lift, z=excluded.z, verdict=excluded.verdict,
                  detail_json=excluded.detail_json, computed_at=excluded.computed_at
            """, (r["key"], canon, r["value"], r["unit"], r["control_p50"],
                  r["control_p95"], r["lift"], r["z"], r["verdict"],
                  _detail(r), now))
            n += 1
    return n


def _detail(r: dict) -> str:
    import json
    return json.dumps({
        "name": r["name"], "what": r["what"], "how": r["how"],
        "caveat": r["caveat"], "controls": r["controls"],
        "control_n": r["control_n"], "higher_is_worse": r["higher_is_worse"],
    }, ensure_ascii=False)


# ══ 汇总评分 ════════════════════════════════════════════
# 档位权重。`uncalibrated` / `insufficient_data` **不计分** ——
# 把「没测到」算成「没问题」是本项目最不能犯的错误。
_WEIGHT = {"normal": 0, "watch": 1, "suspicious": 3, "strong": 5}


def score(results: list) -> dict:
    """把所有信号压成一个可读的汇总。

    ★ 刻意**不**输出单一「造假概率」数字。原因：
      各信号之间不独立（峰值集中度和 Top-7 集中度就是同一现象的两种度量），
      把它们加权求和会假装出一个不存在的精度。
      这里给的是「有几个信号亮了」的分档统计。
    """
    tally = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    scored = [r for r in results if r["verdict"] in _WEIGHT]
    pts = sum(_WEIGHT[r["verdict"]] for r in scored)
    mx = sum(_WEIGHT["strong"] for _ in scored) or 1
    return {
        "tally": tally,
        "scored_n": len(scored),
        "points": pts, "max_points": mx,
        "ratio_pct": pts / mx * 100 if scored else None,
        "uncalibrated": tally.get("uncalibrated", 0),
        "insufficient": tally.get("insufficient_data", 0),
        "flags": [r["name"] for r in results
                  if r["verdict"] in ("suspicious", "strong")],
        "watches": [r["name"] for r in results if r["verdict"] == "watch"],
    }
