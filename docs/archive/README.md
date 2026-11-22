> ⚠️ **归档文档，且含已废弃做法**
>
> 本文件提到的 `GITHUB_TOKEN` / PAT 用法**本项目从未使用、也不会使用** ——
> 项目承诺**匿名只读、零 token、不登录**（见 [表述边界](../research/表述边界与免责.md)）。
> 此处保留仅为记录早期探索；`ghp_xxx` 是占位符，不含任何真实凭据。

# GitHub 仓库造假研究 — 数据采集与分析系统

> 面向 GitHub 刷 star / 刷 fork 现象的公开数据采集、画像分析与造假信号检测管线。
> 匿名只读为主（无 token 也能跑核心分析），配额感知 + 断点续爬 + 代理节点池可选。
> 样本案例：`openclaw/openclaw`（392,298★，三轮信号优化后画像 = "一人+AI 作者驱动型"）·
> `morluto/rea`（37.8 万★/194 天，**强异常**）· `666ghj/BettaFish`（4.3 万★，初判有机）+ 7 对照仓。
> 数据源与利用方法总览见 [`数据采集方案.md`](数据采集方案.md) §七。
>
> 环境：Windows 11 · Python 3.12 · 无 MongoDB / 无 Redis（SQLite + JSONL 存储）。
> ⚠️ **本项目只输出「异常信号」，不下「造假结论」**；所有判据须有对照组基线。

**文档索引**

> 📘 **主文档：[`项目总纲.md`](../history/项目总纲.md)** —— 2026-11-09 合并了下列 9 份文档的最新内容，
> 含方法论、采集单元、指标、已验证/已证伪结论、当前进度。**先读这一份。**
> 下列分册保留作历史归档，内容可能与总纲不一致。

| 文档 | 内容 |
|---|---|
| [`项目总纲.md`](../history/项目总纲.md) | ⭐ **唯一权威版本**：合并全部内容 + 最新结论 |
| [`数据采集方案.md`](数据采集方案.md) | 归档：通道实测、信号↔数据映射、配额预算 |
| [`账户采集方案.md`](账户采集方案.md) | 归档：12 个端点字段级指南 |
| [`账户分析方案.md`](账户分析方案.md) | 归档：字段 → 指标 → 两轴分层 |
| [`账户数据结构.md`](账户数据结构.md) | 归档：数据模型（11 个块） |
| [`采集单元详解.md`](采集单元详解.md) | 归档：D1–D14 逐字段字典 |
| [`用户名编码谱系.md`](用户名编码谱系.md) | 归档：命名类型学 |
| [`rea_论证方案.md`](rea_论证方案.md) | 归档：两仓对照方案 |
| [`用户真人评分设计.md`](用户真人评分设计.md) | 归档：S5 阶段评分框架 |

---

## 目录

| 模块 | 职责 |
|------|------|
| [`account_manager/`](account_manager/) | GitHub 凭据：**只认当前环境变量**（不保存凭据文件）、配额跟踪、冷却恢复 |
| [`crawler/`](crawler/) | 八类数据源采集 + 韧性传输 + 断点续爬 |
| [`analysis/`](analysis/) | 指标计算、造假信号检测、对照组对比、报告生成 |
| `config/` | `targets.json`（目标/对照组/采样参数，随仓库）+ `settings.json`（本机代理连接，gitignored） |
| `data/` | 采集数据（gitignored）：`raw/*.jsonl` 原始归档 + `git_repo_fake.db` + `out/`（报告与图表）+ `state/`（配额状态，**不含凭据**） |
| 根目录 `_*.py` | 独立采集器/抽样器/报告器（forker 全量画像、用户画像档位、S5 分层抽样等，各自可单跑） |
| `_small_repo_collect.py` + `_repo_report.py` | **小仓库端到端闭环**：fork 少的仓几小时内完成「fork 全量 → owner 画像全量 → 单仓完整报告」（`py _repo_report.py --repo owner/repo`） |
| 根目录一次性探针 | `_star_probe` / `_detail_probe` / `_repo_detail` / `_token_check` / `_token_endpoints` / `_test_partial_week` / `_user_pilot` —— **研究过程的验证脚本**（通道可用性实测、端点权限、token 通道死因等），保留作留档，日常分析用不到。<br>`_reconcile_live` 依赖私有 `crawler` 包，已从开源版移除（可从 git 历史找回） |

