# -*- coding: utf-8 -*-
"""头像类型采集：默认 identicon 还是用户上传（0 API 配额）。

原理：GitHub 默认头像是按 user_id 生成的 identicon——5×5 网格、左右对称、只有 2–3 色，
PNG 压缩后只有 ~3 KB；上传的照片有几千种颜色、30–130 KB。

     实测（8 个已知样本）：
       默认：3 KB / 420×420 / 唯一色 53–110 / 前3色 75–82% / 对称度 99–100%
       上传：31–128 KB / 208–460² / 唯一色 3,138–3,881 / 前3色 0.6–11% / 对称度 0–12%

两步走，省流量：
  1) HEAD 取 Content-Length（0 字节下载）→ <8KB 判默认、>20KB 判上传
  2) 只有 8–20KB 的边界样本才真下载解码，算色数/对称度

⚠️ 判据地位：**只能正向**。「有自定义头像」略增真人可能；「默认头像」**不构成**负向——
   大量真实沉默用户从不换头像。批量号也常配 AI 生成头像（实测反例存在）。

用法：
    py _profile_avatar.py --file data/raw/profile_tier_deep_rea_p98.jsonl
    py _profile_avatar.py --file ... --out data/raw/avatar_rea_p98.jsonl
"""
import argparse
import io
import json
import os
import sys
import time
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from curl_cffi import requests as cffi

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"}
AVATAR = "https://avatars.githubusercontent.com/u/{uid}?v=4&s=460"

SMALL_BYTES = 8000        # < 此值：判默认
LARGE_BYTES = 20000       # > 此值：判上传
COLOR_CUT = 300           # 唯一色数阈值
SYM_CUT = 0.95            # 左右镜像对称度阈值


def _session():
    return cffi.Session(impersonate="firefox133")


def head_size(sess, url):
    try:
        r = sess.head(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return None, r.status_code
        return int(r.headers.get("content-length") or 0), 200
    except Exception:
        return None, 0


def analyze_image(sess, url):
    """返回 (w, h, unique_colors, top3_share, symmetry)。"""
    from PIL import Image
    r = sess.get(url, headers=UA, timeout=25)
    if r.status_code != 200:
        return None
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    w, h = im.size
    small = im.resize((64, 64))
    # getcolors 是 Pillow 的官方接口；getdata() 在 Pillow 14 会弃用（且会刷警告）
    cols = small.getcolors(maxcolors=64 * 64) or []
    uniq = len(cols)
    top3 = sum(c for c, _ in sorted(cols, reverse=True)[:3]) / (64 * 64)
    px = im.load()
    same = tot = 0
    for y in range(0, h, 4):
        for x in range(0, w // 2, 4):
            if px[x, y] == px[w - 1 - x, y]:
                same += 1
            tot += 1
    return w, h, uniq, top3, same / max(tot, 1)


def classify_user(sess, user_id):
    url = AVATAR.format(uid=user_id)
    n, st = head_size(sess, url)
    if st != 200:
        return {"status": st, "type": None}
    if n and n < SMALL_BYTES:
        return {"status": 200, "type": "identicon", "bytes": n, "by": "size"}
    if n and n > LARGE_BYTES:
        return {"status": 200, "type": "photo", "bytes": n, "by": "size"}
    got = analyze_image(sess, url)
    if not got:
        return {"status": 0, "type": None, "bytes": n}
    w, h, uniq, top3, sym = got
    ident = (uniq < COLOR_CUT) and (sym > SYM_CUT) and (top3 > 0.5)
    return {"status": 200, "type": "identicon" if ident else "photo", "bytes": n, "by": "image",
            "w": w, "h": h, "unique_colors": uniq, "top3_share": round(top3, 3),
            "symmetry": round(sym, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="profile_tier_*.jsonl")
    ap.add_argument("--out", help="输出文件（默认 data/raw/avatar_{stem}.jsonl）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=10, help="并发线程（CDN 直连，实测 10 线程 ~1000/分钟）")
    args = ap.parse_args()

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.file)),
                                   "avatar_" + os.path.splitext(os.path.basename(args.file))[0] + ".jsonl")
    done = set()
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type"):
                done.add(r["login"])
    sess = _session()
    todo = []
    for line in open(args.file, encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        d1 = r.get("d1") or {}
        lg = r.get("login")
        if r.get("http") == 200 and d1.get("http") == 200 and d1.get("user_id") and lg not in done:
            todo.append((lg, d1["user_id"]))
    if args.limit:
        todo = todo[:args.limit]
    print(f"待采 {len(todo)} 人（已完成 {len(done)}）→ {os.path.basename(out)}", flush=True)

    # 并发：avatars.githubusercontent.com 是 CDN，**直连即可**（实测 0.4s），
    # 单线程太浪费——10 线程能到 1000+/分钟（2026-11-11 实测）。
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()
    n = [0, 0]
    t0 = time.time()

    def work(item):
        lg, uid = item
        try:
            res = classify_user(sess, uid)
        except Exception as e:
            res = {"status": 0, "error": f"{type(e).__name__}"}
        with lock:
            f.write(json.dumps({"login": lg, "user_id": uid, **res}, ensure_ascii=False) + "\n")
            f.flush()
            n[0] += 1
            if res.get("type"):
                n[1] += 1
            if n[0] % 50 == 0:
                rate = n[0] / max(time.time() - t0, 1e-6) * 60
                print(f"[{n[0]}/{len(todo)}] {rate:.0f} 人/分钟 | ok={n[1]}", flush=True)

    with open(out, "a", encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(work, todo))
    print(f"完成 {n[1]}/{n[0]} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
