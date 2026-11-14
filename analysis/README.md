# analysis — 特征工程 · 判据标定 · 报告生成

> **本目录回答一个问题**：拿到九套数据之后，怎么把字段变成判据、怎么防止自欺、怎么复现。
>
> **三条铁律**
> 1. **两轴不求和**——身份自证 × 批量性，禁止造"可疑度总分"
> 2. **任何跨仓比较必须做时间控制**——两仓的 fork 龄差 8 倍（§三）
> 3. **缺失 ≠ 0**——采不到就 `None`，算不出就 `insufficient_data`，绝不填 0

---

## 一、快速开始

```bash
# 全量特征计算（九套数据 → 一人一行 CSV + 两仓对比）
py _compute_all_features.py
# → data/out/features_rea.csv        108,340 人 × 63 特征
# → data/out/features_openclaw.csv    82,574 人 × 57 特征
# → data/out/features_compare.md      对比报告

# 时间轴指标（爆发窗 / 同秒碰撞 / 登录名类型学）
py metrics_fork.py --all

# 时间轴图表
py _plot_fork_timeline.py

# 两仓合并分析（D1+D13+D8）
py _analyze_final.py --compare
```

**全部纯本地计算，不再发任何请求。**

---

## 二、数据 → 特征的映射

| 数据 | 文件 | 提供的特征 | 覆盖率（rea/oc） |
|---|---|---|---|
| **fork 名单** | `forks_all_*.jsonl` | 时间戳、fork 后动过、size、分支、语言、fork 星数、owner 类型 | 100% / 100% |
| **D1 档案** | `profile_tier_screen_*` | 15 字段、账号年龄、粉丝/关注、档案形状 | 98.7% / 92.9% |
| **D13 日历** | `heat_*` | 活跃天数、贡献量、月度分布 | 99.2% / 97.1% |
| **D8H 仓库** | `repos_html_*` | 自建/fork 结构、语言、仓名 | 99.6% / 98.8% |
| **D14 头像** | `avatar_*` | identicon vs photo | 98.7% / 92.9% |
| **D15 画像** | `profile_html_*` | star 数、仓数、成就徽章、pinned | 99.6% / 98.7% |
| **D2 事件** | `atom_*` | 事件类型构成、开发事件占比 | 97.0% / 95.5% |
| **D4L star 明细** | `starlist_*` | 点星时间、star→fork 延迟 | 91.9% / 91.6% |
| **D9 提交** | `commits_*` | 自建仓提交数 | 75.0% / 52.3% |

---

## 三、⚠️ 最危险的方法学陷阱：时间混淆

**两仓的 fork 龄差 8 倍：**

| 仓库 | fork 存活天数（中位） | 主要 fork 时段 |
|---|---:|---|
| rea | **30.6 天** | 2026-10 |
| openclaw | **258.5 天** | 2026-01 ~ 03 |

**凡是"fork 之后发生的事"，都不能直接比。** openclaw 有 8 个月做后续动作，rea 只有 1 个月。

### 3.1 一个"连续错两次"的实例（完整记录）

**特征**：自建仓在 fork 之后被更新过（`own_newest_push > fork_created_at`）

| 阶段 | 做法 | 结论 | 问题 |
|---|---|---|---|
| ① 天真比较 | 全量直接比 | rea 34.4% vs oc **67.5%** = **−33.1pp** | 把时间混淆当成了真实差异 |
| ② 过度纠正 | 只取 fork 龄 >60 天 | "无差异"（72.4% vs 67.8%） | **rea 只剩 n=29**，样本陷阱 |
| ③ 正确做法 | **按 fork 龄分桶，桶内比较** | **−17 ~ −24pp** | 真实效应存在，但被高估约 1.5 倍 |

**桶内数据（图 18b）**：

| fork 龄 | rea | openclaw | 差异 |
|---|---:|---:|---:|
| 8–30 天 | 31.9%（n=35,586） | 48.7%（n=651） | **−16.8pp** |
| 31–60 天 | 36.4%（n=43,247） | 60.2%（n=832） | **−23.8pp** |
| 61–180 天 | 72.4%（n=**29**） | 65.6%（n=5,326） | +6.8pp（**样本太小，不可用**） |
| >180 天 | —（n=0） | 68.0%（n=54,478） | — |

