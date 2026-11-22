# -*- coding: utf-8 -*-
"""三仓对比：只用「完整记录」算画像，并给出 A/B 组归属。

    python scripts/analysis/_compare_full.py
    python scripts/analysis/_compare_full.py --tags agencyagents frp nodejsargo
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import OUT, RAW, iter_jsonl  # noqa: E402

HTML = {"D13", "D15"}
TERM = {"OK", "EMPTY", "DELETED", "NOT_FOUND", "PRIVATE", "AUTH"}

# 已有 CSV 特征（per-dim 老数据）
CSV_TAGS = ["shardeum", "union", "anoma", "rea", "openclaw", "bettafish",
            "ollama", "langchain", "frp", "nodejsargo"]


def best_records(path: str) -> dict[str, dict]:
    """按 login 聚合，**逐维度取最优**。

    为什么不能只挑「维度最多的一条记录」：
        单维补采（如 `--dims D15`）产生的记录只含 1 个维度，
        按「维度数最多」挑选会把它整个丢掉，导致 D15 白采。
        正确做法是每个维度各自取最好的一条，再拼起来。
    """
    PRI = {"OK": 5, "EMPTY": 4, "DELETED": 3, "NOT_FOUND": 3, "PRIVATE": 3,
           "AUTH": 3, "NETWORK": 1, "RATE_LIMIT": 1, "BAD_RESPONSE": 1}
    merged: dict[str, dict] = {}
    for r in iter_jsonl(path):
        lg = (r.get("login") or "").lower()
        if not lg:
            continue
        cur = merged.get(lg)
        if cur is None:
            cur = {"login": lg, "outcomes": {}, "dims": {}, "_ts": 0}
            merged[lg] = cur
        for dim, oc in (r.get("outcomes") or {}).items():
            old = cur["outcomes"].get(dim)
            if old is None or PRI.get(oc, 0) > PRI.get(old, 0):
                cur["outcomes"][dim] = oc
                payload = (r.get("dims") or {}).get(dim)
                if payload is not None:
                    cur["dims"][dim] = payload
        cur["_ts"] = max(cur["_ts"], r.get("ts") or 0)

    # 统计每人的 API 维完成数（供 complete_only 用）
    for rec in merged.values():
        api = {k: v for k, v in rec["outcomes"].items() if k not in HTML}
        rec["_n"] = len(api)
        rec["_s"] = sum(1 for v in api.values() if v in TERM)
    return merged


def complete_only(recs: dict[str, dict]) -> list[dict]:
    return [r for r in recs.values() if r["_n"] and r["_s"] == r["_n"]]


def from_combined(tag: str):
    p = os.path.join(RAW, f"accounts_full_{tag}.jsonl")
    recs = best_records(p)
    comp = complete_only(recs)
    return p, recs, comp


def profile_from_recs(recs: dict[str, dict]) -> dict:
    """从「每人一条最佳记录」算画像。

    **每个指标用自己的分母**（该维度拿到终态的人才计入）——
    如果只取「全维度完整」的人，会引入选择偏差：
    实测 frp 的有自建仓从 74.9%（全样本）掉到 71.9%（仅完整样本）。
    """
    def dim_ok(r, dim):
        return (r.get("outcomes") or {}).get(dim) in TERM

    def g(r, dim, key, default=None):
        return (r.get("dims", {}).get(dim) or {}).get(key, default)

    all_recs = list(recs.values())

    def pct_of(dim, pred):
        sub = [r for r in all_recs if dim_ok(r, dim)]
        if not sub:
            return None
        return round(sum(1 for r in sub if pred(r)) / len(sub) * 100, 1), len(sub)

    def med_of(dim, key):
        sub = [r for r in all_recs if dim_ok(r, dim)]
        vals = [g(r, dim, key) for r in sub]
        vals = [v for v in vals if isinstance(v, (int, float))]
        n = len(vals)
        return (statistics.median(vals) if vals else None), n

    out = {"n": len(all_recs)}
    v = pct_of("D8H", lambda r: (g(r, "D8H", "own_count") or 0) > 0)
    out["has_own"], out["has_own_n"] = v if v else (None, 0)
    v = pct_of("D9", lambda r: (g(r, "D9", "commit_total") or 0) == 0)
    out["commit_zero"], out["commit_zero_n"] = v if v else (None, 0)
    v = pct_of("D14", lambda r: g(r, "D14", "type") == "photo")
    out["avatar_photo"], out["avatar_photo_n"] = v if v else (None, 0)
    v = pct_of("D1", lambda r: (g(r, "D1", "followers") or 0) == 0)
    out["followers_zero"], out["followers_zero_n"] = v if v else (None, 0)
    v = pct_of("D1", lambda r: (g(r, "D1", "following") or 0) == 0)
    out["following_zero"], out["following_zero_n"] = v if v else (None, 0)
    v = pct_of("D15", lambda r: bool(g(r, "D15", "has_pull_shark")))
    out["pull_shark"], out["pull_shark_n"] = v if v else (None, 0)
    v = pct_of("D15", lambda r: bool(g(r, "D15", "has_collab")))
    out["has_collab"], out["has_collab_n"] = v if v else (None, 0)

    out["commit_total_med"], _ = med_of("D9", "commit_total")
    out["own_count_med"], _ = med_of("D8H", "own_count")
    out["followers_med"], _ = med_of("D1", "followers")
    a, _ = med_of("D1", "acct_age_days")
    if a is None:
        a, _ = med_of("D1", "age_days")
    out["acct_age_med"] = a
    return out


def from_csv(tag: str) -> dict:
    import csv
    p = os.path.join(OUT, f"features_{tag}.csv")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(fh))

    def rate(k):
        x = [r for r in rows if r.get(k) not in (None, "")]
        if not x:
            return None
        return round(sum(1 for r in x if str(r[k]) == "1") / len(x) * 100, 1)

    def med(k):
        v = []
        for r in rows:
            s = r.get(k)
            if s in (None, ""):
                continue
            try:
                v.append(float(s))
            except ValueError:
                pass
        return statistics.median(v) if v else None

    return {"n": len(rows), "has_own": rate("has_own"), "commit_zero": rate("commit_zero"),
            "avatar_photo": rate("avatar_photo"), "followers_zero": rate("followers_zero"),
            "following_zero": rate("following_zero"), "commit_total_med": med("commit_total"),
            "own_count_med": med("own_count"), "followers_med": med("followers"),
            "acct_age_med": med("acct_age_days"), "pull_shark": rate("has_pull_shark"),
            "has_collab": rate("has_collab")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", nargs="*",
                    default=["agencyagents", "frp", "nodejsargo"])
    args = ap.parse_args()

    print("=" * 92)
    print("三仓对比（每人一条最佳记录；**每个指标用自己的分母**）")
    print("=" * 92)

    rows = {}
    for tag in args.tags:
        p, recs, comp = from_combined(tag)
        if not comp:
            print(f"  {tag}: 无完整记录（文件 {p}）")
            continue
        prof = profile_from_recs(recs)
        rows[tag] = prof
        print(f"  {tag:14s} 总 {len(recs):>5,} | 完整 {len(comp):>5,} "
              f"({len(comp)/max(len(recs),1)*100:.1f}%) | {os.path.getsize(p)/1e6:.1f} MB")

    # 参考组（来自 features CSV）
    refs = {}
    for t in ("shardeum", "union", "anoma", "rea", "openclaw", "bettafish",
              "ollama", "langchain"):
        d = from_csv(t)
        if d:
            refs[t] = d

    print("\n" + "=" * 92)
    print(f"{'指标':22s}" + "".join(f"{t[:12]:>14s}" for t in rows))
    print("-" * 92)
    KEYS = [("has_own", "有自建仓"), ("commit_zero", "零提交"), ("avatar_photo", "自传头像"),
            ("followers_zero", "零粉丝"), ("following_zero", "零关注"),
            ("pull_shark", "Pull Shark")]
    for k, name in KEYS:
        line = f"{name:22s}"
        for t in rows:
            v = rows[t].get(k)
            line += f"{v:>13.1f}%" if v is not None else f"{'—':>14s}"
        print(line)
    print()
    for k, name in (("commit_total_med", "提交数中位"), ("own_count_med", "自建仓中位"),
                    ("followers_med", "粉丝中位"), ("acct_age_med", "账号年龄中位")):
        line = f"{name:22s}"
        for t in rows:
            v = rows[t].get(k)
            line += f"{v:>14,.0f}" if v is not None else f"{'—':>14s}"
        print(line)

    # A/B 组区间
    A = ["shardeum", "union", "anoma"]
    B = ["ollama", "langchain"]
    print("\n" + "=" * 92)
    print("与已知 A/B 组区间的对照")
    print("=" * 92)
    print(f"{'指标':22s} {'A组区间':>16s} {'B组区间':>16s}" + "".join(f"{t[:12]:>14s}" for t in rows)
          + "  归属")
    print("-" * 92)
    for k, name in KEYS:
        av = [refs[t][k] for t in A if t in refs and refs[t].get(k) is not None]
        bv = [refs[t][k] for t in B if t in refs and refs[t].get(k) is not None]
        line = f"{name:22s}"
        line += f"{min(av):>7.1f}-{max(av):<8.1f}" if av else f"{'—':>16s}"
        line += f"{min(bv):>7.1f}-{max(bv):<8.1f}" if bv else f"{'—':>16s}"
        for t in rows:
            v = rows[t].get(k)
            line += f"{v:>13.1f}%" if v is not None else f"{'—':>14s}"
        # 归属判断（多种子投票）
        votes = []
        for t in rows:
            v = rows[t].get(k)
            if v is None or not av or not bv:
                votes.append("?")
                continue
            da = min(abs(v - min(av)), abs(v - max(av)))
            db = min(abs(v - min(bv)), abs(v - max(bv)))
            votes.append("A" if da < db else ("B" if db < da else "="))
        print(line + "  " + " ".join(f"{t[:6]}={v}" for t, v in zip(rows, votes)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
