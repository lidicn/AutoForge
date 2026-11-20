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

import json
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from homesdk.adm.errors import (
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
    ADM_ERR_PEER_OFFLINE,
    ADM_ERR_UPSTREAM_TIMEOUT,
)

from .base import AdapterError, CallResult
from .http import guarded_open, host_of

__all__ = [
    "ACTIVE_RESULTS",
    "DEFAULT_TV_TRANSPORT_TIMEOUT",
    "DEFECT_DORMANT_RESULT",
    "DORMANT_RESULTS",
    "GRACE_S",
    "PARAMS_ALLOWED",
    "RESULTS",
    "RUN_BUDGET_S",
    "SPEECH_BY_RESULT",
    "TERMINAL_DEADLINE_S",
    "TERMINAL_KEYS",
    "TIMEOUT_SPEECH",
    "TOOL_MORNING_NEWS",
    "TV_ACTIONS",
    "TV_ENDPOINT_ENV",
    "TVMCPClient",
    "TvAnnouncement",
    "TvAdapter",
    "TvMcpHttpClient",
    "TvOutcomeSpeaker",
    "action_of",
    "defect_of_result",
    "map_terminal",
    "new_request_id",
    "speech_of",
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

#: 端点与凭据的读取名（照 `ma.py` 先例：secret 文件 > 环境变量）。**缺省空串**——⛔ 不把联调窗
#: 那台机器的地址／任何凭据写死进仓（本仓 public）。接线窗由 AF-PM 从外面注入（`build_runtime`
#: 的 `tv_endpoint` 参数，或这两个环境变量），空串 ⇒ `call()` 显式 `CallResult.fail`，⛔ 不猜地址。
TV_ENDPOINT_ENV = "AUTOFORGE_TV_MCP_URL"
#: LAN 内的 TVPilot MCP 端点**无鉴权**（AF-PM 卡 2026-11-18）⇒ 凭据是**可选**的：空串时不发
#: `Authorization` 头。留着这个入口是因为二期若有反代／令牌，改配置即可，不必改代码。
TV_TOKEN_ENV = "AUTOFORGE_TV_MCP_TOKEN"

#: MCP 握手口径（与 `ma.MCP_PROTOCOL_VERSION`／`CLIENT_NAME` 同值：同一个 AF 客户端身份）。
MCP_PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "autoforge"

#: 响应体上限：TVPilot 回执带 `steps[]` 全链证据，给足 1 MiB；超了拒收（⛔ 无界吞）。
MAX_RESPONSE_BYTES = 1024 * 1024

#: ── 话术面（AF-PM 卡 2026-11-18 第 4 项｜契约 v3 §2.2 表**逐字**）────────────────
#:
#: 六值里活跃四值各有逐字话术；**休眠两值是 `None`＝静默**（一期零 faceid，出资人 2026-11-14 裁
#: "编排失败不播报"）。⛔ 不许在这里造新措辞、⛔ 不许把 None 改成空串糊过去（空串会被 speak 腿判成
#: `empty_text` 记一次失败，不等于静默）。
SPEECH_BY_RESULT: dict[str, str | None] = {
    "iptv_ok": "新闻已打开",
    "adb_off_tv_on": "电视已打开；ADB 未开启，新闻请打开 tvdesk 手动观看",
    "wake_fail": "电视唤醒失败",
    "iptv_fail": "电视已打开，新闻没能自动打开，请手动观看",
    "faceid_timeout": None,
    "arcface_fail": None,
}

#: 兜底话术（契约 v3 §二-1.1 超时行）：全链预算 240s ＋ 60s 宽限到点仍没有终态回执时播这一句。
TIMEOUT_SPEECH = "电视联动执行超时"

#: 全链预算＝对端 `BUDGET_S`；宽限 60s 是给 MCP 传输与 AF 侧排程的余量；两者之和＝AF 侧兜底线。
RUN_BUDGET_S = 240.0
GRACE_S = 60.0
TERMINAL_DEADLINE_S = RUN_BUDGET_S + GRACE_S

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


class _SessionExpired(Exception):
    """AF-822（墨鉴 N-15）：对端会话失效（HTTP 404）——**私有信号**，`call()` 捕获后
    置 `_session_id=None` 重握手一次（限一次防循环），二次仍失败才具名报错。
    ⛔ 不进 CallResult：这是传输层内部的可恢复态，不是业务结果。
    """


def action_of(action: str) -> str:
    """`tv.morning_news` 与 `morning_news` 两种写法都归一到后者（与 `ma.action_of` 同形）。"""
    return str(action or "").strip().lower().rsplit(".", 1)[-1]


def new_request_id() -> str:
    """缺省自生成的幂等键（uuid4）：对端用它判"同一轮重复调用"，⛔ 不用时间戳等可撞值。"""
    return str(uuid.uuid4())


def speech_of(result: Any) -> str | None:
    """result 六值 → 小爱话术（契约 v3 §2.2 表逐字）；静默两值／枚举外一律 `None`。

    纯函数、不看回执其他键：话术只由 `result` 决定（`af_speech` 那枚对端键是**对端**的话术文本，
    落 `CallResult.data` 供留痕与逐字比对，⛔ 不是 AF 播报的真源——真源是这张表，否则对端改一个字
    就改掉了家里的播报）。
    """
    return SPEECH_BY_RESULT.get(str(result or ""), None)


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
        endpoint: str = "",
        token: str = "",
        dry_run: bool = True,
        transport_timeout: float = DEFAULT_TV_TRANSPORT_TIMEOUT,
        allowed_hosts: tuple[str, ...] = (),
    ) -> None:
        """两种接法二选一（AF-PM 卡 2026-11-18 第 3 项）：

        - **注入客户端**（`client=`）：单测与将来任何非 HTTP 传输；
        - **注入端点**（`endpoint=`／`AUTOFORGE_TV_MCP_URL`）：接线窗的常态——本类内部按
          `ma.py::_tools_call` 那一族的形状走 streamable-http MCP（initialize → tools/call）。
          `endpoint` 缺省**空串**：⛔ 不写死联调窗地址、⛔ 不内置任何凭据；空 ⇒ `call()` 显式拒发。
        """
        self._client = client
        self._endpoint = str(endpoint or "")
        self._token = str(token or "")
        self.dry_run = dry_run
        self.transport_timeout = float(transport_timeout)
        self._allowed_hosts = tuple(h for h in allowed_hosts if h)
        self._http: TvMcpHttpClient | None = None

    # ── 端点解析（照 `ma.py::_resolve_endpoint`／`_resolve_token` 同形）────────────
    def _resolve_endpoint(self) -> str:
        endpoint = self._endpoint or os.environ.get(TV_ENDPOINT_ENV, "")
        if not endpoint:
            return ""
        base = str(endpoint).rstrip("/")
        # TVPilot 把 MCP 挂在 `/mcp`：只给 host:port 时补上那一段（与 `ma` 同一手，两线口径一致）。
        parsed = urlparse(base)
        if parsed.scheme and parsed.hostname and parsed.path in ("", "/"):
            base = f"{base}/mcp"
        return base

    def _resolve_token(self) -> str:
        if self._token:
            return self._token
        from ..af_secrets import load_secret

        return str(load_secret(TV_TOKEN_ENV, ""))

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

        client = self._client
        if client is None:
            endpoint = self._resolve_endpoint()
            if not endpoint:
                # 缺省档（端点空串）不是"配错了"，是「没接线」⇒ 显式拒发，⛔ 不猜地址、⛔ 不静默 dry_run。
                return CallResult.fail(
                    f"tv.{name} 未注入 MCP 客户端且未配置端点（{TV_ENDPOINT_ENV}），拒发"
                    "（连接参数由接线卡给，⛔ 本层不写死 URL/凭据）",
                    code=ADM_ERR_AUTH_REQUIRED,
                    action=name,
                    request_id=request_id,
                )
            if self._http is None:
                self._http = TvMcpHttpClient(
                    endpoint,
                    token=self._resolve_token(),
                    transport_timeout=self.transport_timeout,
                    allowed_hosts=self._allowed_hosts,
                )
            client = self._http

        try:
            payload = client.call(tool, request_id=request_id)
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


