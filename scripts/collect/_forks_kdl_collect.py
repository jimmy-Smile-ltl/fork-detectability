# -*- coding: utf-8 -*-
"""快代理（KDL）版「全量 fork 名单」采集器 —— 按仓库，支持任意 owner/repo。

定位：`_small_repo_collect.py --only forks` 的**代理加速版**。两者输出同一份文件、
同一游标、同一去重键，可以随时来回切（今天走 KDL、明天走 Clash 都不会冲突）。

    data/raw/forks_all_{owner}__{repo}.jsonl    每行一个 fork（与 Clash 版字段一致）
    data/raw/forks_all_{owner}__{repo}.page     游标 = 下一页页码
    data/raw/fork_owners_{owner}__{repo}.txt    去重后的 owner 名单

凭据来源（**任何情况下都不写日志、不进仓库**）：
    1) 环境变量 KDL_API_URL / KDL_USERNAME / KDL_PASSWORD
    2) data/proxy_kdl.json（gitignored）：{"api_url": "...{num}...", "username": "...", "password": "..."}
    api_url 里的 {num} 会被替换成要提取的 IP 个数。

用法：
    py _forks_kdl_collect.py --repo morluto/rea --dry-run     # 只检查配置与路径，不联网
    py _forks_kdl_collect.py --repo morluto/rea --probe       # 取 1 批 IP + 试 1 页（约 3 个请求）
    py _forks_kdl_collect.py --repo morluto/rea               # 全量，断点续采
    py _forks_kdl_collect.py --repo morluto/rea --max-pages 50

纪律：只读公开 API；只采名单不采内容；失败不静默跳过（页级重试 + 游标只在成功后推进）。
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
CFG_PATH = os.path.join(BASE, "data", "proxy_kdl.json")
API = "https://api.github.com"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
      "Accept": "application/vnd.github+json"}
IMPERSONATE = "firefox133"      # 本机实测：firefox133 能过 KDL API 的 TLS 过滤
PER_PAGE = 100


# ── 配置 ────────────────────────────────────────────────
def load_cfg():
    cfg = {}
    if os.path.exists(CFG_PATH):
        try:
            cfg = json.load(open(CFG_PATH, encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
    api = os.environ.get("KDL_API_URL") or cfg.get("api_url") or ""
    user = os.environ.get("KDL_USERNAME") or cfg.get("username") or ""
    pwd = os.environ.get("KDL_PASSWORD") or cfg.get("password") or ""
    return api, user, pwd


def mask(s, keep=4):
    if not s:
        return "（空）"
    return s[:keep] + "…" + s[-2:] if len(s) > keep + 2 else "***"


def check_cfg(api, user, pwd):
    problems = []
    if not api:
        problems.append("api_url 缺失（需含 {num} 占位符）")
    elif "{num}" not in api:
        problems.append("api_url 里没有 {num} 占位符——无法指定提取 IP 数")
    if not user:
        problems.append("username 缺失")
    if not pwd:
        problems.append("password 缺失")
    return problems


# ── 传输 ────────────────────────────────────────────────
class ProxyPool:
    """KDL 私密代理：按需取 IP、失败即弃、批量轮换。"""

    def __init__(self, api, user, pwd):
        self.api, self.user, self.pwd = api, user, pwd
        self.ok = self.dead = 0
        self.ips = []

    def fetch_ips(self, num=5):
        try:
            r = cffi.get(self.api.format(num=num), timeout=15, impersonate=IMPERSONATE,
                         headers={"User-Agent": UA["User-Agent"]})
        except Exception as e:
            print(f"[proxy] KDL 提取失败：{type(e).__name__}", flush=True)
            return []
        if r.status_code != 200:
            # 不回显 api_url（内含 secret/signature）
            print(f"[proxy] KDL HTTP {r.status_code}（正文前 100 字略）", flush=True)
            return []
        ips = [x.strip() for x in r.text.splitlines() if x.strip()]
        self.ips = ips[:num]
        print(f"[proxy] 取得 {len(self.ips)} 个出口：{', '.join(x.split(':')[0] for x in self.ips[:5])}"
              + (" …" if len(self.ips) > 5 else ""), flush=True)
        return self.ips

    def proxies_for(self, ip):
        auth = f"http://{self.user}:{self.pwd}@{ip}/"
        return {"http": auth, "https": auth}

    def get(self, url, params, ips, timeout=25):
        """依次试 ip，返回第一个 200 的响应。404 也是定论（仓库没了），直接返回。

        ★ 403/429（该出口 60/h 用光）要把这个 IP 从列表里摘掉，否则后续每页都会
          先撞它一次、白白多花 1–2 秒 —— 实测这会让吞吐掉一半以上。
        """
        for ip in list(ips or []):
            try:
                r = cffi.get(url, params=params, timeout=timeout, proxies=self.proxies_for(ip),
                             headers=UA, impersonate=IMPERSONATE)
            except Exception:
                self.dead += 1
                continue
            if r.status_code in (200, 404):
                self.ok += 1
                return r
            self.dead += 1
            if r.status_code in (403, 429):
                try:
                    ips.remove(ip)
                except ValueError:
                    pass
        return None


# ── 名单 ────────────────────────────────────────────────
def key_of(repo):
    return repo.replace("/", "__")


def paths(repo):
    k = key_of(repo)
    return (os.path.join(RAW, f"forks_all_{k}.jsonl"),
            os.path.join(RAW, f"forks_all_{k}.page"),
            os.path.join(RAW, f"fork_owners_{k}.txt"))


def load_done(out_path):
    """断点续采：已落盘的 fork_id 集合（只认合法行）。"""
    done = set()
    if not os.path.exists(out_path):
        return done
    for line in open(out_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            fid = json.loads(line).get("fork_id")
        except json.JSONDecodeError:
            continue
        if fid:
            done.add(fid)
    return done


def read_cursor(page_path):
    if not os.path.exists(page_path):
        return 1
    try:
        return max(1, int(open(page_path, encoding="utf-8").read().strip() or 1))
    except (OSError, ValueError):
        return 1


def write_cursor(page_path, page):
    with open(page_path, "w", encoding="utf-8") as f:
        f.write(str(page))


def append_row(out_path, row):
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_of(f, now_iso):
    owner = f.get("owner") or {}
    return {
        "fork_id": f.get("id"),
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
    }


def owners_of(out_path):
    s = set()
    if not os.path.exists(out_path):
        return s
    for line in open(out_path, encoding="utf-8"):
        try:
            lg = json.loads(line).get("login")
        except json.JSONDecodeError:
            continue
        if lg:
            s.add(lg)
    return s


# ── 主流程 ──────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="快代理版全量 fork 名单采集")
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--ip-num", type=int, default=5, help="每批提取的出口 IP 数")
    ap.add_argument("--rotate-every", type=int, default=25, help="每 N 页换一批 IP")
    ap.add_argument("--pace", type=float, default=1.2, help="页间节流（秒）")
    ap.add_argument("--max-pages", type=int, default=0, help="本次最多采多少页（0=不限）")
    ap.add_argument("--dry-run", action="store_true", help="只检查配置与路径，不联网")
    ap.add_argument("--probe", action="store_true", help="取 1 批 IP 并试 1 页（约 3 个请求）")
    args = ap.parse_args()

    repo = args.repo.strip().strip("/")
    if repo.count("/") != 1:
        raise SystemExit("--repo 必须形如 owner/repo")

    out_path, page_path, owners_path = paths(repo)
    api, user, pwd = load_cfg()
    problems = check_cfg(api, user, pwd)

    print(f"仓库      : {repo}")
    print(f"输出      : {os.path.basename(out_path)} / {os.path.basename(page_path)}")
    print(f"KDL 配置  : api={'有' if api else '缺'} user={mask(user)} pwd={'有' if pwd else '缺'}"
          f"（来源：{'环境变量' if os.environ.get('KDL_API_URL') else 'data/proxy_kdl.json'}）")
    if problems:
        print("\n⚠ 配置不完整：" + "；".join(problems))
        print("   → 填 data/proxy_kdl.json 的 api_url/username/password，或导出 "
              "KDL_API_URL/KDL_USERNAME/KDL_PASSWORD。")

    done = load_done(out_path)
    page = read_cursor(page_path)
    print(f"断点      : 已落盘 {len(done):,} 条，从第 {page} 页继续（sort=oldest）")

    if args.dry_run:
        print("\n[dry-run] 未联网。配置齐备后可先跑 --probe 验证出口，再全量。")
        return 0 if not problems else 2

    if problems:
        print("\n✗ 配置不完整，拒绝联网。")
        return 2

    pool = ProxyPool(api, user, pwd)
    ips = pool.fetch_ips(args.ip_num)
    if not ips:
        print("✗ 没拿到出口 IP，退出（不发直连请求——直连只 60/h，会把工期拖成几十小时）")
        return 1

    if args.probe:
        r = pool.get(f"{API}/repos/{repo}/forks",
                     {"per_page": 2, "sort": "oldest", "page": 1}, ips)
        if not r:
            print("✗ 试采失败：所有出口都不可用")
            return 1
        print(f"✓ probe: HTTP {r.status_code}，"
              f"剩余配额 {r.headers.get('x-ratelimit-remaining')}，"
              f"返回 {len(r.json()) if r.status_code == 200 else 0} 条")
        return 0

    if not os.path.exists(owners_path):
        open(owners_path, "w", encoding="utf-8").close()

    total0, n_new, pages_done = len(done), 0, 0
    t0 = time.time()
    while True:
        if args.max_pages and pages_done >= args.max_pages:
            print(f"[stop] 达到 --max-pages={args.max_pages}，游标保留在第 {page} 页")
            break
        if pages_done and pages_done % args.rotate_every == 0:
            ips = pool.fetch_ips(args.ip_num) or ips

        r = pool.get(f"{API}/repos/{repo}/forks",
                     {"per_page": PER_PAGE, "sort": "oldest", "page": page}, ips)
        if r is None:
            # 全批出口都不可用 → 换一批；连续两批都不行就退避
            print(f"    第 {page} 页：本批出口全失败，换 IP 重试", flush=True)
            ips = pool.fetch_ips(args.ip_num) or []
            if not ips:
                time.sleep(120)
            continue
        if r.status_code == 404:
            print(f"✗ {repo} 返回 404（仓库已删/改名/无权限）")
            break
        if r.status_code != 200:
            print(f"    第 {page} 页 HTTP {r.status_code}，换 IP 重试", flush=True)
            ips = pool.fetch_ips(args.ip_num) or ips
            time.sleep(5)
            continue

        items = r.json()
        if not items:
            print(f"[done] 第 {page} 页为空 → 全量完成，共 {len(done):,} 条")
            break

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for f in items:
            row = row_of(f, now_iso)
            if not row["login"] or row["fork_id"] in done:
                continue
            append_row(out_path, row)
            done.add(row["fork_id"])
            n_new += 1

        pages_done += 1
        page += 1
        write_cursor(page_path, page)

        if pages_done % 10 == 0 or pages_done == 1:
            el = max(time.time() - t0, 1e-6)
            rate = pages_done / el * 60          # 页/分钟
            rem = "" if not args.max_pages else f"/{args.max_pages}"
            print(f"[{pages_done}{rem} 页] 累计 {len(done):,} 条（+{n_new}）| {rate:.2f} 页/分钟 "
                  f"| ok={pool.ok} dead={pool.dead}", flush=True)

        if len(items) < PER_PAGE:
            print(f"[done] 末页 {len(items)} 条 → 全量完成，共 {len(done):,} 条")
            break
        time.sleep(args.pace)

    with open(owners_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(owners_of(out_path))) + "\n")
    print(f"[out] {len(done):,} 条 → {os.path.basename(out_path)}；"
          f"owner 名单 → {os.path.basename(owners_path)}")
    print(f"[next] py metrics_fork.py --repo {repo} --min-n 1000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