**教训**：`>60 天` 这个切法对 rea 是空集（它的 fork 都在 60 天内），
拿 n=29 得出"无差异"和拿 n=108,340 得出 33.1pp 一样错。
**分桶比切阈值安全。**

### 3.2 控制后反而更强的实例

**fork 后动过率**，两仓都限制在 fork 龄 ≤30 天：

| 仓库 | 样本 | 动过 | 占比 |
|---|---:|---:|---:|
| rea | 49,508 | 3,147 | **6.4%** |
| openclaw | 1,055 | 393 | **37.3%** |
| **差异** | | | **−30.9pp** |

未控制时是 25.1pp，**控制后是 30.9pp**——判据更硬了。

### 3.3 已知的时间混淆特征（禁止单独使用）

| 特征 | 为什么不能用 |
|---|---|
| 自建仓 fork 后更新过 | 控制后消失（§3.1） |
| 距最后活跃天数 | 随访终点不同 |
| 近 30 条事件数 / 有 push 事件 | RSS 只给最近 30 条，rea 的 fork 还在窗口内 |
| 开发事件占比（DEV） | 同上，rea 的事件被 ForkEvent 挤占 |
| fork 存活天数 / fork 大小 | 定义或上游变化 |
| **fork_stars（fork 自己的星）** | 时间越久越可能有人点星 |

---

## 四、特征目录（按两轴四层）

### L1 档案层（身份自证）

| ID | 特征 | 公式 | rea | openclaw | 差异 | 判定 |
|---|---|---|---:|---:|---:|---|
| `P` | 档案填充度 | 6 项非空计数 | 中位 1 | 中位 1 | 0 | ❌ 无区分 |
| `SHAPE` | 档案形状 | EMPTY/TEMPLATE/PARTIAL/RICH | EMPTY 45.4%<br>RICH 12.5% | EMPTY 44.4%<br>RICH **16.5%** | RICH −4.0pp | ⚠️ 弱 |
| `NAMEMATCH` | 姓名↔登录名 | 姓名分词出现在 login 里 | 51.6% | 55.1% | −3.5pp | ⚠️ 弱 |
| `AVATAR` | 头像类型 | identicon vs photo | 50.9% | 48.7% | +2.2pp | ❌ 证伪 |
| `ACCTV` | 成就数 | 徽章个数 | 中位 0 | 中位 0 | 0 | ❌ 无区分 |
| `PULLSHARK` | 合并过 PR | 成就存在 | **30.8%** | **40.5%** | **−9.7pp** | ✅ 中强 |
| `COLLAB` | 协作提交过 | Pair Extraordinaire | **13.3%** | **23.2%** | **−9.9pp** | ✅ 中强 |
| `FOLLOW` | 粉丝/关注比 | followers/(following+1) | **0.23** | **0.48** | −0.25 | ✅ 弱中 |
| `F0` | 零粉丝 | followers==0 | **41.6%** | **28.1%** | **+13.5pp** | ✅ 强 |
| `FOL0` | 零关注 | following==0 | 36.8% | 32.5% | +4.3pp | ⚠️ 弱 |
| `EMAIL` | 公开邮箱 | has_email | 0.1% | 0.6% | −0.5pp | ❌ 基率太低 |
| `README` | 个人主页仓 | 自建仓里有 `login/login` | 待算 | 待算 | — | 🔲 |

### L2 生产层（真的干活吗）

