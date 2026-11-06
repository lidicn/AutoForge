"""运行期事实落盘（PM 卡 2026-11-04）：触发计数 ＋ 试演/canary 状态。

**为什么有这一层**：此前 `af_service` 的自动化列表把 `trial` / `last_triggered` /
`trigger_7d` 三个字段**硬编码**成 `None` / `None` / `0`，注释也写明「试演期与触发历史
没有可读取的落盘正源——触发记录只在 watch 进程内存里」。于是 UI 永远显示「最近触发 0 次」
「未进入试演」，与真实运行无关（Bug-2 / Bug-3）。本模块补的就是那条**落盘正源**。

**正源放哪**：`<store_root>/<archive>.runtime.json`——与既有 `<archive>.conf.json` 置信度
旁车**同目录、同命名风格**（operator 一眼能把运行事实和它的归档对上）。⛔ 不进
`<archive>/v*.json` 版本目录：那里是 IR 正文，混进去会污染 history/版本语义并和并发写撞车。

**谁写**：
- 触发计数 —— `af_scheduler.Scheduler._try_fire` 成功 spawn 之后的**单一出口**追加一条；
  所有触发形状（state / group(and|or) / time / 队列排空）都汇到这个出口，故 **group 命中天然
  计入**，⛔ 不在匹配处另加判定（那样每加一种触发形状就得补一处，必漏）。
- 试演状态 —— `af_executor.NodeExecutor._do` 的真 canary 分支进/出观察窗时记录。

**口径纪律（PM 批注①，写进判据）**：`trigger_7d` **不是**由「环形窗口里还剩几条」数出来的
——那样封顶一裁，计数就被动偏低（口径洞）。这里用**按日计数器 + 惰性读**：
`trigger_days = {"YYYY-MM-DD": n}`，读 7 天窗口时只累加窗口内的日键，**7 天以前的历史只增
不删也不参与**，所以「窗口内计数」永远等于真实发生次数，与封顶无关。日键另设 `_MAX_DAYS`
硬上限，超限时**先裁最老的日键**——被裁的只会是 >7 天窗口外的键，⛔ 不会影响 `trigger_7d`。

**写纪律**：`FileLock(<archive>.runtime.lock)` ＋ 原子替换（复用 `atomic_write_text`）。
记账失败⛔ 不阻断触发/执行（`_mutate` 一律 try/except 吞成一行 warning，与 approve 审计记账同口径）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .af_atomic import atomic_write_text
from .af_flock import FileLock

__all__ = ["RuntimeFacts", "RUNTIME_SUFFIX", "WINDOW_DAYS"]

logger = logging.getLogger("autoforge.runtime_facts")

#: 旁车文件名后缀：`<archive>.runtime.json`（与 `<archive>.conf.json` 同风格）
RUNTIME_SUFFIX = ".runtime.json"

#: `trigger_7d` 的窗口天数（含今天共 7 个自然日）
WINDOW_DAYS = 7

#: 日计数器最多留几天：只影响"很久以前那几天"的残留键，⛔ 不影响 `trigger_7d`
#: （窗口 7 天，留 14 天给跨窗口边界的旧键余量，同时防无界增长）
_MAX_DAYS = 14

#: 原始触发流水环形上限（只供观测/调试，⛔ 不参与 trigger_7d 计算）
_MAX_TRIGGERS = 200


def _today(now: datetime | None = None) -> str:
    """当天日键（UTC 自然日）。"""
    dt = now or datetime.now(timezone.utc)
    return dt.date().isoformat()


class RuntimeFacts:
    """单份归档的运行期事实旁车（`<archive>.runtime.json`）。

    只做"事实存取"：⛔ 不含业务判定，⛔ 不 import af_service（避免环）。
    """

    def __init__(self, store_root: str | Path, archive: str) -> None:
        self.root = Path(store_root)
        self.archive = str(archive)
        self.path = self.root / f"{self.archive}{RUNTIME_SUFFIX}"
        self._lock_path = self.root / f"{self.archive}.runtime.lock"

    # ── 读 ────────────────────────────────────────────────────────────
    def read(self) -> dict[str, Any]:
        """读整份事实；文件缺/损坏返回空 dict（读侧 fail-soft，⛔ 不抛给列表面）。"""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        """给 UI 读的三件：`last_triggered` / `trigger_7d` / `trial`。

        缺文件或字段 ⇒ 回落 None/0（即改前硬编码行为，向后兼容）。
        """
        data = self.read()
        days = data.get("trigger_days")
        count = 0
        if isinstance(days, Mapping):
            cutoff = (now or datetime.now(timezone.utc)).date() - timedelta(days=WINDOW_DAYS - 1)
            for key, val in days.items():
                try:
                    day = datetime.fromisoformat(str(key)).date()
                    n = int(val)
                except (TypeError, ValueError):
                    continue
                if day >= cutoff:
                    count += n
        trial = data.get("trial")
        # D10 第二面（2026-11-06）：触发沿事实——`last_met` 表示"本轮所有条件是否已成立并已派发"。
        # 分层铁律：计数是事实、派发是决策——`last_met` 与 `trigger_7d` 是两个独立字段，
        # ⛔ 调用方（UI）不许从触发计数反推"是否已派发"。缺文件/字段 ⇒ None（向后兼容）。
        last_met = data.get("last_met")
        last_dispatched_at = data.get("last_dispatched_at")
        return {
            "last_triggered": data.get("last_triggered"),
            "trigger_7d": count,
            "trial": trial if isinstance(trial, Mapping) else None,
            "last_met": last_met if isinstance(last_met, bool) else None,
            "last_dispatched_at": last_dispatched_at if isinstance(last_dispatched_at, str) else None,
        }

    # ── 写 ────────────────────────────────────────────────────────────
    def _mutate(self, fn: Any) -> None:
        """锁内 read-modify-write + 原子替换。记账失败只响一声（⛔ 不阻断主流程）。"""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with FileLock(self._lock_path, timeout=5.0):
                data = self.read()
                if not isinstance(data, dict):
                    data = {}
                data.setdefault("schema", 1)
                data["archive"] = self.archive
                fn(data)
                atomic_write_text(self.path, json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RUNTIME_FACTS_WRITE_FAILED 归档 %r 运行事实记账失败（不影响主流程）：%r",
                self.archive, exc,
            )

    def record_trigger(self, *, automation_id: str, node_id: str = "",
                       entity_id: str = "", via: str = "",
                       at: datetime | None = None) -> None:
        """记一次**真的触发**（`_try_fire` 成功 spawn 后调）。"""
        now = at or datetime.now(timezone.utc)
        day = _today(now)
        entry = {
            "at": now.isoformat(),
            "automation_id": str(automation_id),
            "node_id": str(node_id),
            "entity_id": str(entity_id),
            "via": str(via),
        }

        def _apply(data: dict[str, Any]) -> None:
            days = data.get("trigger_days")
            if not isinstance(days, dict):
                days = {}
            days[day] = int(days.get(day) or 0) + 1
            # 封顶日键：裁最老的（只会裁到 >7 天窗口外的，⛔ 不影响 trigger_7d）
            if len(days) > _MAX_DAYS:
                for old in sorted(days)[: len(days) - _MAX_DAYS]:
                    days.pop(old, None)
            data["trigger_days"] = days
            data["last_triggered"] = entry["at"]
            recent = data.get("triggers")
            if not isinstance(recent, list):
                recent = []
            recent.append(entry)
            data["triggers"] = recent[-_MAX_TRIGGERS:]

        self._mutate(_apply)

    def record_trial(self, *, state: str, automation_id: str = "",
                     since: datetime | None = None, until: datetime | None = None,
                     extra: Mapping[str, Any] | None = None) -> None:
        """记试演/canary 状态流转（observing / verified / rolled_back / failed）。"""
        now = datetime.now(timezone.utc)

        def _apply(data: dict[str, Any]) -> None:
            trial: dict[str, Any] = {"state": str(state)}
            if automation_id:
                trial["automation_id"] = str(automation_id)
            trial["since"] = (since or now).isoformat()
            if until is not None:
                trial["until"] = until.isoformat()
            if extra:
                trial.update({str(k): v for k, v in extra.items()})
            data["trial"] = trial

        self._mutate(_apply)

    def clear_trial(self) -> None:
        """试演结束（转正/放弃）⇒ 清掉 trial 格。"""
        def _apply(data: dict[str, Any]) -> None:
            data.pop("trial", None)

        self._mutate(_apply)

    def record_met(self, *, met: bool, at: datetime | None = None) -> None:
        """D10（2026-11-06）：记**触发沿事实**——本轮所有条件是否已成立（且已派发）。

        `met=True` 表示「本轮条件从不满→满足的跳变沿已派发一次」，`met=False` 表示
        「条件已回落（不满），下一轮重新满足＝新回合才再派发」。`last_dispatched_at`
        只在 `met=True` 时更新（派发那刻的时间戳）；`met=False` 保留上一次派发时刻，
        让 UI 能区分「上回合派发过、本回合已回落」与「从未派发」。

        分层铁律：计数是事实、派发是决策——本字段与 `record_trigger` 的触发计数
        是**两个独立记录**，⛔ 不由计数反推（D10 卡 §一）。
        """
        now = at or datetime.now(timezone.utc)

        def _apply(data: dict[str, Any]) -> None:
            data["last_met"] = bool(met)
            if met:
                data["last_dispatched_at"] = now.isoformat()
            else:
                data.setdefault("last_dispatched_at", None)

        self._mutate(_apply)
