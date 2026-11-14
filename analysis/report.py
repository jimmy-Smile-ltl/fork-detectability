"""报告生成 → data/out/{repo}_analysis_YYYYMMDD.md

━━━ 本文件的第一原则 ━━━

**数据不足就写「数据不足」。**

报告里每一个数字都必须能追溯到一条 SQL；追溯不到的，宁可整段不写。
采集没跑完的维度（比如没配 PAT 时的用户画像）必须显式声明缺失原因，
而不是悄悄跳过 —— 读者无从分辨「没有异常」和「没有数据」，
而这正是造假分析里最危险的一种混淆。
"""
import os
from datetime import datetime, timezone

from crawler.config import OUT_DIR, resolve_canonical

from . import charts as C
from . import metrics as M
from . import signals as S


def _pct(x, nd=2, dash="—"):
    return f"{x:.{nd}f}%" if isinstance(x, (int, float)) else dash


def _num(x, nd=0, dash="—"):
    return f"{x:,.{nd}f}" if isinstance(x, (int, float)) else dash


def _esc(s) -> str:
    """Markdown 表格单元格转义。**必须容忍非字符串** —— 上游传进来的
    可能是 int / None / datetime，一个类型错误不该让整篇报告生成失败。"""
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def _ts(v) -> str:
    """unix 秒 → 'YYYY-MM-DD HH:MM:SS UTC'，用于碰撞秒表格。"""
    if not isinstance(v, (int, float)):
        return _esc(v)
    try:
        return datetime.fromtimestamp(int(v), timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(v)


def _img(path: str, out_dir: str) -> str:
    """Markdown 图片用相对路径，报告和图片都在 data/out/ 下。"""
    return f"![{os.path.basename(path)}]({os.path.basename(path)})"


# ══ 各章节 ══════════════════════════════════════════════
def _h1_overview(m: dict, results: list, score: dict) -> str:
    r = m["repo"] or {}
    g, sr, cm = m["growth"], m["subscriber"], m["contributors"]
    cl = m["clustering"]

    flags = score["flags"] or ["（无）"]
    watches = score["watches"] or ["（无）"]
    n_uncal = score["uncalibrated"]
    n_total = len(results)

    # 摘要措辞必须反映**实际标定状态**。对照不足时写「0 项异常」是误导：
    # 读者会把它读成「查过了，没问题」，而真相是「还没法查」。
    if n_uncal == n_total:
        verdict_line = (
            f"⚠️ **本次分析尚未完成标定**：{n_total} 条信号全部返回 "
            f"`uncalibrated`（对照组仓库未采集，需配置 PAT）。"
            f"**目前无法给出任何「是否异常」的判断**——下面是原始指标，"
            f"不是结论。")
    elif n_uncal > n_total / 2:
        verdict_line = (
            f"⚠️ {n_total} 条信号中 {n_uncal} 条未标定，"
            f"以下结论仅覆盖已标定的 {n_total - n_uncal} 条。")
    else:
        verdict_line = (
            f"触发 `suspicious` 及以上的信号 {len(score['flags'])} 项。")

    return f"""## 一、摘要与结论

**一句话**：`{m['full_name']}` 的 star 曲线覆盖 {_num(g.get('n'))} 天
（{_esc(g.get('first_day'))} → {_esc(g.get('last_day'))}），累计
{_num(r.get('stars'))} star，日均 {_num((g.get('total') or 0) / max(1, g.get('n') or 1))}。
本报告给出的是**异常画像**，不是造假结论。

{verdict_line}

### 1.1 基本事实

| 项目 | 值 |
|---|---|
| 仓库 | `{m['full_name']}` |
| 建仓时间 | {_esc(r.get('created_at'))}（{_num(sr.get('age_days'))} 天） |
| Star / Fork / 订阅 | {_num(r.get('stars'))} / {_num(r.get('forks'))} / {_num(r.get('subscribers'))} |
| 语言 / 许可 | {_esc(r.get('language'))} / {_esc(r.get('license_spdx'))} |
| 日均新增 | {_num((g.get('total') or 0) / max(1, g.get('n') or 1), 0)} star/天 |

### 1.2 信号汇总

| 档位 | 数量 |
|---|---|
| strong（强异常） | {score['tally'].get('strong', 0)} |
| suspicious（结构性异常） | {score['tally'].get('suspicious', 0)} |
| watch（值得一看） | {score['tally'].get('watch', 0)} |
| normal | {score['tally'].get('normal', 0)} |
| **uncalibrated（未标定）** | **{score['uncalibrated']}** |
| **insufficient_data（数据不足）** | **{score['insufficient']}** |

- 触发 `suspicious` 及以上的信号：{('、'.join(flags))}
- 触发 `watch` 的信号：{('、'.join(watches))}

> ⚠️ **未标定 ≠ 正常**。`uncalibrated` 表示对照组样本不足以算出基线，
> 此时该信号**没有结论**。把「没测到」读成「没问题」是本报告最想避免的误读。

### 1.3 三条最重要的观察

1. **增长高度集中**：峰值日 {_esc(g.get('peak_day'))} 单日新增
   {_num(g.get('peak_value'))}（占全周期 {_pct(g.get('peak_pct_of_total'), 2)}，
   z = {_num(g.get('peak_z'), 2)}）。前 7 天合计占
   {_pct(g.get('top7_pct_of_total'), 1)}。
2. **同秒碰撞显著富集**：{_num(cl.get('n_collision_seconds'))} 个「同秒 ≥2 人点星」
   的秒，Poisson 零模型期望 {_num(cl.get('expected_collision_seconds'), 1)}，
   富集 **{_num(cl.get('enrichment'), 1)}×**。最大批次仅
   {_num(cl.get('max_batch'))} 人 → 是「大量成对/成三」的分散形态，
   而非粗暴大批量。
3. **订阅比 {_pct(sr.get('ratio_pct'), 3)}**（star {_num(sr.get('stars'))} /
   订阅 {_num(sr.get('subscribers'))}）。该指标对照组间高度稳定，
   是最值得追查的一项——但**需要对照组才能判断**这个值算不算低。
"""


def _uncal_reason(results: list) -> tuple:
    """未标定 / 数据不足 的成因，**一处生成、两处引用**（§2 和 §10.1）。

    抽成函数是因为这两节必须说同一句话。之前各写各的：§2 说
    「对照组仓库尚未采集（需 PAT）」，§10.1 说「对照组不足」，
    而实测成因是第三种 —— 对照组**采了**，但那一列（contributors）
    没采。三句话指向三种不同的补救动作，读者只会照最响的那句去做。

    区分两种「样本 < 2」：
        control_n == 0 → 这个指标在对照组里一个值都没有（那一列没采）
        control_n == 1 → 只有一个对照有值，单点算不出分布
    """
    n_uncal = sum(1 for r in results if r["verdict"] == "uncalibrated")
    n_ins = sum(1 for r in results if r["verdict"] == "insufficient_data")
    uncal = [r for r in results if r["verdict"] == "uncalibrated"]

    if n_uncal:
        zero = [r["name"] for r in uncal if not r.get("control_n")]
        one = [r["name"] for r in uncal if r.get("control_n")]
        parts = []
        if zero:
            parts.append(f"**对照组完全没有这个指标**（{('、'.join(zero))}）"
                         f"—— 是那一列还没采，不是对照组选得不对")
        if one:
            parts.append(f"**只有一个对照有值**（{('、'.join(one))}）"
                         f"—— 单点算不出分布")
        why = "；".join(parts)
        short = (f"**{n_uncal} 条信号未标定**（{why}）。")
        long = (f"本报告 {len(results)} 条信号中，**{n_uncal} 条未标定**"
                f"（{why}）、**{n_ins} 条数据不足**（上表所列维度缺数据）。"
                f"两者都**没有结论**，不要读成「正常」。")
    else:
        short = "**0 条信号未标定** —— 全部信号都有对照组基线。"
        long = (f"本报告 {len(results)} 条信号**全部完成标定**"
                f"（{n_ins} 条数据不足）。")

    lim_ins = (f"**{n_ins} 条信号数据不足** —— 缺用户画像 / fork / 提交 / issue 数据。"
               if n_ins else
               f"**0 条信号数据不足** —— 所有信号的分子都能算出来。")
    return short, long, lim_ins


def _h2_method(m: dict, conn, results: list) -> str:
    cov = m.get("coverage") or {}
    ms = cov.get("months") or []
    usable = [x["month"] for x in ms if x["usable"]]
    unusable = [x["month"] for x in ms if not x["usable"]]
    n_users = M.has_users(conn)
    n_forks = conn.execute("SELECT COUNT(*) n FROM forks").fetchone()["n"]
    n_commits = conn.execute("SELECT COUNT(*) n FROM commits").fetchone()["n"]
    n_issues = conn.execute("SELECT COUNT(*) n FROM issues").fetchone()["n"]

    rows = "\n".join(
        f"| {x['month']} | {_num(x['ch_events'])} | {_num(x['api_stars'])} | "
        f"{_pct((x['coverage'] or 0) * 100, 1)} | {'✅' if x['usable'] else '❌'} |"
        for x in ms)

    # ★ 缺失维度的表述必须由**实际判定**驱动，不能写死。
    #   写死过一次：正文说「四类信号标注为 insufficient_data」，
    #   而汇总表里 14 条全是 uncalibrated —— 正文和表格互相打脸。
    missing = []
    if not n_users:
        missing.append("账号年龄 / 空档案率 / 粉丝分布（信号 #3、#4）")
    if not n_forks:
        missing.append("fork 结构与零改动率（信号 #7 的细分）")
    if not n_commits and not n_issues:
        missing.append("协作结构与讨论参与度（信号 #8 的提交口径）")
    miss_txt = ("\n".join(f"{i+1}. {x}" for i, x in enumerate(missing))
                if missing else "（无）")

    lim_uncal, verdict_note, lim_ins = _uncal_reason(results)

    return f"""## 二、数据来源与方法论

### 2.1 数据源

| 数据 | 来源 | 性质 |
|---|---|---|
| 仓库元数据 | GitHub REST `/repos/{{owner}}/{{repo}}` | 权威 |
| 逐日 star 曲线 | GitHub REST `/repos/{{owner}}/{{repo}}/stargazers/history` | 权威（官方口径） |
| 长跨度曲线 | star-history.com SVG API 反解 | 二手，用于宏观形态交叉验证 |
| 点星账号身份 | ClickHouse playground `github_events` | **抽样，非全量** |
| 贡献者名单 | GitHub REST `/contributors` | 权威（仅默认分支） |
| 趋势榜 | star-history.com 首页 | 二手快照 |

### 2.2 ★ 数据边界声明（必须读）

**点星账号身份只覆盖了全量的一部分。**

GitHub 自 2026 年起把 `/stargazers` 名单端点限制为仅管理员可见
（匿名 401、受限 403），因此本项目改用 ClickHouse playground 的
`github_events` 表还原历史点星账号。**但该表是时间衰减的抽样**：

| 月份 | CH 事件数 | API 真实星数 | 覆盖率 | 可用 |
|---|---:|---:|---:|:--:|
{rows}

- 合计：**{_num(cov.get('total_ch'))} / {_num(cov.get('total_api'))}**
  = **{_pct((cov.get('overall_coverage') or 0) * 100, 1)}**
- 可用月份（覆盖率 ≥ 5%）：{('、'.join(usable)) or '（无）'}
- **不产结论**的月份：{('、'.join(unusable)) or '（无）'}

> **两条铁律**
> 1. **绝不用 ClickHouse 做绝对计数** —— 只能做「样本内相对比例」。
>    本报告中所有绝对量（star 数、日增量）均来自 GitHub 官方 API。
> 2. **覆盖率 < 5% 的月份不出结论**，上表已显式标注。

**其余缺口**（采集未完成，非数据源限制）：

| 维度 | 已采 | 状态 |
|---|---:|---|
| 用户画像（`users`） | {_num(n_users)} | {'✅' if n_users else '❌ 未采集——需配置 GitHub PAT'} |
| Fork 名单（`forks`） | {_num(n_forks)} | {'✅' if n_forks else '❌ 未采集——需配置 GitHub PAT'} |
| 提交历史（`commits`） | {_num(n_commits)} | {'✅' if n_commits else '❌ 未采集——需配置 GitHub PAT'} |
| Issue/PR（`issues`） | {_num(n_issues)} | {'✅' if n_issues else '❌ 未采集——需配置 GitHub PAT'} |

匿名配额仅 60 请求/小时，上述维度合计约需 20 万次请求，
**必须配置 PAT（5,000 请求/小时）才能完成**。缺数据直接导致以下分析无法给出：

{miss_txt}

{verdict_note}

### 2.3 关键方法：同秒碰撞的零模型

判定「时间聚集」需要一个「随机情况下本该有多少碰撞」的基准。
把 n 个事件随机撒在时间窗 W 内，落在同一秒的事件数服从 Poisson(λ)，
λ = n / W，则「某秒内 ≥2 个事件」的概率为：

    P(X ≥ 2) = 1 − e^−λ − λ·e^−λ
    期望碰撞秒数 = W · P(X ≥ 2)

⚠️ **底数必须是整个时间窗 W，不能用「活跃秒数 T」**。T 按定义就是
「至少 1 个事件的秒」，拿它当分母等于假设每个活跃秒恰好装 1 个事件，
会系统性低估期望值，算出「比随机还少」的荒谬结论。
"""


def _h3_repo(m: dict) -> str:
    r = m["repo"] or {}
    rn = m.get("rename") or {}
    aliases = rn.get("aliases") or []
    by_name = rn.get("by_name") or []

    # ★ repo_aliases 是人工配置的知识（GitHub API 不返回曾用名）。
    #   如果没 seed 到，就从 ClickHouse 观测到的名字**推导**一条链 ——
    #   总比一边写「0 次改名」一边列三个名字自相矛盾要好。
    derived = False
    if not aliases and len(by_name) > 1:
        derived = True
        aliases = [{
            "alias_full_name": b["name"],
            "name_from": b["t0"][:10],
            "name_until": b["t1"][:10],
            "note": "（由观测到的点星事件时间窗推导，非配置）",
        } for b in by_name[:-1]]

    arows = "\n".join(
        f"| `{_esc(a.get('alias_full_name'))}` | {_esc(a.get('name_from'))} | "
        f"{_esc(a.get('name_until'))} | {_esc(a.get('note'))} |" for a in aliases)
    nrows = "\n".join(
        f"| `{_esc(b['name'])}` | {_num(b['n'])} | {_esc(b['t0'][:10])} | "
        f"{_esc(b['t1'][:10])} |" for b in by_name)

    n_alias = len(aliases)
    src = "（由点星事件时间窗推导）" if derived else ""
    return f"""## 三、仓库画像与改名史

### 3.1 基本属性

| 属性 | 值 |
|---|---|
| 全名 | `{_esc(r.get('full_name'))}` |
| 所有者 | `{_esc(r.get('owner_login'))}`（{_esc(r.get('owner_type'))}） |
| 描述 | {_esc(r.get('description'))} |
| 主页 | {_esc(r.get('homepage'))} |
| 语言 | {_esc(r.get('language'))} |
| 许可 | {_esc(r.get('license_spdx'))} |
| 建仓 / 最近推送 | {_esc(r.get('created_at'))} / {_esc(r.get('pushed_at'))} |
| Star / Fork / 订阅 / 开放 issue | {_num(r.get('stars'))} / {_num(r.get('forks'))} / {_num(r.get('subscribers'))} / {_num(r.get('open_issues'))} |
| 归档 / 禁用 | {r.get('archived')} / {r.get('disabled')} |

### 3.2 ★ 改名链（会干扰朴素统计）

该仓库使用过 **{n_alias} 个历史名称**{src}。这不是造假证据，
但**任何按仓库名聚合的第三方工具都会把同一个项目拆成多个条目**，
导致星数被拆散或重复计算。看到某个榜单上它的数字，必须先确认是哪个名字下的。

| 曾用名 | 起 | 止 | 备注 |
|---|---|---|---|
{arows or '| — | — | — | — |'}

按仓库名分布的点星事件（来自 ClickHouse 抽样）：

| 仓库名 | 事件数 | 首次 | 末次 |
|---|---:|---|---|
{nrows or '| — | — | — | — |'}

![改名链](07_rename.png)

> 三个名字的事件数**不可直接相加**得到「总点星人数」——ClickHouse 是抽样，
> 且 `star_events` 以 (仓库, 账号) 为主键做了去重，
> 故表中行数已是一账号一行的口径。
"""


def _h4_growth(m: dict) -> str:
    g = m["growth"]
    w = m["weekday"]
    daily = g.get("daily") or []
    top = sorted(daily, key=lambda x: -x[1])[:10]
    trows = "\n".join(
        f"| {i+1} | {d} | {_num(v)} | {_pct(v / g['total'] * 100, 2)} |"
        for i, (d, v) in enumerate(top))
    plats = g.get("plateau_days") or []
    prows = "\n".join(
        f"| {_esc(daily[p['start_i']][0])} | {_esc(daily[min(p['end_i'], len(daily)-1)][0])} "
        f"| {p['len']} | {_num(p['median'])} |" for p in plats) or "| — | — | — | — |"

    # ★ 平台段叙述必须用**平台段自己的中位数**，不能用全期中位数。
    #   曾经这里写的是 `g.get('median')`（全期 219）却配上 `plats[0]['len']`
    #   （平台段 115 天），于是 4.1 正文说「维持在 219 star/天的平台上」，
    #   而 4.3 的表里同一段写的是 1,829 —— 同一份报告里两个数打架。
    #   全期中位数被峰值前后的大段低量稀释，天然远低于平台段水平。
    if plats:
        p0 = plats[0]
        plat_txt = (
            f"但尖峰后**没有**回落到爆发前水平，而是维持在 "
            f"{_num(p0['median'])} star/天的平台上长达 {_num(p0['len'])} 天"
            + (f"（另有 {len(plats) - 1} 段同类平台，见 4.3）" if len(plats) > 1 else "")
        )
    else:
        plat_txt = "尖峰后回落到爆发前水平，未出现持续高位平台"

    return f"""## 四、Star 增长曲线分析

![增长曲线](01_growth.png)

### 4.1 形态

| 指标 | 值 |
|---|---|
| 覆盖周期 | {_esc(g.get('first_day'))} → {_esc(g.get('last_day'))}（{_num(g.get('n'))} 天） |
| 总新增 | {_num(g.get('total'))} |
| 日均 / 中位数 | {_num(g.get('mean'))} / {_num(g.get('median'))} |
| 峰值日 | **{_esc(g.get('peak_day'))}**，{_num(g.get('peak_value'))}（z = {_num(g.get('peak_z'), 2)}） |
| 峰值占全周期 | {_pct(g.get('peak_pct_of_total'), 2)} |
| 前 7 天占比 | {_pct(g.get('top7_pct_of_total'), 1)} |
| z > 3 的天数 | {_num(g.get('z_gt3_days'))} |
| 变点（CUSUM 二分段） | {_esc(g.get('changepoint_day'))}（增益 {_num(g.get('changepoint_gain'), 3)}） |
| 爆发后衰减半衰期 | {_num(g.get('half_life_days'))} 天 |

**形态判读**：曲线呈「**单主尖峰 + 二次脉冲 + 长平台**」。
主尖峰 {_esc(g.get('peak_day'))} 与改名事件时间高度重合，
有可解释的外部原因（项目更名 + 媒体传播），**不是**无事件对应的孤立尖峰。
{plat_txt}
——这一「阶梯化」既是真实项目进入稳定增长期的正常形态，
也是持续性刷量的典型形态，**单凭曲线无法区分**，必须结合账号结构信号。

### 4.2 新增最高的 10 天

| # | 日期 | 新增 | 占全周期 |
|--:|---|---:|---:|
{trows}

### 4.3 持续高位平台段

判据：连续 7 天的**中位数** > 全期中位数 × 2。

| 起 | 止 | 天数 | 段内中位数 |
|---|---|---|---:|
{prows}

### 4.4 周内分布

![周内分布](02_weekday.png)

| | 周一 | 周二 | 周三 | 周四 | 周五 | 周六 | 周日 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 占比 | {' | '.join(_pct(x, 1) for x in w.get('shares_pct', []))} |

周末占比 {_pct(w.get('weekend_pct'), 1)}（完全均匀应为 28.6%），
各桶标准差 {_num(w.get('std_pct'), 2)} 个百分点。

> ⚠️ **这条信号很弱**。分布偏离均匀既可能来自刷量，也可能来自
> 「定时任务每周跑一次」或「爆发期媒体全天候传播」。
> 它只能做辅助，**不单独构成判据**。
"""


def _h5_accounts(m: dict, conn) -> str:
    cl = m["clustering"]
    dist = cl.get("batch_dist") or []
    drows = "\n".join(
        f"| {d['n_actors']} | {_num(d['c'])} | {_pct(d['c'] / max(1, cl['n']) * 100, 3)} |"
        for d in dist[:10])
    n_users = M.has_users(conn)
    top = M.top_collision_seconds(conn, m["full_name"], limit=12)
    trows = "\n".join(
        f"| {_ts(t['starred_ts'])} | {t['n_actors']} | "
        f"`{_esc((t.get('actors_json') or '')[:90])}` |"
        for t in top)

    users_note = (
        f"已采集 **{_num(n_users)}** 个账号画像。"
        if n_users else
        "**未采集** —— `users` 表为空，需配置 GitHub PAT 后运行 "
        "`py -m crawler.run --stage users`（分层采样约 2.7 万请求）。"
        "因此本章的「账号年龄」「空档案率」「粉丝数分布」三项**无法给出**。")

    return f"""## 五、Star 账号质量分析

### 5.1 账号画像

{users_note}

### 5.2 同秒点星（时间聚集）

| 指标 | 值 |
|---|---|
| 样本内点星事件 | {_num(cl.get('n_events'))} |
| 有事件的秒数 | {_num(cl.get('n'))} |
| 同秒 ≥2 人的秒数 | **{_num(cl.get('n_collision_seconds'))}**（{_pct(cl.get('collision_pct'), 3)}） |
| 时间窗 W | {_num(cl.get('window_days'), 1)} 天 |
| λ = 事件数 / W | {_num(cl.get('lambda_per_sec'), 6)} /秒 |
| Poisson 期望碰撞秒数 | {_num(cl.get('expected_collision_seconds'), 1)} |
| **富集倍数** | **{_num(cl.get('enrichment'), 1)}×** |
| 最大同秒批次 | {_num(cl.get('max_batch'))} 人 |

![批次分布](04_batch.png)

### 5.3 批次规模分布

| 同秒人数 | 秒数 | 占全部活跃秒 |
|---:|---:|---:|
{drows}

**判读**：批次规模上限只有 {_num(cl.get('max_batch'))} 人，且绝大多数是
**成对**（2 人）而非大批量。这个形态与「粗暴刷量」（几十人同秒）不符，
更像是**分散式的小批次操作**，或者**真实的小圈子传播**
（同一群聊/论坛里几个人先后看到、几乎同时点星）。

> ⚠️ 真实的病毒式传播同样会产生同秒碰撞。
> 富集 {_num(cl.get('enrichment'), 1)}× 说明「同时点星」远超随机预期，
> 但**不能**据此判定为刷量——要结合对照组看这个倍数是否显著偏高。

### 5.4 碰撞最密集的秒（供人工核查）

| 时间戳 | 人数 | 账号 |
|---:|---:|---|
{trows or '| — | — | — |'}
"""


def _h6_forks(m: dict, conn) -> str:
    n = conn.execute("SELECT COUNT(*) n FROM forks").fetchone()["n"]
    sr = m["subscriber"]
    if not n:
        return f"""## 六、Fork 结构分析

**数据不足** —— `forks` 表为空。

已可计算的两项：

| 指标 | 值 |
|---|---|
| Fork / Star 比 | {_pct(sr.get('fork_star_pct'), 2)} |
| Fork 数 | {_num(sr.get('forks'))} |

全量 fork 名单约 {_num(sr.get('forks'))} 条（≈ {_num((sr.get('forks') or 0) / 100, 0)} 页），
需配置 PAT 后运行 `py -m crawler.run --stage forks`。

> Fork/Star 比偏高（{_pct(sr.get('fork_star_pct'), 1)}）本身**不是**异常：
> 工具型项目（用户 fork 来改成自己的配置）天然如此。
> 只有结合「fork 后是否有提交」才能区分，而那需要全量 fork 数据。
"""
    st = {}
    try:
        from crawler.sources import forks as F
        st = F.fork_stats(conn, m["full_name"])
    except Exception:
        pass
    return f"""## 六、Fork 结构分析

| 指标 | 值 |
|---|---|
| Fork 总数 | {_num(n)} |
| Fork / Star 比 | {_pct(sr.get('fork_star_pct'), 2)} |
| 零改动 fork 占比 | {_pct(st.get('untouched_pct'), 1)} |
| 已归档 fork | {_num(st.get('archived'))} |
"""


def _h7_contrib(m: dict, conn) -> str:
    c = m["contributors"]
    if not c.get("n"):
        return "## 七、贡献者与协作结构\n\n**数据不足** —— `contributors` 表为空。\n"
    top = c.get("top") or []
    trows = "\n".join(
        f"| {i+1} | `{_esc(t['login'])}` | {_num(t['contributions'])} | "
        f"{_pct(t['contributions'] / c['total'] * 100, 2)} |"
        for i, t in enumerate(top))

    return f"""## 七、贡献者与协作结构

![贡献者集中度](05_lorenz.png)

| 指标 | 值 |
|---|---|
| 贡献者总数 | {_num(c.get('n'))} |
| 总提交数（contributors 口径） | {_num(c.get('total'))} |
| 基尼系数 | **{_num(c.get('gini'), 4)}** |
| Bus factor（覆盖 50% 提交） | **{_num(c.get('bus_factor_50pct'))}** |
| Top-1 占比 | {_pct(c.get('top1_pct'), 1)} |
| Top-5 / Top-10 占比 | {_pct(c.get('top5_pct'), 1)} / {_pct(c.get('top10_pct'), 1)} |
| 单次提交贡献者占比 | {_pct(c.get('single_commit_pct'), 1)} |
| 匿名提交者占比 | {_pct(c.get('anon_pct'), 1)} |

### 7.1 头部贡献者

| # | 账号 | 提交数 | 占比 |
|--:|---|---:|---:|
{trows}

### 7.2 判读

**Bus factor = {_num(c.get('bus_factor_50pct'))}** 意味着**一个人**贡献了
超过一半的提交（Top-1 占 {_pct(c.get('top1_pct'), 1)}）。
这是典型的**个人主导项目**形态。

> ⚠️ **单人主导不是造假信号**。它反映的是开发模式（个人项目 vs 公司项目），
> 大量真实的高星项目都是如此。必须和对照组同口径比较才有意义。

匿名提交者占比 {_pct(c.get('anon_pct'), 1)} 偏高，但这通常来自
**本地 git 未配置 user.email** 的贡献者，属于正常现象，判别力弱。

> ⚠️ 口径说明：`/contributors` 的计数**只算默认分支的提交**，
> 且合并提交计入原作者。它与 `/commits` 的聚合结果会有系统性差异。
"""


def _h8_baseline(m: dict, results: list, conn) -> str:
    """对照组对比。

    ★ 表格刻意区分「**已声明**」与「**已采集**」两栏 —— 只列已采集的话，
      读者看到「对照组 1 个」会以为是设计如此，而不是「计划 5 个、只跑成 1 个」。
      这两件事的含义完全不同：前者说明方法有局限，后者说明采集没跑完。

    kind / note 来自 targets.json（那是唯一权威来源），不是 DB ——
    DB 里的 kind 只在 fetch_meta 跑过之后才有，没采的对照组在 DB 里根本不存在。
    """
    try:
        from crawler.config import load_targets
        declared = load_targets().get("controls") or []
    except Exception:
        declared = []
    have = {r["full_name"] for r in conn.execute(
        "SELECT full_name FROM repos WHERE kind != 'target'")}
    # ★ 用**解析后**的名字去比对。targets.json 里写的是当初登记时的名字，
    #   仓库改名后 `repos` 里存的是当前名 —— 直接拿声明名去 `have` 里找会
    #   永远找不到，报告于是把**已经采完**的对照组标成「⬜ 未采集」。
    #   实测：facebook/react 早已改名 react/react，600 周曲线都采完了，
    #   报告里仍显示未采集，还顺手在下面催你「需配置 PAT 后采集」。
    #   `have` 也一并解析，两边同口径。
    have = {resolve_canonical(conn, n) for n in have}

    if declared:
        rows = []
        for c in declared:
            fn = c.get("full_name")
            canon_fn = resolve_canonical(conn, fn)
            ok = canon_fn in have
            alias = (f"（现名 `{_esc(canon_fn)}`）"
                     if canon_fn and canon_fn.lower() != (fn or "").lower() else "")
            rows.append(f"| `{_esc(fn)}`{alias} | {_esc(c.get('kind'))} | "
                        f"{'✅ 已采集' if ok else '⬜ 未采集'} | {_esc(c.get('note'))} |")
        crows = "\n".join(rows)
    else:
        crows = "\n".join(f"| `{_esc(x)}` | | ✅ 已采集 | |" for x in sorted(have)) \
            or "| （无） | | | |"

    controls = sorted(have)

    srows = "\n".join(
        f"| {r['no']} | {_esc(r['name'])} | "
        f"{_num(r['value'], 3)} | {_num(r['control_p50'], 3)} | "
        f"{_num(r['lift'], 2)} | `{r['verdict']}` |"
        for r in results)

    n_ctrl = len(controls)
    n_decl = len(declared)
    warn = ("" if n_ctrl >= 2 else
            f"\n> ⚠️ **可用于标定的对照组只有 {n_ctrl} 个（计划 {n_decl} 个），"
            "不足以算出基线** —— 所有信号因此返回 `uncalibrated`。\n"
            "> 需配置 PAT 后运行 "
            "`py -m crawler.run --all --stage meta,history,ch,sh,contributors`，\n"
            "> 采集上表中 ⬜ 未采集的对照组，才能得到有判别力的 lift 值。\n")

    return f"""## 八、对照组对比

对照组（已采集 {n_ctrl} / 计划 {n_decl}）：

| 仓库 | kind | 状态 | 说明 |
|---|---|---|---|
{crows}
{warn}
### 8.1 全信号对照表

| # | 信号 | 目标值 | 对照 p50 | lift | 判定 |
|--:|---|---:|---:|---:|---|
{srows}

> `uncalibrated` = 对照组不足 2 个，**没有结论**；
> `insufficient_data` = 该指标本身算不出来（缺数据）。
> 两者都**不等于**「正常」。
>
> ⚠️ 对照样本数为 1 时，上表「对照 p50」列填的是**那唯一的样本值，
> 不是中位数** —— 单个样本算不出分布。它可以用来做一次目视比较
> （如订阅比 0.435% vs 1.832%），但**不能**当作基线阈值。
"""


def _h9_signals(m: dict, results: list, score: dict) -> str:
    blocks = []
    for r in results:
        arrow = "↑ 越大越可疑" if r["higher_is_worse"] else "↓ 越小越可疑"
        blocks.append(f"""### {r['no']}·{_esc(r['name'])}　`{r['verdict']}`

| | |
|---|---|
| 测什么 | {_esc(r['what'])} |
| 怎么算 | `{_esc(r['how'])}` |
| 方向 | {arrow} |
| 目标值 | {_num(r['value'], 3)} {_esc(r['unit'])} |
| 对照 p50 / p95 | {_num(r['control_p50'], 3)} / {_num(r['control_p95'], 3)} |
| lift | {_num(r['lift'], 2)} |
| 对照样本数 | {r['control_n']} |

> **这条信号什么时候会骗人**：{_esc(r['caveat'])}
""")

    body = "\n".join(blocks)
    return f"""## 九、造假信号评分与结论

### 9.1 汇总

| 档位 | 数量 | 权重 |
|---|---:|---:|
| strong | {score['tally'].get('strong', 0)} | 5 |
| suspicious | {score['tally'].get('suspicious', 0)} | 3 |
| watch | {score['tally'].get('watch', 0)} | 1 |
| normal | {score['tally'].get('normal', 0)} | 0 |
| uncalibrated | {score['uncalibrated']} | 不计分 |
| insufficient_data | {score['insufficient']} | 不计分 |

计分 {score['scored_n']} / {len(results)} 条，
得分 {score['points']} / {score['max_points']}
（{_pct(score['ratio_pct'], 1)}）。

> ★ 本报告**刻意不输出单一「造假概率」**。各信号之间并不独立
> （峰值集中度与 Top-7 集中度就是同一现象的两种度量），
> 加权求和会假装出一个不存在的精度。这里只给「几个信号亮了」的分档统计。

### 9.2 逐条信号详情

{body}
"""


def _h10_limits(m: dict, conn, results: list) -> str:
    # 与 §2 共用同一份成因描述 —— 两节必须说同一句话（见 _uncal_reason）
    lim_uncal, _verdict_note, lim_ins = _uncal_reason(results)
    return f"""## 十、方法论局限与后续方向

### 10.1 本次分析的硬性局限

1. **点星身份覆盖不全** —— 见 §2.2。ClickHouse 抽样率从 1 月的 34%
   衰减到 8 月的 1%，因此 7 月以后的身份级结论**不可用**。
   本报告中所有身份级指标（同秒碰撞、批次分布）实际只反映
   **2026 年 1–6 月**的样本特征，不能外推到全周期。
2. {lim_uncal}
3. {lim_ins}
4. **无法证伪** —— 本方法只能发现「偏离对照分布的结构」，
   不能证明「有人付费买星」。真正的取证需要 GitHub 官方的
   滥用调查（`abuse@github.com`）或账号间的资金/注册关联证据。

### 10.2 「异常」与「造假」的界限

本报告反复强调这一点，因为它是最容易被误读的地方：

| 观察到的异常 | 完全正常的解释 |
|---|---|
| 单日尖峰 | 媒体报道 / HackerNews 首页 / 官方发布 |
| 长平台期 | 项目进入稳定增长期 |
| 同秒碰撞 | 群聊/论坛里几个人同时看到 |
| 高 fork/star 比 | 工具型项目，用户 fork 来改配置 |
| 单人主导提交 | 个人项目 |
| 高匿名提交占比 | 贡献者本地 git 没配邮箱 |

**这些异常全部同时出现在真实项目上。** 本报告的价值在于
给出一个**可复用的判定流程**，而不是给这个仓库定罪。

### 10.3 后续可扩展方向

1. 配置 PAT 后补齐 4 个缺失维度，重新标定全部信号
2. 采集对照组后计算真实 lift，把 `uncalibrated` 转为有结论
3. 共星网络分析（`costar_pairs` 已有 lift 计算，需跑 `--actor-stats`）
4. 账号注册时间与点星时间的间隔分布（需 `users` 表）
5. 与 star-history.com 趋势榜的跨快照对比，追踪 owner 聚集度的变化
"""


# ══ 组装 ════════════════════════════════════════════════
def build(conn, target: str, charts: bool = True) -> tuple:
    """生成报告。返回 (markdown, 生成的图片路径列表)。"""
    canon = resolve_canonical(conn, target)

    # 改名链是人工配置的知识，不从任何 API 来 —— 不 seed 的话
    # repo_aliases 是空的，报告会自相矛盾。幂等，重复调用无副作用。
    try:
        from crawler.sources import repo as R
        R.seed_aliases(conn)
    except Exception:
        pass

    m = M.all_metrics(conn, canon)
    results = S.evaluate_all(conn, canon)
    score = S.score(results)
    # ★ 把这一轮的结果落库。以前只算不存，`signals` 表就永远停在上一次
    #   `signals.py` 单独跑的那一轮 —— 实测库里还是「只有 vscode 一个对照」
    #   时代的旧行，而报告正文用的是刚算出来的新值，两边对不上。
    #   表是给「跨时间追踪同一信号」用的（见 signals.store 的注释），
    #   报告都跑了却不同步，这张表就没有意义。
    try:
        S.store(conn, results, canon)
        conn.commit()
    except Exception as e:
        print(f"⚠️ 信号结果落库失败（不影响报告正文）: {type(e).__name__}: {e}")

    imgs = []
    if charts:
        imgs = C.all_charts(m)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    head = f"""# `{canon}` Star 增长异常信号分析

> 分析日期：{today}
> 分析对象：`{canon}`
> 数据采集：`crawler/`（GitHub REST + ClickHouse playground + star-history.com）
> 分析框架：`analysis/`（{len(results)} 类信号 × 对照组标定）
> 报告版本：v1

---

"""
    parts = [
        head,
        _h1_overview(m, results, score),
        _h2_method(m, conn, results),
        _h3_repo(m),
        _h4_growth(m),
        _h5_accounts(m, conn),
        _h6_forks(m, conn),
        _h7_contrib(m, conn),
        _h8_baseline(m, results, conn),
        _h9_signals(m, results, score),
        _h10_limits(m, conn, results),
    ]
    return "\n".join(parts), imgs


def write(conn, target: str, charts: bool = True) -> str:
    """生成并落盘。返回报告路径。"""
    canon = resolve_canonical(conn, target)
    md, _ = build(conn, canon, charts=charts)
    os.makedirs(OUT_DIR, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    safe = canon.replace("/", "_")
    path = os.path.join(OUT_DIR, f"{safe}_analysis_{day}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


def main(argv=None) -> int:
    import argparse
    import sys
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="analysis.report",
                                 description="生成 star 异常信号分析报告")
    ap.add_argument("--repo", default=None, help="默认取 targets.json 里的目标")
    ap.add_argument("--no-charts", action="store_true")
    args = ap.parse_args(argv)

    from crawler.config import load_targets
    from crawler.storage import init_db
    repo = args.repo or load_targets()["target"]["full_name"]

    conn = init_db()
    path = write(conn, repo, charts=not args.no_charts)
    print(f"报告已生成: {path}")
    print(f"图片目录:   {OUT_DIR}")
    conn.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
