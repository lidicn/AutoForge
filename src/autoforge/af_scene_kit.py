# -*- coding: utf-8 -*-
"""v1.29 场景对象：`SceneDef` / `SceneStep` / `SceneRunner`（对话操控三分法第三类）。

**一句话定义**：场景 ＝ 预设的**批量设备动作清单**——一句话触发 → 按序执行 → 跑完即止。
⛔ 它不是 `af_scene.Scene`（一组 automation 的开/关编排，带 active 持久态与互斥组）：
两套语义在代码里**物理隔离**（`SceneDef.kind == "scene_def"`，`SceneManager` 的 scenes 字典
永远收不到它），判据 `test_scene_namespace_isolated` 钉住这条。

设计稿：`docs/architecture/v129_scene_object_design.md`（五节）。本模块只落**执行器**，
⛔ 不改 `af_scene.py` / `af_task_order.py`（只点名）。

三条不变量（DCD Q3 甲）：

1. **每步独立过 grant**：逐步 `GrantStore.check`，判定只看 `check.code` 单码视图。
   `hard_denied` ⇒ 完成当前步、**取消未执行步**、⛔ 不回滚已成步（设备动作不可逆）。
2. **`needs_confirm` 走单次放行票**：`issue_confirm` 出票 → 人工确认 → `consume_confirm`
   （验票即毁）；⛔ 确认不升档，只放行这一次。
3. **`uses` 每 run 计一次**：`bump_use` 是整盘重写，逐步计数会让账本抖动。

档位（L0/L1/L2/L3）**不落 IR**：执行期由 `af_adapters.base.classify_action` 现算，
IR 与判定器⛔ 双真源；配置期只做「能不能入清单」的准入拒（L3／禁入域直接拒）。
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .af_adapters.base import L3_DANGEROUS, classify_action
from .af_grant import (
    GRANT_BANNED_DOMAINS,
    GRANT_OK,
    GrantCheck,
    GrantStore,
)

#: 场景定义的 kind 字面量。⛔ 不是 `"scene"`——与 `af_scene.Scene` 的自动化组语义隔离。
SCENE_DEF_KIND = "scene_def"

#: 每步失败策略：`abort`（默认，停）｜`continue`（记一笔继续下一步）。
ON_ERROR_CHOICES = ("abort", "continue")

#: 未执行步的取消标记（回执 `skipped_reason`）。硬拒后**所有**未执行步都记这个。
SKIP_AFTER_HARD_DENY = "cancelled_after_hard_deny"
#: 人工确认未放行（或验票失败）时当前步的标记。
SKIP_CONFIRM_DENIED = "confirm_denied"
#: 执行器报错且 `on_error=abort` 时，后续未执行步的标记。
SKIP_AFTER_ERROR = "aborted_after_error"

# ── AF-756（2026-11-12）：回执必须带失败原因（`error` 字段）─────────────────
#: 回执 `error` 的截断长度（与 `af_adapters/ha.py` 的错误正文同长 200）。
ERROR_TEXT_MAX = 200
#: 脱敏占位文案（⛔ 不带原值、也不带长度/前缀等可反推信息）。
REDACTED_TOKEN = "<令牌已隐去>"
REDACTED_PATH = "<路径已隐去>"
#: 令牌形状（与 `scripts/check_secrets.py` 的两族同口径）：`af_`＋40 位 hex、`Bearer`＋高熵串。
#: 回执会进 MCP 会话与知识库历史，令牌进了历史聚合就被反复回显、事后洗不掉。
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"af_[0-9a-f]{40}"),
    re.compile(r"Bearer[ \t]+\S+"),
)
#: 凭据键值对（`token=…`／`password: …` 等）：值整段隐去，键名保留（仍能看出泄漏面）。
_CREDENTIAL_RE = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)\b(\s*[=:]\s*)\S+"
)
#: 绝对路径（与 `af_mcp._PATH_RE` 同形；⛔ 不反向 import——`af_mcp` 是 L2，本模块是 L1）。
_PATH_RE = re.compile(
    r"[A-Za-z]:[\\/]{1,2}(?:[\w.\-@+ ]+[\\/]){1,}[\w.\-@+ ]*"
    r"|(?<![\w.\-])(?:[\\/][\w.\-@+ ]{1,}){2,}"
)

#: 场景定义**只认**这些键（未声明即拒，与 `af_grant._normalize_scope` 同纪律）。
_DEF_KEYS = frozenset(
    {
        "kind",
        "scene_id",
        "name",
        "aliases",
        "version",
        "owner",
        "condition",
        "steps",
        "created_via",
        "created_at",
    }
)
#: 步骤**只认**这些键。
_STEP_KEYS = frozenset(
    {"step_id", "adapter", "action", "target", "params", "on_error", "delay_ms_after"}
)
#: `condition` 的声明形状（⛔ 不发明隐式条件：只允许对实体/属性做断言）。
_CONDITION_KEYS = frozenset({"entity_id", "attribute", "equals", "above", "below"})
#: `created_via` 取值域。
CREATED_VIA_CHOICES = ("ask", "import", "api")

#: 场景定义硬上限（人配清单的容量面：超限拒收，⛔ 不淘汰已有定义）。
SCENE_MAX_DEFS = 256
#: 单次 ask 会话的步骤硬上限（六轮问答里"要动哪些设备"的清单不会无限长）。
ASK_MAX_STEPS = 64
#: 单次 ask 会话的别名硬上限（"以后说哪句话触发"同理）。
ASK_MAX_ALIASES = 32


class SceneDefError(ValueError):
    """场景定义在**配置期**就被拒（fail-closed）。⛔ 半成品不落到任何存储。"""


class SceneConditionNotMet(Exception):
    """`condition` 求值为假：本次不执行（⛔ 不留半成品、⛔ 不记成失败）。"""

    def __init__(self, scene_id: str, detail: str = "") -> None:
        super().__init__(f"场景 {scene_id!r} 的前置条件不满足：{detail or '条件为假'}")
        self.scene_id = scene_id
        self.detail = detail


def _require_text(value: Any, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SceneDefError(f"{what} 必须是非空字符串")
    return text


def _require_mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SceneDefError(f"{what} 必须是对象")
    return value


def _reject_unknown(mapping: Mapping[str, Any], allowed: frozenset[str], what: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise SceneDefError(f"{what} 有未声明的键：{sorted(unknown)}（只认 {sorted(allowed)}）")


def _config_risk(adapter: str, action: str, params: Mapping[str, Any]) -> str:
    """配置期准入：L3 或禁入域 ⇒ 直接拒（⛔ 不写进 IR 再等执行期拒）。"""
    level = classify_action(adapter, action, params or {})
    if level == L3_DANGEROUS:
        raise SceneDefError(f"动作 {action!r} 判 L3（高危）：⛔ 不得进入场景清单")
    domain = str(action or "").split(".", 1)[0].lower() if "." in str(action or "") else str(adapter or "").lower()
    if domain in GRANT_BANNED_DOMAINS:
        raise SceneDefError(
            f"动作 {action!r} 落在禁入域 {domain!r}（门锁/燃气阀/热水器/安防面板）：⛔ 不得进入场景清单"
        )
    return level


@dataclass(frozen=True)
class SceneStep:
    """场景里的一步。`step_no` ＝ 在 `steps` 里的索引（顺序即执行顺序）。"""

    step_no: int
    step_id: str
    adapter: str
    action: str
    target: str
    params: Mapping[str, Any] = field(default_factory=dict)
    on_error: str = "abort"
    delay_ms_after: int = 0

    @property
    def actions(self) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        """给 `GrantStore.check` 的动作三元组（与 `af_grant.collect_actions` 同形）。"""
        return ((self.adapter, self.action, dict(self.params or {})),)

    @classmethod
    def from_ir(cls, payload: Any, *, step_no: int) -> "SceneStep":
        data = _require_mapping(payload, f"steps[{step_no}]")
        _reject_unknown(data, _STEP_KEYS, f"steps[{step_no}]")
        adapter = _require_text(data.get("adapter"), f"steps[{step_no}].adapter")
        action = _require_text(data.get("action"), f"steps[{step_no}].action")
        target = _require_text(data.get("target"), f"steps[{step_no}].target")
        params = data.get("params") or {}
        _require_mapping(params, f"steps[{step_no}].params")
        on_error = str(data.get("on_error") or "abort").strip().lower()
        if on_error not in ON_ERROR_CHOICES:
            raise SceneDefError(f"steps[{step_no}].on_error 只认 {ON_ERROR_CHOICES}，现读 {on_error!r}")
        raw_delay = data.get("delay_ms_after", 0)
        if isinstance(raw_delay, bool) or not isinstance(raw_delay, int) or raw_delay < 0:
            raise SceneDefError(f"steps[{step_no}].delay_ms_after 必须是非负整数")
        _config_risk(adapter, action, params)
        return cls(
            step_no=step_no,
            step_id=_require_text(data.get("step_id"), f"steps[{step_no}].step_id"),
            adapter=adapter,
            action=action,
            target=target,
            params=dict(params),
            on_error=on_error,
            delay_ms_after=raw_delay,
        )

    def to_ir(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "adapter": self.adapter,
            "action": self.action,
            "target": self.target,
            "params": dict(self.params),
            "on_error": self.on_error,
            "delay_ms_after": self.delay_ms_after,
        }


@dataclass(frozen=True)
class SceneDef:
    """一份场景定义。⛔ 无激活态、无断点——跑完即止（与 `af_scene.Scene`／任务单的分界见设计稿 §4）。"""

    scene_id: str
    name: str
    steps: tuple[SceneStep, ...]
    aliases: tuple[str, ...] = ()
    version: int = 1
    owner: str = ""
    condition: Mapping[str, Any] | None = None
    created_via: str = "api"
    created_at: str = ""

    #: kind 字面量（名字空间隔离的判据锚点：⛔ 不是 "scene"）。
    kind: str = SCENE_DEF_KIND

    def __post_init__(self) -> None:
        if self.kind != SCENE_DEF_KIND:
            raise SceneDefError(f"kind 必须是 {SCENE_DEF_KIND!r}，⛔ 不是 {self.kind!r}")
        _require_text(self.scene_id, "scene_id")
        _require_text(self.name, "name")
        if not self.steps:
            raise SceneDefError("steps 不能为空（空场景没有可执行的东西）")
        seen: set[str] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise SceneDefError(f"step_id 重复：{step.step_id!r}")
            seen.add(step.step_id)
        if self.created_via not in CREATED_VIA_CHOICES:
            raise SceneDefError(f"created_via 只认 {CREATED_VIA_CHOICES}，现读 {self.created_via!r}")
        if self.condition is not None:
            cond = _require_mapping(self.condition, "condition")
            _reject_unknown(cond, _CONDITION_KEYS, "condition")
            _require_text(cond.get("entity_id"), "condition.entity_id")
            if not any(k in cond for k in ("equals", "above", "below")):
                raise SceneDefError("condition 必须给出 equals/above/below 之一（⛔ 空条件≠无条件）")

    # ── 构造 / 序列化 ───────────────────────────────────────────────
    @classmethod
    def from_ir(cls, payload: Any, *, created_at: str = "") -> "SceneDef":
        """从 IR 字典构造（fail-closed：未声明键、空 steps、坏 on_error、L3/禁入域全在配置期拒）。"""
        data = _require_mapping(payload, "scene_def")
        _reject_unknown(data, _DEF_KEYS, "scene_def")
        kind = str(data.get("kind") or SCENE_DEF_KIND)
        if kind != SCENE_DEF_KIND:
            raise SceneDefError(f"kind 必须是 {SCENE_DEF_KIND!r}（⛔ 不是 af_scene 的 Scene），现读 {kind!r}")
        raw_steps = data.get("steps")
        if isinstance(raw_steps, (str, bytes)) or not isinstance(raw_steps, Sequence) or not raw_steps:
            raise SceneDefError("steps 必须是非空列表")
        steps = tuple(SceneStep.from_ir(item, step_no=i) for i, item in enumerate(raw_steps))
        aliases_raw = data.get("aliases") or []
        if isinstance(aliases_raw, (str, bytes)) or not isinstance(aliases_raw, Iterable):
            raise SceneDefError("aliases 必须是字符串列表")
        condition = data.get("condition")
        if condition is not None:
            _require_mapping(condition, "condition")
        version_raw = data.get("version", 1)
        if isinstance(version_raw, bool) or not isinstance(version_raw, int) or version_raw < 1:
            raise SceneDefError("version 必须是正整数")
        return cls(
            scene_id=_require_text(data.get("scene_id"), "scene_id"),
            name=_require_text(data.get("name"), "name"),
            steps=steps,
            aliases=tuple(str(a).strip() for a in aliases_raw if str(a or "").strip()),
            version=version_raw,
            owner=str(data.get("owner") or "").strip(),
            condition=dict(condition) if condition is not None else None,
            created_via=str(data.get("created_via") or "api").strip().lower(),
            created_at=str(data.get("created_at") or created_at),
        )

    def to_ir(self) -> dict[str, Any]:
        """回写 IR。⛔ 不落档位（L0/L1/L2/L3 执行期现算，D5）。"""
        return {
            "kind": self.kind,
            "scene_id": self.scene_id,
            "name": self.name,
            "aliases": list(self.aliases),
            "version": self.version,
            "owner": self.owner,
            "condition": dict(self.condition) if self.condition is not None else None,
            "steps": [step.to_ir() for step in self.steps],
            "created_via": self.created_via,
            "created_at": self.created_at,
        }


class SceneRegistry:
    """场景定义**查重**面（scene_id 唯一）。⛔ 不是 `SceneManager`：这里没有 active 态、没有互斥组。

    容量面：场景定义是**人配的有限清单**，不是运行时事件流——`add()` 超过 `SCENE_MAX_DEFS`
    一律拒（fail-closed），⛔ 不做淘汰：淘汰一条定义等于让人配好的口令消失。
    """

    def __init__(self, *, max_defs: int = SCENE_MAX_DEFS) -> None:
        self.max_defs = int(max_defs)
        self._defs: dict[str, SceneDef] = {}  # bounded-cache: exempt(场景定义注册表：写入口 `add()` 既查重（scene_id 唯一）又按 `SCENE_MAX_DEFS` 封顶，超限即拒（fail-closed）；上界＝人配的场景清单条数，⛔ 不是运行时事件流，没有 TTL 腿——场景定义不该"自己过期消失")

    def add(self, scene: SceneDef) -> SceneDef:
        if scene.scene_id in self._defs:
            raise SceneDefError(f"scene_id 重复：{scene.scene_id!r}（场景 id 必须唯一）")
        if len(self._defs) >= self.max_defs:
            raise SceneDefError(
                f"场景定义已达硬上限 {self.max_defs} 条：拒收 {scene.scene_id!r}（fail-closed，⛔ 不淘汰既有定义）"
            )
        self._defs[scene.scene_id] = scene
        return scene

    def get(self, scene_id: str) -> SceneDef | None:
        return self._defs.get(str(scene_id or "").strip())

    def list(self) -> list[SceneDef]:
        return list(self._defs.values())

    def __len__(self) -> int:
        return len(self._defs)


class SceneAskSession:
    """配置期多轮 ask 的**暂存面**（设计稿 §3 六轮）。

    任一轮取消/超时 ⇒ 整单放弃：`cancel()` 之后 `commit()` 一律拒 ⇒ ⛔ 不落半成品。
    只有 `commit()` 那一步才产出 `SceneDef`（构造校验 fail-closed 在这里生效）。
    """

    def __init__(self, *, created_via: str = "ask") -> None:
        self.created_via = created_via
        self._name = ""
        self._aliases: list[str] = []  # bounded-cache: exempt(单次 ask 会话的暂存器：随 `commit()` 产出定义 / `cancel()` 清空即失引，上界＝`ASK_MAX_ALIASES` 条别名（超出即拒）；⛔ 不是跨请求缓存，没有 TTL 腿)
        self._steps: list[dict[str, Any]] = []  # bounded-cache: exempt(单次 ask 会话的暂存器：随 `commit()` 产出定义 / `cancel()` 清空即失引，上界＝`ASK_MAX_STEPS` 步（超出即拒）；⛔ 不是跨请求缓存，没有 TTL 腿)
        self._condition: dict[str, Any] | None = None
        self._cancelled = False
        self._cancelled_reason = ""

    # ── 六轮作答面 ─────────────────────────────────────────────────
    def ask_name(self, name: str, aliases: Iterable[str] = ()) -> None:
        self._ensure_open()
        alias_list = [str(a).strip() for a in aliases if str(a or "").strip()]
        if len(alias_list) > ASK_MAX_ALIASES:
            raise SceneDefError(
                f"aliases 超过硬上限 {ASK_MAX_ALIASES} 条（现给 {len(alias_list)}）——⛔ 触发词不该无穷多"
            )
        self._name = str(name or "").strip()
        self._aliases = alias_list

    def add_step(
        self,
        *,
        step_id: str,
        adapter: str,
        action: str,
        target: str,
        params: Mapping[str, Any] | None = None,
        on_error: str = "abort",
        delay_ms_after: int = 0,
    ) -> None:
        self._ensure_open()
        if len(self._steps) >= ASK_MAX_STEPS:
            raise SceneDefError(
                f"steps 超过硬上限 {ASK_MAX_STEPS} 步（现已有 {len(self._steps)}）——⛔ 场景不该无限长"
            )
        self._steps.append(
            {
                "step_id": step_id,
                "adapter": adapter,
                "action": action,
                "target": target,
                "params": dict(params or {}),
                "on_error": on_error,
                "delay_ms_after": delay_ms_after,
            }
        )

    def ask_condition(self, condition: Mapping[str, Any] | None) -> None:
        self._ensure_open()
        self._condition = dict(condition) if condition is not None else None

    # ── 收口 / 取消 ────────────────────────────────────────────────
    def commit(self, *, scene_id: str, owner: str = "", created_at: str = "") -> SceneDef:
        self._ensure_open()
        return SceneDef.from_ir(
            {
                "kind": SCENE_DEF_KIND,
                "scene_id": scene_id,
                "name": self._name,
                "aliases": list(self._aliases),
                "version": 1,
                "owner": owner,
                "condition": self._condition,
                "steps": list(self._steps),
                "created_via": self.created_via,
                "created_at": created_at,
            },
            created_at=created_at,
        )

    def cancel(self, reason: str = "") -> None:
        """取消：⛔ 不产出任何 SceneDef（半成品留在本对象里也随 GC 走，⛔ 不落盘）。"""
        self._cancelled = True
        self._cancelled_reason = str(reason or "").strip()
        self._steps.clear()
        self._name = ""
        self._aliases.clear()
        self._condition = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def cancelled_reason(self) -> str:
        return self._cancelled_reason

    def _ensure_open(self) -> None:
        if self._cancelled:
            raise SceneDefError(
                f"ask 会话已取消（{self._cancelled_reason or '无原因'}）：⛔ 不落半成品定义"
            )


class SceneRunner:
    """按序执行一份 `SceneDef`：每步独立过 grant → 过检则下发 → 汇总回执。

    注入面（⛔ 模块不自己打开设备/网络）：

    - `executor(step)`：真正下发动作；返回假值视为失败（`on_error` 决定停还是继续）。
    - `room_of(entity)`：实体 → 区域名（留给 `GrantStore.check`）。
    - `confirm(check)`：人工确认回调；返回 True 表示放行（随后 `consume_confirm` 验票）。
    - `sleep(seconds)`：节流（测试注入假实现，免得真睡）。
    - `now()`：时间源（回执时间戳）。
    """

    def __init__(
        self,
        store: GrantStore,
        *,
        executor: Callable[[SceneStep], Any] | None = None,
        room_of: Callable[[str], str] | None = None,
        confirm: Callable[[GrantCheck], bool] | None = None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self.executor = executor or (lambda step: True)
        self.room_of = room_of
        self.confirm = confirm
        self._sleep = sleep or time.sleep
        self._now = now or time.time

    # ── 主路径 ─────────────────────────────────────────────────────
    def run(
        self,
        scene: SceneDef,
        *,
        grant: Mapping[str, Any],
        agent: str,
        precondition: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """执行一次场景，返回回执（逐步条目复用 `GrantCheck` 的单码视图）。

        每步条目带 `error`：成功恒 `None`；失败是**脱敏＋截断**后的原因文本
        （AF-756：回执说"失败了"却不说"为什么"＝不可诊断）。`code` 仍只表达授权放行。
        """
        if scene.condition is not None:
            if precondition is None:
                # fail-closed：带条件却没有求值器 ⇒ 不许"当它没有条件"就执行。
                raise SceneDefError("场景带 condition，必须注入 precondition 求值器（fail-closed）")
            if not precondition(dict(scene.condition)):
                raise SceneConditionNotMet(scene.scene_id)

        run_id = f"r-{uuid.uuid4().hex[:12]}"
        started_at = _iso(self._now())
        entries: list[dict[str, Any]] = []
        skip_rest = ""
        dispatched_any = False

        for step in scene.steps:
            if skip_rest:
                entries.append(self._skip_entry(scene, step, code="", reason=skip_rest))
                continue
            check = self.store.check(
                grant,
                agent=agent,
                actions=step.actions,
                targets=(step.target,),
                room_of=self.room_of,
            )
            entry: dict[str, Any] = {
                "step_no": step.step_no,
                "step_id": step.step_id,
                "target": step.target,
                "grant_id": check.grant_id,
                "code": check.code,
                "done": False,
                "error": None,
            }
            if check.hard_denied:
                # 完成当前步（不下发）、取消未执行步、⛔ 不回滚已成步（D3）。
                entry["skipped_reason"] = SKIP_AFTER_HARD_DENY
                entries.append(entry)
                skip_rest = SKIP_AFTER_HARD_DENY
                continue
            if check.needs_confirm:
                ticket = self.store.issue_confirm(check)
                entry["ticket"] = ticket["ticket"]
                allowed = bool(self.confirm(check)) if self.confirm is not None else False
                # 票绑的实体集合＝判定时的越界集合：只有"越界放行"型票才校验实体，
                # 档位超授型（GRANT_RISK_EXCEEDS）票的目标集为空，⛔ 不能拿步目标去对。
                if not allowed or not self.store.consume_confirm(
                    ticket["ticket"],
                    agent=agent,
                    targets=tuple(check.out_of_scope) or None,
                    grant_id=check.grant_id,
                ):
                    entry["skipped_reason"] = SKIP_CONFIRM_DENIED
                    entries.append(entry)
                    skip_rest = SKIP_CONFIRM_DENIED
                    continue
            # 过检（或验票放行）⇒ 下发
            dispatched_any = True
            try:
                ok, error = _executor_outcome(self.executor(step))
            except Exception as exc:  # noqa: BLE001 —— 执行器是注入的，任何异常都按"这一步失败"记
                # AF-756：异常也要留下**类型名＋原文**——只说"失败了"等于没留痕。
                ok, error = False, _redact_error_text(f"{type(exc).__name__}: {exc}")
            entry["done"] = ok
            entry["error"] = error
            if not ok:
                entry["skipped_reason"] = SKIP_AFTER_ERROR
                if step.on_error == "abort":
                    entries.append(entry)
                    skip_rest = SKIP_AFTER_ERROR
                    continue
            entries.append(entry)
            if step.delay_ms_after:
                self._sleep(step.delay_ms_after / 1000.0)

        if dispatched_any and str(grant.get("grant_id") or "").strip():
            # uses 每 run 计一次（D4）：逐步计数＝逐步整盘重写账本。
            self.store.bump_use(str(grant["grant_id"]))

        return {
            "run_id": run_id,
            "scene_id": scene.scene_id,
            "name": scene.name,
            "agent": agent,
            "code": _overall_code(entries),
            "started_at": started_at,
            "steps": entries,
        }

    # ── 内部 ───────────────────────────────────────────────────────
    @staticmethod
    def _skip_entry(scene: SceneDef, step: SceneStep, *, code: str, reason: str) -> dict[str, Any]:
        return {
            "step_no": step.step_no,
            "step_id": step.step_id,
            "target": step.target,
            "grant_id": "",
            "code": code,
            "done": False,
            "error": None,
            "skipped_reason": reason,
        }


def _executor_outcome(value: Any) -> tuple[bool, str | None]:
    """把执行器回值归一成 `(ok, error)`（AF-756）。

    三种形状都认：`CallResult`（真机执行器，`af_adapters.base`）、`(ok, error)` 元组、
    `bool`（旧注入面／测试桩）。⛔ 成功步恒回 `(True, None)`——失败文本只有失败时才有，
    不把"重试过但最后成了"写成错误。脱敏与截断统一在 `_redact_error_text` 做。
    """
    if isinstance(value, tuple) and len(value) == 2:
        ok, error = value
        return bool(ok), (None if ok else _redact_error_text(error))
    success = getattr(value, "success", None)
    if isinstance(success, bool):
        return success, (None if success else _redact_error_text(getattr(value, "error", None)))
    return bool(value), None


def _redact_error_text(text: Any) -> str | None:
    """回执 `error` 脱敏＋截断：⛔ 令牌/绝对路径不外发，长文切到 `ERROR_TEXT_MAX`。

    先脱敏再截断：反过来会切出**半截令牌**（前缀仍可反推），也会切坏脱敏占位文案。
    """
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None
    for pattern in _TOKEN_PATTERNS:
        raw = pattern.sub(REDACTED_TOKEN, raw)
    raw = _CREDENTIAL_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED_TOKEN}", raw)
    raw = _PATH_RE.sub(REDACTED_PATH, raw)
    return raw[:ERROR_TEXT_MAX]


def _overall_code(entries: Iterable[Mapping[str, Any]]) -> str:
    """整场单码：硬拒优先，其次确认未过，最后 ok（⛔ 不另造码）。"""
    codes = [str(e.get("code") or "") for e in entries]
    for code in codes:
        if code and code not in (GRANT_OK, "") and code != "GRANT_CONFIRM_REQUIRED":
            return code
    if any(code == "GRANT_CONFIRM_REQUIRED" for code in codes):
        return "GRANT_CONFIRM_REQUIRED"
    return GRANT_OK


def _iso(stamp: float) -> str:
    """本地时区 ISO 时间戳（与 `af_grant._now_iso` 同形）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stamp))


__all__ = [
    "ASK_MAX_ALIASES",
    "ASK_MAX_STEPS",
    "CREATED_VIA_CHOICES",
    "ERROR_TEXT_MAX",
    "ON_ERROR_CHOICES",
    "SCENE_DEF_KIND",
    "SCENE_MAX_DEFS",
    "SKIP_AFTER_ERROR",
    "SKIP_AFTER_HARD_DENY",
    "SKIP_CONFIRM_DENIED",
    "SceneAskSession",
    "SceneConditionNotMet",
    "SceneDef",
    "SceneDefError",
    "SceneRegistry",
    "SceneRunner",
    "SceneStep",
]