---

## 快速开始

```bash
# 1. 装依赖
pip install -r requirements.txt

# 2. （可选）在**当前 shell** 里导出令牌 —— 没有也能跑，只是跳过重阶段
#    PowerShell:  $env:GITHUB_TOKEN = "ghp_xxx"
#    cmd:         set GITHUB_TOKEN=ghp_xxx
#    bash:        export GITHUB_TOKEN=ghp_xxx

# 3. 探测凭据（不联网）
py -m account_manager.cli probe

# 4. 冒烟测试（秒级，拿仓库元数据 + star 曲线）
py -m crawler.run --repo openclaw/openclaw --stage meta,history

# 5. 全量采集
py -m crawler.run --repo openclaw/openclaw --stage all

# 6. 出报告
py -m analysis.report --repo openclaw/openclaw
```

### （可选）代理节点池 —— 大批量匿名采集的加速档

匿名配额按 IP 计（60 req/h/IP）。配了本地 HTTP 代理
外部控制后，采集器可以在配额见底时自动切换策略组节点换出口 IP（实测 ~900-1,600 req/h，
共享机场出口配额不可预期，轮换是常态）：

```bash
# 复制模板并填写 proxy 段（host / username / password）
copy config\settings.example.json config\settings.json

# 之后所有 _*.py 采集器自动读取；也可用环境变量覆盖：
#   PROXY_API_URL / PROXY_SECRET / PROXY_GROUP / PROXY_PROXY_PORT
```

> ⚠️ 代理轮换分摊匿名限流**默认不启用也不鼓励**——本项目历史上仅两次例外使用（均为
> 项目负责人决策并在 README 决策记录中留档）。是否使用、合规边界由使用者自负，
> 参见「合规与边界」。切策略组会带动本机走该组的全部流量，注意与其他用途互斥。

> ⚠️ **凭据只认当前环境变量，不读任何保存的令牌文件。**
> 没有配置时，需要凭据的阶段（`forks` / `commits` / `issues` / `users` 等）
> 会被**跳过并告警**，只跑 `meta` / `history` / `ch` / `sh` —— 不转匿名硬跑。
> 理由见 `account_manager/README.md`。

> ⚠️ Windows 下 PATH 里的 `python` 是 Microsoft Store 存根（静默失败、退出码 49），
> 一律用 `py` 启动器。

---

## 数据源矩阵

| 需求 | 数据源 | 状态 | 规模（openclaw） |
|------|--------|------|------|
| 仓库元数据 | `GET /repos/{o}/{r}` | ✅ 匿名 | — |
| **star 曲线（逐日）** | `GET /repos/{o}/{r}/stargazers/history` | ✅ 匿名 · **分页，47 桶 = 全生命周期** | 329 天 / 392,302 |
| **star 曲线（长跨度）** | star-history.com `api/svg` | ✅ 反解 SVG path | 20 点 |
| **star 身份名单** | ❌ **通道已死（2026-06-30 政策）**：`/stargazers` `/subscribers` 收归 admin/collaborator-only——匿名 401、**任何 token 404/403**（样本账号 token 已实测验证）、UI 全站 404 | — |
| **star 身份（历史）** | ClickHouse `play.clickhouse.com` · `github_events` | ✅ 免凭证 · **事实上的唯一身份通道** | **106,834 事件 / 105,696 账号**（另含 rea/BettaFish/photocraft/5 对照样本） |
| **单账号 star 名单** | `/users/{login}/starred` | ✅ 匿名（token 后仍开放）——person→repos 方向，行为宽度校准用 | 逐人抽样 |
| fork 名单 | `GET /repos/{o}/{r}/forks` | ✅ 全公开 · **API 无 100 页上限**（实测到第 822 页）· **不可撤回**（fork 不能转私有，见下） | 82,583（可列出 82,174） |
| 贡献者 | `/contributors` | ✅ **分页无上限** | **2,898** |
| 提交历史 | `/commits` | ✅ | 112,682（contributors 口径） |
| issue / PR / 评论 | `/issues` `/pulls` `/issues/comments` | ✅ | 9,366 open |
| 用户画像 | `/users/{login}`（+ events/repos/starred/orgs 按档位组合） | ✅ | forker 全量 D1 采集中（82k，本机代理路线）；档位采集器已实现（4–10 req/人） |
| 生态趋势榜 | star-history.com 首页 | ✅ | 20 仓库 |

