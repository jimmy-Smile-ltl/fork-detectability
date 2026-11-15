# -*- coding: utf-8 -*-
"""fork 名单采集器（**直连版**，不用代理）：全量 + 增量两种模式。

为什么单独写一个：`_small_repo_collect.py` 走代理节点池，节点全枯竭时它会"全池熔断"
睡到最早解冻（最长 1 小时），而本机直连还有独立的 60/h 配额没被用掉。

两种排序，用途完全不同：

  --sort oldest   全量补历史。sort=oldest 时针头单调向后，**页码不会因为新 fork 漂移**，
                  配合 .page 游标可以断点续采（openclaw 82k / rea 107k 都用这个）。
  --sort newest   增量。sort=newest 时新 fork 在最前面，翻到"连续 N 个已知 fork_id"
                  就停 —— 每天通常 1–3 页（100–300 条）就能对齐。
                  （GitHub 的 forks 端点**没有 since 参数**，只能靠这个边界法。）

配额：直连 60 次/小时/IP，24 万条 ≈ 400 小时? 不 —— 是 per_page=100，所以
      107,836 条 = 1,079 页 = **18 小时**；82,790 条 = 828 页 = **14 小时**。
      用 X-RateLimit-Reset 精确睡到窗口重置，不做无谓轮询。

输出（与代理版共用同一份文件与游标，两条路线可互切）：
    data/raw/forks_all_{owner}__{repo}.jsonl
    data/raw/forks_all_{owner}__{repo}.page
    data/raw/forks_all_{owner}__{repo}.inc.json    增量水位（最近一次 success 的最大 created_at）

用法：
    py _forks_direct_collect.py --repo morluto/rea                 # 全量续采
    py _forks_direct_collect.py --repo morluto/rea --incremental   # 只拉新增
    py _forks_direct_collect.py --repo morluto/rea --max-pages 5   # 小步试跑
"""
import argparse
import datetime
import json
import os
import sys
import time

from curl_cffi import requests as cffi

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(BASE, "data", "raw")
API = "https://api.github.com"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
      "Accept": "application/vnd.github+json"}
PROFILES = ["chrome", "firefox133", "safari17_0", "edge101"]   # 本机对 TLS 指纹有选择性抑制
PER_PAGE = 100
SAFE_REMAIN = 2          # 剩余 ≤2 就先睡到重置，别把配额打空后撞 403
KNOWN_RUN_STOP = 50      # 增量模式：连续遇到这么多已知 fork_id 就判定已对齐


def paths(repo):
    k = repo.replace("/", "__")
    return (os.path.join(RAW, f"forks_all_{k}.jsonl"),
            os.path.join(RAW, f"forks_all_{k}.page"),
            os.path.join(RAW, f"forks_all_{k}.inc.json"))


