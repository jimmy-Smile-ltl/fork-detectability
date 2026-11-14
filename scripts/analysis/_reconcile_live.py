"""一次性对账：实时 /stargazers/history 首页 vs 库里已存数据。

用项目自己的 GhClient（带指纹轮换 + 按主机抑制窗口记账），匿名即可，
不碰任何凭据。结果写 data/out/_reconcile.txt。

★ 实测要点：桶里 `week` 字段是 **int 时间戳**（不是字符串），
  必须先转日期再跟库里的 week_start 比。
"""
import sys, os, sqlite3, datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from crawler.http_client import GhClient
from crawler.config import DB_PATH

REPOS = [
    "openclaw/openclaw",
    "torvalds/linux",
    "microsoft/vscode",
    "ollama/ollama",
    "n8n-io/n8n",
    "react/react",
]
OUT = os.path.join("data", "out", "_reconcile.txt")
lines = []


def log(s=""):
    print(s, flush=True)
    lines.append(s)


def wk_of(bucket) -> str:
    """桶的 week 字段转 YYYY-MM-DD（实测是 int 时间戳）。"""
    w = bucket.get("week")
    if isinstance(w, (int, float)):
        return dt.datetime.fromtimestamp(w, dt.timezone.utc).strftime("%Y-%m-%d")
    return str(w)[:10]


def main():
    now = dt.datetime.now(dt.timezone.utc)
    log(f"对账时刻: {now.isoformat()[:19]}Z")
    log("=" * 84)

    gh = GhClient(task="reconcile", auth=False, rate=0.5, verbose=False)
    conn = sqlite3.connect(DB_PATH)
    today = now.strftime("%Y-%m-%d")

    for repo in REPOS:
        log()
        log(f"### {repo}")
        try:
            r = gh.request(
                f"https://api.github.com/repos/{repo}/stargazers/history",
                params={"page": 1},
                accept="application/vnd.github+json",
                max_attempts=40,
            )
        except Exception as e:
            log(f"  请求异常: {type(e).__name__}: {e}")
            continue

        if r.status_code != 200:
            log(f"  HTTP {r.status_code}: {r.text[:120]}")
            continue

        try:
            buckets = r.json()
        except Exception as e:
            log(f"  JSON 解析失败: {e}")
            continue

        if not isinstance(buckets, list) or not buckets:
            log(f"  返回非预期: {str(buckets)[:120]}")
            continue

        log(f"  实时返回 {len(buckets)} 桶")
        drift_days = []
        compared_weeks = 0

        for b in buckets:
            wk = wk_of(b)
            live_days = b["days"]
            # 当前周（含今天）= 未完成，跳过对账
            if wk <= today < (
                dt.datetime.strptime(wk, "%Y-%m-%d") + dt.timedelta(days=7)
            ).strftime("%Y-%m-%d"):
                log(f"  [当前周·未完成] {wk}  total={b['total']:>6}  days={live_days}")
                continue

            rows = conn.execute(
                "SELECT day, stars FROM star_history WHERE canonical_full_name=? AND week_start=? ORDER BY day",
                (repo, wk),
            ).fetchall()
            if not rows:
                continue

            compared_weeks += 1
            for i, (d, s) in enumerate(rows):
                lv = live_days[i] if i < len(live_days) else None
                if lv is not None and lv != s:
                    drift_days.append((d, s, lv))

        log(f"  已对账 {compared_weeks} 周")
        if drift_days:
            log(f"  ★ 有差异的日 {len(drift_days)} 个：")
            for d, dbv, lv in drift_days:
                log(f"      {d}  库里={dbv:>5}  实时={lv:>5}  差={lv-dbv:+d}")
        else:
            log("  ✓ 逐日完全一致")

        # 累计对账
        curve = conn.execute(
            "SELECT SUM(stars) FROM star_history WHERE canonical_full_name=?", (repo,)
        ).fetchone()[0]
        meta = conn.execute(
            "SELECT stars, fetched_at FROM repos WHERE full_name=?", (repo,)
        ).fetchone()
        if meta:
            log(f"  库里曲线合计={curve:,}   repos.stars={meta[0]:,}   差={meta[0]-curve}")
            log(f"  快照时刻={meta[1]}")

        # 未来日期（幽灵周）检查
        fut = conn.execute(
            "SELECT COUNT(*), SUM(stars) FROM star_history WHERE canonical_full_name=? AND day > ?",
            (repo, today),
        ).fetchone()
        if fut and fut[0]:
            log(f"  ⚠ 库里有 {fut[0]} 行**未来日期**（幽灵周），合计 {fut[1]} star")

    log()
    log("=" * 84)
    log("完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback

        log(f"致命错误: {type(e).__name__}: {e}")
        log(traceback.format_exc())
    finally:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n结果已写入 {OUT}")
