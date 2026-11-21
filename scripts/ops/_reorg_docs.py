# -*- coding: utf-8 -*-
"""把 docs/ 重组成分层结构，并自动改写所有 Markdown 内部链接。

    python scripts/ops/_reorg_docs.py --dry-run   # 只看计划
    python scripts/ops/_reorg_docs.py            # 执行

算法：
  1. 移动前记录每个 md 的「旧绝对路径」
  2. 建立 old_abs → new_abs 映射
  3. 移动文件
  4. 对每个 md（按新位置），把链接按「旧目录」解析成 old_abs，
     查表得到 new_abs，再算「从该文件新目录到 new_abs」的相对路径
  5. 最后校验：遍历所有链接，报告无法解析的目标
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = os.path.join(BASE, "docs")

MAP: list[tuple[str, str]] = [
    # 研究结论
    ("24_完整分析报告.md",           "research/完整分析报告.md"),
    ("20_研究报告_最终版.md",         "research/研究报告.md"),
    ("18_fork与账户质量分析.md",      "research/fork与账户质量分析.md"),
    ("19_表述边界与免责.md",          "research/表述边界与免责.md"),
    # 数据
    ("17_数据结构设计.md",            "data/数据结构设计.md"),
    ("23_采集完整性报告.md",          "data/采集完整性报告.md"),
    ("13_采集结果示例.md",            "data/采集结果示例.md"),
    ("14_账户画像demo.md",           "data/账户画像demo.md"),
    ("09_维度采集清单.md",            "data/维度采集清单.md"),
    # 方法
    ("03_采集方案.md",               "method/采集方案.md"),
    ("10_对照组设计.md",             "method/对照组设计.md"),
    ("04_特征清单.md",               "method/特征清单.md"),
    ("05_指标设计清单.md",            "method/指标设计清单.md"),
    ("15_图表与报告生成.md",          "method/图表与报告生成.md"),
    # 选样
    ("21_候选仓库筛选.md",            "selection/候选仓库筛选.md"),
    ("22_新一批候选仓建议.md",         "selection/新一批候选仓建议.md"),
    # 历史
    ("00_项目总纲.md",               "history/项目总纲.md"),
    ("00_手册总览.md",               "history/手册总览.md"),
    ("01_研究报告.md",               "history/研究报告_v1.md"),
    ("02_图文报告.md",               "history/图文报告_v1.md"),
    ("06_特征分析报告.md",            "history/特征分析报告.md"),
    ("07_fortune设计分析.md",         "history/fortune设计分析.md"),
    ("08_采集性能诊断.md",            "history/采集性能诊断.md"),
    ("11_真人判别指标手册.md",         "history/真人判别指标手册.md"),
    ("12_补全计划.md",               "history/补全计划.md"),
    ("16_交付清单.md",               "history/交付清单.md"),
]

BANNER = """> ⚠️ **已归档到 `docs/history/`** —— 这是研究过程中的中间版本，
> 其中的数字与结论**可能已被 [完整分析报告](../research/完整分析报告.md) 取代**。
> 保留它只为记录研究演进轨迹；引用前请先核对最新报告。

"""

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+?\.md)(#[^)]*)?\)")


def md_files(root: str) -> list[str]:
    out = []
    for r, dirs, fs in os.walk(root):
        parts = r.split(os.sep)
        if ".git" in parts:
            continue
        for f in fs:
            if f.endswith(".md"):
                out.append(os.path.join(r, f))
    return out


def rewrite(p_new: str, old_abs_of_this: str, old2new: dict[str, str]) -> tuple[int, list[str]]:
    """改写一个文件的链接。返回 (改写数, 无法解析的目标列表)。"""
    s = open(p_new, encoding="utf-8", errors="replace").read()
    old_dir = os.path.dirname(old_abs_of_this)
    new_dir = os.path.dirname(p_new)
    n = 0
    bad: list[str] = []

    def repl(m: re.Match) -> str:
        nonlocal n
        label, target, anchor = m.group(1), m.group(2), m.group(3) or ""
        if target.startswith(("http://", "https://", "mailto:")):
            return m.group(0)
        old_target = os.path.normpath(os.path.join(old_dir, target))
        new_target = old2new.get(old_target)
        if new_target is None:
            if os.path.exists(old_target):
                new_target = old_target            # 未移动的文件
            else:
                # 已经按新结构写过的链接？尝试直接解析
                cand = os.path.normpath(os.path.join(new_dir, target))
                if os.path.exists(cand):
                    return m.group(0)
                bad.append(target)
                return m.group(0)
        rel = os.path.relpath(new_target, new_dir).replace("\\", "/")
        n += 1
        return f"[{label}]({rel}{anchor})"

    out = LINK_RE.sub(repl, s)
    if out != s:
        open(p_new, "w", encoding="utf-8").write(out)
    return n, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print(f"计划移动 {len(MAP)} 个文件\n")
        for a, b in MAP:
            ok = os.path.exists(os.path.join(DOCS, a))
            print(f"  {'✅' if ok else '❌ 缺失'} {a:30s} → {b}")
        return 0

    # 1) 旧路径快照
    before = md_files(DOCS) + [os.path.join(BASE, "README.md")]
    old2new: dict[str, str] = {}
    for a, b in MAP:
        src = os.path.join(DOCS, a)
        dst = os.path.join(DOCS, b)
        if os.path.exists(src):
            old2new[src] = dst

    # 2) 每个文件的新路径
    new_of: dict[str, str] = {}
    for p in before:
        new_of[p] = old2new.get(p, p)

    # 3) 移动
    for old, new in old2new.items():
        os.makedirs(os.path.dirname(new), exist_ok=True)
        shutil.move(old, new)

    # 4) history 加提示
    hdir = os.path.join(DOCS, "history")
    if os.path.isdir(hdir):
        for f in sorted(os.listdir(hdir)):
            if not f.endswith(".md"):
                continue
            p = os.path.join(hdir, f)
            s = open(p, encoding="utf-8", errors="replace").read()
            if "已归档到 `docs/history/`" not in s:
                open(p, "w", encoding="utf-8").write(BANNER + s)

    # 5) 改写链接
    total = 0
    broken: list[tuple[str, str]] = []
    for old, new in new_of.items():
        if not os.path.exists(new):
            continue
        n, bad = rewrite(new, old, old2new)
        total += n
        broken += [(os.path.relpath(new, BASE), b) for b in bad]

    print(f"移动 {len(old2new)} 个文件，改写 {total} 条链接")
    if broken:
        print(f"\n⚠️ 无法解析的链接 {len(set(broken))} 条：")
        for p, t in sorted(set(broken))[:20]:
            print(f"  {p}  →  {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
