# -*- coding: utf-8 -*-
"""把全部提交的作者/提交者改写为目标身份（保留原始时间戳与提交信息）。

为什么不用 `git filter-branch`：
    它是 shell 脚本，在当前沙箱里 sh.exe 无法创建命名对象（0xC0000022），
    必然失败。所以这里用 **纯 git 管线命令**（rev-parse / commit-tree / update-ref）
    在 Python 里逐提交重建，完全不经过 shell。

用法：
    python scripts/ops/_rewrite_authors.py --dry-run
    python scripts/ops/_rewrite_authors.py --apply --name "jimmy-Smile-ltl" \
        --email "73268541+jimmy-Smile-ltl@users.noreply.github.com"
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def git(*args: str, inp: bytes | None = None, env: dict | None = None) -> bytes:
    r = subprocess.run(["git", *args], cwd=BASE, input=inp, capture_output=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--name", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()
    if not args.dry_run and not args.apply:
        ap.error("需要 --dry-run 或 --apply")

    commits = git("rev-list", "--reverse", "--topo-order", args.branch).decode().split()
    print(f"分支 {args.branch}：{len(commits)} 个提交（线性，从旧到新）\n")

    old2new: dict[str, str] = {}
    created: list[tuple[str, str, str]] = []   # (old, new, subject)

    for i, old in enumerate(commits, 1):
        tree = git("rev-parse", f"{old}^{{tree}}").decode().strip()
        # 父提交（已重写过的用新 hash）
        pline = git("rev-list", "--parents", "-n", "1", old).decode().split()
        old_parents = pline[1:]
        new_parents = [old2new.get(p, p) for p in old_parents]
        # 原始时间戳
        adate = git("log", "-1", "--format=%aI", old).decode().strip()
        cdate = git("log", "-1", "--format=%cI", old).decode().strip()
        msg = git("log", "-1", "--format=%B", old)
        subject = msg.decode("utf-8", "replace").splitlines()[0][:50] if msg.strip() else "(空)"

        if args.dry_run:
            created.append((old[:8], "(dry)", subject))
            old2new[old] = old
            continue

        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": args.name,
            "GIT_AUTHOR_EMAIL": args.email,
            "GIT_AUTHOR_DATE": adate,
            "GIT_COMMITTER_NAME": args.name,
            "GIT_COMMITTER_EMAIL": args.email,
            "GIT_COMMITTER_DATE": cdate,
        })
        cmd = ["git", "commit-tree", tree]
        for p in new_parents:
            cmd += ["-p", p]
        r = subprocess.run(cmd, cwd=BASE, input=msg, capture_output=True, env=env)
        if r.returncode != 0:
            print(f"  第 {i} 个失败: {r.stderr.decode('utf-8', 'replace')[:200]}")
            return 1
        new = r.stdout.decode().strip()
        old2new[old] = new
        created.append((old[:8], new[:8], subject))
        if i % 10 == 0 or i == len(commits):
            print(f"  已重建 {i}/{len(commits)}")

    if args.dry_run:
        print("（dry-run）前 5 条：")
        for o, n, s in created[:5]:
            print(f"  {o} → {n}  {s}")
        print("\n将把以下身份应用到全部提交：")
        print(f"  name : {args.name}")
        print(f"  email: {args.email}")
        return 0

    head = old2new[commits[-1]]
    git("update-ref", f"refs/heads/{args.branch}", head)
    print(f"\n✅ 新 HEAD: {head[:12]}")
    print("作者分布：")
    out = git("log", "--format=%an <%ae>").decode("utf-8", "replace")
    from collections import Counter
    for k, v in Counter(out.splitlines()).most_common():
        print(f"  {k:50s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