> ⚠️ `/stargazers/history` **不是** 30 周滚动窗口 —— `?per_page` / `?weeks` / `?since`
> **全部被忽略**（静默返回第 1 页），只有 `?page=N` 生效。翻到底即全生命周期。
> 另：`total` 字段是**当周新增**而非累计值。详见 `crawler/README.md`。

### 仓库改名史（决定 star 身份如何拼接）

| 仓库名 | star 数 | 时间窗 |
|--------|---------|--------|
| `clawdbot/clawdbot` | 19,371 | 2026-01-04 → 01-27 |
| `moltbot/moltbot` | 13,576 | 2026-01-27 → 01-30 |
| `openclaw/openclaw` | 73,887 | 2026-01-30 → 至今 |
| **合计** | **106,834** | vs 当前 392,302 |

---

## 请求配额与令牌（实测 2026-10-24）

GitHub REST API 的配额有**两个维度**，混淆它们会直接算错工期：

| 请求方式 | 配额 | 计数单位 | 含义 |
|---|---:|---|---|
| 匿名 | **60 次/小时** | **IP** | 换出口 IP = 换一份配额 |
| 认证（带 token） | **5,000 次/小时** | **账号** | 换 IP 无效；同账号的多个 token **共享**这一份 |

> 判断当前请求走哪一档，看响应头 `X-RateLimit-Limit` 即可：`60` = 匿名，`5000` = 认证。
> 这是比任何口头声明都硬的证据。

### token 是什么

**在请求头里带 `Authorization: Bearer <token>`** 即视为认证请求。token 是 GitHub API 凭据，与 cookie / 账号密码 / git 凭据都不是一回事：

| 类型 | 前缀 | 配额 |
|---|---|---:|
| classic PAT | `ghp_` | 5,000/h |
| fine-grained PAT | `github_pat_` | 5,000/h |
| OAuth / App 令牌 | `gho_` / `ghu_` / `ghs_` | 5,000/h（GitHub App 按安装级另算） |

要点：

1. **配额与 token 类型无关** —— classic 与 fine-grained 都是 5,000/h。
2. **配额按账号计，不按 token 计** —— 同一账号签发多个 token，共用同一个 5,000/h 池。
3. **公开端点不需要任何权限** —— 零作用域的 fine-grained PAT 也能拿到 5,000/h；token 只负责「标识账号」，不决定能读什么数据。
4. **二级限流独立存在** —— 约 1 次/秒的软约束，与小时配额叠加。
5. **账号被风控降级时，token 有效也拿不到 5,000/h** —— 实测某样本账号两个 token 均有效，但认证后 `X-RateLimit-Limit: 60`（账号级限制）。

常用响应头与端点：

| 名称 | 作用 |
|---|---|
| `X-RateLimit-Limit` | 本档配额上限（60 / 5000）——判断匿名还是认证 |
| `X-RateLimit-Remaining` | 当前窗口剩余次数 |
| `X-RateLimit-Reset` | 窗口重置的 Unix 时间戳（固定小时窗口，非滑动） |
| `X-OAuth-Scopes` | classic token 的作用域（fine-grained 不返回，`None`） |
| `github-authentication-token-expiration` | fine-grained token 到期时间 |
| `GET /rate_limit` | 查询配额，**本身不消耗配额** |

### 配额怎么算工期

```
请求数    = ceil(条目数 / per_page)
匿名耗时  = 请求数 / 60            小时
认证耗时  ≈ 请求数 / 5000          小时   （下限还受 ≈1 次/秒 二级限流约束）
```

### 本仓库实测案例：fork 名单

