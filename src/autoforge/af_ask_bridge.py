"""ask 双向桥（v1.28 R1）——**照 DCD 裁定 2026-11-04 阶段2 契约逐字实现**。

出处：`E:\\NAS\\关键决策部\\decisions\\20261104-AF-主动问询MQTT跨仓契约-裁定.md`
（§2.3 C1–C9、§四 执行要点）。本模块只做**契约面**：主题名、载荷形状、签名、落盘。

**这条腿补的是什么**：`ask` 挂起本身早已存在（`af_executor._suspend` ＋ `pending_asks`），
WebUI 腿（`POST /api/asks/answer` → `answer_inbox/`）与文件回注腿也在。缺的是
**MQTT 版 ask 面**——裁定 §2.1 明写「阶段 2 的前置＝AF 先出 MQTT 版 ask 面」，
在此之前全仓 `af/ask` 零命中（DB 侧因此无从订阅）。

**裁定逐字（⛔ 不许自由发挥）**：
- **C1** 出向主题 `af/ask/pending`（AF 命名空间；QoS=1、**retain=false**——§2.2 同族纪律）。
- **C2** 入向主题 `af/ask/answer`。
- **C3** AF→DB 载荷 `{ask_id, room, prompt, source, priority}`；`ask_id` 沿用
  `trace_id` 约束（**非空、≤64**）。本模块另加裁定点名的 `timeout_remaining`（C6）。
- **C4** DB→AF 载荷 `{ask_id, answer:"yes"/"no", raw_text}` —— **MVP 只两值**；
  ⛔ 带参数回答（"设到 25 度"）不进 MVP（§八.3、§四 不得项）。
- **C5** 分类归 DB：AF **消费枚举**，⛔ 不对 `raw_text` 二次分类——`homesdk.classify_answer`
  把 unknown 映成 `no`，拿语音原文再分一次会把「yes」悄悄翻成「no」（实现里由 `verdict=`
  直通路径兑现，见 `af_live.read_answer_inbox`）。

**`answered_via` 的信道口径**（PM 卡 §二 补钉）：值只认 `xiaomi`/`webui`；判定**信道优先
于载荷自述**——MQTT 腿一律 `xiaomi`，载荷自称别的来源即整条拒收；文件回注腿默认 `webui`。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .af_atomic import atomic_write_text
from .af_env import env_number

__all__ = [
    "ASK_PENDING_TOPIC",
    "ASK_ANSWER_TOPIC",
    "ASK_TOPIC_PREFIX",
    "ASK_SOURCE",
    "ASK_PRIORITY",
    "ANSWERED_VIA_XIAOMI",
    "ANSWERED_VIA_WEBUI",
    "ANSWERED_VIA_VALUES",
    "DEFAULT_ANSWERED_VIA",
    "VERDICT_YES",
    "VERDICT_NO",
    "VERDICTS",
    "ASK_TIMEOUT_ENV",
    "DEFAULT_ASK_TIMEOUT_S",
    "INBOX_KEY_ENV",
    "MAX_ASK_ID_CHARS",
    "AnswerRejected",
    "answer_inbox_dir",
    "build_pending_payload",
    "default_ask_timeout_s",
    "normalize_answered_via",
    "parse_answer_payload",
    "sign_answer",
    "write_answer_inbox_file",
]

logger = logging.getLogger("autoforge.ask_bridge")

#: 裁定 C1/C2 的主题名**逐字**（AF 自己的命名空间，⛔ 不是 `butler/inbox/*`——
#: 那一族是 DB 的所有权面，本仓对它有 `FORBIDDEN_SUBSCRIPTIONS` 护栏）。
ASK_TOPIC_PREFIX = "af/ask/"
ASK_PENDING_TOPIC = f"{ASK_TOPIC_PREFIX}pending"
ASK_ANSWER_TOPIC = f"{ASK_TOPIC_PREFIX}answer"

#: C3 载荷里的固定两格（`source` 沿用 DB 交接单 §3 的 "autoforge"；`priority`=1 同 §3 最小载荷）。
ASK_SOURCE = "autoforge"
ASK_PRIORITY = 1

# ── C4：MVP 只两值 ────────────────────────────────────────────────────
VERDICT_YES = "yes"
VERDICT_NO = "no"
VERDICTS: frozenset[str] = frozenset({VERDICT_YES, VERDICT_NO})

# ── answered_via（卡 §二：枚举只此二值，⛔ 不自行扩）────────────────────
ANSWERED_VIA_XIAOMI = "xiaomi"
ANSWERED_VIA_WEBUI = "webui"
ANSWERED_VIA_VALUES: frozenset[str] = frozenset({ANSWERED_VIA_XIAOMI, ANSWERED_VIA_WEBUI})
DEFAULT_ANSWERED_VIA = ANSWERED_VIA_WEBUI

#: `answer_inbox/` 的签名密钥（与 `af_api` 写侧、`af_live` 读侧同一把）。
INBOX_KEY_ENV = "AUTOFORGE_INBOX_KEY"

#: 挂起超时默认（PM v1.28 卡 §二.1 补钉：300s，与 canary 观察窗同数量级；env 可覆盖）。
#: ⚠️ **与 DCD 裁定 C6 的张力争执已报 PM**：裁定写「AF 的 ask timeout（60s）不改」，
#: 卡面写默认 300s。本模块取**卡面默认 300s ＋ env 可覆盖**（`AUTOFORGE_ASK_TIMEOUT_S=60`
#: 即可逐字回到裁定取值），并把真实剩余时长作为 `timeout_remaining` 发给出向主题，
#: 让 DB 不必猜 AF 这一侧到底等了多久。
ASK_TIMEOUT_ENV = "AUTOFORGE_ASK_TIMEOUT_S"
DEFAULT_ASK_TIMEOUT_S = 300.0

#: C3：`ask_id` 沿用 `trace_id` 约束——非空、≤64（裁定逐字）。
MAX_ASK_ID_CHARS = 64


class AnswerRejected(ValueError):
    """应答被拒（fail-closed）。`reason` 是机器可读码，`detail` 给人看。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def default_ask_timeout_s() -> float:
    """挂起超时默认秒数（env 可覆盖，坏值 fail-safe 回落默认，⛔ 不让 import 期抛）。"""
    return float(env_number(ASK_TIMEOUT_ENV, DEFAULT_ASK_TIMEOUT_S, lo=1.0))