# ── 传输面：streamable-http MCP 客户端（批T 真接线｜AF-PM 卡 2026-11-18 第 3 项）──────
#
# 为什么这里有一份"像 ma.py"的实现：`ma.py` 那套 HTTP/JSON-RPC 编码是本仓已经验过的形状
# （SSE／JSON 双解码、会话头、白名单收口），但它是**私有实现**（`_post`／`_tools_call`），
# 跨模块 import 私有名会把两条线焊死，任一侧改签名都是隐性破线；而把 `ma.py` 抽公共层是动
# **已双票 PASS 的待命件**（⛔ 本卡红线）。所以本文件独立一份，口径与 `ma.py` 逐处对齐
# （超时/鉴权/对端故障三档映射、`guarded_open` 收口、`structuredContent` 优先回落 text），
# 并把「抽公共 MCP 传输层」写成报告 §八 的后续项，交 AF-PM 排（⛔ 本卡不自行重构）。


def _decode_envelope(raw: bytes, content_type: str, msg_id: Any) -> dict[str, Any]:
    """把一次 POST 的响应体解成 JSON-RPC 信封；解不出一律具名失败（⛔ 不静默当空答复）。"""
    text = raw.decode("utf-8", errors="replace")
    candidates: list[Any] = []
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    candidates.append(json.loads(chunk))
                except (ValueError, TypeError):
                    continue
    else:
        try:
            candidates.append(json.loads(text))
        except (ValueError, TypeError) as exc:
            raise _UpstreamFailure(
                ADM_ERR_PAYLOAD_INVALID, f"tv 响应不是合法 JSON：{type(exc).__name__}"
            ) from exc
    matched = None
    for item in candidates:
        if not isinstance(item, dict):
            continue
        # AF-823（墨鉴 N-16）：**通知帧显式跳过**——无 `id` 的帧是对端主动推送（如
        # `notifications/*`），⛔ 不许被当成本次请求的答复收下。
        if "id" not in item:
            continue
        if msg_id is None or item.get("id") == msg_id:
            matched = item
            break
    if matched is not None:
        return matched
    # AF-823：id 不匹配一律具名失败，⛔ 回退 candidates[-1]——旧回退最坏会把对端
    # 的通知/别次响应当成本次答复（违反「失败要具名、成功要有据」）。
    raise _UpstreamFailure(
        ADM_ERR_PAYLOAD_INVALID,
        f"tv 响应里没有 id={msg_id!r} 的 JSON-RPC 信封（收到 {len(candidates)} 帧，"
        "id 不匹配一律失败，⛔ 回退）",
    )