| ID | 特征 | 公式 | rea | openclaw | 差异 | 判定 |
|---|---|---|---:|---:|---:|---|
| `OWN` | 有自建仓 | own_count>0 | 73.1% | 75.2% | −2.1pp | ❌ 证伪 |
| `OWNCODE` | 自建仓含代码语言 | 语言 ∈ 主流编程语言 | 59.9% | 62.1% | −2.3pp | ❌ 证伪 |
| `OWNCMT` | 自建仓提交数 | D9 commit_total | 中位 6 | 中位 6 | **0** | ❌ 证伪 |
| `OWNCMT0` | 自建仓零提交 | commit_total==0 | 17.0% | 18.1% | −1.1pp | ❌ 证伪 |
| `OWNRATIO` | 自建仓占比 | own/(own+fork) | 0.17 | 0.18 | −0.01 | ❌ 无区分 |
| `REPONAME` | 仓名含目标仓 | 仓名含 `rea`/`openclaw` | **9.3%** | **2.8%** | **+6.5pp** | ✅ 中弱 |
| `HEAT` | 全年活跃天 | D13 active_days | 中位 9 | 中位 12 | −3 | ✅ 弱中 |
| `CONTRIB` | 年度贡献 | D13 total_contribs | 中位 17 | 中位 26 | −9 | ✅ 弱中 |
| `LE5` | 活跃≤5天 | active_days≤5 | 38.2% | 34.2% | +4.0pp | ⚠️ 弱 |
| `ZERO` | 全年零活跃 | active_days==0 | 2.0% | 2.4% | −0.4pp | ❌ 无区分 |
| `STREAK` | 最长连续 | max_streak | 中位 2 | 中位 2 | 0 | ❌ 无区分 |
| `DEV` | 开发事件占比 | (push+PR)/events | **中位 0.00** | **中位 0.27** | **+0.27** | ✅ 强（⚠️ 时间混淆） |
| `PRMRG` | PR 合并率 | merged/created | 中位 1.00 | 中位 1.00 | 0 | ❌ 无区分 |

### L3 时间层（批量性）

| ID | 特征 | 公式 | rea | openclaw | 判定 |
|---|---|---|---:|---:|---|
| `B3h` | 爆发窗占比 | 滚动 3h > μ+2σ | **58.5%** | **39.7%** | ✅ 强（群体级） |
| `PEAKDAY` | 峰值日单日量 | max(daily) | **14,257** | 3,112 | ✅ 强 |
| `MAX3H` | 最高 3h 窗口 | 滚动最大 | **3,004** | 568 | ✅ 强 |
| `COLL` | 同秒碰撞 | 实测/期望（W 为底） | 1.02× | 0.95× | ❌ 证伪 |
| `AGE@f` | fork 时账号年龄 | fork−注册 | 中位 2,001 天 | 中位 2,110 天 | ❌ 无区分 |
| `TOUCH` | fork 后动过 | pushed>created | **8.4%**<br>（≤30d: **6.4%**） | **33.5%**<br>（≤30d: **37.3%**） | ✅ **最强** |
| `TOUCHDELAY` | fork→首次 push 延迟 | 小时 | 🔲 待算 | 🔲 | 🔲 |

### L4 形态层（批量性）

| ID | 特征 | 公式 | rea | openclaw | 判定 |
|---|---|---|---:|---:|---|
| `TS%` | 时间戳形态 | 10 位数字且 10⁹≤v<2×10⁹ | 0.56% | 0.34% | ❌ 基率太低 |
| `PHONE%` | 手机号形态 | 11 位首位 1 | 0.09% | 0.07% | ❌ 基率太低 |
| `NUM%` | 纯数字登录名 | 全数字 | 0.72% | 0.50% | ❌ 无区分 |
| `TSD` | 时间戳解码年份 | 直方图 | 跨 2001–2033 | 跨 2001–2032 | ❌ 无聚集 |
| `TSC` | 时间戳批次聚集 | 解码值互差 <60s | **1 对** | **4 对** | ❌ 证伪 |
| `BATCH` | 注册批次 | 同日注册 ≥3 人 vs 泊松期望 | **0.9×** | **0.9×** | ❌ 证伪 |
| `EVAP` | 蒸发率 | D1→404 占比 | 0.3% | 1.0% | ⚠️ 待核 |

### L5 耦合层（**本项目新增，区分力最强**）

这一层是"两个维度之间的关系"，比单维度更强。

