"""F12 预测性触发消费闭环（AutoForge）。

把 af_predict 的 ``Predictor`` 接进运行时：周期扫描所有自动化，按 band 策略决定
是否「提前触发」。设计边界（与 af_predict 的纯新增契约一致）：

* 本模块**不修改** af_predict 的既有行为；只 import 其 ``Predictor``。
* **不 import af_conf**；band 判定由本服务在调用 ``pre_trigger`` 之前完成
  （调用方注入 band 策略），保持 af_predict 与 G4 解耦。
* 经验先验（F11② ``ExperienceStore.empirical_prior``）由本服务读取后注入
  ``Predictor.predict`` / ``pre_trigger``（调用方注入先验）。

band 策略（已决策 A）：只有 ``band == "auto"`` 才允许预测命中后**自动提前开设备**；
shadow / ask 档命中后**永不自动开设备**（沿用 G4 闸门：shadow 只读比对、ask 须人工确认）。
本服务对 shadow/ask 仅做「跳过 + 计数」，不写设备、也不做比对记录。

真实触发历史来源：包裹 ``runtime.instances.on_spawn`` 钩子（每次自动化真正触发都会落点
于此），把 ``automation_id`` 记进 ``TriggerHistory`` 喂给 ``Predictor.learn``。总线
``BusEvent`` 是实体级事件、不带 ``automation_id`` 且不支持通配订阅，故不采用。
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable

from .af_atomic import atomic_write_text, refuse_when_shape_unreadable
from .af_predict import PREDICTIONS_FILE, Predictor
from .af_time import SystemTimeSource, ensure_aware, load_tz

__all__ = [
    "PreTriggerService",
    "TriggerHistory",
    "PreTriggerBandPolicy",
    "PRETRIGGER_HISTORY_FILE",
    "DEFAULT_THRESHOLD",
    "DEFAULT_WINDOW_MINUTES",
    "DEFAULT_INTERVAL_SECONDS",
]

logger = logging.getLogger(__name__)

PRETRIGGER_HISTORY_FILE = "pretrigger_history.json"
DEFAULT_THRESHOLD = 0.8
DEFAULT_WINDOW_MINUTES = 5.0
DEFAULT_INTERVAL_SECONDS = 90.0
AUTO_BAND = "auto"


# ── 触发历史（Predictor 的 history_store）────────────────────────────────── #
class TriggerHistory:
    """记录真实触发，供 ``Predictor.learn`` 拉取。

    鸭子类型满足 ``af_predict.HISTORY_METHODS`` 探测顺序（``events_for`` 优先，
    其余别名同义）。持久化到 ``<persist_dir>/pretrigger_history.json``，与
    af_predict 的 ``predictions.json`` 同目录隔离、互不干扰。
    """

    def __init__(self, persist_dir: str | None = None) -> None:
        self.persist_dir = persist_dir
        self._events: dict[str, list[dict]] = {}
        self._load()

    @property
    def path(self) -> str | None:
        if not self.persist_dir:
            return None
        return os.path.join(self.persist_dir, PRETRIGGER_HISTORY_FILE)

    # history_store 探测接口（af_predict 按此顺序找方法）──────
    def events_for(self, automation_id: str) -> list[dict]:
        return list(self._events.get(automation_id, ()))

    def events(self, automation_id: str) -> list[dict]:
        return self.events_for(automation_id)

    def history_for(self, automation_id: str) -> list[dict]:
        return self.events_for(automation_id)

    def get_events(self, automation_id: str) -> list[dict]:
        return self.events_for(automation_id)

    def history(self, automation_id: str) -> list[dict]:
        return self.events_for(automation_id)

    # 写入 ───────────────────────────────────────────────────────
    def record(
        self,
        automation_id: str,
        at: Any,
        state: Mapping[str, Any] | None = None,
    ) -> None:
        """记录一次触发（at 接受 datetime / epoch 数值 / ISO 字符串）。"""
        if not isinstance(automation_id, str) or not automation_id.strip():
            return
        rec: dict[str, Any] = {"at": _iso(at)}
        if state is not None:
            rec["state"] = {str(k): str(v) for k, v in state.items()}
        self._events.setdefault(automation_id, []).append(rec)
        self._save()

    # 持久化 ─────────────────────────────────────────────────────
    def _save(self) -> None:
        path = self.path
        if path is None:
            return
        # AF4（第二期第二轮确证）：`_load` 损坏时冷启动（`self._events` 保持空），而这里是整档
        # 覆盖——一次记录就把盘上全部预触发历史抹掉，预测器 learn 的历史全没。落盘前拒写。
        refuse_when_shape_unreadable(path, "预触发历史")
        try:
            os.makedirs(self.persist_dir, exist_ok=True)
            atomic_write_text(
                path,
                json.dumps(
                    {"events": self._events}, ensure_ascii=False, indent=2, sort_keys=True
                ) + "\n",
            )
        except OSError as exc:  # fail-open：写盘失败不影响运行
            logger.warning("预触发历史持久化失败（%s）", exc)

    def _load(self) -> None:
        path = self.path
        if path is None or not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning("预触发历史文件损坏（%s），冷启动", exc)
            return
        if isinstance(data, Mapping) and isinstance(data.get("events"), Mapping):
            self._events = {str(k): list(v) for k, v in data["events"].items()}


def _iso(value: Any) -> str:
    """触发时间戳 → ISO 字符串。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise ValueError(f"触发时间戳不是合法时间：{value!r}")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        from datetime import datetime, timezone

        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    if hasattr(value, "isoformat"):
        return ensure_aware(value, who="trigger_history.at").isoformat()
    raise ValueError(f"不支持的触发时间戳：{value!r}")


