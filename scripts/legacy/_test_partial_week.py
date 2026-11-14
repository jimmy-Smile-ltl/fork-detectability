"""验证 _store_weeks 的「未完成当前周丢弃」修复。

用合成桶打真实 DB（哨兵仓库名，跑完即清），不碰任何真实数据。
"""
import sys, os, json, sqlite3, datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from crawler.config import DB_PATH
from crawler.sources.repo import _store_weeks

SENTINEL = "_test/partial-week"


class FakeGh:
    def __init__(self, conn):
        self.conn = conn

    def _log(self, m):
        print("   ", m)


def ts(y, m, d):
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp())


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    gh = FakeGh(conn)

    today = dt.datetime.now(dt.timezone.utc).date()
    print(f"现在 UTC = {dt.datetime.now(dt.timezone.utc).isoformat()[:19]}  (今天 {today})")
    print()

    # 构造：上周（完整）+ 本周（未完成）
    last_sun = today - dt.timedelta(days=today.weekday() + 1)  # 上一个周日
    this_sun = last_sun + dt.timedelta(days=7)
    print(f"  合成「完整周」起始 = {last_sun}")
    print(f"  合成「未完成周」起始 = {this_sun}")
    print()

    weeks = [
        (1, {"week": ts(last_sun.year, last_sun.month, last_sun.day),
             "total": 700, "days": [100] * 7}),
        (1, {"week": ts(this_sun.year, this_sun.month, this_sun.day),
             "total": 25, "days": [25, 0, 0, 0, 0, 0, 0]}),
    ]

    r = _store_weeks(gh, SENTINEL, weeks)
    print(f"  _store_weeks 返回: {r}")
    print()

    rows = conn.execute(
        "SELECT day, stars, week_start FROM star_history WHERE canonical_full_name=? ORDER BY day",
        (SENTINEL,),
    ).fetchall()
    print(f"  实际入库 {len(rows)} 行：")
    for x in rows:
        print(f"    {x['day']}  {x['stars']:>5}  (week {x['week_start']})")
    wk = conn.execute(
        "SELECT week_start FROM star_history_week WHERE canonical_full_name=?", (SENTINEL,)
    ).fetchall()
    print(f"  周表入库 {len(wk)} 行：{[x['week_start'] for x in wk]}")

    # 判定
    ok = True
    if len(rows) != 7:
        print(f"\n  ✗ 期望 7 行（只落完整周），实得 {len(rows)} 行")
        ok = False
    elif any(x["week_start"] == str(this_sun) for x in rows):
        print("\n  ✗ 未完成周被入库了")
        ok = False
    elif r.get("skipped_partial") != 1:
        print(f"\n  ✗ skipped_partial 期望 1，实得 {r.get('skipped_partial')}")
        ok = False

    if ok:
        print("\n  ✓ 通过：完整周入库，未完成周被丢弃，skipped_partial=1")

    # 清理哨兵数据
    with conn:
        conn.execute("DELETE FROM star_history WHERE canonical_full_name=?", (SENTINEL,))
        conn.execute("DELETE FROM star_history_week WHERE canonical_full_name=?", (SENTINEL,))
    print("\n  哨兵数据已清理")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