| ID | 特征 | 公式 | rea | openclaw | 差异 | 判定 |
|---|---|---|---:|---:|---:|---|
| `SF_DELAY` | star→fork 延迟 | fork−nearest_star（小时） | 中位 **0.01** | 中位 **0.00** | — | — |
| `SF_SAMEH` | 同小时 star+fork | \|延迟\|≤1h | **45.6%** | **29.0%** | **+16.6pp** | ✅ 强 |
| `SF_BEFORE` | 先 star 后 fork | 符号判断 | **74.3%** | **57.8%** | **+16.5pp** | ✅ 强 |
| `SF_TARGET` | star 过目标仓 | star 列表含目标仓 | **43.8%** | **33.0%** | **+10.8pp** | ✅ 中强 |
| `SF_P90` | 延迟 p90（群体） | 分位 | **62.2h** | **904.1h** | **14.5×** | ✅ 强 |
| `STARBURST` | 单日最多点星 | 同日 star 计数 max | 3 | 3 | 0 | ❌ 无区分 |
| `FORKDAYSTAR` | fork 当天点星数 | 当日 star 计数 | 0 | 0 | 0 | ❌ 无区分 |

### 群体层（仓库级）

| 特征 | rea | openclaw |
|---|---:|---:|
| 累积曲线 p50 | 峰值日 **+1.4 天** | 峰值日 +10 天之后 |
| 累计占比 90% 所需 | +13.9 天 | 超出 30 天窗口 |
| fork 仓数（中位） | 10 | 13 |
| fork 语言被检测出 | **7.2%** | **20.8%** |
| fork 自己被 star > 0 | 2.12% | 3.71% |
| owner 是组织 | 0.78% | **2.95%** |

### 一个观察（不是判据）

**两仓的"注册高峰月份"都等于"fork 高峰月份"**：

| 仓库 | fork 高峰 | 注册高峰 | 2026-09 后注册占比 |
|---|---|---|---:|
| rea | 2026-10 | **2026-10（2,488 人）** | 4.00% |
| openclaw | 2026-01~02 | **2026-02（2,234 人）** | 0.53% |

这是**自然现象**：新用户发现项目 → 当场注册 → 当场 fork。
**不能当作批量证据**（两仓都有，只是月份不同）。

---

## 五、判据最终排名（时间安全版）

| 排名 | 判据 | 效应量 | 维度 | 独立性 |
|---|---|---:|---|---|
| **1** | `TOUCH` fork 后动过（≤30d 控制） | **30.9pp** | 行为后续 | — |
| **2** | `SF_SAMEH` 同小时 star+fork | **16.6pp** | 动作耦合 | 独立于 #1 |
| **3** | `SF_BEFORE` 先 star 后 fork | 16.5pp | 动作顺序 | 与 #2 相关 |
| **4** | `F0` 零粉丝 | 13.5pp | 社交图谱 | 独立 |
| **5** | `SF_TARGET` star 过目标仓 | 10.8pp | 消费行为 | 与 #2/#3 相关 |
| **6** | `COLLAB` 协作徽章 | 9.9pp | 开发行为 | 独立 |
| **7** | `PULLSHARK` 合并过 PR | 9.7pp | 开发行为 | 与 #6 相关 |
| **8** | `REPONAME` 仓名含目标仓 | 6.5pp | 命名行为 | 独立 |
| **9** | `LE5` 活跃≤5天 | 4.0pp | 综合 | 弱 |

**前四名分属四个不同维度（后续行为 / 动作耦合 / 社交图谱 / 开发行为）**，互为独立证据。

---

## 六、已证伪清单（不要再算）

| 特征 | 实测 | 问题 |
|---|---|---|
| 同秒碰撞 `COLL` | 1.02× | 局部速率校正后无富集 |
| user_id 相邻 | 0.00% | 与随机无异 |
| 默认头像 `AVATAR` | 差 2.2pp | 一半真人也用默认 |
| 无自建仓 `OWN` | 差 2.1pp | 两仓都是约 1/4 |
| 自建仓提交数 `OWNCMT` | 中位都是 6 | 完全相同 |
| 自建仓零提交 `OWNCMT0` | 差 1.1pp | 两仓都是约 18% |
| 跨仓协同 | 7.19% vs 6.71% | 无差异 |
| 登录名时间戳 `TS%` | 0.56% vs 0.34% | 基率太低 |
| 账号年龄 `AGE@f` | 2,001 vs 2,110 天 | 无区分 |
| 成就数 `ACCTV` | 中位都是 0 | 无区分 |

---

## 七、合成规则（写死，禁止加权求和）

