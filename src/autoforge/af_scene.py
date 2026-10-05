"""af_scene —— 场景模式：一组自动化的协同编排（激活 / 关闭 / 互斥）。

一个 `Scene` = 一组 automation_id + 可选互斥组 `exclusive_group`。`SceneManager` 负责：

- ``activate(scene_id)``  ：打开场景内全部自动化；若场景属某互斥组，
  **先**关掉组内其他已激活场景（fail-closed：宁可短暂空档，绝不两个互斥场景并存）。
- ``deactivate(scene_id)``：关闭场景内全部自动化（逐个尽力下发，不短路）。
- ``list_scenes()``       ：返回全部场景快照（拷贝，外部改写不影响内部状态）。
- 状态持久化到 ``persist_dir/scenes.json``（原子写走 ``af_atomic.atomic_write_text``：随机 tmp + fsync）。

依赖注入：``executor`` 承担真正的自动化开/关（af_executor），本模块只做编排与状态。
executor 的形状见 :class:`SceneExecutor`；由于现网签名未在契约中给出，
构造期做形状探测并兼容三种常见签名（见 ``_detect_shape``）。

失败语义一览（细节见模块设计说明）：
- 持久化读写失败 → fail-open（不阻断现场操作，但记录 ``last_persist_error``）；
- executor 返回假值 / 抛异常  → fail-closed（视为该自动化未生效）；
- ``activate`` 部分失败        → 回滚已下发的 enable，场景保持 inactive；
- ``activate`` 互斥关闭失败    → 放弃本次激活（互斥不变量优先）；
- ``deactivate`` 部分失败      → 返回 False 且保持 active=True（状态反映现实，可重试）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Iterable, Protocol, runtime_checkable

from .af_atomic import atomic_write_text

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注
    from typing import Any

logger = logging.getLogger(__name__)

#: 持久化文件名与格式版本
PERSIST_FILENAME = "scenes.json"
PERSIST_VERSION = 1

#: executor 形状标识
_SHAPE_SET_ENABLED = "set_automation_enabled"
_SHAPE_ENABLE_DISABLE = "enable/disable"
_SHAPE_ENABLE_AUTOMATION = "enable_automation/disable_automation"


@runtime_checkable
class SceneExecutor(Protocol):
    """场景模式需要的最小 executor 能力：开 / 关某个自动化。

    返回 ``True``/``None`` 视为成功，``False`` 视为失败；抛异常按失败处理（fail-closed）。
    """

    def set_automation_enabled(self, automation_id: str, enabled: bool) -> "bool | None":
        ...


@dataclass
class Scene:
    """一组自动化的协同编排单元。

    - ``scene_id``       ：唯一标识（构造期查重，重复即 ValueError）。
    - ``name``           ：展示名。
    - ``automations``    ：本场景要开/关的 automation_id 列表（顺序即下发顺序）。
    - ``exclusive_group``：互斥组名；同组场景同时最多激活一个。``None`` 表示不参与互斥。
    - ``active``         ：当前是否激活。构造入参可给初值，随后会被持久化状态覆盖。
    """

    scene_id: str
    name: str
    automations: list[str] = field(default_factory=list)
    exclusive_group: str | None = None
    active: bool = False


def _detect_shape(executor: object) -> str:
    """探测 executor 支持的开/关签名；不支持则 fail-fast。"""
    if callable(getattr(executor, "set_automation_enabled", None)):
        return _SHAPE_SET_ENABLED
    if callable(getattr(executor, "enable", None)) and callable(getattr(executor, "disable", None)):
        return _SHAPE_ENABLE_DISABLE
    if callable(getattr(executor, "enable_automation", None)) and callable(
        getattr(executor, "disable_automation", None)
    ):
        return _SHAPE_ENABLE_AUTOMATION
    raise TypeError(
        "executor 必须提供 set_automation_enabled(id, enabled) 或 enable()/disable() "
        "或 enable_automation()/disable_automation() 之一，got %r" % type(executor).__name__
    )


def _dispatch(executor: object, shape: str, automation_id: str, enabled: bool) -> bool:
    """按探测到的形状下发一次开/关；异常与假值返回一律按失败处理（fail-closed）。"""
    try:
        if shape == _SHAPE_SET_ENABLED:
            ret = executor.set_automation_enabled(automation_id, enabled)  # type: ignore[attr-defined]
        elif shape == _SHAPE_ENABLE_DISABLE:
            method = getattr(executor, "enable" if enabled else "disable")
            ret = method(automation_id)
        else:
            method = getattr(executor, "enable_automation" if enabled else "disable_automation")
            ret = method(automation_id)
    except Exception as exc:  # noqa: BLE001 - 执行器故障不得击穿编排器
        logger.error("executor %s(%s) 抛异常：%s", "enable" if enabled else "disable", automation_id, exc)
        return False
    ok = ret is None or bool(ret)
    if not ok:
        logger.error("executor %s(%s) 返回失败", "enable" if enabled else "disable", automation_id)
    return ok


class SceneManager:
    """场景编排器：激活 / 关闭 / 互斥 / 持久化。

    所有公开方法线程安全（内部 ``RLock`` 串行化）。
    """

    def __init__(
        self,
        scenes: "Iterable[Scene]",
        executor: object,
        persist_dir: "str | os.PathLike[str]",
    ) -> None:
        self._shape = _detect_shape(executor)  # fail-fast：形状不对立刻炸，不留到半夜
        self._executor = executor
        self._persist_dir = os.fspath(persist_dir)
        self._lock = threading.RLock()
        self._scenes: "dict[str, Scene]" = {}
        self._groups: "dict[str, list[str]]" = {}
        #: 最近一次落盘失败的原因；成功落盘后清空。供运维观测用。
        self.last_persist_error: "str | None" = None

        for raw in scenes:
            if raw.scene_id in self._scenes:
                raise ValueError("duplicate scene_id: %r" % raw.scene_id)
            scene = replace(raw, automations=list(raw.automations))  # 防御性拷贝
            self._scenes[scene.scene_id] = scene
            if scene.exclusive_group:
                self._groups.setdefault(scene.exclusive_group, []).append(scene.scene_id)

        self._load_state()

    # ------------------------------------------------------------------ 查询
    def list_scenes(self) -> "list[Scene]":
        """返回全部场景快照（拷贝，按声明顺序）。"""
        with self._lock:
            return [self._copy(s) for s in self._scenes.values()]

    def get_scene(self, scene_id: str) -> "Scene | None":
        """按 id 取场景快照；不存在返回 ``None``。"""
        with self._lock:
            scene = self._scenes.get(scene_id)
            return self._copy(scene) if scene is not None else None

    def is_active(self, scene_id: str) -> bool:
        """场景是否激活；未知 id 视为未激活。"""
        with self._lock:
            scene = self._scenes.get(scene_id)
            return bool(scene.active) if scene is not None else False

    def active_scenes(self) -> "list[Scene]":
        """当前全部激活场景的快照。"""
        with self._lock:
            return [self._copy(s) for s in self._scenes.values() if s.active]

    @property
    def state_path(self) -> str:
        """持久化文件路径（``persist_dir/scenes.json``）。"""
        return os.path.join(self._persist_dir, PERSIST_FILENAME)

    # ------------------------------------------------------------------ 编排
    def activate(self, scene_id: str) -> bool:
        """激活场景：同组互斥关闭 → 打开本场景全部自动化 → 落盘。

        返回 ``True`` 表示场景内所有自动化都已成功打开。任一环节失败即回滚 / 放弃，
        场景保持未激活（fail-closed）。
        """
        with self._lock:
            scene = self._scenes.get(scene_id)
            if scene is None:
                logger.warning("activate: 未知 scene_id=%r，忽略", scene_id)
                return False
            if scene.active:
                logger.debug("activate: 场景 %r 已激活，幂等返回", scene_id)
                return True

            # 1) 互斥：先关同组其他场景。关不干净就整单放弃（互斥不变量优先）。
            if scene.exclusive_group:
                for other_id in self._groups.get(scene.exclusive_group, ()):
                    if other_id == scene_id:
                        continue
                    other = self._scenes[other_id]
                    if other.active and not self._force_off(other):
                        logger.error(
                            "activate: 互斥组 %r 的场景 %r 关闭失败，放弃激活 %r",
                            scene.exclusive_group, other_id, scene_id,
                        )
                        self._save_state()
                        return False

            # 2) 打开本场景自动化；任一失败 → 回滚已打开的。
            opened: "list[str]" = []
            for auto_id in scene.automations:
                if not _dispatch(self._executor, self._shape, auto_id, True):
                    logger.error("activate: 场景 %r 打开 automation=%r 失败，回滚", scene_id, auto_id)
                    for done in opened:
                        _dispatch(self._executor, self._shape, done, False)
                    self._save_state()
                    return False
                opened.append(auto_id)

            scene.active = True
            self._save_state()
            logger.info("activate: 场景 %r 已激活（%d 个自动化）", scene_id, len(opened))
            return True

    def deactivate(self, scene_id: str) -> bool:
        """关闭场景：关闭场景内全部自动化并落盘。

        对未激活场景幂等返回 ``True`` 且不下发指令。若任一自动化关闭失败，
        返回 ``False`` 且场景保持 ``active=True``（状态反映执行器现实，可重试）。
        """
        with self._lock:
            scene = self._scenes.get(scene_id)
            if scene is None:
                logger.warning("deactivate: 未知 scene_id=%r，忽略", scene_id)
                return False
            if not scene.active:
                logger.debug("deactivate: 场景 %r 本就未激活，幂等返回", scene_id)
                return True
            ok = self._force_off(scene)
            self._save_state()
            logger.info("deactivate: 场景 %r 关闭%s", scene_id, "成功" if ok else "失败")
            return ok

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _copy(scene: Scene) -> Scene:
        return replace(scene, automations=list(scene.automations))

    def _force_off(self, scene: Scene) -> bool:
        """尽力关闭场景内全部自动化（不短路）。全成功才把 active 置 False。"""
        ok = True
        for auto_id in scene.automations:
            if not _dispatch(self._executor, self._shape, auto_id, False):
                ok = False
        if ok:
            scene.active = False
        else:
            logger.error(
                "场景 %r 关闭未完全成功：可能仍有自动化在运行，保持 active=True 以便重试",
                scene.scene_id,
            )
        return ok

    def _load_state(self) -> None:
        """从 persist_dir/scenes.json 回灌 active 状态。

        缺文件 → 全新开始；文件损坏 / 结构非法 → fail-open 忽略之；
        状态里的未知 scene_id → 忽略（定义以构造入参为唯一真相）。
        """
        path = self.state_path
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning("持久化状态读取失败，忽略之（fail-open）：%s", exc)
            return
        records = payload.get("scenes") if isinstance(payload, dict) else None
        if not isinstance(records, dict):
            logger.warning("持久化状态结构非法，忽略之（fail-open）：%s", path)
            return
        for scene_id, record in records.items():
            scene = self._scenes.get(scene_id)
            if scene is None:
                logger.debug("持久化状态含未知场景 %r，忽略", scene_id)
                continue
            active = bool(record.get("active", False)) if isinstance(record, dict) else bool(record)
            scene.active = active

    def _save_state(self) -> bool:
        """原子落盘全量快照（定义 + 状态）。失败 fail-open：返回 False 并记录原因。"""
        payload = {
            "version": PERSIST_VERSION,
            "saved_at": time.time(),
            "scenes": {
                sid: {
                    "name": s.name,
                    "automations": list(s.automations),
                    "exclusive_group": s.exclusive_group,
                    "active": s.active,
                }
                for sid, s in self._scenes.items()
            },
        }
        path = self.state_path
        try:
            os.makedirs(self._persist_dir, exist_ok=True)
            atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        except OSError as exc:
            self.last_persist_error = str(exc)
            logger.warning("持久化状态写入失败（fail-open，状态未落盘）：%s", exc)
            return False
        self.last_persist_error = None
        return True