# -*- coding: utf-8 -*-
"""_repo_report.py 的离线测试：合成 fixture 全路径覆盖，不联网、不碰真实 data/。

跑法：py -X utf8 _test_repo_report.py

覆盖面：
  1. 纯函数单测（_med/_pctl/_share/_fi/_parse_iso/ch_actor_rows 三种行格式）
  2. 无 metrics → SystemExit
  3. 完整 fixture → 报告生成 + 关键段落断言（§一/§四曲线/§五fork/§六画像/§七CH/§八对照/§九档位）
  4. 各数据源单独缺失 → 「未采集」如实标注（metrics/forks/history/ch/tiers）
  5. CH 覆盖率 ≥5% 分支（覆盖充足措辞）
  6. 旧版 history 格式（无 buckets）→ 提示删文件重跑
  7. 对照组 <2 → uncalibrated 档位
  8. 404-heavy 画像 → 删号率 strong
  9. meta 字段缺值（size_kb/open_issues=None）→ 不崩、出「—」
 10. 对照仓 meta 缺 stars/forks（None）→ 不崩、出「—」
 11. join 损失（画像里有 fork 名单没有的 login）→ 量化输出
"""
import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _repo_report as rr

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f" —— {detail}" if (detail and not cond) else ""))


def iso(days_ago):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 1. 纯函数 ─────────────────────────────────────────
def test_pure():
    print("[1] 纯函数")
    check("_med 奇数取中位", rr._med([3, 1, 2]) == 2)
    check("_med 偶数取均值", rr._med([1, 2, 3, 4]) == 2.5)
    check("_med 空表 None", rr._med([]) is None)
    check("_med 过滤 None", rr._med([None, 5, 7]) == 6)
    check("_pctl nearest-rank", rr._pctl([1, 2, 3, 4, 5], 50) == 3)
    check("_pctl 空 None", rr._pctl([], 25) is None)
    check("_share 比例", abs(rr._share([1, 0, 1], lambda x: x == 1) - 66.666) < 0.01)
    check("_share 空 None", rr._share([], lambda x: x) is None)
    check("_fi int 千分位", rr._fi(1234567) == "1,234,567")
    check("_fi None → —", rr._fi(None) == "—")
    check("_fi 字符串 → —", rr._fi("12kb") == "—")
    check("_parse_iso Z 后缀", rr._parse_iso("2026-01-01T00:00:00Z") is not None)
    check("_parse_iso 垃圾 None", rr._parse_iso("not-a-date") is None)
    check("_parse_iso 空 None", rr._parse_iso(None) is None)

    # ch_actor_rows 三种行格式
    ch_rows = [
        {"body": [{"actor_login": "alice", "starred_ts": 100}, {"actor_login": "bob", "starred_ts": 200}]},  # 分页包装
        {"actor_login": "carol", "starred_ts": 300},   # dict 行
        ["dave", 400],                                  # 裸数组行
        {"body": "not-a-list"},                         # 垃圾行（跳过）
        "garbage",                                      # 非法（跳过）
    ]
    got = rr.ch_actor_rows(ch_rows)
    check("ch_actor_rows 兼容三格式", sorted(got) == [("alice", 100), ("bob", 200), ("carol", 300), ("dave", 400)],
          f"got {got}")


# ── fixture 构造 ──────────────────────────────────────
def wjsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def meta_row(stars=142, forks=244, subs=4, size_kb=1060, open_issues=22):
    return {"kind": "meta", "stars": stars, "forks": forks, "subscribers": subs,
            "open_issues": open_issues, "created_at": iso(22), "pushed_at": iso(1),
            "language": "Rust", "size_kb": size_kb, "collected_at": iso(0)}