| 项目 | 实测值 |
|---|---:|
| `forks_count` | **82,583** |
| API 可列出 | **82,174**（差 409 条 = 已删除/不可列出，任何翻页方式都拿不到） |
| `per_page=100` 页数 | **822 页**（末页 74 条；第 823 页为空） |
| API 深翻上限 | **无 100 页限制**（第 800 页仍满 100 条） |
| HTML 网页版 | ~100 页封顶（每页 50 条 ≈ 5,000 条），**不能**用于全量（用户侧观察；本次 `github.com` 被抑制，未复验） |
| 匿名耗时 | 822 / 60 ≈ **13.7 小时** |
| 认证耗时 | 822 次 ≈ **14 分钟**（受二级限流约束） |

采集器：`py _forker_probe.py --stage forks-all` —— `sort=oldest` 保证长跑期间页码不漂移，
游标落 `data/raw/probe_forks_all.page`，中断可续；配额耗尽睡到重置，
curl_cffi 全档位被抑制时自动切 `requests` 直连兜底。

带 `--proxy` 则走快代理（KDL）私密代理出口：`py _forker_probe.py --stage forks-all --proxy`，
每批 5 个出口、每 25 页轮换、1.2s/页节流，822 页 ≈ 20–25 分钟。凭据读
`data/proxy_kdl.json`（gitignored），代理版与直连版共用同一游标和 JSONL，可随时互切。

> **代理路线的决策记录（2026-10-24）**：匿名直连 13.7 小时 vs 任何 GitHub 账号都不动用 ——
> 项目负责人选择了自有商业代理分摊出口配额。量级很小（822 次只读 GET，约 20 个出口 IP），
> 不涉及账号安全，风险由决策者自担。此路线**仅限本次 fork 名单**，不推广为项目默认打法；
> 账号路线（多账号池分摊）依旧禁止 —— 那会牵连真实账号。
>
> **代理路线第二例（2026-10-31）**：forker 主页全量画像（82,006 人 × 1 req D1）改走**本地
> 本机代理订阅节点池**（用户决策）。与 KDL 例同性质：匿名只读、不涉账号、零新增凭据（已有订阅）。
> 实现 = `_forker_profiles_all.py`（配额驱动轮换：读业务响应自身的 `X-RateLimit-Remaining`，
> ≤3 即 PUT 换节点 ~0.2s；节点池含熔断冷却；实测 ~930 req/h，全量 ETA 2–5 天，
> 断点续采随时可停可续）。连接参数在 `config/settings.json`（gitignored，
> 模板见 `config/settings.example.json`）。同样不推广为项目默认打法。

### 本项目的凭据纪律

- 凭据**只从当前环境变量读**（`GITHUB_TOKEN`），不读任何保存的 token 文件；
- 没有配置时，需要认证的阶段**跳过并告警**，不转匿名硬跑；
- **禁止**多账号池分摊配额 —— 会触发账号关联封禁（含主账号）；
- 代理轮换多 IP 分摊匿名限流：**默认禁止**，本项目仅两次例外（① 2026-10-24 fork 名单 822 页
  走 KDL；② 2026-10-31 forker 主页 82k 走本地 本机代理节点池——均为用户决策，决策记录见上方
  「请求配额与令牌」）—— 不作为可复用的常规手段；
- 匿名采集产物（`data/raw/probe_forks_all.jsonl` 等）不含任何凭据。

---

## ⚠️ 数据边界声明（必读）

**star 身份只能拿到 26.9%（105,696 / 392,302）。** GitHub 自 **2026-06-30** 起把 `/stargazers`
与 `/subscribers` 收归为仅管理员/协作者可见（changelog「Upcoming access restrictions to public
API endpoints and UI views」）：匿名 401，**带任何 token（含零作用域有效 PAT）一律 404/403**，
UI /stargazers 页全站 404——**token 路线已实测终审死亡**（2026-10-25 样本账号 token 排除法验证，
`数据采集方案.md` §二）。唯一可行的替代是 ClickHouse 的 `github_events` 表（+ `/users/{login}/starred`
做逐人校准），但它是**时间衰减抽样**，不是完整数据。

逐月覆盖率（CH 事件数 ÷ API 真实星数）：

