# -*- coding: utf-8 -*-
"""TV 适配器 —— AF 作为 **MCP 客户端**向 TVPilot 调 `af_morning_news`（G1 早间新闻·纯 A 链）。

本文件是《AF ↔ TVPilot 联动契约》v2.x 的 AF 侧**待命件**（AF-PM 卡 2026-11-15）：落码＋单测，
⛔ 不接线、⛔ 不动 `af_orchestrator.py`、⛔ 不合入。落形是 `do` + `adapter: tv` + `action: morning_news`
（AF 的 kind 词表封闭 8 枚，⛔ 不新增节点 kind）。

⛔ 本层不碰设备：唤醒／ADB／IPTV／选台全在 TVPilot 侧编排（`tvpilot/af_morning_news.py` 的
`MorningNewsOrchestrator.run()`），AF 只调**一个** MCP 工具、只映射回执。

**回执两形状**（对端 `trigger` L240-248／`run` L250-254 现读，本层照此透传）：

1. **幂等通道**（执行中重复调用）：`{accepted: false, reason: "already_running", request_id, last}`
   ⇒ 原样透传，`last`（上一次完整回执）带回调用方。⛔ 不排队、⛔ 不重跑、⛔ 不自造终态。
   `accepted=False` 是对端的**合法答复**，不是失败——要不要当成"这次没做成"由 IR 按 `data.accepted`
   分叉，⛔ 不归本层判。
2. **终态回执**（对端 `_terminal` L212-238 ＋ `_finish` L271-275 补 `total_ms`）：`result`／`stage`／
   `gap`／`detail`／`af_speech`／`s3_s4`／`screen_on_after`／`steps[]`／`total_ms` 九键逐字映射，
   ⛔ 不多收不少收（`action`／`request_id` 是 AF 侧相关键，对端没有、也不是对端新增的语义）。

**四格"值可能就是空"⛔ 不许当成功判据**（AF-PM 亲读对端后点名的坑）：

- `s3_s4`：`result ∉ (iptv_ok, iptv_fail)` 时为空 dict（对端 L217-218）⇒ 按键常在、值可空建模，
  ⛔ 不当必有子键（`s3_tv_foreground_app` / `s4_tv_current_channel` 两枚只在两支里出现）；
- `gap`／`af_speech`：可为 null（休眠两值的话术就是 `None`）；
- `screen_on_after`：对端 L227 **硬编码 None**（G6 未落码，TV 侧收口项；v3 冻结措辞「冻结期允许 null」）
  ⇒ ⛔ 不做非空断言、⛔ 不当成功判据。

**result 六值**：活跃四值 `iptv_ok`／`adb_off_tv_on`（stage=done）、`wake_fail`／`iptv_fail`（stage=failed）；
休眠两值 `faceid_timeout`／`arcface_fail` 是「**可识别但不可期待**」（契约 v2.1 纯 A、一期零 faceid）——
⛔ 不写成 nullable、⛔ 不写「可能出现 null」：枚举校验保留全六值，但**收到即判 TVPilot 实现越界**
（`_interpret` 走缺陷路径：`CallResult.fail` ＋ `ADM_ERR_INTERNAL` ＋ `defect=dormant_result_in_phase1`
留痕），⛔ 按正常 failed 终态收尾冒充，⛔ 当成"这次没做成"的业务答复放过。

**形状塌 vs 业务答复**（与 `ma.py` 同口径）：对端明说"没成／正在跑"是可兜底的业务答复 ⇒ `CallResult`；
**读空冒充成功**（缺九键、result 不在枚举、`steps` 非对象列表、`total_ms` 非数值）⇒ 抛 `AdapterError`
＋`ADM_ERR_PAYLOAD_INVALID`，⛔ 不让 `result_var` 静默绑成空值再当真。

**传输面**：`client` 注入（`TVMCPClient` Protocol，形如 `af_orchestrator.MCPClient.call(tool, **kwargs)`），
⛔ 本层不写死 URL／凭据——v3 冻结后由 AF-PM 给连接参数、接线卡注客户端。传输层超时归客户端执行
（`base.py`：套接超时留在适配器/传输层，业务超时由 IR 的 `wait`＋`on_timeout` 表达）；本层只把
客户端抛出的超时／通道故障**映射**成 `adm.errors` 具名码。`DEFAULT_TV_TRANSPORT_TIMEOUT=250s`
＝对端 `BUDGET_S 240s` ＋ 10s 余量，接线时作为客户端的超时参数。

`dry_run=True` 是缺省档（与 `ma.py` 同口径）：只在进程内回一条"这次会调什么"，一个字节都不出网、
⛔ 不伪造回执值。⛔ 也不落意图清单——待命件不需要无界写；接线卡要做仿真效果展开时，照 `ma.py`
的封顶环形清单（`INTENTS_MAX`）补，⛔ 不要在这里先长长一条。
"""