def base_fixture(d):
    """一个数据源齐全的仓库 fixture（storytold 形态）。"""
    key = "test__full"
    wjsonl(os.path.join(d, f"repo_metrics_{key}.jsonl"), [
        meta_row(),
        {"kind": "search", "metric": "issue_count", "total": 1},
        {"kind": "search", "metric": "pr_count", "total": 34},
        {"kind": "commits_total", "last_page": 29},
        {"kind": "contributors", "top100": [{"login": "echelon", "contributions": 2900}]},
        {"kind": "events_recent", "types": {"ForkEvent": 7}},
    ])
    # fork 名单：6 人，其中 user_x 不在画像里（join 损失由 11 验证）
    wjsonl(os.path.join(d, f"forks_all_{key}.jsonl"), [
        {"fork_id": i, "login": f"user{i}", "fork_created_at": iso(20 - i),
         "fork_pushed_at": iso(20 - i), "size_kb": 1060} for i in range(1, 7)
    ] + [{"fork_id": 99, "login": "user_x", "fork_created_at": iso(19), "fork_pushed_at": iso(19)}])
    # 曲线：4 周，前两周 86%（单脉冲）
    wjsonl(os.path.join(d, f"star_history_{key}.jsonl"), [
        {"page": 1, "bucket_count": 2, "total_week": 122, "buckets": [
            {"week": 1791072000, "total": 37, "days": [5] * 7},
            {"week": 1791676800, "total": 85, "days": [12] * 7}]},
        {"page": 2, "bucket_count": 2, "total_week": 20, "buckets": [
            {"week": 1792281600, "total": 18, "days": [3] * 7},
            {"week": 1792886400, "total": 2, "days": [0] * 7}]},
    ])
    # CH：2/142 = 1.4% < 5%（覆盖不足分支）
    wjsonl(os.path.join(d, "ch_star_events", f"{key}.jsonl"), [
        {"body": [{"actor_login": "ch1", "starred_ts": 1791100000},
                  {"actor_login": "ch2", "starred_ts": 1791100005}]}])
    wjsonl(os.path.join(d, f"ch_star_seconds_{key}.jsonl"), [
        {"task": "ch_star_seconds", "body": [{"sec": 1791100000, "n": 2, "actors": ["ch1", "ch2"]}]}])
    # 画像：5 人 ok + 1 人 404；user5 缺 fork_created_at join（user_x 反向：在 fork 名单不在画像）
    tiers = []
    for i in range(1, 6):
        tiers.append({"login": f"user{i}", "http": 200,
                      "d1": {"created_at": iso(3000 - i * 100), "followers": i, "public_repos": 10 * i,
                             "name": f"User {i}" if i < 3 else None, "has_email": i == 1},
                      "d2": {"http": 200, "events_total": 5},
                      "d4": {"http": 200, "starred_total": 40 + i},
                      "d6": {"http": 200, "orgs": ["org"] if i == 1 else []},
                      "d8": {"http": 200, "own_count": 0 if i <= 2 else 3}})
    tiers.append({"login": "user_gone", "http": 404})
    # join 损失样本：画像里有、fork 名单没有（fork 后改名 / 名单时点差）
    tiers.append({"login": "renamed_after", "http": 200,
                  "d1": {"created_at": iso(2500), "followers": 2, "public_repos": 8},
                  "d2": {"http": 200, "events_total": 3},
                  "d4": {"http": 200, "starred_total": 20},
                  "d6": {"http": 200, "orgs": []},
                  "d8": {"http": 200, "own_count": 1}})
    wjsonl(os.path.join(d, f"profile_tier_full_{key}.jsonl"), tiers)
    # 对照组：2 个（够 calibrate）
    for ckey, stars, forks in [("ctrl__one", 10000, 1500), ("ctrl__two", 5000, 1200)]:
        wjsonl(os.path.join(d, f"repo_metrics_{ckey}.jsonl"), [
            meta_row(stars=stars, forks=forks, subs=stars // 200),
            {"kind": "contributors", "top100": [{"login": "a", "contributions": 60}, {"login": "b", "contributions": 40}]},
            {"kind": "search", "metric": "issue_count", "total": stars // 20},
        ])
    return key


def run_report(d, repo):
    """在 fixture 目录 d 上跑 main()，返回报告文本。"""
    old_raw, old_out = rr.RAW, rr.OUT
    rr.RAW, rr.OUT = d, os.path.join(d, "out")
    old_argv = sys.argv
    sys.argv = ["_repo_report.py", "--repo", repo]
    try:
        rr.main()
        key = repo.replace("/", "__")
        p = os.path.join(rr.OUT, f"report_{key}.md")
        return open(p, encoding="utf-8").read() if os.path.exists(p) else None
    finally:
        rr.RAW, rr.OUT = old_raw, old_out
        sys.argv = old_argv


# ── 3. 完整 fixture ───────────────────────────────────
def test_full(tmp):
    print("[3] 完整 fixture → 报告生成")
    d = os.path.join(tmp, "full")
    os.makedirs(d)
    key = base_fixture(d)
    txt = run_report(d, "test/full")
    check("报告生成", txt is not None)
    if not txt:
        return
    check("§一 fork÷star 摘要", "fork ÷ star = 171.8%" in txt, txt[:200])
    check("§二 采集于列", "| 采集于 |" in txt)
    check("§四 周曲线表", "2026-10-04" in txt or "周（起始日）" in txt)
    check("§四 前两周占比", "前两周占比 86%" in txt)
    check("§五 fork 全量表", "全量 fork **7** 条" in txt)
    check("§六 删号率", "6 存活 / 1 已删号(404)" in txt)
    check("§六 join 损失量化", "join 损失" in txt)
    check("§七 覆盖不足不出结论", "不出身份级结论" in txt)
    check("§八 对照组表", "ctrl/one" in txt and "ctrl/two" in txt)
    check("§八 对照倍数", "5.6×" in txt or "×" in txt)
    check("§九 档位输出", "档位：" in txt)
    check("§九 曲线单脉冲", "单脉冲（爆发后归零）" in txt)
    check("§十 局限声明", "## 十、方法论局限" in txt)


# ── 2. 无 metrics ─────────────────────────────────────
def test_no_metrics(tmp):
    print("[2] 无 metrics → SystemExit")
    d = os.path.join(tmp, "empty")
    os.makedirs(d)
    try:
        run_report(d, "nobody/nothing")
        check("SystemExit 抛出", False, "没有抛 SystemExit")
    except SystemExit as e:
        check("SystemExit 抛出", "repo_metrics" in str(e), str(e))


# ── 4. 各数据源单独缺失 ────────────────────────────────
def test_missing_sources(tmp):
    print("[4] 各数据源单独缺失 → 如实标注")
    cases = [
        ("forks", "forks_all", "**未采集 fork 名单。**"),
        ("history", "star_history", "**未采集 star 曲线。**"),
        ("tiers", "profile_tier_full", "**未采集 fork owner 画像。**"),
    ]
    for name, prefix, expect in cases:
        d = os.path.join(tmp, f"miss_{name}")
        os.makedirs(d)
        base_fixture(d)
        for p in os.listdir(d):
            if p.startswith(prefix):
                os.remove(os.path.join(d, p))
        txt = run_report(d, "test/full")
        check(f"缺 {name} → 标注", txt and expect in txt,
              (txt or "")[:300])

    # CH 缺失（子目录文件）
    d = os.path.join(tmp, "miss_ch")
    os.makedirs(d)
    base_fixture(d)
    os.remove(os.path.join(d, "ch_star_events", "test__full.jsonl"))
    txt = run_report(d, "test/full")
    check("缺 ch → §二/§七 标注", txt and "未采集 CH star 身份" in txt)


# ── 5. CH 覆盖 ≥5% ───────────────────────────────────
def test_ch_covered(tmp):
    print("[5] CH 覆盖率 ≥5% 分支")
    d = os.path.join(tmp, "ch_ok")
    os.makedirs(d)
    base_fixture(d)
    # 10/142 = 7.0% ≥ 5%
    body = [{"actor_login": f"ch{i}", "starred_ts": 1791100000 + i} for i in range(10)]
    wjsonl(os.path.join(d, "ch_star_events", "test__full.jsonl"), [{"body": body}])
    txt = run_report(d, "test/full")
    check("覆盖充足措辞", txt and "≥ 5.0%：可做样本内相对统计" in txt, (txt or "")[:400])


# ── 6. 旧版 history 格式 ──────────────────────────────
def test_old_history(tmp):
    print("[6] 旧版 history（无 buckets）")
    d = os.path.join(tmp, "old_hist")
    os.makedirs(d)
    base_fixture(d)
    wjsonl(os.path.join(d, "star_history_test__full.jsonl"), [
        {"page": 1, "bucket_count": 4, "total_week": 142}])
    txt = run_report(d, "test/full")
    check("旧格式提示", txt and "旧版摘要格式" in txt, (txt or "")[:400])


# ── 7. 对照组 <2 → uncalibrated ───────────────────────
def test_uncalibrated(tmp):
    print("[7] 对照组 <2 → uncalibrated")
    d = os.path.join(tmp, "one_ctrl")
    os.makedirs(d)
    base_fixture(d)
    os.remove(os.path.join(d, "repo_metrics_ctrl__two.jsonl"))
    txt = run_report(d, "test/full")
    check("uncalibrated 档位", txt and "uncalibrated" in txt, (txt or "")[:400])


# ── 8. 404-heavy 画像 ─────────────────────────────────
def test_404_heavy(tmp):
    print("[8] 404-heavy 画像 → 删号率 strong")
    d = os.path.join(tmp, "gone")
    os.makedirs(d)
    base_fixture(d)
    tiers = [{"login": f"g{i}", "http": 404} for i in range(5)] + [
        {"login": "user1", "http": 200, "d1": {"created_at": iso(3000), "followers": 3, "public_repos": 10}}]
    wjsonl(os.path.join(d, "profile_tier_full_test__full.jsonl"), tiers)
    txt = run_report(d, "test/full")
    check("删号率 strong", txt and ("删号率(404)" in txt and "🔴" in txt), (txt or "")[:400])
    check("删号率数值 83.3%", txt and "83.3%" in txt, (txt or "")[:400])


# ── 9. meta 字段缺值 ─────────────────────────────────
def test_meta_none(tmp):
    print("[9] meta 字段缺值（None）不崩")
    d = os.path.join(tmp, "none_meta")
    os.makedirs(d)
    base_fixture(d)
    wjsonl(os.path.join(d, "repo_metrics_test__full.jsonl"), [
        {"kind": "meta", "stars": 142, "forks": 244, "subscribers": 4,
         "open_issues": None, "created_at": iso(22), "pushed_at": iso(1),
         "language": None, "size_kb": None},   # 旧数据缺 size_kb
        {"kind": "search", "metric": "issue_count", "total": None},
    ])
    txt = run_report(d, "test/full")   # 不抛 TypeError 即过
    check("不崩 + 出「—」", txt is not None and " / — KB" in txt, (txt or "")[:300])
    check("issue 总数 —", txt and "issue 总数（search） | —" in txt)


# ── 10. 对照仓 meta 缺 stars/forks ────────────────────
def test_ctrl_none(tmp):
    print("[10] 对照仓 meta 缺 stars/forks（None）不崩")
    d = os.path.join(tmp, "ctrl_none")
    os.makedirs(d)
    base_fixture(d)
    wjsonl(os.path.join(d, "repo_metrics_ctrl__one.jsonl"), [
        {"kind": "meta", "stars": None, "forks": None, "subscribers": None}])
    txt = run_report(d, "test/full")   # 不抛 TypeError 即过
    check("不崩 + 表内 —", txt is not None and "| ctrl/one | — | — | **—** |" in txt, (txt or "")[:400])


def main():
    print("═" * 60)
    print("_repo_report.py 离线测试（合成 fixture，不联网）")
    print("═" * 60)
    test_pure()
    tmp = tempfile.mkdtemp(prefix="rr_test_")
    try:
        test_no_metrics(tmp)
        test_full(tmp)
        test_missing_sources(tmp)
        test_ch_covered(tmp)
        test_old_history(tmp)
        test_uncalibrated(tmp)
        test_404_heavy(tmp)
        test_meta_none(tmp)
        test_ctrl_none(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("═" * 60)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print("全部通过 ✅")


if __name__ == "__main__":
    main()
