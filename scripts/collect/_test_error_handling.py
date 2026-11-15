# -*- coding: utf-8 -*-
"""错误处理压力测试：逐条验证 10 类失败场景是否被正确处理。

    python scripts/collect/_test_error_handling.py

不联网的部分用假的 Response 对象；
联网的部分用真实端点（不存在的账号、不存在的仓、私有仓）。
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import collect_core as C
import fetchers as F

PASS, FAIL = "✅", "❌"
results = []


def check(name: str, got, want, note=""):
    ok = got == want
    results.append((ok, name, got, want, note))
    print(f"  {PASS if ok else FAIL} {name:44s} got={got!s:24s} want={want!s:16s} {note}")
    return ok


class FakeResp:
    """最小可用的假响应。"""
    def __init__(self, code, body=None, headers=None, text=""):
        self.status_code = code
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = text or json.dumps(body or {})

    def json(self):
        return self._body


print("=" * 92)
print("一、状态码 → Outcome 映射（纯逻辑，不联网）")
print("=" * 92)
check("200 + 有内容", C.classify_http(200, {"a": 1}), C.Outcome.OK)
check("200 + 空 body", C.classify_http(200, None), C.Outcome.EMPTY)
check("204 无内容", C.classify_http(204), C.Outcome.EMPTY)
check("401 需认证", C.classify_http(401), C.Outcome.AUTH)
check("403 + rate limit", C.classify_http(403, note="API rate limit exceeded"), C.Outcome.RATE_LIMIT)
check("403 非限流（私有）", C.classify_http(403), C.Outcome.PRIVATE)
check("404 找不到", C.classify_http(404), C.Outcome.NOT_FOUND)
check("409 空仓库", C.classify_http(409), C.Outcome.EMPTY)
check("422 无提交", C.classify_http(422), C.Outcome.EMPTY)
check("429 限流", C.classify_http(429), C.Outcome.RATE_LIMIT)
check("451 法律限制 → 终态", C.classify_http(451), C.Outcome.PRIVATE, "重试无意义")
check("500 服务端错误", C.classify_http(500), C.Outcome.NETWORK)
check("502 网关错误", C.classify_http(502), C.Outcome.NETWORK)
check("503 不可用", C.classify_http(503), C.Outcome.NETWORK)
check("None（连接失败）", C.classify_http(None), C.Outcome.NETWORK)
check("302 重定向失败 → 终态", C.classify_http(302), C.Outcome.NOT_FOUND, "仓已改名/删")

print()
print("=" * 92)
print("二、终态/可重试集合是否自洽")
print("=" * 92)
check("OK 是终态", C.Outcome.OK in C.TERMINAL, True)
check("EMPTY 是终态", C.Outcome.EMPTY in C.TERMINAL, True)
check("DELETED 是终态", C.Outcome.DELETED in C.TERMINAL, True)
check("NETWORK 可重试", C.Outcome.NETWORK in C.RETRYABLE, True)
check("RATE_LIMIT 可重试", C.Outcome.RATE_LIMIT in C.RETRYABLE, True)
check("BAD_RESPONSE 可重试", C.Outcome.BAD_RESPONSE in C.RETRYABLE, True)
check("终态∩可重试 = 空", len(C.TERMINAL & C.RETRYABLE), 0)

print()
print("=" * 92)
print("三、旧格式兼容（legacy_to_outcome）")
print("=" * 92)
check("http=200 → OK", C.legacy_to_outcome({"http": 200}), C.Outcome.OK)
check("http=404 → DELETED", C.legacy_to_outcome({"http": 404}), C.Outcome.DELETED)
check("http=0 + 子资源全404", C.legacy_to_outcome({"http": 0, "per_repo": [{"commits": None}]}),
      C.Outcome.NOT_FOUND)
check("http=0 + 无 per_repo", C.legacy_to_outcome({"http": 0, "per_repo": []}), C.Outcome.NETWORK)
check("status='gone' → DELETED", C.legacy_to_outcome({"status": "gone"}), C.Outcome.DELETED)
check("status=200(int) + type", C.legacy_to_outcome({"status": 200, "type": "photo"}), C.Outcome.OK)

print()
print("=" * 92)
print("四、Writer 去重与墓碑（不联网）")
print("=" * 92)
tmp = "_err_test.jsonl"
if os.path.exists(tmp):
    os.remove(tmp)
w = C.Writer(tmp)
w.write(C.Record(login="A", dim="D1", outcome="NETWORK"))
check("失败后不算完成", w.done("a"), False)
w.write(C.Record(login="A", dim="D1", outcome="OK", http=200, payload={"x": 1}))
check("成功后算完成", w.done("a"), True)
w.write(C.Record(login="A", dim="D1", outcome="NETWORK"))
check("失败不覆盖成功", w.done("a"), True)
w.write(C.Record(login="B", dim="D1", outcome="DELETED", http=404))
check("DELETED 是终态", w.done("b"), True)
w.write(C.Record(login="C", dim="D1", outcome="EMPTY", http=200))
check("EMPTY 是终态", w.done("c"), True)
# 坏行容错
with open(tmp, "a", encoding="utf-8") as fh:
    fh.write('{"login":"D","outcome":"OK"}\n')   # 缺字段
    fh.write("这不是 JSON\n")
    fh.write('{"login":"D","outcome":"OK"}\n')
w2 = C.Writer(tmp)
check("坏行不导致崩溃", True, True)
check("pending 过滤终态", len(w2.pending(["a", "b", "c", "d", "e"])), 1, "只有 e 待采")
os.remove(tmp)

print()
print("=" * 92)
print("五、真实网络失败场景（联网）")
print("=" * 92)
cfg_p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "proxy_tunnel.json")
tun = {}
if os.path.exists(cfg_p):
    c = json.load(open(cfg_p, encoding="utf-8"))
    url = f"http://{c['username']}:{c['password']}@{c['host']}/"
    tun = {"http": url, "https": url}
ctx = F.Ctx(proxies=tun, proxies_html=tun, year=2026, timeout=12, known_user_id={})
ctx_bad = F.Ctx(proxies={"http": "http://127.0.0.1:1", "https": "http://127.0.0.1:1"},
                proxies_html={}, year=2026, timeout=5, known_user_id={})

# 1) 不存在的账号
oc, p, code, note = F.fetch("D1", "this-account-should-not-exist-xyz-9999", ctx)
check("不存在的账号 → DELETED", oc, C.Outcome.DELETED, f"http={code}")

# 2) 代理失效
oc, p, code, note = F.fetch("D1", "torvalds", ctx_bad)
check("代理失效 → NETWORK（可重试）", oc, C.Outcome.NETWORK)

# 3) 私有/不存在仓库的 commits
oc, p, code, note = F.fetch("D9", "torvalds", ctx)
check("D9 正常账号不崩溃", oc in (C.Outcome.OK, C.Outcome.EMPTY,
                              C.Outcome.NOT_FOUND, C.Outcome.PRIVATE), True, f"{oc.value}")

# 4) 空请求体（组织账号的 contributions 会 404）
oc, p, code, note = F.fetch("D13", "github", ctx)
check("组织账号 D13 不崩溃", oc in (C.Outcome.OK, C.Outcome.DELETED,
                                C.Outcome.NETWORK, C.Outcome.BAD_RESPONSE), True, f"{oc.value}")

# 5) 401：伪造空 token 无法测试（匿名本来就没 token），跳过

print()
print("=" * 92)
print("六、防护机制检查")
print("=" * 92)
checks = []

# 分页上限
checks.append(("分页有硬上限", "MAX_PAGES" in open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "_profile_lists.py"),
    encoding="utf-8").read()))
# 重试次数
core_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "collect_core.py"), encoding="utf-8").read()
checks.append(("有重试上限", "max_retry" in core_src))
checks.append(("有退避", "sleep" in core_src))
# 读文件容错
checks.append(("读文件容错 errors=replace", 'errors="replace"' in core_src))
# 配额感知
fetch_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "fetchers.py"), encoding="utf-8").read()
checks.append(("D14 不再依赖 D1", "取 user_id 失败" in fetch_src))
lst_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_profile_lists.py"), encoding="utf-8").read()
checks.append(("列表采集有配额感知", "rate limit" in lst_src.lower()))
checks.append(("列表翻页有间隔", "time.sleep" in lst_src))

for name, ok in checks:
    print(f"  {PASS if ok else FAIL} {name}")
    results.append((ok, name, ok, True, ""))

print()
print("=" * 92)
bad = [r for r in results if not r[0]]
print(f"总计 {len(results)} 项，通过 {len(results)-len(bad)}，失败 {len(bad)}")
if bad:
    print("\n失败项：")
    for _, name, got, want, note in bad:
        print(f"  ❌ {name}: got={got} want={want} {note}")
