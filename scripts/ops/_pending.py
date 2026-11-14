# -*- coding: utf-8 -*-
"""打印某数据集的「待采数」= 目标 − 成功 − 删号。

去重口径与 collect_state.warm_from_jsonl 一致：**成功优先**（同名多条里
只要出现过 200 就算成功），避免重试把成功覆盖成失败。
"""
import argparse
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--target", type=int, required=True)
    a = ap.parse_args()

    seen = {}
    for line in open(a.file, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        lg = (r.get("login") or "").lower()
        if not lg:
            continue
        h = r.get("http")
        prev = seen.get(lg)
        # 成功优先：已经有 200 就不被后面的 0 覆盖
        if prev == 200:
            continue
        seen[lg] = h

    ok = sum(1 for v in seen.values() if v == 200)
    gone = sum(1 for v in seen.values() if v == 404)
    print(a.target - ok - gone)


if __name__ == "__main__":
    main()
