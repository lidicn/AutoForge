"""af_version —— 自动化版本管理 / 囍滚（Lever-Hub 单：AutoForge-版本管理回滚）。

每次部署前自动快照 IR 完整快照，支持 diff / 一键回滚 / 版本标签 / 版本历史，
持久化到 ``.forge/versions/<automation_id>.json``。

契约落点（§1 ↔ 本模块）::

    Version{version_id, automation_id, ir_snapshot, created_at, label, parent_id}
                                    -> dataclass Version
    snapshot(automation_id) -> Version          -> VersionManager.snapshot
    diff(v1, v2) -> dict                        -> VersionManager.diff / diff()
    rollback(automation_id, version_id) -> bool -> VersionManager.rollback
    tag(version_id, label)                      -> VersionManager.tag
    history(automation_id) -> list[Version]     -> VersionManager.history
    部署前自动快照（无需手动触发）              -> VersionManager.wrap_deployer
    .forge/versions/<automation_id>.json        -> VersionManager 写透持久化

硬约束（§2.2）：不改任何现有文件、零新依赖（标准库 + 现有 autoforge 模块）、
不碰现网配置、**不 import af_store 内部**——IR 读写全部走注入接口
(``ir_provider`` 取 IR / ``ir_writer`` 回写)。
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

try:  # 与 af_feedback 的时钟 / 审计口径对齐；极简环境下本地降级（不构成硬依赖）
    from autoforge.af_feedback import audit_write as _af_audit_write
    from autoforge.af_feedback import clock_now as _af_clock_now
except Exception:  # pragma: no cover —— 仅无 af_feedback 时降级
    _af_audit_write = None
    _af_clock_now = None

__all__ = [
    "LABELS",
    "SCHEMA_VERSION",
    "Version",
    "VersionError",
    "VersionManager",
    "VersionStore",
    "VersionService",
    "Versions",
    "diff",
    "diff_ir",
    "get_default_store",
    "history",
    "rollback",
    "set_default_store",
    "snapshot",
    "tag",
]

#: 版本标签取值（契约枚举：stable / canary / experimental）
LABELS: tuple[str, ...] = ("stable", "canary", "experimental")

#: 持久化文件结构版本
SCHEMA_VERSION = 1

_COND_KEYS = ("when", "condition", "if", "guard", "expr")
_SRC_KEYS = ("from", "source", "src")
_DST_KEYS = ("to", "target", "dst")
_EDGE_ID_KEYS = ("id", "edge_id")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_MISSING = object()


class VersionError(ValueError, RuntimeError):
    """版本管理错误。

    同时继承 ``ValueError`` / ``RuntimeError``：契约未规定异常类型，
    调用方写 ``except ValueError`` 或 ``except RuntimeError`` 都能接住。
    """


# --------------------------------------------------------------------------- #
# 版本模型
# --------------------------------------------------------------------------- #

@dataclass
class Version:
    """一个 IR 完整快照（§1 契约字段，一个不多、一个不少）。"""

    version_id: str
    automation_id: str
    ir_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    label: str | None = None
    parent_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        """序列化（持久化行格式）。"""
        return {
            "version_id": self.version_id,
            "automation_id": self.automation_id,
            "ir_snapshot": _jsonable(self.ir_snapshot),
            "created_at": self.created_at,
            "label": self.label,
            "parent_id": self.parent_id,
        }

    @classmethod
    def from_json(cls, row: Mapping[str, Any]) -> "Version":
        """反序列化（缺字段按缺省值，兼容旧结构）。"""
        return cls(
            version_id=str(row["version_id"]),
            automation_id=str(row["automation_id"]),
            ir_snapshot=copy.deepcopy(dict(row.get("ir_snapshot") or {})),
            created_at=float(row.get("created_at") or 0.0),
            label=row.get("label"),
            parent_id=row.get("parent_id"),
        )


# --------------------------------------------------------------------------- #
# IR 形态归一（duck typing；不 import af_ir / af_store 内部）
# --------------------------------------------------------------------------- #

def _jsonable(obj: Any) -> Any:
    """把任意 IR 形态（dict / list / af_ir dataclass / 带 to_json 的对象）转成可 JSON 结构。"""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Mapping):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    to_json = getattr(obj, "to_json", None)
    if callable(to_json):
        return _jsonable(to_json())
    attrs = getattr(obj, "__dict__", None)
    if isinstance(attrs, Mapping):
        return {str(k): _jsonable(v) for k, v in attrs.items() if not str(k).startswith("_")}
    return str(obj)


def _iter_automations(doc: Any) -> dict[str, dict[str, Any]]:
    """从任意 IR 形态抽出 ``{automation_id: automation dict}``。

    兼容三种输入：``{"automations": [...]}`` 文档、``{"automations": {...}}`` 文档、
    单个 automation 映射（af_proposal.build_ir 的产物 / 注入的单自动化 IR）。
    """
    doc = _jsonable(doc)
    out: dict[str, dict[str, Any]] = {}
    if isinstance(doc, list):
        for idx, auto in enumerate(doc):
            if isinstance(auto, Mapping):
                auto = dict(auto)
                out[str(auto.get("id") or f"#{idx}")] = auto
        return out
    if not isinstance(doc, Mapping):
        return out
    if "automations" in doc:
        container = doc["automations"]
        if isinstance(container, Mapping):
            for key, auto in container.items():
                auto = dict(auto) if isinstance(auto, Mapping) else {}
                out[str(auto.get("id") or key)] = auto
        else:
            for idx, auto in enumerate(container or []):
                if isinstance(auto, Mapping):
                    auto = dict(auto)
                    out[str(auto.get("id") or f"#{idx}")] = auto
        return out
    if "nodes" in doc or "trigger" in doc:
        out[str(doc.get("id") or "")] = dict(doc)
    return out


def _automation_of(doc: Any, automation_id: str | None = None) -> dict[str, Any] | None:
    """按 automation_id 抽取单个 automation（只有一个时可省略 id）。"""
    autos = _iter_automations(doc)
    if automation_id is not None:
        key = str(automation_id)
        if key in autos:
            return autos[key]
        if len(autos) == 1:
            return next(iter(autos.values()))
        return None
    if len(autos) == 1:
        return next(iter(autos.values()))
    return None


def _first_of(obj: Any, keys: Sequence[str]) -> Any:
    if not isinstance(obj, Mapping):
        return None
    for key in keys:
        if key in obj and obj[key] is not None and obj[key] != "":
            return obj[key]
    return None


def _nodes_of(auto: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """节点索引：id -> 节点 dict（兼容 mapping / list 两种 nodes 形态）。"""
    raw = auto.get("nodes") if isinstance(auto, Mapping) else None
    out: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        items = [(str(k), v) for k, v in raw.items()]
    else:
        items = [(f"#{idx}", v) for idx, v in enumerate(raw or [])]
    for key, node in items:
        node = dict(node) if isinstance(node, Mapping) else {"value": node}
        out[str(node.get("id") or key)] = node
    return out


def _edges_of(auto: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """边索引：稳定 key -> 边 dict。key 优先取显式 id，否则 ``src->dst#kind``。"""
    raw = None
    if isinstance(auto, Mapping):
        for key in ("edges", "transitions"):
            if key in auto:
                raw = auto[key]
                break
    items = list(raw.values()) if isinstance(raw, Mapping) else list(raw or [])
    out: dict[str, dict[str, Any]] = {}
    for edge in items:
        edge = dict(edge) if isinstance(edge, Mapping) else {"value": edge}
        explicit = _first_of(edge, _EDGE_ID_KEYS)
        if explicit is not None:
            key = str(explicit)
        else:
            src = _first_of(edge, _SRC_KEYS)
            dst = _first_of(edge, _DST_KEYS)
            key = f"{src if src is not None else '?'}->{dst if dst is not None else '?'}#{edge.get('kind') or ''}"
        base, n = key, 1
        while key in out:                       # 同 key 多条边：确定性消歧
            n += 1
            key = f"{base}@{n}"
        out[key] = edge
    return out