from __future__ import annotations

import socket
import uuid
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError

from homesdk.adm.errors import (
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
    ADM_ERR_PEER_OFFLINE,
    ADM_ERR_UPSTREAM_TIMEOUT,
)

from .base import AdapterError, CallResult

__all__ = [
    "ACTIVE_RESULTS",
    "DEFAULT_TV_TRANSPORT_TIMEOUT",
    "DEFECT_DORMANT_RESULT",
    "DORMANT_RESULTS",
    "PARAMS_ALLOWED",
    "RESULTS",
    "TERMINAL_KEYS",
    "TOOL_MORNING_NEWS",
    "TV_ACTIONS",
    "TVMCPClient",
    "TvAdapter",
    "action_of",
    "defect_of_result",
    "map_terminal",
    "new_request_id",
]

#: 对端 MCP 工具名（链主从 TV 仓读出、AF-PM 亲读 `af_morning_news.py` 核对）。
TOOL_MORNING_NEWS = "af_morning_news"

#: AF 侧动作名 ⇒ 工具名。`do` + `adapter: tv` + `action: morning_news`；`tv.morning_news` 两种写法
#: 都归一到后者（`action_of` 剥前缀，与 `ma.action_of`／`inbox.kind_of` 同形）。
TV_ACTIONS: dict[str, str] = {"morning_news": TOOL_MORNING_NEWS}

#: 允许出现在 IR `params` 里的键名：**只有** `request_id`（对端 `run(request_id=None)` 的唯一入参）。
#: 名单外的键不透传——那是"参数未声明却可用"那一族。
PARAMS_ALLOWED = frozenset({"request_id"})

#: 活跃四值（一期真会出现的）与休眠两值（枚举保留、一期不会出现）。
ACTIVE_RESULTS = ("iptv_ok", "adb_off_tv_on", "wake_fail", "iptv_fail")
DORMANT_RESULTS = ("faceid_timeout", "arcface_fail")
RESULTS = ACTIVE_RESULTS + DORMANT_RESULTS

#: 休眠值的缺陷标记（契约 v2.1 纯 A／一期零 faceid：对端发它＝实现越界，不是业务失败）。
DEFECT_DORMANT_RESULT = "dormant_result_in_phase1"

#: 终态回执九键（对端 `_terminal` 的键集，`total_ms` 由 `_finish` 补成数值）。⛔ 不多收不少收。
TERMINAL_KEYS = (
    "result",
    "stage",
    "gap",
    "detail",
    "af_speech",
    "s3_s4",
    "screen_on_after",
    "steps",
    "total_ms",
)

#: 传输层超时（秒）＝对端 `BUDGET_S=240s` 全链预算 ＋ 10s 余量：这是一次**长调用**，
#: 照 `ma.py` 的 10s 会一刀切在链中途，照"无超时"又会单进程挂死 ⇒ 预算＋余量。
DEFAULT_TV_TRANSPORT_TIMEOUT = 250.0

#: 客户端抛出的"超时"三种写法都要认：`socket.timeout`、内建 `TimeoutError`（3.11 起含
#: `concurrent.futures.TimeoutError`）。⛔ 只认其中一种会把另外两种误映射成"对端不在线"。
_TIMEOUT_EXC = (socket.timeout, TimeoutError)

#: HTTP 侧的超时／鉴权／对端故障三档（与 `ma.py::_post` 同一张表，⛔ 不另定一套）。
_HTTP_TIMEOUT_CODES = frozenset({408, 504})
_HTTP_AUTH_CODES = frozenset({401, 403})