# ── band 策略（已决策 A：仅 auto 允许自动提前写设备）──────────────────────── #
@dataclass
class PreTriggerBandPolicy:
    """band -> 是否允许预测命中后自动提前写设备。

    默认即决策 A：只有 auto 档写设备；shadow / ask 永不自动写。
    """

    auto_fire: bool = True
    shadow_fire: bool = False
    ask_fire: bool = False

    def allows(self, band: str) -> bool:
        return bool(getattr(self, f"{band}_fire", False))


# ── 服务 ────────────────────────────────────────────────────────────────── #
class PreTriggerService:
    """F12 消费闭环服务：周期扫描 + band 闸门 + 经验先验注入。

    构造契约（与 G4 组装层一致）：``PreTriggerService(runtime, ...)``，其余参数
    keyword-only 且有默认值。``predictor`` / ``history`` / ``conf`` / ``experience``
    均可注入（测试用），生产由 ``af_runtime_ext.install`` 组装。
    """

    def __init__(
        self,
        runtime,
        *,
        predictor: Predictor | None = None,
        conf=None,
        experience=None,
        history: TriggerHistory | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        window_minutes: float = DEFAULT_WINDOW_MINUTES,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        persist_dir: str | None = None,
        tz_name: str | None = None,
        policy: PreTriggerBandPolicy | None = None,
    ) -> None:
        if not (0.0 < float(threshold) <= 1.0):
            raise ValueError(f"threshold 必须落在 (0, 1]：{threshold!r}")
        if not (0.0 < float(window_minutes)):
            raise ValueError(f"window_minutes 必须为正：{window_minutes!r}")

        self._runtime = runtime
        self._conf = conf or getattr(runtime, "conf", None)
        self._experience = experience
        self._threshold = float(threshold)
        self._window = float(window_minutes)
        self._interval = float(interval_seconds)
        self._tz = load_tz(tz_name)
        self._policy = policy or PreTriggerBandPolicy()

        pdir = persist_dir or getattr(runtime, "persist_dir", None)
        if pdir:
            os.makedirs(pdir, exist_ok=True)
        self._history = history or TriggerHistory(persist_dir=pdir)

        self._predictor = predictor or Predictor(
            self._history,
            getattr(runtime, "executor", None),
            pdir or ".",
            clock=getattr(runtime, "clock", None),
            tz_name=tz_name,
        )
        self._stats = {  # bounded-cache: exempt(固定键计数器：所有写入点用的都是字面键，键空间编译期封闭)
            "scans": 0, "automations": 0, "fired": 0,
            "skipped_band": 0, "below_threshold": 0, "cold_start": 0,
            "last_scan": None,
        }

    # ── 只读属性 ─────────────────────────────────────────────────
    @property
    def predictor(self) -> Predictor:
        return self._predictor

    @property
    def history(self) -> TriggerHistory:
        return self._history

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def interval_seconds(self) -> float:
        return self._interval

    # ── 记录真实触发（on_spawn 钩子入口）────────────────────────
    def record_fire(self, instance: Any) -> None:
        """包裹 ``runtime.instances.on_spawn``：把真实触发喂进历史。

        ``instance`` 至少有 ``automation_id``；时间戳优先用 ``created_at``。
        """
        aid = getattr(instance, "automation_id", None)
        if not aid:
            return
        at = getattr(instance, "created_at", None) or self._clock_now_iso()
        self._history.record(aid, at)

    def _clock_now_iso(self) -> str:
        clock = getattr(self._runtime, "clock", None) or SystemTimeSource()
        return clock.now().isoformat()

    # ── 经验先验（F11②，调用方注入）────────────────────────────
    def experience_prior_for(self, automation_id: str) -> tuple[float | None, float]:
        """读经验先验并映射成 (mean, weight)。无经验库或读取失败 → (None, 0.0)。"""
        exp = self._experience
        if exp is None or not hasattr(exp, "empirical_prior"):
            return (None, 0.0)
        try:
            auto = self._runtime.graph.get(automation_id)
        except Exception:  # noqa: BLE001 - 图里没有该自动化
            return (None, 0.0)
        entities = auto.reads() | auto.writes()
        adapters = {n.adapter for n in auto.nodes.values() if n.adapter}
        try:
            mean, weight = exp.empirical_prior(entities, adapters)
        except Exception as exc:  # noqa: BLE001
            logger.warning("经验先验读取失败（%s）：%s", automation_id, exc)
            return (None, 0.0)
        if not (isinstance(weight, (int, float)) and weight > 0):
            return (None, 0.0)
        return (float(mean), float(weight))

    # ── 周期扫描（核心）──────────────────────────────────────────
    def scan(self, now=None) -> dict[str, Any]:
        """扫描全部自动化：learn → band 闸门 → 预测 → 命中则提前触发（仅 auto）。

        返回本次扫描的累计统计快照。
        """
        self._stats["scans"] += 1
        clock = getattr(self._runtime, "clock", None) or SystemTimeSource()
        scan_now = (
            ensure_aware(now, who="pretrigger.scan")
            if now is not None
            else clock.local_now()
        )
        self._stats["last_scan"] = scan_now.isoformat()

        fired = skipped = below = cold = 0
        automation_count = 0
        for auto in self._runtime.graph:
            automation_count += 1
            aid = auto.id

            # 1) 学习（从 TriggerHistory 拉真实触发，幂等去重）
            try:
                self._predictor.learn(aid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("pretrigger learn 失败（%s）：%s", aid, exc)
                continue

            # 2) band 判定（调用方注入策略，不 import af_conf）
            if self._conf is None or not self._policy.allows(self._conf.band(aid)):
                skipped += 1
                continue

            # 3) 预测 + 经验先验注入
            prior, prior_w = self.experience_prior_for(aid)
            info = self._predictor.explain(
                aid, scan_now, self._window,
                prior=prior, prior_weight=prior_w,
            )
            if info.get("cold_start"):
                cold += 1
                continue
            if float(info["p"]) <= self._threshold:
                below += 1
                continue

            # 4) 自动提前触发（auto 档才走到这里）
            ok = self._predictor.pre_trigger(
                aid, self._threshold,
                window_minutes=self._window,
                prior=prior, prior_weight=prior_w,
            )
            if ok:
                fired += 1

        self._stats["automations"] = automation_count
        self._stats["fired"] += fired
        self._stats["skipped_band"] += skipped
        self._stats["below_threshold"] += below
        self._stats["cold_start"] = cold
        self._save()
        return dict(self._stats)

    def _save(self) -> None:
        try:
            self._predictor.flush()
            self._history._save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("pretrigger 持久化失败：%s", exc)

    # ── 定时器 ───────────────────────────────────────────────────
    def start(self, later: Callable[[float, Callable[[], Any]], Any] | None) -> None:
        """用 ``later(seconds, callback)`` 注册周期扫描（af_runtime_ext.make_later）。"""
        if later is None:
            return

        def _tick() -> None:
            self.scan()
            later(self._interval, _tick)

        later(self._interval, _tick)

    def stats(self) -> dict[str, Any]:
        return dict(self._stats)
