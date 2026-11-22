# scripts 地图

> 仓库里共有 **59 个 `.py`：58 个功能脚本 + `common.py` 公共模块**。根 [README](../README.md) 只列最常用入口，
> 这一页回答两个问题：**我想做的事该跑哪个**、**某个脚本到底是干什么的**。

先读一条约定：**带 `_` 前缀不等于废弃**。它只是历史习惯，表示“不是对外 API”，
其中既有一次性研究脚本，也有 `_full_run.py` / `_patrol.py` 这样的正式入口。
判断以本页「定位」列为准。

---

## 30 秒：我想……

| 我想 | 跑这个 | 产物 / 说明 |
|---|---|---|
| 采一个仓的 fork 名单 | `python scripts/collect/collect_run.py --repo fatedier/frp --list-only` | `data/raw/forks_all_*.jsonl`，无需 token |
| 一人一条采全部维度 | `python scripts/collect/collect_full.py --repo fatedier/frp --workers 16` | `data/raw/accounts_full_*.jsonl`，推荐入口 |
| 跑全量三阶段 | `python scripts/collect/_full_run.py --phase api` → `hard` → `d15` | 多轮补齐，适合整批仓库 |
| 看覆盖矩阵 | `python scripts/ops/_dim_matrix.py` | 终端表格：每仓每维覆盖率 + <90% 缺口清单 |
| 算 A/B 判据 | `python scripts/analysis/_compare_full.py --tags agencyagents frp nodejsargo` | 终端输出；每个指标用自己的分母 |
| 出结论图 | `python scripts/figures/_make_ab_figure.py`<br>`python scripts/figures/_make_decay_figure.py` | `figs/21_ab_separation.png`、`figs/23_judge_decay.png` |
| 巡检 / 看进度 | `python scripts/ops/_patrol.py` | 在采什么、采到哪、数据健康不健康 |
| 看某仓还剩多少 | `python scripts/ops/_pending.py --file <名单> --target <计划数>` | 目标 − 成功 − 删号 |

---

## 数据流

```
GitHub API / HTML / RSS / CDN
        │
        │  scripts/collect/         采集（九态：OK/EMPTY/DELETED/…）
        ▼
data/raw/*.jsonl                    原始归档，gitignored
        │
        │  scripts/analysis/        指标、置信区间、缺口
        ▼
data/out/*.md | *.csv               分析产物，gitignored
        │
        │  scripts/figures/         只读 data/，写 figs/
        ▼
figs/*.png                          唯一入库的产物（18 张）
```

**三条硬规则**（详见[数据结构设计](../docs/data/数据结构设计.md)）：

1. `data/raw/` 只追加不修改：补采写新记录，分析脚本只读。
2. **判据类**分析必须按「该指标有终态的账号数」做分母，不能只用全维完整样本。
3. 图脚本不要自己算指标：能读 `data/out/` / CSV 就读；少数结论图（21–23）把终版数字固化在脚本里，改数必须同步改报告。

---

## scripts/ 根

| 文件 | 定位 | 说明 |
|---|---|---|
| `common.py` | 库 | 路径常量（`BASE/RAW/OUT/FIG`）、`iter_jsonl/load_jsonl`、`pct()`；新脚本优先用它，别再手写三重 `dirname` |