@runtime_checkable
class TVMCPClient(Protocol):
    """注入的 MCP 客户端（形如 `af_orchestrator.MCPClient`）：`call(tool, **kwargs) -> Mapping`。"""

    def call(self, tool: str, **kwargs: Any) -> Mapping[str, Any]: ...


class _UpstreamFailure(Exception):
    """通道／鉴权／对端层面的失败，携带 `adm.errors` 具名码。

    私有异常：在本模块内就被映射成 `CallResult.fail`，⛔ 不冒到执行器——"对端没答上"是可兜底的
    业务结果，走 IR 的 `on_error`；只有**读空冒充成功**才抛 `AdapterError`。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def action_of(action: str) -> str:
    """`tv.morning_news` 与 `morning_news` 两种写法都归一到后者（与 `ma.action_of` 同形）。"""
    return str(action or "").strip().lower().rsplit(".", 1)[-1]


def new_request_id() -> str:
    """缺省自生成的幂等键（uuid4）：对端用它判"同一轮重复调用"，⛔ 不用时间戳等可撞值。"""
    return str(uuid.uuid4())


def defect_of_result(result: Any) -> str | None:
    """休眠两值 ⇒ 返回缺陷标记；其余（含枚举外的脏值）返回 None。

    「可识别但不可期待」（AF-PM 2026-11-15 · 契约 v3 冻结稿 §3.2）：`faceid_timeout`／`arcface_fail`
    在**纯 A 一期**根本没有产生路径（无问询前采脸、无 ArcFace 前置闸）⇒ 对端真发出这枚值，只能是
    TVPilot 侧实现越界（把二期支线接进了一期链路，或把占位值当真值落盘）。这不是"这次没做成"
    的业务失败，⛔ 不许按正常 `failed` 终态收尾冒充——要么修对端、要么二期放闸，⛔ 在 AF 适配器里
    静默咽下。枚举外的脏值归 `map_terminal` 的形状判据（`ADM_ERR_PAYLOAD_INVALID`），⛔ 在这里兜。
    """
    return DEFECT_DORMANT_RESULT if result in DORMANT_RESULTS else None


def _is_number(value: Any) -> bool:
    """`bool` 是 `int` 的子类，`True` 不能算"数值回执"——这里显式排掉。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _failure_of(exc: BaseException, *, budget_s: float) -> _UpstreamFailure:
    """把客户端抛出的一切通道异常映射成具名码（⛔ 静默吞、⛔ 归成 generic 字符串）。"""
    if isinstance(exc, _TIMEOUT_EXC):
        return _UpstreamFailure(
            ADM_ERR_UPSTREAM_TIMEOUT,
            f"tv 未在 {budget_s:.0f}s 内应答（对端全链预算 240s；超时不是失败终态，由 IR 兜）",
        )
    if isinstance(exc, HTTPError):
        code = getattr(exc, "code", 0)
        if code in _HTTP_TIMEOUT_CODES:
            return _UpstreamFailure(
                ADM_ERR_UPSTREAM_TIMEOUT,
                f"tv 超时（HTTP {code}；对端全链预算 240s，超时不等于失败终态，由 IR 兜）",
            )
        if code in _HTTP_AUTH_CODES:
            return _UpstreamFailure(
                ADM_ERR_AUTH_REQUIRED, f"tv 拒绝凭据（HTTP {code}）"
            )
        if code >= 500:
            return _UpstreamFailure(ADM_ERR_PEER_OFFLINE, f"tv 侧故障（HTTP {code}）")
        return _UpstreamFailure(ADM_ERR_INTERNAL, f"tv 返回 HTTP {code}")
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, _TIMEOUT_EXC):
            return _UpstreamFailure(
                ADM_ERR_UPSTREAM_TIMEOUT, "tv 未在传输层超时内应答"
            )
        return _UpstreamFailure(
            ADM_ERR_PEER_OFFLINE, f"tv 不可达：{type(reason).__name__}: {reason}"
        )
    if isinstance(exc, OSError):
        return _UpstreamFailure(ADM_ERR_PEER_OFFLINE, f"tv 不可达：{type(exc).__name__}: {exc}")
    return _UpstreamFailure(
        ADM_ERR_INTERNAL, f"tv 调用抛异常：{type(exc).__name__}: {exc}"
    )


