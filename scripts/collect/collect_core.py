# -*- coding: utf-8 -*-
"""统一采集核心：把「两种调度策略」与「八种结果」抽象出来。

设计要点
--------
1. **底层只有一个东西**：`fetch()` 发一次请求 → 返回 `(Outcome, payload)`。
   所有维度（D1/D2/D8H/D9/D13/D15/D4L）都是它的不同参数组合。

2. **上层只有两种调度**，输出结构完全相同：
   - `per_account`：外层遍历账号，内层遍历维度  → 一人采完再下一个
   - `per_dim`    ：外层遍历维度，内层遍历账号  → 一维采完再下一维
   二者可自由互换，因为落盘 schema 与顺序无关。

3. **八种结果必须分开**（这是本项目历史上最贵的教训）：
   - `http=0` 同时代表"网络失败"和"所有子资源 404" → 无法判断该不该重试
   - 统一成 `Outcome` 后，**终态记墓碑不再重试**，**可重试的才重试**

4. **缺失 ≠ 0**：
   - `EMPTY`（确实没有）→ payload 里的值是 **0**
   - `DELETED/NOT_FOUND/NETWORK`（拿不到）→ payload 是 **None**，并标注 outcome

用法
----
    from collect_core import Collector, Outcome, DIMS

    c = Collector(tag="rea", out_dir="data/raw", profile_dir="data/raw")
    c.run(accounts=[...], dims=["D1", "D15"], mode="per_dim", workers=20)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# 一、八种结果（唯一权威定义）
# ---------------------------------------------------------------------------
class Outcome(str, Enum):
    """一次采集请求的最终归类。**只有 OK/EMPTY 是"有效数据"。**"""

    OK = "OK"                # 200，且解析出非空数据
    EMPTY = "EMPTY"          # 资源存在但内容为空（0 仓 / 0 提交 / 0 事件）→ 值是 0
    DELETED = "DELETED"      # 账号 404：已注销 / 被封禁 / 改名
    NOT_FOUND = "NOT_FOUND"  # 子资源 404：仓库已删 / 改名 / 转私有
    PRIVATE = "PRIVATE"      # 403 且非配额：私有内容，无权访问
    AUTH = "AUTH"            # 401：需认证（本项目匿名，理论上不出现）
    RATE_LIMIT = "RATE_LIMIT"  # 403/429 + 配额耗尽 → **可重试**
    NETWORK = "NETWORK"      # 连接失败 / 超时 / TLS 重置 → **可重试**
    BAD_RESPONSE = "BAD_RESPONSE"  # 200 但解析失败 → **可重试**


#: 终态：再次请求也不会有不同结果 → 记墓碑，不再重试
TERMINAL = {Outcome.OK, Outcome.EMPTY, Outcome.DELETED,
            Outcome.NOT_FOUND, Outcome.PRIVATE, Outcome.AUTH}

#: 可重试：换个 IP / 等配额重置有可能成功
RETRYABLE = {Outcome.RATE_LIMIT, Outcome.NETWORK, Outcome.BAD_RESPONSE}

#: 有效数据：可以进特征表
USABLE = {Outcome.OK, Outcome.EMPTY}


def classify_http(code: Optional[int], body: Any = None,
                  note: str = "") -> Outcome:
    """把一次 HTTP 结果映射成 Outcome。

    `code=None` 表示请求根本没拿到响应（连接异常/超时）。
    """
    if code is None:
        return Outcome.NETWORK
    if code == 200:
        return Outcome.OK if body else Outcome.EMPTY
    if code == 204:
        return Outcome.EMPTY          # 无内容，但资源存在
    if code == 401:
        return Outcome.AUTH
    if code == 404:
        # 账号级还是子资源级，由调用方指定 note/dim 决定
        return Outcome.NOT_FOUND
    if code in (409, 422):
        return Outcome.EMPTY          # 空仓库（GitHub 对空仓 commits 返回 409）
    if code == 403:
        if "rate limit" in (note or "").lower():
            return Outcome.RATE_LIMIT
        return Outcome.PRIVATE
    if code == 429:
        return Outcome.RATE_LIMIT
    if 500 <= code < 600:
        return Outcome.NETWORK        # 服务端错误，值得重试
    return Outcome.BAD_RESPONSE


def legacy_to_outcome(rec: dict) -> Outcome:
    """把旧格式（http 整数 / status 字符串）翻译成 Outcome。

    旧格式的两处歧义：
      - `http=0` 既可能是网络失败，也可能是"所有子资源 404"
      - D13 用 `status: ok/unavailable/gone`
    这里按各维度的已知语义保守还原。
    """
    st = rec.get("status")
    if isinstance(st, str):                   # D13 风格：status 是字符串
        return {"ok": Outcome.OK,
                "unavailable": Outcome.NETWORK,   # 当时记的是"拿不到"
                "gone": Outcome.DELETED}.get(st, Outcome.BAD_RESPONSE)
    if rec.get("type") in ("photo", "identicon", "default"):
        return Outcome.OK                     # D14 风格：只记类型
    http = rec.get("http", st if isinstance(st, int) else None)
    if http == 200:
        return Outcome.OK
    if http == 404:
        return Outcome.DELETED
    if http == 403:
        return Outcome.PRIVATE
    if http == 0:
        # 有 per_repo 且全为 None → 子资源全 404（NOT_FOUND）；否则当网络失败
        per = rec.get("per_repo") or []
        if per and all((x.get("commits") is None) for x in per if isinstance(x, dict)):
            return Outcome.NOT_FOUND
        return Outcome.NETWORK
    return Outcome.BAD_RESPONSE


# ---------------------------------------------------------------------------
# 二、统一记录信封
# ---------------------------------------------------------------------------
@dataclass
class Record:
    """所有维度共用的落盘结构。**dim 决定 payload 的内容。**"""

    login: str
    dim: str
    outcome: str                 # Outcome 值
    http: Optional[int] = None
    ts: float = field(default_factory=time.time)
    attempt: int = 1
    payload: dict = field(default_factory=dict)
    note: str = ""               # 人读原因，如 "账号不存在" / "空仓库"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# 三、写入器：只追加 + 成功优先去重 + 墓碑
# ---------------------------------------------------------------------------
class Writer:
    """一个维度一个文件；同一 login 以"最好的结果"为准。

    去重优先级：OK > EMPTY > 其它终态 > 可重试
    理由：一次成功就够，后来的失败不应覆盖它。
    """

    _PRIORITY = {Outcome.OK: 5, Outcome.EMPTY: 4, Outcome.DELETED: 3,
                 Outcome.NOT_FOUND: 3, Outcome.PRIVATE: 3, Outcome.AUTH: 3,
                 Outcome.RATE_LIMIT: 1, Outcome.NETWORK: 1,
                 Outcome.BAD_RESPONSE: 1}

    def __init__(self, path: str):
        self.path = path
        self._done: dict[str, Outcome] = {}
        self._load()

    def _load(self) -> None:
        """启动时把已有文件读进内存（**必须做，否则会重复采**）。"""
        if not os.path.exists(self.path):
            return
        for line in open(self.path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue   # 半行/坏行：跳过即可，下一轮会重采
            lg = (r.get("login") or "").lower()
            if not lg:
                continue
            try:
                oc = Outcome(r.get("outcome") or legacy_to_outcome(r).value)
            except ValueError:
                oc = Outcome.BAD_RESPONSE
            self._done[lg] = self._better(self._done.get(lg), oc)

    @classmethod
    def _better(cls, a: Optional[Outcome], b: Outcome) -> Outcome:
        if a is None:
            return b
        return a if cls._PRIORITY.get(a, 0) >= cls._PRIORITY.get(b, 0) else b

    def done(self, login: str) -> bool:
        """该账号该维度是否已经有**终态**结果（终态不再重试）。"""
        oc = self._done.get(login.lower())
        return oc is not None and oc in TERMINAL

    def pending(self, logins: Iterable[str]) -> list[str]:
        return [x for x in logins if not self.done(x)]

    def write(self, rec: Record) -> None:
        lg = rec.login.lower()
        oc = Outcome(rec.outcome)
        if not self._better(self._done.get(lg), oc) == oc:
            return                    # 已有更好的结果，丢弃本次
        self._done[lg] = oc
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(rec.to_json() + "\n")

    def stats(self) -> dict:
        from collections import Counter
        return dict(Counter(o.value for o in self._done.values()))


# ---------------------------------------------------------------------------
# 四、维度定义表：底层原语 + 通道 + 是否消耗 API
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DimSpec:
    code: str
    name: str
    channel: str          # api / html / cdn / rss
    uses_api: bool
    per_account_calls: int
    file_pattern: str     # 落盘文件名模板，{tag} 会被替换
    note: str = ""


DIMS: dict[str, DimSpec] = {
    "D1":  DimSpec("D1",  "账户档案",     "api",  True,  1,
                   "profile_tier_screen_{tag}_all.jsonl", "/users/{login}，15 字段"),
    "D2":  DimSpec("D2",  "事件流",       "rss",  False, 1,
                   "atom_{tag}.jsonl", "/{login}.atom，近 30 条事件"),
    "D4L": DimSpec("D4L", "star 明细",    "api",  True,  1,
                   "starlist_{tag}.jsonl", "/users/{login}/starred，带时间戳"),
    "D8H": DimSpec("D8H", "仓库结构",     "html", False, 1,
                   "repos_html_{tag}.jsonl", "?tab=repositories"),
    "D9":  DimSpec("D9",  "自建仓提交数", "api",  True,  1,
                   "commits_{tag}.jsonl", "/repos/{l}/{r}/commits?author="),
    "D13": DimSpec("D13", "全年贡献日历", "html", False, 1,
                   "heat_{tag}_all.jsonl", "/users/{login}/contributions"),
    "D14": DimSpec("D14", "头像类型",     "cdn",  False, 1,
                   "avatar_{tag}.jsonl", "avatars HEAD，按字节判"),
    "D15": DimSpec("D15", "主页画像",     "html", False, 1,
                   "profile_html_{tag}.jsonl", "/{login}，一次拿 7 字段"),
}


def dim_path(dim: str, tag: str, out_dir: str = "data/raw") -> str:
    """维度的落盘路径（唯一权威映射，别在别处硬编码文件名）。"""
    return os.path.join(out_dir, DIMS[dim].file_pattern.format(tag=tag))


# ---------------------------------------------------------------------------
# 五、两种调度策略（上层唯一的差异）
# ---------------------------------------------------------------------------
class Collector:
    """两种模式的唯一区别是**遍历顺序**，落盘与重试逻辑完全共用。

    - mode="per_dim"     ：外层维度、内层账号  → **推荐**（见文档 §3.10.3）
    - mode="per_account" ：外层账号、内层维度  → 适合小样本 / 要"一人一行"的即时结果
    """

    def __init__(self, tag: str, out_dir: str = "data/raw",
                 fetch: Optional[Callable] = None, max_retry: int = 4,
                 dims: Optional[list[str]] = None):
        self.tag = tag
        self.out_dir = out_dir
        self.fetch = fetch or (lambda dim, login: (Outcome.NETWORK, {}, None, ""))
        self.max_retry = max_retry
        # 只加载用到的维度：大盘文件不必读，也避免与其它进程并发读
        use = dims or list(DIMS)
        self.writers = {d: Writer(dim_path(d, tag, out_dir)) for d in use}

    # -- 单个 (维度, 账号) 的采集单元：重试逻辑只写一次 ----------------------
    def _unit(self, dim: str, login: str) -> Record:
        for attempt in range(1, self.max_retry + 1):
            oc, payload, code, note = self.fetch(dim, login)
            if oc in TERMINAL:
                return Record(login=login, dim=dim, outcome=oc.value, http=code,
                              attempt=attempt, payload=payload, note=note)
            time.sleep(min(0.4 * (2 ** (attempt - 1)), 2.5))   # 退避 0.4/0.8/1.6/2.5 秒
        # 重试耗尽 → 记最后一次的 outcome（可重试态，下次启动会再试）
        return Record(login=login, dim=dim, outcome=oc.value, http=code,
                      attempt=self.max_retry, payload=payload,
                      note=(note + " | 重试耗尽").strip(" |"))

    # -- 策略 A：一维采完再下一维 -------------------------------------------
    def run_per_dim(self, logins: list[str], dims: Optional[list[str]] = None,
                    on_progress: Optional[Callable] = None) -> dict:
        dims = dims or list(DIMS)
        report = {}
        for dim in dims:
            w = self.writers[dim]
            todo = w.pending(logins)
            report[dim] = {"total": len(logins), "todo": len(todo)}
            for i, lg in enumerate(todo, 1):
                w.write(self._unit(dim, lg))
                if on_progress and i % 200 == 0:
                    on_progress(dim, i, len(todo))
        return report

    # -- 策略 B：一人采完再下一个 -------------------------------------------
    def run_per_account(self, logins: list[str], dims: Optional[list[str]] = None,
                        on_progress: Optional[Callable] = None) -> dict:
        dims = dims or list(DIMS)
        todo_dims = {d: self.writers[d] for d in dims}
        for i, lg in enumerate(logins, 1):
            for dim, w in todo_dims.items():
                if w.done(lg):
                    continue
                w.write(self._unit(dim, lg))
            if on_progress and i % 200 == 0:
                on_progress("all", i, len(logins))
        return {d: self.writers[d].stats() for d in dims}

    def run(self, logins: list[str], dims: Optional[list[str]] = None,
            mode: str = "per_dim", **kw) -> dict:
        if mode == "per_dim":
            return self.run_per_dim(logins, dims, **kw)
        if mode == "per_account":
            return self.run_per_account(logins, dims, **kw)
        raise ValueError(f"未知 mode: {mode}（可选 per_dim / per_account）")

    def stats(self) -> dict:
        return {d: self.writers[d].stats() for d in DIMS}


# ---------------------------------------------------------------------------
# 六、聚合视图：两种模式产出的文件**完全相同**，聚合代码只有一份
# ---------------------------------------------------------------------------
def load_account(tag: str, login: str, out_dir: str = "data/raw") -> dict:
    """把某人的所有维度拼成一条记录。**不关心当初是按人采还是按维采。**

    兼容两种落盘结构：
      - 新格式：`payload` 字段装维度数据
      - 旧格式：维度数据平铺在记录里（D1 的 `d1`、D13 的 `cells` 等）
    """
    lg = login.lower()
    acc: dict[str, Any] = {"login": lg}
    for dim in DIMS:
        path = dim_path(dim, tag, out_dir)
        if not os.path.exists(path):
            continue
        best = None
        for line in open(path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue   # 半行/坏行：跳过即可，下一轮会重采
            if (r.get("login") or "").lower() != lg:
                continue
            oc = r.get("outcome") or legacy_to_outcome(r).value
            if best is None or oc == Outcome.OK.value:
                best = (oc, _payload_of(r, dim))
        if best:
            usable = best[0] in {o.value for o in USABLE}
            acc[f"{dim}_outcome"] = best[0]
            acc[f"{dim}_data"] = best[1] if usable else None
    return acc


_ENVELOPE = {"login", "dim", "outcome", "http", "ts", "attempt", "note", "payload"}


def _payload_of(rec: dict, dim: str) -> dict:
    """取出一条记录里的"维度数据"，新老格式都能取。"""
    if isinstance(rec.get("payload"), dict) and rec["payload"]:
        return rec["payload"]
    # 旧格式：数据平铺，且常带一个与维度同名的嵌套键（如 d1）
    nested = rec.get(dim.lower())
    if isinstance(nested, dict):
        return nested
    return {k: v for k, v in rec.items() if k not in _ENVELOPE}


if __name__ == "__main__":
    # 自检：不联网，只验证分类与去重逻辑
    assert classify_http(200, {"a": 1}) == Outcome.OK
    assert classify_http(200, None) == Outcome.EMPTY
    assert classify_http(404) == Outcome.NOT_FOUND
    assert classify_http(409) == Outcome.EMPTY
    assert classify_http(401) == Outcome.AUTH
    assert classify_http(403, note="API rate limit exceeded") == Outcome.RATE_LIMIT
    assert classify_http(403) == Outcome.PRIVATE
    assert classify_http(429) == Outcome.RATE_LIMIT
    assert classify_http(503) == Outcome.NETWORK
    assert classify_http(None) == Outcome.NETWORK
    assert Outcome.OK in TERMINAL and Outcome.NETWORK in RETRYABLE
    assert legacy_to_outcome({"http": 0, "per_repo": [{"commits": None}]}) == Outcome.NOT_FOUND
    assert legacy_to_outcome({"http": 0, "per_repo": []}) == Outcome.NETWORK
    assert legacy_to_outcome({"status": "gone"}) == Outcome.DELETED
    w = Writer("_selftest.jsonl")
    w.write(Record(login="A", dim="D1", outcome="NETWORK", http=None))
    assert not w.done("a"), "可重试态不应算完成"
    w.write(Record(login="A", dim="D1", outcome="OK", http=200, payload={"x": 1}))
    assert w.done("a"), "成功后应算完成"
    w.write(Record(login="A", dim="D1", outcome="NETWORK", http=None))
    assert w.done("a"), "失败不应覆盖成功"
    os.remove("_selftest.jsonl")
    print("collect_core 自检通过：分类 11 条 / 去重 3 条")
