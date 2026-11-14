"""这条 fine-grained 令牌能不能干本项目的活？

逐端点试，每个只取 1 条（除 users 外都是 per_page=1），总消耗 < 20 次请求。
令牌只从环境变量读，绝不打印原文。
"""
import sys, os, time

sys.stdout.reconfigure(encoding="utf-8")
from curl_cffi import requests as cr

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
if not TOKEN:
    sys.exit("未设置 GITHUB_TOKEN")

PROF = ["firefox147", "edge101", "chrome120", "safari17_2_1", "firefox133", "chrome110"]
H = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "spider-research",
}


def call(url):
    for p in PROF:
        try:
            return cr.get(url, headers=H, impersonate=p, timeout=25), p
        except Exception:
            time.sleep(3)
    return None, None


# (标签, URL, 本项目用途)
TESTS = [
    ("仓库元数据", "https://api.github.com/repos/openclaw/openclaw", "meta 阶段"),
    ("star 曲线", "https://api.github.com/repos/openclaw/openclaw/stargazers/history?page=1", "history 阶段"),
    ("贡献者", "https://api.github.com/repos/openclaw/openclaw/contributors?per_page=1", "contributors 阶段"),
    ("fork 名单", "https://api.github.com/repos/openclaw/openclaw/forks?per_page=1", "forks 阶段（825 页）"),
    ("提交历史", "https://api.github.com/repos/openclaw/openclaw/commits?per_page=1", "commits 阶段（1137 页）"),
    ("issue 列表", "https://api.github.com/repos/openclaw/openclaw/issues?per_page=1&state=all", "issues 阶段"),
    ("issue 评论", "https://api.github.com/repos/openclaw/openclaw/issues/comments?per_page=1", "issues 阶段"),
    ("用户画像", "https://api.github.com/users/torvalds", "users 阶段（2.7 万）"),
    ("★ 订阅者名单", "https://api.github.com/repos/openclaw/openclaw/subscribers?per_page=1", "头号信号 #5b 的名单源"),
    ("★ star 名单", "https://api.github.com/repos/openclaw/openclaw/stargazers?per_page=1", "身份分析主体"),
]

print(f"令牌 github_...{TOKEN[-4:]}   配额 5000/小时")
print("=" * 92)
print(f"{'端点':<14}{'阶段/用途':<26}{'状态':>6}  说明")
print("-" * 92)

ok = bad = 0
for label, url, use in TESTS:
    r, p = call(url)
    if r is None:
        print(f"  {label:<12}{use:<26}{'—':>6}  网络失败")
        continue
    code = r.status_code
    note = ""
    if code == 200:
        try:
            d = r.json()
            if isinstance(d, list):
                note = f"拿到 {len(d)} 条"
            elif isinstance(d, dict):
                note = "拿到对象"
        except Exception:
            note = "非 JSON"
        ok += 1
    else:
        try:
            note = r.json().get("message", "")[:44]
        except Exception:
            note = r.text[:44]
        bad += 1
    mark = "✓" if code == 200 else "✗"
    print(f"  {label:<12}{use:<26}{code:>6} {mark} {note}")
    time.sleep(4)

print("-" * 92)
print(f"可用 {ok} 项，不可用 {bad} 项")
print("令牌原文未出现在以上任何输出中")