def map_terminal(payload: Mapping[str, Any], *, request_id: str, action: str = "morning_news") -> dict[str, Any]:
    """把对端终态回执映射成 `CallResult.data`：九键逐字 + AF 侧两枚相关键。

    形状塌一律抛 `AdapterError`（⛔ 静默放行）：缺键、`result` 不在六值、`s3_s4` 不是对象、
    `steps` 不是对象列表、`total_ms` 不是数值 —— 这五种都是"读到了个空壳却想冒充终态"。
    ⛔ 反过来**不**校验：`s3_s4` 内部的子键是否齐（两支不同形）、`gap`/`af_speech`/`screen_on_after`
    是否为 null（对端就是这么发的）、`stage` 与 `result` 的对应关系（那是契约 §三 的表，
    漂了该红在 TV 侧与契约核对，⛔ 不在 AF 适配器里反推）。
    """
    missing = [key for key in TERMINAL_KEYS if key not in payload]
    if missing:
        raise AdapterError(
            f"{ADM_ERR_PAYLOAD_INVALID}: tv.{action} 的终态回执缺键 {missing}"
            f"（⛔ 读空冒充成功；对端 `_terminal` 的键集见 `af_morning_news.py` L220-230）"
        )
    result = payload.get("result")
    if result not in RESULTS:
        raise AdapterError(
            f"{ADM_ERR_PAYLOAD_INVALID}: tv.{action} 的 result={result!r} 不在六值枚举 {list(RESULTS)}"
        )
    if not isinstance(payload.get("s3_s4"), Mapping):
        raise AdapterError(
            f"{ADM_ERR_PAYLOAD_INVALID}: tv.{action} 的 s3_s4 不是对象"
            "（可为空 dict——`result ∉ (iptv_ok, iptv_fail)` 时对端就发 `{}`——但⛔ 不许整个缺形状）"
        )
    steps = payload.get("steps")
    if not isinstance(steps, list) or any(not isinstance(item, Mapping) for item in steps):
        raise AdapterError(
            f"{ADM_ERR_PAYLOAD_INVALID}: tv.{action} 的 steps 不是对象列表"
            "（元素形如 {step, status, evidence, at}；本层⛔ 不逐键硬要，但⛔ 不把非列表当空链）"
        )
    total_ms = payload.get("total_ms")
    if not _is_number(total_ms):
        raise AdapterError(
            f"{ADM_ERR_PAYLOAD_INVALID}: tv.{action} 的 total_ms={total_ms!r} 不是数值"
            "（对端 `_finish` 必把它补成数值；拿到 None 说明终态没走完，⛔ 不自造）"
        )
    data: dict[str, Any] = {"action": action, "request_id": request_id}
    for key in TERMINAL_KEYS:
        data[key] = payload[key]
    return data