def _tool_payload(name: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """MCP `tools/call` 的 result → 业务 dict（结构化内容优先，回落 text 腿；与 `ma` 同口径）。"""
    if result.get("isError") is True:
        text = next(
            (
                str(item.get("text") or "")
                for item in (result.get("content") or [])
                if isinstance(item, Mapping)
            ),
            "",
        )
        raise _UpstreamFailure(ADM_ERR_INTERNAL, f"tv.{name} 被 TVPilot 判为出错：{text[:200]}")
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        return dict(structured)
    for item in result.get("content") or []:
        if not isinstance(item, Mapping) or item.get("type") not in (None, "text"):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise _UpstreamFailure(
                ADM_ERR_PAYLOAD_INVALID,
                f"tv.{name} 的 text 载荷不是合法 JSON：{type(exc).__name__}",
            ) from exc
        if isinstance(parsed, Mapping):
            return dict(parsed)
        raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, f"tv.{name} 的 text 载荷不是对象")
    raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, f"tv.{name} 的 result 里没有结构化内容")


class TvMcpHttpClient:
    """`TVMCPClient` 的 HTTP 实现：initialize → tools/call，一次调用一次往返。

    会话（`Mcp-Session-Id`）每个实例只握一次；**过期不在这里重试**——纯执行层契约不允许适配器
    兜重试（`base.py` 头注），重取由 IR 的 `wait`＋`on_error` 显式表达。
    """

    def __init__(
        self,
        endpoint: str,
        *,
        token: str = "",
        transport_timeout: float = DEFAULT_TV_TRANSPORT_TIMEOUT,
        allowed_hosts: tuple[str, ...] = (),
    ) -> None:
        self.endpoint = str(endpoint or "")
        self.host = host_of(self.endpoint)
        self._token = str(token or "")
        self.transport_timeout = float(transport_timeout)
        self._allowed_hosts = tuple(h for h in allowed_hosts if h)
        self._session_id: str | None = None
        self._rpc_id = 0

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _post(self, body: Mapping[str, Any]) -> dict[str, Any]:
        if not self.host:
            # AF-815（墨鉴 PM 自抓 tv.py:596）：旧文案 `{self.endpoint!r}` 把**完整 endpoint**
            # 原样回显——endpoint 可能带 `user:pass@` 内嵌凭据，回执外泄给持令牌的 agent
            # 等于把凭据原文交出去。⛔ 不回显 URL，只报「缺主机名或含 @ 凭证注入」。
            raise _UpstreamFailure(
                ADM_ERR_AUTH_REQUIRED,
                f"tv 端点缺主机名或含 @ 凭证注入（拒发，⛔ 不猜地址、⛔ 不回显 endpoint）",
            )
        req = Request(
            self.endpoint,
            data=json.dumps(dict(body)).encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        if self._session_id:
            req.add_header("Mcp-Session-Id", self._session_id)
        hosts = self._allowed_hosts or (self.host,)
        try:
            with guarded_open(req, allowed_hosts=hosts, timeout=self.transport_timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                content_type = (resp.headers.get("content-type") or "").lower()
                sid = resp.headers.get("mcp-session-id") or self._session_id
        except HTTPError as exc:
            code = getattr(exc, "code", 0)
            if code in _HTTP_AUTH_CODES:
                raise _UpstreamFailure(
                    ADM_ERR_AUTH_REQUIRED, f"tv 拒绝凭据（HTTP {code}）"
                ) from exc
            if code in _HTTP_TIMEOUT_CODES:
                raise _UpstreamFailure(
                    ADM_ERR_UPSTREAM_TIMEOUT, f"tv 超时（HTTP {code}；对端全链预算 240s）"
                ) from exc
            if code >= 500:
                raise _UpstreamFailure(ADM_ERR_PEER_OFFLINE, f"tv 侧故障（HTTP {code}）") from exc
            if code == 404 and self._session_id:
                # AF-822（墨鉴 N-15）：streamable-http MCP 对端在会话过期时对带旧
                # `Mcp-Session-Id` 的请求回 404——旧实现归 ADM_ERR_INTERNAL 且**永不清会话**
                # ⇒ 适配器单例一张死会话污染进程后续所有 tv 动作直到 serve 重启，
                # 错误码还误导成 tv 侧故障。这里改抛私有 `_SessionExpired` 信号：
                # `call()` 捕获后置 `_session_id=None` 重握手一次（限一次防循环）。
                # ⚠️ 只对**带会话头**的请求生效：握手（无头）404 是端点错，仍归 INTERNAL。
                raise _SessionExpired("tv 会话失效（HTTP 404，携带旧 Mcp-Session-Id）") from exc
            raise _UpstreamFailure(ADM_ERR_INTERNAL, f"tv 返回 HTTP {code}") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise _UpstreamFailure(
                ADM_ERR_UPSTREAM_TIMEOUT, "tv 未在传输层超时内应答"
            ) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, _TIMEOUT_EXC):
                raise _UpstreamFailure(ADM_ERR_UPSTREAM_TIMEOUT, "tv 未在传输层超时内应答") from exc
            raise _UpstreamFailure(
                ADM_ERR_PEER_OFFLINE, f"tv 不可达：{type(reason).__name__}: {reason}"
            ) from exc
        except OSError as exc:
            raise _UpstreamFailure(
                ADM_ERR_PEER_OFFLINE, f"tv 不可达：{type(exc).__name__}: {exc}"
            ) from exc
        self._session_id = sid
        if len(raw) > MAX_RESPONSE_BYTES:
            raise _UpstreamFailure(
                ADM_ERR_PAYLOAD_INVALID,
                f"tv 响应超过上限 {MAX_RESPONSE_BYTES} 字节，拒收（⛔ 无界吞）",
            )
        if not raw.strip() and "id" not in body:
            # JSON-RPC 通知（`notifications/initialized`）按规范回 202 空体是**合法**的：
            # ⛔ 不许把"没有信封"判成形状塌（那会把每次握手都判成失败）。
            return {}
        return _decode_envelope(raw, content_type, body.get("id"))

    def _ensure_session(self) -> None:
        if self._session_id is not None:
            return
        envelope = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": CLIENT_NAME, "version": "1"},
                },
            }
        )
        if "error" in envelope:
            err = envelope["error"]
            raise _UpstreamFailure(
                ADM_ERR_INTERNAL,
                f"tv 拒绝握手：{str(err.get('message') if isinstance(err, Mapping) else err)[:200]}",
            )
        # 握手成功即会话可用；`notifications/initialized` 只是礼节，失败不影响后续 tools/call。
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, **kwargs: Any) -> Mapping[str, Any]:
        """发一次 `tools/call`。参数按 MCP 的 `arguments` 透传（本线只有幂等键一枚）。

        AF-822：会话失效（404）⇒ 置 `_session_id=None` 重握手后**重试一次**（限一次防循环）；
        二次仍失败才把 `_SessionExpired` 落成具名 `_UpstreamFailure`（"会话失效已重试"，
        ⛔ 再归 INTERNAL）。手写循环而不是递归：重试与首发共用同一段信封解析。
        """
        attempts = 0
        while True:
            self._ensure_session()
            try:
                envelope = self._post(
                    {
                        "jsonrpc": "2.0",
                        "id": self._next_id(),
                        "method": "tools/call",
                        "params": {"name": tool, "arguments": dict(kwargs)},
                    }
                )
                break
            except _SessionExpired:
                attempts += 1
                if attempts > 1:
                    raise _UpstreamFailure(
                        ADM_ERR_INTERNAL,
                        "tv 会话失效已重试一次仍 404（⛔ 不再循环重握手——查对端会话生命周期）",
                    ) from None
                self._session_id = None  # AF-822：丢弃死会话，下一轮 `_ensure_session` 重握手
                continue
        if "error" in envelope:
            err = envelope["error"]
            raise _UpstreamFailure(
                ADM_ERR_INTERNAL,
                f"tv 工具调用出错：{str(err.get('message') if isinstance(err, Mapping) else err)[:200]}",
            )
        result = envelope.get("result")
        if not isinstance(result, Mapping):
            raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, "tv 响应里没有 result 对象")
        return _tool_payload(tool, result)