def _condition_of(obj: Any) -> Any | None:
    return _first_of(obj, _COND_KEYS)


def _conditions_of(auto: Mapping[str, Any] | None) -> dict[str, Any]:
    """条件索引：``node::<id>`` / ``edge::<key>`` / ``trigger`` -> 条件表达式。"""
    out: dict[str, Any] = {}
    for nid, node in _nodes_of(auto).items():
        cond = _condition_of(node)
        if cond is not None:
            out[f"node::{nid}"] = cond
    for ekey, edge in _edges_of(auto).items():
        cond = _condition_of(edge)
        if cond is not None:
            out[f"edge::{ekey}"] = cond
    trigger = auto.get("trigger") if isinstance(auto, Mapping) else None
    if isinstance(trigger, Mapping):
        cond = _condition_of(trigger)
        if cond is not None:
            out["trigger"] = cond
    return out


# --------------------------------------------------------------------------- #
# diff 内核
# --------------------------------------------------------------------------- #

def _diff_section(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    added = {k: copy.deepcopy(v) for k, v in after.items() if k not in before}
    deleted = {k: copy.deepcopy(v) for k, v in before.items() if k not in after}
    modified: dict[str, Any] = {}
    unchanged: list[str] = []
    for key, old in before.items():
        if key not in after:
            continue
        new = after[key]
        if old == new:
            unchanged.append(key)
            continue
        changed = None
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            changed = sorted(
                k for k in (set(old) | set(new)) if old.get(k, _MISSING) != new.get(k, _MISSING)
            )
        modified[key] = {
            "before": copy.deepcopy(old),
            "after": copy.deepcopy(new),
            "changed": changed,
        }
    return {
        "added": added,
        "deleted": deleted,
        "removed": deleted,          # 别名：兼容 removed / deleted 两种读法
        "modified": modified,
        "added_ids": list(added),
        "deleted_ids": list(deleted),
        "modified_ids": list(modified),
        "unchanged": unchanged,
    }


def _diff_pair(
    auto_before: Mapping[str, Any] | None,
    auto_after: Mapping[str, Any] | None,
    *,
    automation_id: str | None = None,
) -> dict[str, Any]:
    before = auto_before or {}
    after = auto_after or {}
    nodes = _diff_section(_nodes_of(before), _nodes_of(after))
    edges = _diff_section(_edges_of(before), _edges_of(after))
    conds = _diff_section(_conditions_of(before), _conditions_of(after))

    summary = {
        "nodes_added": len(nodes["added"]),
        "nodes_deleted": len(nodes["deleted"]),
        "nodes_modified": len(nodes["modified"]),
        "edges_added": len(edges["added"]),
        "edges_deleted": len(edges["deleted"]),
        "edges_modified": len(edges["modified"]),
        "conditions_added": len(conds["added"]),
        "conditions_deleted": len(conds["deleted"]),
        "conditions_modified": len(conds["modified"]),
    }
    summary["total_changes"] = sum(summary.values())
    identical = summary["total_changes"] == 0

    added_view = {"nodes": nodes["added"], "edges": edges["added"], "conditions": conds["added"]}
    deleted_view = {"nodes": nodes["deleted"], "edges": edges["deleted"], "conditions": conds["deleted"]}
    modified_view = {"nodes": nodes["modified"], "edges": edges["modified"], "conditions": conds["modified"]}

    return {
        "automation_id": automation_id,
        "identical": identical,
        # 视图 A：按元素分组（nodes / edges / conditions → added / deleted / modified）
        "nodes": nodes,
        "edges": edges,
        "conditions": conds,
        # 视图 B：按变更类型分组（added / deleted / modified → nodes / edges / conditions）
        "added": added_view,
        "deleted": deleted_view,
        "removed": deleted_view,
        "modified": modified_view,
        # 视图 C：扁平 id 列表（便于断言 / 打印）
        "added_nodes": nodes["added_ids"],
        "deleted_nodes": nodes["deleted_ids"],
        "modified_nodes": nodes["modified_ids"],
        "added_edges": edges["added_ids"],
        "deleted_edges": edges["deleted_ids"],
        "modified_edges": edges["modified_ids"],
        "added_conditions": conds["added_ids"],
        "deleted_conditions": conds["deleted_ids"],
        "modified_conditions": conds["modified_ids"],
        "summary": summary,
    }


def diff_ir(ir1: Any, ir2: Any, *, automation_id: str | None = None) -> dict[str, Any]:
    """纯函数 diff：比较两份 IR 里的同一个 automation（v1=旧，v2=新）。"""
    left = _automation_of(ir1, automation_id)
    right = _automation_of(ir2, automation_id)
    if left is None and right is None:
        raise VersionError("diff 输入里找不到可比较的 automation（多 automation 文档请传 automation_id=）")
    return _diff_pair(left, right, automation_id=automation_id)


def _resolve_ref(ref: Any, store: Any = None) -> tuple[str, str | None, Any]:
    """把 Version / 版本 id / 原始 IR 统一成 ``(version_id, automation_id, ir)``。"""
    if isinstance(ref, Version):
        return ref.version_id, ref.automation_id, ref.ir_snapshot
    if isinstance(ref, str):
        target = store or _default_store
        if target is None:
            raise VersionError(f"按版本 id 查找需要 store：{ref!r}（传 store= 或 set_default_store）")
        version = target.find(ref)
        if version is None:
            raise VersionError(f"版本不存在：{ref}")
        return version.version_id, version.automation_id, version.ir_snapshot
    ir_attr = getattr(ref, "ir_snapshot", None)
    if ir_attr is not None:
        return (
            str(getattr(ref, "version_id", "") or ""),
            str(getattr(ref, "automation_id", "") or "") or None,
            ir_attr,
        )
    if isinstance(ref, (Mapping, list, tuple)):
        autos = _iter_automations(ref)
        aid = next(iter(autos)) if len(autos) == 1 else None
        return "", aid, ref
    raise VersionError(f"无法识别的 diff 输入：{type(ref).__name__}")


def diff(v1: Any, v2: Any, *, store: Any = None) -> dict[str, Any]:
    """两个版本的节点 / 边 / 条件差异（新增 / 删除 / 修改）。

    入参可以是 :class:`Version`、版本 id（需 store）、或原始 IR 映射；
    v1 视为旧、v2 视为新。返回结构见模块 docstring 与交付说明 §7-2。
    """
    id1, aid1, ir1 = _resolve_ref(v1, store)
    id2, aid2, ir2 = _resolve_ref(v2, store)
    if aid1 and aid2 and aid1 != aid2:           # 跨 automation：各自抽取后比较
        result = _diff_pair(_automation_of(ir1, aid1), _automation_of(ir2, aid2), automation_id=None)
    else:
        aid = aid1 or aid2
        result = diff_ir(ir1, ir2, automation_id=aid)
    result["v1"] = id1 or None
    result["v2"] = id2 or None
    return result


# --------------------------------------------------------------------------- #
# 时钟 / 审计旁路（与 af_feedback 同口径；旁路失败不阻断主流程）
# --------------------------------------------------------------------------- #

def _now(clock: Any) -> float:
    if clock is None:
        return time.time()
    if isinstance(clock, (int, float)):
        return float(clock)
    if _af_clock_now is not None:
        try:
            return float(_af_clock_now(clock))
        except Exception:
            pass
    for attr in ("now", "time"):
        fn = getattr(clock, attr, None)
        if callable(fn):
            try:
                return float(fn())
            except Exception:
                pass
    if callable(clock):
        try:
            return float(clock())
        except Exception:
            pass
    return time.time()                            # fail-open：时钟坏了退回墙钟


def _audit(audit: Any, **details: Any) -> None:
    if audit is None:
        return
    try:
        if _af_audit_write is not None:
            _af_audit_write(audit, **details)
        elif callable(audit):
            audit(**details)
    except Exception:
        pass                                      # 审计是旁路（fail-open）


def _pick(aliases: dict[str, Any], names: Sequence[str], current: Any) -> Any:
    if current is not None:
        for name in names:
            aliases.pop(name, None)
        return current
    for name in names:
        if name in aliases:
            return aliases.pop(name)
    return None


def _resolve_dirs(root: Any) -> tuple[Path, Path]:
    """root 是项目根 → ``<root>/.forge``；root 本身是 .forge → 直接用它。"""
    base = Path(root) if root is not None else Path.cwd()
    forge_dir = base if base.name == ".forge" else base / ".forge"
    return forge_dir, forge_dir / "versions"


# --------------------------------------------------------------------------- #
# 版本仓库
# --------------------------------------------------------------------------- #

class VersionManager:
    """版本仓库：snapshot / diff / rollback / tag / history + 写透持久化。

    数据出入口全部注入：
    - ``ir_provider``：``automation_id -> IR``。接受 callable，或 ``{automation_id: ir}``
      映射，也接受无参 provider（返回整个 IR 文档，内部按 id 抽取）。
    - ``ir_writer``：``(automation_id, ir) -> None``，回滚时把 IR 写回执行侧；
      不注入时只更新仓库内"当前生效 IR"（:meth:`current_ir`）。

    构造参数接受常见别名（``path``/``base_dir``/``store_dir``/``forge_dir`` → root，
    ``ir_source``/``provider``/``ir_loader`` → ir_provider，
    ``ir_applier``/``applier``/``ir_setter`` → ir_writer，``now``/``clock_fn`` → clock），
    因为契约只规定了函数签名、没规定构造器命名。
    """

    def __init__(
        self,
        root: Any = None,
        *,
        ir_provider: Any = None,
        ir_writer: Any = None,
        clock: Any = None,
        audit: Any = None,
        id_factory: Callable[[], str] | None = None,
        **aliases: Any,
    ) -> None:
        root = _pick(aliases, ("root", "path", "base_dir", "store_dir", "dir", "forge_dir"), root)
        ir_provider = _pick(aliases, ("ir_provider", "ir_source", "provider", "ir_loader"), ir_provider)
        ir_writer = _pick(aliases, ("ir_writer", "ir_applier", "applier", "ir_setter"), ir_writer)
        clock = _pick(aliases, ("clock", "now", "clock_fn"), clock)
        audit = _pick(aliases, ("audit", "audit_sink"), audit)
        id_factory = _pick(aliases, ("id_factory", "new_id"), id_factory)
        if aliases:
            raise TypeError(f"VersionManager 未知参数：{sorted(aliases)}")

        self.ir_provider = ir_provider
        self.ir_writer = ir_writer
        self.clock = clock
        self.audit = audit
        self.id_factory: Callable[[], str] = id_factory or (lambda: uuid4().hex[:12])
        self.forge_dir, self.versions_dir = _resolve_dirs(root)
        self._cache: dict[str, dict[str, Any]] = {}

    # ---- 契约 API -------------------------------------------------------- #

    def snapshot(self, automation_id: str, ir: Any = None) -> Version:
        """部署前自动快照：保存 IR **完整**快照（深拷贝），返回新 Version。"""
        aid = str(automation_id)
        payload = self._read_ir(aid) if ir is None else ir
        doc = _jsonable(payload)
        if doc is None:
            raise VersionError(f"automation {aid} 没有可快照的 IR，拒绝创建空版本")
        now = _now(self.clock)
        data = self._entry(aid)
        parent = data["versions"][-1].version_id if data["versions"] else None
        version = Version(
            version_id=str(self.id_factory()),
            automation_id=aid,
            ir_snapshot=copy.deepcopy(doc),
            created_at=now,
            label=None,
            parent_id=parent,
        )
        data["versions"].append(version)
        self._save(aid)
        _audit(self.audit, at=now, kind="version_snapshot", automation_id=aid,
               version_id=version.version_id, parent_id=parent)
        return version

    def diff(self, v1: Any, v2: Any) -> dict[str, Any]:
        """两个版本（Version / 版本 id / 原始 IR）的节点 / 边 / 条件差异。"""
        return diff(v1, v2, store=self)

    def rollback(self, automation_id: str, version_id: str) -> bool:
        """一键回滚到指定版本；成功后**自动创建新快照**（可再次回滚）。

        fail-closed：找不到版本 / ir_writer 失败 / 持久化失败都返回 False，
        且不留半套状态（不追加快照）。
        """
        aid = str(automation_id)
        now = _now(self.clock)
        data = self._entry(aid)
        target = next((v for v in data["versions"] if v.version_id == str(version_id)), None)
        if target is None:
            _audit(self.audit, at=now, kind="version_rollback_failed", automation_id=aid,
                   version_id=str(version_id), reason="version_not_found")
            return False

        restored = copy.deepcopy(target.ir_snapshot)
        if self.ir_writer is not None:
            try:
                self.ir_writer(aid, copy.deepcopy(restored))
            except Exception as exc:              # noqa: BLE001 —— 契约要求返回 bool
                _audit(self.audit, at=now, kind="version_rollback_failed", automation_id=aid,
                       version_id=target.version_id, reason=f"{type(exc).__name__}: {exc}")
                return False

        parent = data["versions"][-1].version_id if data["versions"] else None
        applied = Version(
            version_id=str(self.id_factory()),
            automation_id=aid,
            ir_snapshot=copy.deepcopy(restored),
            created_at=now,
            label=None,
            parent_id=parent,
        )
        try:
            data["versions"].append(applied)
            self._save(aid)
        except Exception as exc:                  # noqa: BLE001 —— 持久化失败要回撤内存追加
            if data["versions"] and data["versions"][-1] is applied:
                data["versions"].pop()
            _audit(self.audit, at=now, kind="version_rollback_failed", automation_id=aid,
                   version_id=target.version_id, reason=f"{type(exc).__name__}: {exc}")
            return False

        _audit(self.audit, at=now, kind="version_rollback_applied", automation_id=aid,
               from_version=target.version_id, version_id=applied.version_id)
        return True

    def tag(self, version_id: str, label: str | None) -> Version:
        """打版本标签：stable / canary / experimental（label=None 清除标签）。"""
        if label is not None and label not in LABELS:
            raise VersionError(f"未知标签：{label!r}（允许：{', '.join(LABELS)}；label=None 清除）")
        version = self.find(version_id)
        if version is None:
            raise VersionError(f"版本不存在：{version_id}")
        version.label = label
        self._save(version.automation_id)
        _audit(self.audit, at=_now(self.clock), kind="version_tagged",
               automation_id=version.automation_id, version_id=version.version_id, label=label)
        return version

    def history(self, automation_id: str) -> list[Version]:
        """版本历史，按时间**倒序**（同刻按创建次序倒序，冻结时钟也稳定）。"""
        data = self._entry(automation_id)
        ordered = sorted(
            enumerate(data["versions"]),
            key=lambda item: (item[1].created_at, item[0]),
            reverse=True,
        )
        return [version for _, version in ordered]

    # ---- 辅助 API -------------------------------------------------------- #

    def find(self, version_id: str) -> Version | None:
        """按 version_id 全仓查找（跨 automation）。"""
        want = str(version_id)
        for aid in self._known_automations():
            try:
                data = self._entry(aid)
            except VersionError:
                continue                          # 坏文件只跳过自己，不拖死其它 automation
            for version in data["versions"]:
                if version.version_id == want:
                    return version
        return None

    def current_ir(self, automation_id: str) -> Any | None:
        """当前生效 IR（head 快照；无快照时回落到 ir_provider）。"""
        data = self._entry(automation_id)
        if data["versions"]:
            return copy.deepcopy(data["versions"][-1].ir_snapshot)
        try:
            return copy.deepcopy(_jsonable(self._read_ir(str(automation_id))))
        except VersionError:
            return None

    def autosnapshot(self, automation_id: str | None) -> Version | None:
        """部署钩子用的 fail-open 快照：失败只记审计，绝不阻断部署。"""
        if not automation_id:
            return None
        try:
            return self.snapshot(str(automation_id))
        except Exception as exc:                  # noqa: BLE001 —— fail-open
            _audit(self.audit, at=_now(self.clock), kind="version_autosnapshot_skipped",
                   automation_id=str(automation_id), reason=f"{type(exc).__name__}: {exc}")
            return None

    def wrap_deployer(
        self, deploy: Callable[[Any], Any], *, when: str = "before"
    ) -> Callable[[Any], Any]:
        """把自动快照挂进部署调用（每次部署前自动调用 snapshot，无需手动触发）。

        直接对接 ``ProposalManager(deployer=...)`` 注入点：
        ``ProposalManager(..., deployer=versions.wrap_deployer(real_deployer))``。
        ``when``：``before``（契约字面义，默认）/ ``after`` / ``both``。
        """
        if when not in ("before", "after", "both"):
            raise VersionError(f"未知 when：{when!r}（before | after | both）")
        manager = self

        def _deploy(plan: Any) -> Any:
            if when in ("before", "both"):
                manager.autosnapshot(_plan_automation_id(plan))
            result = deploy(plan)
            if when in ("after", "both"):
                aid = result if isinstance(result, str) else _plan_automation_id(plan)
                manager.autosnapshot(aid)
            return result

        return _deploy

    # ---- 存储 ------------------------------------------------------------ #

    def _read_ir(self, automation_id: str) -> Any:
        aid = str(automation_id)
        provider = self.ir_provider
        if provider is None:
            raise VersionError("未注入 ir_provider，无法取 IR（契约要求通过注入接口拿数据）")
        if isinstance(provider, Mapping):
            if provider.get(aid) is not None:
                return provider[aid]
            if len(provider) == 1 and next(iter(provider.values())) is not None:
                return next(iter(provider.values()))
            raise VersionError(f"ir_provider 未提供 automation {aid} 的 IR")
        ir = None
        try:
            ir = provider(aid)
        except TypeError:
            ir = None
        if ir is None:
            try:
                ir = provider()
            except TypeError:
                ir = None
            if ir is not None:
                auto = _automation_of(ir, aid)
                if auto is not None:
                    return auto
        if ir is None:
            raise VersionError(f"ir_provider 未返回 automation {aid} 的 IR")
        return ir

    def _file_for(self, automation_id: str) -> Path:
        name = _SAFE_NAME.sub("_", str(automation_id)) or "_"
        if set(name) <= {"."}:
            name = "_" + name                        # 防路径穿越 / 保留名
        return self.versions_dir / f"{name}.json"

    def _entry(self, automation_id: str) -> dict[str, Any]:
        aid = str(automation_id)
        if aid in self._cache:
            return self._cache[aid]
        path = self._file_for(aid)
        data: dict[str, Any] = {"automation_id": aid, "versions": []}
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            try:
                loaded = json.loads(raw)
            except Exception as exc:                 # noqa: BLE001 —— 明确报错优于吞历史
                raise VersionError(f"版本文件损坏：{path}（{type(exc).__name__}: {exc}）") from exc
            if isinstance(loaded, Mapping):
                declared = str(loaded.get("automation_id") or aid)
                if declared != aid:
                    raise VersionError(
                        f"版本文件 {path.name} 声明 automation_id={declared!r}，与请求 {aid!r} 不一致"
                    )
                rows = loaded.get("versions") or []
            else:                                    # 兼容裸列表旧格式
                rows = loaded or []
            data["versions"] = [Version.from_json(row) for row in rows if isinstance(row, Mapping)]
        self._cache[aid] = data
        return data

    def _save(self, automation_id: str) -> None:
        aid = str(automation_id)
        data = self._entry(aid)
        path = self._file_for(aid)
        payload = {
            "schema": SCHEMA_VERSION,
            "automation_id": data["automation_id"],
            "versions": [v.to_json() for v in data["versions"]],
        }
        try:
            self.versions_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.parent / (path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)                    # 原子替换，崩溃不留半截文件
        except Exception as exc:                     # noqa: BLE001
            raise VersionError(f"版本持久化失败：{path}（{type(exc).__name__}: {exc}）") from exc

    def _known_automations(self) -> list[str]:
        ids = set(self._cache)
        if self.versions_dir.is_dir():
            for path in sorted(self.versions_dir.glob("*.json")):
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    ids.add(path.stem)
                    continue
                if isinstance(loaded, Mapping) and loaded.get("automation_id"):
                    ids.add(str(loaded["automation_id"]))
                else:
                    ids.add(path.stem)
        return sorted(ids)


#: 命名兼容（契约只给了函数签名、没给类名）
VersionStore = VersionManager
VersionService = VersionManager
Versions = VersionManager


def _plan_automation_id(plan: Any) -> str | None:
    """从 DeployPlan（duck typing）推断目标 automation_id。"""
    ir = getattr(plan, "ir", None)
    autos = _iter_automations(ir) if ir is not None else {}
    if autos:
        return next(iter(autos))
    proposal = getattr(plan, "proposal", None)
    aid = getattr(proposal, "automation_id", None)
    if aid:
        return str(aid)
    pid = getattr(proposal, "proposal_id", None)
    if pid:                                          # 与 af_proposal.build_ir 的 id 规则一致
        return f"auto_{str(pid).replace('-', '_')}"
    return None


# --------------------------------------------------------------------------- #
# 模块级便捷入口（绑定默认 store；也可显式传 store=）
# --------------------------------------------------------------------------- #

_default_store: VersionManager | None = None


def set_default_store(store: VersionManager | None) -> VersionManager | None:
    """设置 / 清空模块级默认版本仓库。"""
    global _default_store
    _default_store = store
    return store


def get_default_store() -> VersionManager | None:
    return _default_store


def _need(store: Any = None) -> VersionManager:
    target = store or _default_store
    if target is None:
        raise VersionError("未配置版本仓库：请传 store=... 或先 set_default_store(...)")
    return target


def snapshot(automation_id: str, ir: Any = None, *, store: Any = None) -> Version:
    """契约入口：``snapshot(automation_id) -> Version``。"""
    return _need(store).snapshot(automation_id, ir)


def rollback(automation_id: str, version_id: str, *, store: Any = None) -> bool:
    """契约入口：``rollback(automation_id, version_id) -> bool``。"""
    return _need(store).rollback(automation_id, version_id)


def tag(version_id: str, label: str | None, *, store: Any = None) -> Version:
    """契约入口：``tag(version_id, label)``。"""
    return _need(store).tag(version_id, label)


def history(automation_id: str, *, store: Any = None) -> list[Version]:
    """契约入口：``history(automation_id) -> list[Version]``。"""
    return _need(store).history(automation_id)