| 月份 | 覆盖率 | 可用 | | 月份 | 覆盖率 | 可用 |
|------|-------:|:----:|---|------|-------:|:----:|
| 2026-01 | 33.8% | ✅ | | 2026-06 | 3.2% | ❌ |
| 2026-02 | 33.2% | ✅ | | 2026-07 | 0.8% | ❌ |
| 2026-03 | 22.3% | ✅ | | 2026-08 | 0.8% | ❌ |
| 2026-04 | 18.3% | ✅ | | 2026-09 | 10.5% | ✅ |
| 2026-05 | 10.5% | ✅ | | 2026-10 | 2.0% | ❌ |

**两条铁律**：

1. **绝不用 ClickHouse 做绝对计数** —— 只能做「样本内相对比例」。
2. **覆盖率 < 5% 的月份不出结论**（`MIN_USABLE_COVERAGE`）。

结论：身份级指标（同秒碰撞、批次分布）实际只反映 **2026 年 1–6 月**的样本特征，
**不能外推到全周期**。缺口 ~285k 无法从任何免费源补齐。
**报告的方法论章节必须声明此边界**，不能把「样本内的分布」当作「全体的分布」来下结论。
（2026-10-25 更新：9 月覆盖恢复中、10 月新事件已可查——rea 单月 5,923 actor，
CH 增量重查任务 #11 待批；1–6 月的结论不受影响。）

> ★ 这份数据必须**采集时立刻归档** —— CH 是时间衰减抽样，明天再查就是**另一个样本**。
> 归档在 `data/raw/ch_star_events/`（9.87 MB）+ `ch_star_seconds/`（4.98 MB）。

### fork 名单为什么能「拿全」——项目的地基（2026-11-05 确认）

这不是采集技巧，是 GitHub 的产品规则：**fork 的可见性只由上游决定，fork 主人没有开关**。