用法（scripts/ 下任意层级）：

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import RAW, OUT, iter_jsonl
```

---

## 一、collect/：采集（22 个）

### 1.1 核心库（被 import，不单独跑）

| 文件 | 作用 |
|---|---|
| `collect_core.py` | 九态 `Outcome`、终态/可重试集合、`Record`/`Writer`、`Collector`。**采集语义的唯一真源** |
| `fetchers.py` | 8 个维度的真实 fetcher：HTTP → `Outcome`，含 D14 头像字节判定、D15 徽章解析、D2 atom RSS |

### 1.2 推荐入口

| 文件 | 定位 | 何时用 |
|---|---|---|
| `collect_run.py` | 入口 | 通用 runner：取名单 → 按 `per_account` / `per_dim` 采全部维度；`--list-only` 只取名单 |
| `collect_full.py` | 入口 | 一人一条记录，按维重试；支持 `--dims`、`--sample`、`--redo`。**日常补采用这个** |
| `collect_loop.py` | 入口（长跑） | 子进程 + 硬超时，绕过线程卡死；适合无人值守的批处理 |
| `_full_run.py` | 入口（全量） | 三阶段驱动：`api`（5 维）→ `hard`（D9/D6）→ `d15`（徽章） |
| `collect_state.py` | 工具 | 账户完整性状态集合（auto/redis/sqlite）；按 login 查询哪些单元已完成 |

### 1.3 名单与输入准备

| 文件 | 定位 | 说明 |
|---|---|---|
| `_rebuild_forkers.py` | 处理 | 从名单重建全量 forker：去重、按 login 排序，结果确定 |
| `_order_forkers.py` | 处理 | 给名单排优先级——先采最可能出信号的账号，中断也不亏 |
| `_forks_direct_collect.py` | 专项 | 直连版 fork 名单：全量 + 增量（`--incremental`） |
| `_forks_kdl_collect.py` | 专项 | KDL 隧道版 fork 名单；多出口轮换，需要代理配置 |
| `_combine_dims.py` | 处理 | 旧「按维度分文件」→ `accounts_full_*.jsonl`「一人一条」 |

### 1.4 分维度专项采集

通用 runner 覆盖不到、或想省 API 配额时用：

| 文件 | 维度 | 特点 |
|---|---|---|
| `_profile_atom.py` | D2 | 读 `github.com/{login}.atom`，0 API 配额 |
| `_profile_avatar.py` | D14 | 头像 URL 字节判定，0 API 配额（走 CDN） |
| `_profile_commits.py` | D9 | 本人在自建仓的提交数（“自己写代码”的硬证据） |
| `_profile_html_all.py` | D1/D8/D15 | 一次个人主页请求取仓库数 / star / 徽章 / 名字 |
| `_profile_lists.py` | D4L/D8 | star 列表 + 自建/fork 列表，**全分页不截断** |
| `_profile_repos_html.py` | D8 | 仓库列表页 HTML 版：自建 vs fork 结构 |
| `_d15_batch.py` | D15 | 对多个仓库各随机抽 N 人，串行补徽章 |
| `_d15_fill.py` | D15 | 把各仓 D15 覆盖率补到目标值（默认 90%） |
| `_repair_loop.py` | 多维 | 反复重采不完整账号，直到完整率达标 |
| `_test_error_handling.py` | 测试 | 逐条验证 10 类失败场景（404/403/限流/坏响应…） |

---

## 二、analysis/：分析与指标（19 个）

### 2.1 常用

| 文件 | 定位 | 读什么 → 写什么 |
|---|---|---|
| `metrics_fork.py` | 入口 | fork 名单 → `data/out/fork_metrics_*.md`；0 请求、纯本地重算 |
| `_compare_full.py` | 入口 | `accounts_full_*.jsonl` → 终端 A/B 对比（参考组读 `features_*.csv`）；逐维取最优 + 每指标自己的分母 |
| `_indicators_formal.py` | 入口 | `features_*.csv` → `indicators_formal.md`：比例 + Wilson CI + RD/OR |
| `_effective_indicators.py` | 汇报 | → `effective_indicators.md` / `effective_medians.md`：12 个有效指标，全部带 X/N |
| `_fork_quality_stats.py` | 汇报 | fork 名单 + 账户质量统计，供报告引用 |
| `_audit_gaps.py` | 质检 | 逐仓逐维实测缺口，不用假设值 |

### 2.2 特征表

| 文件 | 范围 |
|---|---|
| `_build_features.py` | fork 名单 + 账户档案 → 账号级特征表 |
| `_compute_all_features.py` | 九套数据并成一人一行，70+ 特征 |
| `_compute_any.py` | 任意 tag，统一信封构建 |
| `_compute_bettafish.py` | BettaFish 专用特征表（与新采集框架同源） |

### 2.3 专项 / 阶段性分析

| 文件 | 用途 | 状态 |
|---|---|---|
| `_analyze_repo.py` | rea 单仓：身份自证 × 批量性两轴 | 阶段性 |
| `_analyze_final.py` | D1+D13+D8 合并，两仓对比 | 阶段性 |
| `_compare_three.py` | rea / openclaw / BettaFish 三仓判据对照 | 阶段性 |
| `_form_layer_metrics.py` | L4 形态层：解码登录名里的时间戳，找批次 | 专项 |
| `_internal_burst_control.py` | 仓库内部对照：爆发窗内 vs 窗外 | 专项 |
| `_data_volume.py` | 三仓全维度数据量报告 | 质检 |
| `_make_profile_demo.py` | 把一个人的 8 个维度拼成完整档案 | 文档素材 |
| `_make_samples.py` | 生成文档样例，自动脱敏 | 文档素材 |
| `_select_deep.py` | 从 screen 结果挑 deep 档账号（8 req/人） | 抽样 |

---

## 三、figures/：出图（6 个）

所有脚本只读 `data/`，写 `figs/`，使用 matplotlib `Agg`，无 GUI 依赖。

| 文件 | 产物 | 内容 |
|---|---|---|
| `_make_figures.py` | `10`–`17`、`20`（9 张） | 效应量、热力图、CDF、特征分布、形状堆叠、洛伦兹、四象限、散点、头像年龄 |
| `_make_time_control_figures.py` | `18`–`19` | 时间控制：fork 龄与爆发窗 |
| `_make_ab_figure.py` | `21_ab_separation.png` | **最终结论图**：7 条判据在 A/B 两组的分离度 |
| `_make_validation_figure.py` | `22_out_of_sample_validation.png` | 独立验证：`watchers/★` 与 Pull Shark |
| `_make_decay_figure.py` | `23_judge_decay.png` | **判据衰减曲线**：对照变干净，间距如何缩水 |
| `_plot_fork_timeline.py` | `fork_timeline_*.png`、`fork_cumulative_norm.png` | fork 增长时间轴 + 峰值日对齐的累积曲线 |

图 10–23 共 14 张编号图，加 4 张时间轴图，共 **18 张**（索引见 [figs/README](../figs/README.md)）。

---

## 四、ops/：运维与状态（11 个）

| 文件 | 定位 | 说明 |
|---|---|---|
| `_patrol.py` | 入口 | 采集巡检：在采什么 / 采到哪 / 健康不健康；`--watch N` 轮询 |
| `_dim_matrix.py` | 入口 | 全仓库 × 全维度覆盖矩阵 + <90% 缺口清单 |
| `_full_status.py` | 查看 | 所有 `accounts_full_*` 的完整度一览 |
| `_status_report.py` | 查看 | 采集状态一览（早期汇总脚本） |
| `_pending.py` | 查看 | 打印「目标 − 成功 − 删号」的待采数 |
| `_quota_check.py` | 探测 | 快代理出口的 GitHub 剩余配额（-1 = 探测失败） |
| `_test_proxy_throughput.py` | 探测 | 隧道代理 vs KDL 提取型代理的真实吞吐 |
| `_check_trending.py` | 选仓 | GitHub Trending 与候选仓比对 |
| `_screen_repos.py` | 选仓 | fork > 10k 且由个人（非组织）创建的仓库 |
| `_reorg_docs.py` | 一次性 | 重组 `docs/` 并改写 Markdown 内部链接 |
| `_rewrite_authors.py` | 一次性 | 改写提交作者/提交者（保留时间戳与提交信息） |

> `_reorg_docs.py` 与 `_rewrite_authors.py` 会改仓库本身的状态，
> 默认先跑 `--dry-run`；后者在开源仓库里通常不再需要。

---

## 五、维护约定

1. **新脚本放哪**：采集进 `collect/`，指标进 `analysis/`，图进 `figures/`，运维进 `ops/`。
2. **每个脚本必须有模块 docstring**：第一行说清“做什么”，再写用法与产物。
3. **登记到本页**：新增/移动/删除脚本时，同步改本页对应表格。
4. **通用件进 `common.py`**：路径、JSONL、百分比；业务逻辑不要放进去。
5. **不写凭据**：代理与 token 只从 `config/settings.json`（gitignored）或环境变量读。
6. **不写回 `data/raw/`**：分析脚本只读；补采只追加新记录。

---

## 已移除

| 文件 | 原因 |
|---|---|
| `analysis/_reconcile_live.py` | 依赖私有 `crawler` 包（只存在于 `archive_private/`），开源版运行即 `ImportError`；已从工作区删除，可从 git 历史找回 |