def load_known(out_path):
    """已落盘的 fork_id 集合 + 最大 created_at。"""
    ids, newest = set(), None
    if not os.path.exists(out_path):
        return ids, newest
    for line in open(out_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        fid = r.get("fork_id")
        if fid:
            ids.add(fid)
        c = r.get("fork_created_at")
        if c and (newest is None or c > newest):
            newest = c
    return ids, newest


def read_cursor(path):
    if not os.path.exists(path):
        return 1
    try:
        return max(1, int(open(path, encoding="utf-8").read().strip() or 1))
    except (OSError, ValueError):
        return 1


def fetch(url, tries=3):
    """TLS 档位轮换 + 每档 2 次。返回 response 或 None。"""
    for attempt in range(tries):
        for prof in PROFILES:
            for _ in range(2):
                try:
                    r = cffi.get(url, headers=UA, timeout=25, impersonate=prof)
                except Exception:
                    time.sleep(1.2)
                    continue
                if r.status_code in (200, 404):
                    return r
                if r.status_code in (403, 429):
                    return r            # 交给调用方判断配额/二级限流
                time.sleep(1.5)
        time.sleep(5)
    return None


def sleep_to_reset(r):
    try:
        reset = int(r.headers.get("X-RateLimit-Reset") or 0)
    except (TypeError, ValueError):
        reset = 0
    wait = max(reset - time.time() + 10, 60) if reset else 120
    wait = min(wait, 3700)
    print(f"   配额见底，睡 {wait / 60:.1f} 分钟到窗口重置", flush=True)
    time.sleep(wait)


def main():
    ap = argparse.ArgumentParser(description="直连版 fork 名单采集（全量 / 增量）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sort", choices=("oldest", "newest"), default="oldest")
    ap.add_argument("--incremental", action="store_true",
                    help="增量：sort=newest，遇到连续已知 fork_id 即停")
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--pace", type=float, default=1.2)
    ap.add_argument("--stop-after-known", type=int, default=KNOWN_RUN_STOP)
    ap.add_argument("--known-from", action="append", default=[],
                    help="额外已知名单文件（可多次）。旧文件名/多来源时用，避免把已知集合当成空")
    args = ap.parse_args()

    repo = args.repo.strip().strip("/")
    out_path, page_path, inc_path = paths(repo)
    known, newest = load_known(out_path)
    for extra_path in args.known_from:
        if not os.path.exists(extra_path):
            print(f"[warn] --known-from 文件不存在：{extra_path}")
            continue
        extra, extra_newest = load_known(extra_path)
        known |= extra
        if extra_newest and (newest is None or extra_newest > newest):
            newest = extra_newest
        print(f"[known] 从 {os.path.basename(extra_path)} 并入 {len(extra):,} 个已知 fork_id")
    sort = "newest" if args.incremental else args.sort
    page = 1 if args.incremental else read_cursor(page_path)

    print(f"仓库 {repo} | 模式 {'增量' if args.incremental else '全量'} | sort={sort} | "
          f"已有 {len(known):,} 条 | 起始页 {page}", flush=True)
    if args.incremental and newest:
        print(f"    增量水位：已知最大 fork_created_at = {newest}", flush=True)

    out = open(out_path, "a", encoding="utf-8")
    added = pages = 0
    run_known = 0
    empty_retry = 0
    t0 = time.time()
    try:
        while True:
            if args.max_pages and pages >= args.max_pages:
                print(f"[stop] 达到 --max-pages={args.max_pages}")
                break
            url = f"{API}/repos/{repo}/forks?per_page={PER_PAGE}&sort={sort}&page={page}"
            r = fetch(url)
            if r is None:
                print(f"    第 {page} 页拿不到（TLS 抑制/网络），30s 后重试", flush=True)
                time.sleep(30)
                continue
            if r.status_code == 404:
                print(f"✗ {repo} 404（仓库删除/改名/无权限）")
                break
            if r.status_code in (403, 429):
                rem = r.headers.get("X-RateLimit-Remaining")
                if rem == "0":
                    sleep_to_reset(r)
                else:
                    print(f"    二级限流（{r.status_code}），睡 120s", flush=True)
                    time.sleep(120)
                continue

            try:
                items = r.json()
            except Exception:
                # 200 但 body 为空/截断（实测会让整轮直接崩掉）→ 视为瞬时错误，重试同一页
                empty_retry += 1
                if empty_retry > 5:
                    print(f"    第 {page} 页连续 5 次空响应，停止；游标已保存可续采")
                    break
                print(f"    第 {page} 页空响应（{empty_retry}/5），等 {min(30 * empty_retry, 120)}s 重试",
                      flush=True)
                time.sleep(min(30 * empty_retry, 120))
                continue
            empty_retry = 0
            if not isinstance(items, list):
                print(f"    第 {page} 页返回非列表（{type(items).__name__}），跳过", flush=True)
                time.sleep(10)
                continue
            if not items:
                print(f"[done] 第 {page} 页为空 → 全量完成，共 {len(known):,} 条")
                break

            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for f in items:
                fid = f.get("id")
                if fid in known:
                    run_known += 1
                    continue
                run_known = 0
                owner = f.get("owner") or {}
                out.write(json.dumps({
                    "fork_id": fid,
                    "login": (owner.get("login") or "").lower(),
                    "owner_type": owner.get("type"),
                    "fork_full_name": f.get("full_name"),
                    "fork_created_at": f.get("created_at"),
                    "fork_pushed_at": f.get("pushed_at"),
                    "fork_stars": f.get("stargazers_count"),
                    "size_kb": f.get("size"),
                    "fork_language": f.get("language"),
                    "default_branch": f.get("default_branch"),
                    "collected_at": now_iso,
                }, ensure_ascii=False) + "\n")
                known.add(fid)
                added += 1
            out.flush()
            pages += 1

            if args.incremental and run_known >= args.stop_after_known:
                print(f"[inc] 连续 {run_known} 个已知 fork_id → 已对齐，本次新增 {added} 条")
                break

            if sort == "oldest":
                with open(page_path, "w", encoding="utf-8") as fh:
                    fh.write(str(page + 1))

            rem = r.headers.get("X-RateLimit-Remaining")
            if pages % 10 == 0:
                el = max(time.time() - t0, 1e-6)
                print(f"[{pages} 页] 新增 {added:,} | 累计 {len(known):,} | "
                      f"{pages / el * 3600:.0f} 页/小时 | 剩余配额 {rem}", flush=True)
            if rem is not None and int(rem) <= SAFE_REMAIN:
                sleep_to_reset(r)
            else:
                time.sleep(args.pace)

            if len(items) < PER_PAGE:
                print(f"[done] 末页 {len(items)} 条 → 结束，累计 {len(known):,} 条")
                break
            page += 1
    except KeyboardInterrupt:
        print("\n手动中断，游标已保存，可续采")
    finally:
        out.close()
        # 增量水位：记录本次写入的最大 created_at
        _, cur_newest = load_known(out_path)
        if cur_newest:
            with open(inc_path, "w", encoding="utf-8") as fh:
                json.dump({"last_max_created_at": cur_newest,
                           "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                           "total": len(known)}, fh, ensure_ascii=False, indent=1)
        print(f"收尾：新增 {added:,} 条 / {pages} 页 / {(time.time() - t0) / 3600:.1f} 小时 "
              f"| 总计 {len(known):,} → {os.path.basename(out_path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
