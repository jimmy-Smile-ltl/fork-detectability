# 图表索引

> 18 张图，全部由 `scripts/figures/` 下的脚本生成。
> 每张图都标注了「回答什么问题」和「结论」，图片本身在仓库根目录的 `figs/`。

---

## 一、结论图（先看这三张）

### [21_ab_separation.png](21_ab_separation.png) — A/B 组 7 条判据的分离度 ⭐

**回答**：哪些判据真的能把两组分开？

7 个面板，每个面板一条判据。红点 = A 侧（4 仓），蓝点 = B 侧（4 仓），
标题里带 `★` 的表示两组区间零重叠。

**结论**：7 条全部零重叠，其中 4 条通过 95% 置信检验。
最强的 `Pull Shark` 间距 12.3pp。
最右一栏 `fork 后动过` 用灰色标注"重叠（不可用）"——**保留它作为反面教材**。

生成：`python scripts/figures/_make_ab_figure.py`

---

### [23_judge_decay.png](23_judge_decay.png) — 判据衰减曲线 ⭐ 最重要的图

**回答**：判据在对照变干净的过程中，是怎么变弱的？

横轴 = 对照规模（3A/2B → 4A/4B 未补齐 → 4A/4B 全量），
纵轴 = A/B 两组的间距。`0` 表示区间重叠 —— **判据当场失效**。

**结论**：

| 判据 | 3A/2B | 未补齐 | 全量 | 结局 |
|---|---:|---:|---:|---|
| Pull Shark | 32.3 | 11.1 | 12.3 | ✅ 显著 |
| 有自建仓 | 11.6 | 6.7 | 10.2 | ✅ 显著 |
| 零粉丝 | 12.0 | 0.3 | 6.8 | ✅ 显著 |
| 零提交 | 14.6 | 5.5 | 6.7 | ✅ 显著 |
| 零关注 | 12.0 | 0 | 2.8 | ❌ |
| 自传头像 | 12.9 | 0 | 2.1 | ❌ |
| 协作徽章 | 20.1 | 1.4 | 2.0 | ❌ |

**三条判据最后掉进了 3pp 以下的噪声区。**
图中标注了两个关键事件：加入 `frp` 那一刀（32.3 → 11.1），
以及补齐 D15/D6 后部分判据的回升。

生成：`python scripts/figures/_make_decay_figure.py`

---

### [22_out_of_sample_validation.png](22_out_of_sample_validation.png) — 留出验证（含一次自我否定）

**回答**：`watchers/★` 能不能当低成本筛选信号？

**结论：不能。** 左图显示 A/B 两组在 `w/★` 上完全重叠（0.96% vs 0.82%）。
新仓看似"单调"，是因为它们**就是按 `w/★` 挑的**——用预测变量选样再验证它，是循环论证。

图中斜纹柱 = 留出样本。唯一站得住的只有 `dsh` 一例
（`w/★` 与 `f/★` 两个方向同时极端 → 6 条判据全落 A 侧）。

**这张图记录的是我们撤回过的一个说法。**

生成：`python scripts/figures/_make_validation_figure.py`

---

## 二、早期分析图（研究中期产物，结论多已整合进报告）

| 图 | 回答什么问题 | 当时的结论 |
|---|---|---|
| [10_effect_ranking.png](10_effect_ranking.png) | 12 个判据谁最强？ | 前四名分属四个独立维度 |
| [11_fork_heatmap.png](11_fork_heatmap.png) | fork 在周内 × 小时上怎么分布？ | 颜色越深 = 该时段占比越高 |
| [12_star_fork_cdf.png](12_star_fork_cdf.png) | star 到 fork 的延迟分布 | rea 曲线更陡 = 动作更同步 |
| [13_feature_matrix.png](13_feature_matrix.png) | 六个关键特征的分布对比 | 小提琴 = 密度，箱 = 四分位 |
| [14_shape_stack.png](14_shape_stack.png) | 档案填充度的形状 | 两仓几乎一致 —— **填不填档案不是判据** |
| [15_lorenz.png](15_lorenz.png) | fork 的集中度 | 洛伦兹曲线 |
| [16_quadrant.png](16_quadrant.png) | 两轴（身份自证 × 批量性） | **禁止求和**，只做象限展示 |
| [17_scatter_dev.png](17_scatter_dev.png) | 开发者比例散点 | — |
| [18_time_control.png](18_time_control.png) | 做时间控制后判据还成立吗？ | 分桶后判据更稳；差异被高估约 1.5 倍但未消失 |
| [19_fork_age_dist.png](19_fork_age_dist.png) | fork 龄分布 | openclaw 97% 超过 180 天，rea 几乎都在 30 天内 |
| [20_avatar_age.png](20_avatar_age.png) | 头像类型 × 账号年龄 | 自传头像与年龄相关 |

生成：`python scripts/figures/_make_figures.py`（10–17, 20）、
`_make_time_control_figures.py`（18–19）

---

## 三、fork 时间线（三张，早期形态分析）

| 图 | 内容 |
|---|---|
| [fork_timeline_rea.png](fork_timeline_rea.png) | rea 的逐小时 fork 数 + 峰值前后 24 天放大 |
| [fork_timeline_openclaw.png](fork_timeline_openclaw.png) | openclaw 同上 |
| [fork_cumulative_norm.png](fork_cumulative_norm.png) | 以峰值日对齐的累积曲线（越陡 = 增长越集中） |
| [fork_cumulative.png](fork_cumulative.png) | 累积曲线（绝对值） |

生成：`python scripts/figures/_plot_fork_timeline.py`

> ⚠️ 时间线图属于**研究中期**的形态分析。后期加入对照后发现：
> 增长形态的异常**不能**作为判据（A/B 两侧区间重叠），
> 因为它更多反映"项目什么时候被推上热门"而非"是否刷量"。

---

## 四、怎么重新生成全部图

```bash
python scripts/figures/_make_figures.py              # 10–17, 20
python scripts/figures/_make_time_control_figures.py # 18–19
python scripts/figures/_plot_fork_timeline.py        # 时间线 3 张
python scripts/figures/_make_ab_figure.py            # 21 结论图
python scripts/figures/_make_validation_figure.py    # 22 留出验证
python scripts/figures/_make_decay_figure.py         # 23 衰减曲线
```

**注意**：前五组脚本依赖 `data/` 里的原始数据（未入库），
所以**克隆本仓库后只有 21/22/23 三张结论图能重生成**
（它们的数据来自已发布的统计口径）。其余图需先跑完采集。

---

## 五、图的编号为什么不是连续的

`10`–`23` 是历史编号，早期还生成过 `01`–`09`（已随文档归档删除）。
保留原编号是为了让旧文档里的引用不失效。
