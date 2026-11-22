# -*- coding: utf-8 -*-
"""脚本共用的最小工具：路径、JSONL 读取、百分比。

只放**无业务含义**的通用件；采集/分析逻辑不要进这里。

用法（scripts/ 下任意层级）：

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from common import BASE, RAW, OUT, iter_jsonl

约定：输入只读 `data/`，分析产物写 `data/out/`，图写 `figs/`。
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator

# scripts/common.py → 仓库根目录
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
RAW = os.path.join(DATA, "raw")
OUT = os.path.join(DATA, "out")
FIG = os.path.join(BASE, "figs")


def iter_jsonl(path: str, *, skip_bad: bool = True) -> Iterator[dict[str, Any]]:
    """逐行读 JSONL；文件不存在时返回空迭代器（不抛异常）。

    skip_bad=True（默认）跳过空行、坏行与非对象行——分析脚本用这个，
    一行脏数据不该让整批统计失败。
    skip_bad=False 时遇到坏行直接抛 ValueError，便于数据质检定位到行号。
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                if skip_bad:
                    continue
                raise ValueError(f"{path}:{lineno} 不是合法 JSON: {exc}") from exc
            if isinstance(obj, dict):
                yield obj
            elif not skip_bad:
                raise ValueError(f"{path}:{lineno} 不是 JSON 对象")


def load_jsonl(path: str, *, skip_bad: bool = True) -> list[dict[str, Any]]:
    """把 JSONL 一次读成列表；需要逐行流式处理时用 iter_jsonl。"""
    return list(iter_jsonl(path, skip_bad=skip_bad))


def pct(num: float, den: float | None, ndigits: int = 1) -> float | None:
    """百分比；分母为 0/None 时返回 None。

    不要用 0 代替“无数据”——本项目的九态体系里，
    “值是 0”（EMPTY）和“没采到”（DELETED/失败）必须区分。
    """
    if not den:
        return None
    return round(num / den * 100, ndigits)