# ── 消费面：result 六值 → 话术 → 播报（AF-PM 卡 2026-11-18 第 4 项）──────────────────


@dataclass(frozen=True)
class TvAnnouncement:
    """一次消费的留痕记录（⛔ 不抛异常：调用方拿它记账/落审计）。"""

    spoken: bool
    text: str | None
    result: str | None
    reason: str
    defect: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "spoken": self.spoken,
            "text": self.text,
            "result": self.result,
            "reason": self.reason,
            "defect": self.defect,
            "error": self.error,
        }


class TvOutcomeSpeaker:
    """把 `TvAdapter` 的回执消费成一声播报（契约 v3 §2.2 表逐字｜兜底 300s）。

    四条硬纪律：

    1. **话术真源＝`SPEECH_BY_RESULT`**，⛔ 不抄回执里的 `af_speech`（对端改一个字就改掉了家里
       的播报；回执那枚键仍落 `CallResult.data` 供逐字比对）；
    2. **静默两值**（休眠）与**幂等答复**（`accepted=False`）都不出声，⛔ 不拿"这次没做成"当语气；
    3. **兜底**：调用方把已经过的秒数交进来，超过 `240+60s` 仍无终态 ⇒ 播「电视联动执行超时」；
    4. **⛔ 异常不冒执行链**：`publish_speak` 抛错/回 `published=False` 一律吞成计数＋`reason`，
       与桥自己的 `_account_publish_error` 同一纪律（播报旁路失败绝不改动作结果）。
    """

    def __init__(
        self,
        publish_speak: Callable[..., Any] | None = None,
        *,
        deadline_s: float = TERMINAL_DEADLINE_S,
    ) -> None:
        self._publish = publish_speak
        self.deadline_s = float(deadline_s)
        self.counters: dict[str, int] = {
            "requested": 0,
            "spoken": 0,
            "silent": 0,
            "timeout": 0,
            "failed": 0,
            "defect": 0,
            "publish_failed": 0,
        }

    # ── 判据 ───────────────────────────────────────────────────────
    def expired(self, elapsed_s: float | None) -> bool:
        """到点判据：`elapsed_s` 缺省（没钟）⇒ False，⛔ 不许拿"没钟"当超时。"""
        if elapsed_s is None:
            return False
        return float(elapsed_s) > self.deadline_s

    # ── 消费 ───────────────────────────────────────────────────────
    def consume(
        self,
        result: Any,
        *,
        trace_id: str,
        room: str = "",
        elapsed_s: float | None = None,
    ) -> TvAnnouncement:
        """吃一条 `CallResult`（或任何回执形状），返回留痕记录。**永不抛**。"""
        self.counters["requested"] += 1
        if self.expired(elapsed_s):
            return self.announce_timeout(trace_id, room=room)
        data = getattr(result, "data", None)
        if not isinstance(data, Mapping):
            self.counters["failed"] += 1
            return TvAnnouncement(
                spoken=False, text=None, result=None, reason="not_a_result",
                error="回执不是 CallResult（⛔ 不自造终态）",
            )
        defect = data.get("defect")
        if defect == DEFECT_DORMANT_RESULT or data.get("result") in DORMANT_RESULTS:
            # 休眠两值＝对端实现越界（adapter 已落 `ADM_ERR_INTERNAL`＋defect）⇒ 静默＋计缺陷。
            self.counters["defect"] += 1
            return TvAnnouncement(
                spoken=False,
                text=None,
                result=data.get("result"),
                reason="dormant_defect",
                defect=str(defect or DEFECT_DORMANT_RESULT),
            )
        if getattr(result, "success", False) is not True:
            self.counters["failed"] += 1
            return TvAnnouncement(
                spoken=False,
                text=None,
                result=data.get("result"),
                reason="call_failed",
                error=str(getattr(result, "error", "") or "")[:200] or None,
            )
        if data.get("accepted") is False:
            # 对端说"正在跑"是合法答复：⛔ 不播报（否则一次询问念两遍），⛔ 不判失败。
            self.counters["silent"] += 1
            return TvAnnouncement(
                spoken=False, text=None, result=None, reason="already_running"
            )
        outcome = data.get("result")
        text = speech_of(outcome)
        if text is None:
            self.counters["silent"] += 1
            return TvAnnouncement(
                spoken=False, text=None, result=str(outcome or "") or None, reason="no_speech"
            )
        return self._speak(text, result=str(outcome), trace_id=trace_id, room=room, reason="ok")

    def announce_timeout(self, trace_id: str, *, room: str = "") -> TvAnnouncement:
        """到点兜底：播「电视联动执行超时」（⛔ 不冒充任何 business 终态）。"""
        self.counters["timeout"] += 1
        return self._speak(
            TIMEOUT_SPEECH,
            result=None,
            trace_id=trace_id,
            room=room,
            reason="timeout",
        )

    # ── 播报腿 ─────────────────────────────────────────────────────
    def _speak(
        self, text: str, *, result: str | None, trace_id: str, room: str, reason: str
    ) -> TvAnnouncement:
        if self._publish is None:
            # fail-closed：没有播报腿就如实记「没出声」，⛔ 不许记成 spoken（那会骗过验收读数）。
            self.counters["publish_failed"] += 1
            return TvAnnouncement(
                spoken=False, text=text, result=result, reason=f"{reason}:no_leg",
                error="未注入 publish_speak",
            )
        # 三枚按关键字交出去（与 `AfMqttBridge.publish_speak` 的签名同形）：`room` 恒递，空串由桥自己
        # 决定要不要进载荷（`if room:`）——本层⛔ 替它判，也⛔ 在这里拼一个 dict 去凑（那会把
        # `trace_id` 变成字符串常量，被可观测性门的"信封 trace_id 站点"当成一个假站点数进去）。
        try:
            out = self._publish(trace_id=str(trace_id), text=text, room=str(room or ""))
        except Exception as exc:  # noqa: BLE001 —— 播报旁路失败⛔ 冒到执行链（同桥的记账纪律）
            self.counters["publish_failed"] += 1
            return TvAnnouncement(
                spoken=False, text=text, result=result, reason=f"{reason}:publish_raised",
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
        if isinstance(out, Mapping) and out.get("published") is False:
            self.counters["publish_failed"] += 1
            return TvAnnouncement(
                spoken=False, text=text, result=result, reason=f"{reason}:not_published",
                error=str(out.get("code") or out.get("error") or "not_published")[:200],
            )
        self.counters["spoken"] += 1
        return TvAnnouncement(spoken=True, text=text, result=result, reason=reason)
