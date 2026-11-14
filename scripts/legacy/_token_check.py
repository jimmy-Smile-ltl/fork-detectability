"""令牌有效性 + star 名单端点权限检查。

令牌只从环境变量 GITHUB_TOKEN 读，**绝不打印原文**（只显示脱敏形态）。
不写任何文件、不留状态。
"""
import sys, os, json, time

sys.stdout.reconfigure(encoding="utf-8")
from curl_cffi import requests as cr

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
if not TOKEN:
    sys.exit("未设置 GITHUB_TOKEN")


def mask(t):
    return f"{t[:7]}...{t[-4:]}" if len(t) > 14 else "***"


PROF = ["firefox147", "edge101", "chrome120", "safari17_2_1", "firefox133", "chrome110"]
H = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "spider-research",
}


def call(url, method="GET", payload=None, accept=None):
    h = dict(H)
    if accept:
        h["Accept"] = accept
    last = None
    for p in PROF:
        try:
            if method == "POST":
                r = cr.post(url, headers=h, json=payload, impersonate=p, timeout=25)
            else:
                r = cr.get(url, headers=h, impersonate=p, timeout=25)
            return r, p
        except Exception as e:
            last = f"{type(e).__name__}"
            time.sleep(1.5)
    return None, last


print(f"令牌: {mask(TOKEN)}   长度 {len(TOKEN)}")
print("=" * 78)

# ── 1. 有效性 + 配额 ────────────────────────────────────────────
print("\n[1] 令牌有效性  GET /rate_limit")
r, p = call("https://api.github.com/rate_limit")
if r is None:
    print(f"    ✗ 全部指纹失败: {p}")
else:
    print(f"    HTTP {r.status_code}  [{p}]")
    if r.status_code == 200:
        c = r.json()["resources"]["core"]
        print(f"    配额 {c['limit']}/小时   剩余 {c['remaining']}")
        sc = r.headers.get("x-oauth-scopes")
        if sc is not None:
            print(f"    scopes: {sc!r}   （classic 令牌才有这个头）")
        else:
            print("    scopes: （无此头 → 不是 classic 令牌）")
        print("    ✓ 令牌有效" if c["limit"] >= 5000 else "    ⚠ 配额仍像匿名")
    else:
        print(f"    {r.text[:160]}")

# ── 2. star 名单端点（REST）─────────────────────────────────────
print("\n[2] 身份名单端点（REST）")
TARGETS = [
    ("star 名单", "https://api.github.com/repos/{r}/stargazers?per_page=1"),
    ("订阅者名单", "https://api.github.com/repos/{r}/subscribers?per_page=1"),
]
for label, tmpl in TARGETS:
    print(f"  ── {label}")
    for repo in ("openclaw/openclaw", "torvalds/linux", "facebook/react"):
        r, p = call(tmpl.format(r=repo))
        if r is None:
            print(f"    {repo:<22} 失败: {p}")
            continue
        tag = ""
        if r.status_code == 200:
            try:
                d = r.json()
                tag = f"  → 拿到 {len(d)} 条，首条 {d[0].get('login') if d else '-'}"
            except Exception:
                tag = "  → 返回非 JSON 数组"
        elif r.status_code == 403:
            tag = f"  → remaining={r.headers.get('X-RateLimit-Remaining')}"
        print(f"    {repo:<22} HTTP {r.status_code}{tag}")
        if r.status_code != 200:
            print(f"        {r.text[:130]}")
        time.sleep(1)

# ── 3. GraphQL 的 stargazers 连接 ──────────────────────────────
print("\n[3] GraphQL  stargazers 连接")
q = """
query($owner:String!,$name:String!){
  repository(owner:$owner,name:$name){
    stargazerCount
    stargazers(first:3){ totalCount edges{ starredAt node{ login } } }
  }
}
"""
r, p = call("https://api.github.com/graphql", method="POST",
            payload={"query": q, "variables": {"owner": "openclaw", "name": "openclaw"}})
if r is None:
    print(f"    ✗ 全部指纹失败: {p}")
else:
    print(f"    HTTP {r.status_code}  [{p}]")
    try:
        j = r.json()
        if "errors" in j:
            print(f"    errors: {json.dumps(j['errors'], ensure_ascii=False)[:220]}")
        d = (j.get("data") or {}).get("repository")
        if d:
            print(f"    stargazerCount = {d['stargazerCount']:,}")
            st = d.get("stargazers") or {}
            print(f"    stargazers.totalCount = {st.get('totalCount')}")
            for e in (st.get("edges") or [])[:3]:
                print(f"      {e['starredAt'][:10]}  {e['node']['login']}")
    except Exception as e:
        print(f"    解析失败 {e}: {r.text[:200]}")

print("\n" + "=" * 78)
print("令牌原文未出现在以上任何输出中")