class TvAdapter:
    """单次调用、无重试、无降级、无语义层超时（协议见 `base.Adapter`）。

    ⛔ 不排队：对端回答 `already_running` 时原样带回 `last`，重试/退避由 IR 用
    `wait` + `on_error`（或再来一次 `do`）显式表达。
    """

    name = "tv"

    def __init__(
        self,
        client: TVMCPClient | None = None,
        *,
        dry_run: bool = True,
        transport_timeout: float = DEFAULT_TV_TRANSPORT_TIMEOUT,
    ) -> None:
        self._client = client
        self.dry_run = dry_run
        self.transport_timeout = float(transport_timeout)

    # ── 调用面 ─────────────────────────────────────────────────────
    def call(self, action: str, params: Mapping[str, Any] | None = None) -> CallResult:
        name = action_of(action)
        tool = TV_ACTIONS.get(name)
        if tool is None:
            # 名单外的动作名是 AF 侧的写法错，不是对端的回答 ⇒ 抛出去（`AdapterError`），
            # 让它在编译/仿真面响亮，而不是静默返回一条"没做成"。
            raise AdapterError(
                f"{ADM_ERR_PAYLOAD_INVALID}: tv 适配器不认的动作 {action!r}"
                f"（已定义：{sorted(TV_ACTIONS)}）"
            )
        fields = dict(params or {})
        unknown = sorted(key for key in fields if key not in PARAMS_ALLOWED)
        if unknown:
            return CallResult.fail(
                f"tv.{name} 不接受这些参数：{unknown}（允许：{sorted(PARAMS_ALLOWED)}）",
                code=ADM_ERR_PAYLOAD_INVALID,
                action=name,
            )
        request_id = str(fields.get("request_id") or "").strip() or new_request_id()

        if self.dry_run:
            return CallResult.ok(
                {"dry_run": True, "action": name, "tool": tool, "request_id": request_id}
            )

        if self._client is None:
            return CallResult.fail(
                f"tv.{name} 未注入 MCP 传输客户端（连接参数由接线卡给，⛔ 本层不写死 URL/凭据），拒发",
                code=ADM_ERR_AUTH_REQUIRED,
                action=name,
                request_id=request_id,
            )

        try:
            payload = self._client.call(tool, request_id=request_id)
        except _UpstreamFailure as exc:  # pragma: no cover - 私有异常只在内部产生
            return CallResult.fail(
                exc.message, code=exc.code, action=name, request_id=request_id
            )
        except Exception as exc:  # noqa: BLE001 - 通道异常一律映射成具名码，⛔ 不冒到执行器
            failure = _failure_of(exc, budget_s=self.transport_timeout)
            return CallResult.fail(
                failure.message, code=failure.code, action=name, request_id=request_id
            )
        return self._interpret(name, payload, request_id)

    # ── 回执两形状 ─────────────────────────────────────────────────
    @staticmethod
    def _interpret(name: str, payload: Any, request_id: str) -> CallResult:
        """终态回执／幂等答复二分；其余形状按"塌"处理，⛔ 不猜。"""
        if not isinstance(payload, Mapping):
            raise AdapterError(
                f"{ADM_ERR_PAYLOAD_INVALID}: tv.{name} 的回执不是对象"
                f"（type={type(payload).__name__}）"
            )
        if "result" in payload:
            outcome = map_terminal(payload, request_id=request_id, action=name)
            defect = defect_of_result(outcome["result"])
            if defect is not None:
                # 休眠两值 ⇒ 缺陷路径（AF-PM 2026-11-15 · 契约 v3 冻结稿 §3.2）：
                # `ADM_ERR_INTERNAL` ＝ 对端实现越界；`defect` 留痕便于 QA 计数；
                # ⛔ 不把它收成 `success=True` 的正常 `failed` 终态（那等于替对端圆谎）。
                return CallResult.fail(
                    f"tv.{name} 收到休眠值 result={outcome['result']}：契约 v2.1 纯 A／一期零 faceid "
                    "没有产生它的路径 ⇒ 判 TVPilot 实现越界（缺陷），⛔ 不按正常 failed 终态收尾",
                    code=ADM_ERR_INTERNAL,
                    action=name,
                    request_id=request_id,
                    result=outcome["result"],
                    stage=outcome["stage"],
                    defect=defect,
                )
            return CallResult.ok(outcome)
        if payload.get("accepted") is False:
            last = payload.get("last")
            if not isinstance(last, Mapping):
                raise AdapterError(
                    f"{ADM_ERR_PAYLOAD_INVALID}: tv.{name} 的幂等答复缺 last 对象"
                    "（对端 `trigger` 必带 `last=dict(self._last)`；首轮为空 dict 也算有）"
                )
            return CallResult.ok(
                {
                    "action": name,
                    "request_id": str(payload.get("request_id") or request_id),
                    "accepted": False,
                    "reason": payload.get("reason"),
                    "last": dict(last),
                }
            )
        if payload.get("accepted") is True:
            # 只回了"收到"没有终态：对端 `run()` 的终态必达被违约。⛔ 不自造 result、⛔ 不重跑一轮，
            # 交给 IR 的 `on_error`（这是可兜底的业务结果，不是形状塌——键形本身是对的）。
            return CallResult.fail(
                f"tv.{name} 只回了受理（accepted=True）没有终态回执——对端终态必达违约，"
                "本层⛔ 不自造终态、⛔ 不重跑",
                code=ADM_ERR_PAYLOAD_INVALID,
                action=name,
                request_id=request_id,
            )
        raise AdapterError(
            f"{ADM_ERR_PAYLOAD_INVALID}: tv.{name} 的回执既不是终态也不是幂等答复"
            f"（键={sorted(payload)}）"
        )