def normalize_answered_via(value: Any) -> str:
    """把来源标归一；未知/缺失 ⇒ `webui`（回退口径，⛔ 不猜第三个值）。"""
    text = str(value or "").strip().lower()
    if text in ANSWERED_VIA_VALUES:
        return text
    if text:
        logger.warning(
            "ASK_VIA_UNKNOWN answered_via=%r 不在枚举内（只认 xiaomi/webui），按回退口径记 webui",
            text,
        )
    return DEFAULT_ANSWERED_VIA


def answer_inbox_dir(root: str | Path) -> Path:
    """`<store>/answer_inbox`（写侧读侧同一目录；唯一拼装点）。"""
    return Path(root) / "answer_inbox"


def sign_answer(key: str, ask_id: Any, text: str, room: Any, answer_json: str, ts: Any) -> str:
    """`answer_inbox/` 文件签名（写侧/读侧唯一实现）。

    消息形状是既有契约（AF-001 之后带 `ts` 新鲜度）：`ask_id|text|room|answer_json|ts`，
    ⛔ 改这里等于同时改三条腿，必须一起改并跑 `tests/contract/test_af_ask_contract.py`。
    """
    msg = f"{ask_id}|{text}|{room}|{answer_json}|{ts}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def build_pending_payload(
    *,
    ask_id: Any,
    room: Any,
    prompt: Any,
    source: str = ASK_SOURCE,
    priority: int = ASK_PRIORITY,
    timeout_remaining: float | None = None,
) -> dict[str, Any]:
    """裁定 C3 的 AF→DB 载荷（＋C6 点名的 `timeout_remaining`）。

    `ask_id` 必须非空且 ≤64 ⇒ 违反**当场抛** `AnswerRejected`：宁可不发，
    ⛔ 不发一条 DB 认不出身份的挂起（裁定 C3 的 `trace_id` 约束是身份，不是装饰）。
    """
    aid = str(ask_id or "").strip()
    if not aid:
        raise AnswerRejected("missing_ask_id", "ask_id 为空：挂起身份缺失，⛔ 不发")
    if len(aid) > MAX_ASK_ID_CHARS:
        raise AnswerRejected(
            "ask_id_too_long", f"ask_id 长 {len(aid)} 字，裁定 C3 上限 {MAX_ASK_ID_CHARS}"
        )
    payload: dict[str, Any] = {
        "ask_id": aid,
        "room": room,
        "prompt": str(prompt or ""),
        "source": str(source or ASK_SOURCE),
        "priority": int(priority),
    }
    if timeout_remaining is not None:
        payload["timeout_remaining"] = round(float(timeout_remaining), 1)
    return payload


