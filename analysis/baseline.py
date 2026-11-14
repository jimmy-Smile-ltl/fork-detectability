"""对照组标定 —— 把「异常」从「数值大」里分离出来。

━━━ 为什么必须有这一层 ━━━

单独看一个仓库的数字毫无意义：openclaw 有 392k star，这个数**本身**
既不是异常也不是正常。只有和同量级、同领域、同年代的仓库比，
才知道它的结构是不是偏离了。

所以本模块的硬约束是：**没有对照组就不给结论**。
标定不出来时 verdict 返回 `uncalibrated`，报告里照实写「未标定」，
而不是拿一个拍脑袋的阈值充数。

━━━ 为什么只用「同类基线」会高估 ━━━

对照组里有两种：
    mega  微软 vscode / react / linux —— 十年老库，形态极稳
    peer  ollama / n8n —— 同为近年爆红的 AI/自动化工具

只看 mega 会把 lift 算得虚高（老库的社区结构天然更健康）；
只看 peer 又可能把「AI 圈普遍现象」误判成「这一个仓库有问题」。
**两个基线都要给**，让读者看到区间。
"""
from typing import Callable, Optional

import numpy as np

from crawler.config import resolve_canonical

# 判定档位。措辞刻意保守 —— 本项目的产出是「异常信号」不是「造假结论」。
VERDICTS = ("normal", "watch", "suspicious", "strong", "uncalibrated", "insufficient_data")


def percentile(values: list, q: float) -> Optional[float]:
    """线性插值分位数。样本 < 2 时返回 None（单点算不出分布）。"""
    v = [x for x in values if x is not None]
    if len(v) < 2:
        return None
    return float(np.percentile(v, q))


def calibrate(value: Optional[float], controls: list,
              higher_is_worse: bool = True,
              watch_lift: float = 1.5, susp_lift: float = 3.0,
              strong_lift: float = 6.0) -> dict:
    """把目标值放到对照分布里定位。

    `higher_is_worse=False` 用于「越大越健康」的指标（如订阅比）。

    档位判据（默认阈值，可被调用方覆盖）：
        lift < watch      → normal
        watch ≤ lift < susp    → watch       （值得看一眼）
        susp  ≤ lift < strong  → suspicious  （结构性异常）
        lift ≥ strong          → strong      （强异常，需人工核查）
    """
    cs = [x for x in controls if x is not None]
    if value is None:
        return {"verdict": "insufficient_data", "lift": None, "control_n": len(cs),
                "control_p50": None, "control_p95": None}
    if len(cs) < 2:
        # 对照组不足 —— 照实说，不给结论
        return {"verdict": "uncalibrated", "lift": None, "control_n": len(cs),
                "control_p50": (cs[0] if cs else None), "control_p95": None}

    p50 = percentile(cs, 50)
    p95 = percentile(cs, 95)
    if not p50:
        # 基线为 0：lift 无定义（除零）。这时用「是否 > 任何对照」来判断
        lift = None
        verdict = ("strong" if value > max(cs) else "normal") if higher_is_worse \
            else ("normal" if value < min(cs) else "watch")
    else:
        lift = value / p50 if higher_is_worse else p50 / value if value else None
        if lift is None:
            verdict = "insufficient_data"
        elif lift >= strong_lift:
            verdict = "strong"
        elif lift >= susp_lift:
            verdict = "suspicious"
        elif lift >= watch_lift:
            verdict = "watch"
        else:
            verdict = "normal"

    z = None
    if len(cs) >= 3:
        sd = float(np.std(cs, ddof=1))
        if sd > 0:
            z = (value - float(np.mean(cs))) / sd

    return {"verdict": verdict, "lift": lift, "z": z, "control_n": len(cs),
            "control_p50": p50, "control_p95": p95}


def all_repos(conn) -> list:
    """库里所有仓库的 full_name（目标在前，对照在后）。"""
    return [r["full_name"] for r in conn.execute(
        "SELECT full_name FROM repos ORDER BY CASE kind WHEN 'target' THEN 0 "
        "WHEN 'peer' THEN 1 ELSE 2 END, full_name")]


def by_kind(conn, kind: str) -> list:
    return [r["full_name"] for r in conn.execute(
        "SELECT full_name FROM repos WHERE kind=? ORDER BY full_name", (kind,))]


def sweep(conn, extract: Callable[[dict], Optional[float]]) -> dict:
    """对每个仓库跑一遍 extract，返回 {full_name: value}。

    extract 收到的是一次 `metrics.all_metrics()` 的结果 —— 这样各信号
    可以复用同一份计算，不必每个信号重查一次库。
    """
    from . import metrics as M
    out = {}
    for full in all_repos(conn):
        try:
            m = M.all_metrics(conn, full)
            out[full] = extract(m)
        except Exception as e:                     # 单仓库失败不该拖垮整轮
            out[full] = None
            out.setdefault("_errors", {})[full] = f"{type(e).__name__}: {e}"
    return out


def compare(conn, extract: Callable[[dict], Optional[float]], target: str,
            higher_is_worse: bool = True, **kw) -> dict:
    """目标 vs 对照组的一站式标定。

    对照组只取 kind != 'target' 的仓库 —— 这是刻意的：
    把目标自己混进基线会让 lift 永远趋近 1。
    """
    vals = sweep(conn, extract)
    canon = resolve_canonical(conn, target)
    value = vals.get(canon)
    controls = [v for k, v in vals.items()
                if k != canon and not k.startswith("_") and v is not None]
    out = calibrate(value, controls, higher_is_worse=higher_is_worse, **kw)
    out["value"] = value
    out["target"] = canon
    out["controls"] = {k: v for k, v in vals.items() if not k.startswith("_")}
    return out