- **不能闭源**：公开仓的 fork 永远公开，设置页直接拒绝——「For security reasons, you cannot
  change the visibility of a fork.」；反向（私有仓 fork 转公开）同样禁止。官方原文：
  「A fork's visibility depends on the upstream repository… **You cannot change the visibility of a
  fork by itself.**」(<https://docs.github.com/en/pull-requests/reference/forks#visibility-of-forks>)
- **上游也收不走它**：上游转私有 → 公开 fork 留在**独立网络**继续公开；上游被删 → 活跃 fork
  **接任新 upstream**，其余 fork 仍在。
- ⇒ **fork 名单不可撤回**。这是它相对 star 名单的根本优势：star 名单通道已被 GitHub 关掉
  （2026-06-30 admin-only），而 fork 一旦发生就永久可列——**没有「事后洗白」这条路**。
- **唯一的消失方式是删除**（fork 或整个账号）。所以 `forks_count` 与可列出条数的差额**只能**是
  删除，**不存在**「被转成私有所以看不见」这个替代解释（openclaw 的 409 条即此类）。
- **漏采方向是低估**：想留私人副本的人用 *Duplicate repository*（独立仓库，**不是** fork）或
  下载 ZIP 重建——都不进 `/forks`。名单只会漏「没按 fork 按钮的人」，不会误收「已私有化的人」。
- **fork 操作本身也是公开的**，三处可交叉验证：`/forks` 名单、forker 自己的仓库列表、其
  public events 流的 `ForkEvent`。
- ⚠️ 检索陷阱：GitHub **搜索默认不返回 fork**，必须加 `fork:true` / `fork:only`
  （官方搜索文档明示）。所以「搜不到」≠「不存在」——**名单一律以 `/forks` API 为准**，
  不要拿网页搜索结果当覆盖率口径。

### 案例档案：数字登录名的「单动作」forker（2026-11-05 实测）

`morluto/rea` 的 forks 列表从第 97 页起大批出现**纯数字登录名**，条目一律是
`Created 2–3 weeks ago / Never updated / 0 0 0 0`。抽 5 个走公开 API 核实（本机代理节点池，只读 D1）：

| 样本 | 注册 | 显示名 | 公开仓 | 关注 | 登录名解码 | 除 fork 外的公开痕迹 |
|---|---:|---|---:|---|---:|---|
| D1 | 2020-10 | 空 | **1**（唯一仓 = rea 的 fork） | 0/0 | 2024-12-03 | 无（90 天事件仅 1 条 `ForkEvent`） |
| D2 | 2018-12 | 空 | 184 | 6/57 | 2026-02-25 | 未查 |
| D3 | 2021-02 | `lml` | 34 | 0/0 | 2026-06-20 | 未查 |
| D4 | 2021-05 | 中文名 | 1 | 0/0 | 2026-09-23 | 未查 |
| D5 | 2019-05 | 中文名 | 6 | 0/0 | 2024-06-22 | 未查 |

**三条结论：**

1. **登录名 ≠ 注册时间**：5/5 对不上（差 2–7 年）。所以「用户名带时间戳 = 注册时间戳」不成立；
   它更可能是**改名时间**（账号被处置/转手的时间点）。GitHub 不公开改名史，只能靠分布统计推断。
2. **号龄在本样本里完全失效**：5 个全是 2018–2021 的老号，按「账号年龄」打分它们全部属于
   高置信真人——这是 [`用户真人评分设计.md`](用户真人评分设计.md#L13) 里「高分 ≠ 清白」的第一批实证样本。
3. **真正能测的是形态与比例**：抽到的 25 条连续条目里，10 位「1.7×10⁹」（Unix 秒形态）**9 个（36%）**、
   11 位数字（中国手机号形态）**6 个（24%）**、短数字 6 个、词+数字 4 个。对照同一口径：
   openclaw 全量 82,006 个 forker 的 `TS%` = **0.22%（177 人）**、`PHONE%` = **0.11%（94 人）**；
   storytold/craft 246 人 `TS%` = `PHONE%` = 0。**差距 160–200 倍**（n=25 的单页观察，需全量验证）。

**由此得到 4 条零请求指标（纯字符串计算）：**

| ID | 指标 | 定义 | 用途 |
|---|---|---|---|
| `TS%` | 时间戳形态率 | 最长数字串 = 10 位且 `10⁹ ≤ v < 2×10⁹` 的 forker 占比 | 群体比例 |
| `PHONE%` | 手机号形态率 | 最长数字串 = 11 位且首位 `1`、次位 `3–9` | 群体比例（国内批量注册常见） |
| `TS-dist` | 解码年份分布 | 上述样本按年分桶，与对照仓比 | openclaw 纯数字样本解码落在 2001–2015（n=81）；rea 待测 |
| `TS-cluster` | 批次聚集 | 同仓 forker 的解码时间戳**互相间隔 <60 秒**的账号对数 | 脚本循环硬指纹（openclaw：0 对） |

> ⚠️ **「能解码成时间戳」本身零信息量**——任何 10 位、首位为 1 的数字都落在 2001–2033。
> 判别力只来自**解码时间的分布**（`TS-dist`）与**与 `created_at`/`updated_at` 的对齐**。
> ⚠️ rea 的 fork 名单（94,367 条 / 944 页）**尚未采集**，上面是单页观察，还不是总体比例；
> 采完后 `TS%` 才能和 openclaw 的 0.10% 并排比较。
> 🔒 纪律：案例账号不在报告里点名；精确 login 存 `data/raw/named_anchors.txt`（gitignored）。

---

## 环境约束（实测，直接决定架构）

| 约束 | 实测结果 | 架构后果 |
|------|----------|----------|
| MongoDB / Redis | **均未运行** | SQLite + JSONL；断点续爬用 SQLite `checkpoints` 表 |
| api.github.com 网络 | **间歇性连接重置，且按 TLS 指纹选择性抑制**——抑制窗口内 `impersonate="chrome"` 0/5 失败、`"firefox133"` 5/5 通过；窗口外全通。**抑制只针对该主机**：同一时刻 ClickHouse 67 秒拉完 10.5 万行 | HTTP 层**指纹轮换**（失败即换 profile）+ 抑制窗口**按主机**记账 |
| 已装包 | matplotlib / networkx / requests / curl_cffi / playwright / pymongo / redis；**无 pandas** | 分析用 stdlib + matplotlib |

---

## 造假信号检测框架（2026-10-25 三轮优化后修订）

> 完整版（含每个信号的依赖数据、参照系与利用方法）见
> [`数据采集方案.md`](数据采集方案.md) §三、§七。

| # | 信号 | 状态 | 异常判据（对照组标定） |
|---|------|------|------------------------|
| 1 | **star 行为宽度**（单-star 率/p50 宽度） | ✅ **最强（新）** | rea 42.8% 单-star vs ollama 15.1% / BettaFish 12.9%；宽度窗须与选样窗解耦 |
| 2 | **单脉冲 vs 持续增长** | ✅ 第一眼分类器 | rea：15 天 37.7 万★ 后归零（交付即止）；openclaw 11 个月不衰减 |
| 3 | **issue/千★** | ✅ 便宜的人群代理 | rea 8.5 / BettaFish 10.4 vs openclaw 155 / ollama 62 / n8n 51 |
| 4 | 时间聚集（同秒批次 + Poisson 校正） | ✅（基线待扩） | 本样本 13.6× 富集、max 批次 4 → 分散式小批次；新对照 dify/browser-use 加入后需重算 |
| 5 | 账号年龄@star | ✅ 待 S5 采样 | 看中位数与 **<30 天占比**；rea 单-star 池 = 老号新号混合 |
| 6 | 共星网络 | 🔄 待跑 | 反复共同出现的账号簇（networkx 社区检测） |
| 7 | fork 结构 | ◐ 弱 | fork/★ 被 n8n（29%）抵消，区分度弱 |
| 8 | 贡献者集中度 | ◐ 弱 | openclaw 61% = 作者驱动型可解释（非独有） |
| 9 | 改名/迁移史 | ⚠️ 声明项 | storytold/rea→morluto/rea 等；干扰统计，不定罪 |
| 10 | 生态协同 | ✅ | 同 owner 多仓同日建+同期起飞（storytold 5 仓 09-30 同建） |
| — | ~~watch/★ 比~~ | ❌ **死**（基线伪影） | 建仓年代效应：2023+ 仓一律 0.4-0.6%，browser-use 比 openclaw 还低 |
| — | ~~夜间提交占比~~ | ❌ **死**（混杂） | 70% push = 作者本人（AI 对话开发节奏）；须作者级归因后才可用 |
| — | ~~CONTRIBUTOR 占比高=刷~~ | ❌ **反转** | 实检 25 人：15 个 >2 年老号 = 真实深度社区 |

> **措辞纪律**：报告区分「**异常**」与「**造假**」。所有判据必须有对照组基线，
> 不可仅凭单指标下结论。

---

## 采集顺序（按分析价值排序，全程可中断）

1. 仓库元数据 + `/stargazers/history` — 秒级
2. ClickHouse star 身份导出 — 15 秒拿 10.7 万条
3. star-history.com 曲线 + 趋势榜
4. fork 名单 — 82,174 条 / 822 页（匿名 ≈13.7 小时；`py _forker_probe.py --stage forks-all` 可断点续跑）
5. fork owner 主页画像全量 — 82,006 人 × 1 req（D1 档，本机代理节点池路线 ~930 req/h，
   `py _forker_profiles_all.py` 后台长跑，断点续采）
6. 用户画像深挖（D2–D8 按档位组合）— `_profile_tiers.py`：minimal 4 req/人 ·
   standard 5–7 · full 6–10；D1 自动复用全量采集缓存不重复花钱
7. 贡献者 / 提交历史 — 113k
8. issue / PR / 评论

> 详见 `crawler/README.md` —— 含韧性传输层、三层失败分离、
> TLS 指纹轮换、以及 `/stargazers/history` 与 `/contributors` 的几个反直觉行为。

## 用户画像分层采样

| 群体 | 规模 | 采样 | 理由 |
|------|------|------|------|
| **P0** 同秒批量里的全部账号 | ~2,600 | **全采** | 最可疑，必须有精确画像 |
| **P1** 归档首现距点星 < 30 天的账号 | ~8,000 | **全采** | 新号点星 |
| **P2** 爆发周点星者的 10% | ~11,600 | 抽样 | 爆发窗口代表性样本 |
| **P3** 全体 5%（`bucket(login, 20) == 0`） | ~5,300 | 抽样 | 整体分布的无偏估计 |
| 贡献者 | 2,898 | **全采** | 协作结构分析主体 |
| issue / PR / 评论作者 | 数千 | **全采** | 「参与讨论的账户」维度 |
| fork owner（D1 档） | 82,006 | **全采中** | 总体号龄/粉丝分布（`_forker_profiles_all.py`，本机代理路线） |
| **合计** | — | **~27,500 深挖 + 82k D1** | D1 全量后，S5 深挖档位（4–10 req/人）只需对可疑子群跑 |

抽样**必须确定性**：用 `cityHash64(actor_login) % N = 0`（`users.bucket()`），
**不要用裸 `LIMIT n`** —— ClickHouse 并行扫描下 LIMIT 结果不确定，无法复现。
采样名单落 `user_sample` 表，可复现。

S5 名单生成器 `_s5_sample.py` 的确定性抽样用 `md5(login)` 排序取前 N；
公开锚点（owner/名人对照）写在脚本里，**私有锚点**（如实证小号等涉及
第三方定性的名单）放 `data/raw/named_anchors.txt`（gitignored，每行一个
login，`#` 注释），存在则自动并入 P4 层——**不入公开仓库**。

> ⚠️ `run.py` 目前**没有** `--sample full` 开关；全量扩样需另行实现
> （当前实现只到分层采样这一档）。

---

## 分析层

指标计算、14 条信号检测、对照组标定、报告生成 —— 详见
`analysis/README.md`。

```bash
py -m analysis.report                    # 出报告 → data/out/
py -m analysis.report --no-charts
py -m analysis.metrics --key subscriber  # 人工复算报告里的数字
```

> **判定档位**：`normal` / `watch` / `suspicious` / `strong` /
> `uncalibrated`（对照组 < 2 个，**没有结论**）/ `insufficient_data`（**没有数据**）。
> 后两者**都不等于「正常」** —— 这是造假分析里最危险的一种混淆。
>
> 报告**刻意不输出单一「造假概率」**：各信号之间并不独立，加权求和会假装出
> 一个不存在的精度。

---

## 合规与边界

- ✅ **只用公开 API 采集公开数据**
- ✅ 凭据来自**当前环境变量**，由使用者显式导出，关掉终端即失效
- ✅ 配额跟踪用于**冷却恢复**，**不是**为了突破配额
- ❌ **不保存凭据** —— 不写 `tokens.json`，不落盘，不进日志/DB/归档
- ❌ **禁止**多账号池分摊配额 —— 属规避速率限制，会触发账号关联封禁（含主账号）
- ❌ 代理轮换多 IP 规避匿名限流：**默认禁止**；本项目历史仅两次例外使用（2026-10-24 fork 名单
  822 页、2026-10-31 forker 主页 82k，均为项目负责人决策并在「请求配额与令牌」留档），
  不推广为常规手段，是否使用由使用者自担
- ❌ **禁止**登录非本人持有的账号
- 疑似小号样本（使用者自备）**只采公开画像，不登录**；名单属对第三方账号的定性，
  **不入公开仓库**（`config/targets.json` 的 `suspicious_samples.logins` 留空交付，本地自填）

## 免责声明

- 本项目是**研究性开源工具**，输出的是「异常信号」与「对照组基线对比」，**不是**法律意义的
  造假认定。报告措辞纪律：区分「异常」与「造假」，个体账号不点名定性，聚合统计为主。
- 仓库/账号的「强异常」等结论基于采集时点的公开数据快照与明确声明的数据边界
  （如 star 身份仅 ~27% 可得、ClickHouse 为衰减抽样）——引用本项目结论时须一并引用其边界声明。
- 使用者须遵守 GitHub 服务条款、目标站点 robots 与当地法律；采集行为的风控/封禁风险由使用者自负。

## 许可证

[MIT](LICENSE) © 2026 git_repo_fake contributors。**不含** `data/` 下的采集数据与
`config/settings.json`（本机连接配置，均已 gitignore）。