```
第 1 步 硬证据（直接定档，不参与加权）
    PULLSHARK=1（合并过 PR）      → 生产能力：真
    OWNCMT>0（自建仓有提交）      → 生产能力：真
    HEAT 活跃≥20 天且贡献≥100     → 有持续编码行为
  命中任一 → 主体层至少「中高」

第 2 步 软证据（只看相对基线，不看绝对值）
    RICH 档案 / 零粉丝 / 活跃≤5天 / 无自建仓
  每条换算 lift = P(值|目标群体) / P(值|对照群体)，>3 才记一条"亮灯"

第 3 步 批量性轴（独立计分，不进主体层）
    TOUCH 低 / SF_SAMEH 高 / B3h 高 / TS% 高

第 4 步 输出二维
                       批量性低        批量性高
    自证高         正常开发者       ★ 买号参刷（最有价值象限）
    自证低         潜水/新号        批量小号
    无信息          ——             ——（必须单独成档）
```

**"无信息"独立成档是硬要求**：档案空白占 45%，其中大部分是真人不爱填，不能判低。

---

## 八、输出格式（每人一行 CSV）

`features_rea.csv` 的列（63 个），按来源分组：

```
身份锚点   login, user_id, fork_ts
时间轴     fork_age_days, size_kb, fork_hour, fork_weekday, branch_other, fork_touched, fork_push_delay_h
档案       acct_age_days, acct_age_at_fork_days, filled_n, shape, namematch, has_email,
           public_repos, public_gists, followers, following, follower_ratio,
           followers_zero, following_zero, user_type, login_type, login_len, login_has_digit, login_is_ts
活跃       active_days, total_contribs, max_streak, active_le5, active_zero, days_since_last,
           contrib_fork_month, contrib_month_before, contrib_month_after,
           continued_after_fork, active_before_fork, silent_before_but_active_after
仓库       own_count, fork_count, own_ratio, has_own, own_langs_n, own_has_code_lang,
           own_updated_after_fork, ownlive_ratio, ownlive_any, repo_named_target
画像       starred_count, achievement_n, has_pull_shark, has_collab, pinned_n, has_display_name
事件       events_n, ev_push, ev_star, ev_fork, ev_pr_created, ev_pr_merged,
           has_push_ev, dev_event_ratio, pr_merge_rate
star 明细  starred_target, star_fork_delay_h, star_same_hour, star_before_fork,
           stars_on_fork_day, star_max_same_day, star_active_days
提交       commit_total, repos_checked, commit_zero
```

---

## 九、添加一个新特征

1. 在 `_compute_all_features.py` 的 `compute()` 里加一行，**缺失必须留 `None`**，不要写 `0`
2. 如果是跨仓比较的特征，**先问一句：它会不会被时间混淆影响？**
   - 会 → 在报告里注明，或限制时间窗口后重算
3. 在 `MEDIAN_KEYS`（数值型）或 `RATIO_KEYS`（比例型）里注册，报告会自动出表
4. 重跑 `py _compute_all_features.py`

---

## 十、已知问题

| 问题 | 影响 | 状态 |
|---|---|---|
| `ownlive_any` = 100%（两仓） | **无效特征** | HTML 的 `updated` 字段恒有值，不是"后续 push"。需 API 的 `pushed_at` 才能算 |
| `DEV` 有时间混淆 | 只能作参考 | rea 的事件被 ForkEvent 挤占 |
| `fork_stars` 有时间混淆 | 不能单独用 | fork 越老越可能被点星 |
| D9 覆盖率 75%/52% | 提交数分布可能偏高 | 活跃者更容易采到 |
| D2 只有最近 30 条 | 不能比时间，只能比类型 | RSS 限制 |
| **泊松尾部不能用截断求和** | 会把期望缩小几十倍 | `BATCH` 曾因此算出 rea 10.8× vs oc 1.8× 的假信号；正确做法是 `1−P0−P1−P2`，修正后两仓都是 0.9× |

---

## 十一、复现纪律

1. **只读 `data/raw/*.jsonl`** 重算，不依赖在线请求
2. **每个特征都要能追溯到源文件与字段**
3. **缺失 → `None`**，聚合时按 `n=` 标注样本量
4. **比例必须带分母**（报告里每个百分比都写成 `X/N`）
5. **阈值来自对照组分位**，不拍脑袋