def parse_answer_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """校验裁定 C4 的 DB→AF 载荷，返回 `{ask_id, verdict, raw_text}`。

    拒收口径（fail-closed，⛔ 缺省即拒、不脑补）：
    - `ask_id` 缺失/非串/空白/超 64 字 ⇒ `missing_ask_id` / `ask_id_too_long`（C3 约束）；
    - `answer` 不是 `"yes"`/`"no"` ⇒ `answer_not_verdict`——**MVP 只两值**（C4），
      带参数回答（温度/时间段）按 §四 不得项**整条拒**，⛔ 不落盘、不推进；
    - `raw_text` 非串 ⇒ `raw_text_not_string`；
    - 自称来源不是 `xiaomi` ⇒ `via_not_allowed_on_channel`（信道优先于载荷自述）。
    """
    ask_id_raw = payload.get("ask_id")
    ask_id = str(ask_id_raw).strip() if ask_id_raw is not None else ""
    if not ask_id:
        raise AnswerRejected("missing_ask_id", "载荷没有 ask_id")
    if len(ask_id) > MAX_ASK_ID_CHARS:
        raise AnswerRejected(
            "ask_id_too_long", f"ask_id 长 {len(ask_id)} 字，裁定 C3 上限 {MAX_ASK_ID_CHARS}"
        )
    verdict_raw = payload.get("answer")
    verdict = str(verdict_raw).strip().lower() if verdict_raw is not None else ""
    if verdict not in VERDICTS:
        raise AnswerRejected(
            "answer_not_verdict",
            f"answer={verdict_raw!r} 不是 yes/no（裁定 C4：MVP 只两值，带参数回答⛔ 进 MVP）",
        )
    raw_text = payload.get("raw_text")
    if raw_text is None:
        raw_text = ""
    if not isinstance(raw_text, str):
        raise AnswerRejected("raw_text_not_string", f"raw_text 是 {type(raw_text).__name__}")
    declared = payload.get("via", payload.get("answered_via"))
    if declared is not None and str(declared).strip().lower() != ANSWERED_VIA_XIAOMI:
        raise AnswerRejected(
            "via_not_allowed_on_channel",
            f"本通道只产 xiaomi，载荷自称 {declared!r}（信道优先于载荷自述）",
        )
    return {"ask_id": ask_id, "verdict": verdict, "raw_text": raw_text}


def write_answer_inbox_file(
    root: str | Path,
    *,
    ask_id: Any,
    text: str = "",
    room: Any = None,
    answer: Any = None,
    verdict: str | None = None,
    via: str = DEFAULT_ANSWERED_VIA,
    require_key: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """把应答写成 `answer_inbox/` 文件（原子写，读侧 `af_live.read_answer_inbox` 消费）。

    `verdict` 非空 ⇒ 走**已分类投票**形状：`answer={"verdict": "yes"|"no"}`——
    它落在被签名的 `answer_json` 里，所以**分类结果与原文一起受签名保护**，
    读侧据此走 `executor.answer(verdict=…)`，⛔ 不对 `raw_text` 二次分类（裁定 C5）。

    返回 `{path, name, signed, channel_error, via}`。`require_key=True` 时缺密钥直接抛
    （MQTT 腿 fail-closed：⛔ 不写一份读侧必然丢的文件，那是 AF-732 之前的假绿形状）。
    """
    key = (os.environ.get(INBOX_KEY_ENV) or "").strip()
    ts = int(now if now is not None else time.time())
    body_answer = answer
    if verdict is not None:
        v = str(verdict).strip().lower()
        if v not in VERDICTS:
            raise AnswerRejected("answer_not_verdict", f"verdict={verdict!r} 不是 yes/no")
        body_answer = {"verdict": v}
    answer_json = json.dumps(body_answer or {}, sort_keys=True, ensure_ascii=False)
    body: dict[str, Any] = {
        "ask_id": ask_id or None,
        "text": text,
        "room": room,
        # 来源标：读侧据此打 `answered_via`（信道判定写在这里，⛔ 读侧不猜）
        "via": normalize_answered_via(via),
    }
    if body_answer is not None:
        body["answer"] = body_answer
    if not key:
        if require_key:
            raise AnswerRejected(
                "inbox_key_missing",
                f"{INBOX_KEY_ENV} 未配置：应答无法签名，读侧必然拒收（不发注定被丢的文件）",
            )
        body["ts"] = ts
        body["sig"] = ""
    else:
        body["ts"] = ts
        body["sig"] = sign_answer(key, ask_id or None, text, room, answer_json, ts)
    inbox = answer_inbox_dir(root)
    inbox.mkdir(parents=True, exist_ok=True)
    # 毫秒时间戳作文件名（保留排序语义）＋ uuid 后缀（同毫秒两条不再互相覆盖，R14-03）。
    fname = inbox / f"{int(ts * 1000)}-{uuid4().hex[:8]}.json"
    atomic_write_text(fname, json.dumps(body, ensure_ascii=False))
    return {
        "path": str(fname),
        "name": fname.name,
        "signed": bool(key),
        "channel_error": not bool(key),
        "via": body["via"],
    }
