# src/autoforge/af_ownership.py · 归属判定单一真源（af-ownership-audit-model-v3-v3）
"""归属判定与可见性范围的唯一出口（C-3 卡，设计稿正式稿 §2）。

⛔ 读面/写面不得再内联第二份归属判断：verdict、范围过滤、拒答文案只从这里出。
owner 面名单仍是 `af_api._OWNER_SUBJECTS`（既有安全契约，本模块只读不改）；
归属读取仍是 `af_service._existing_owner` 语义，经 `owner_of` 回调注入，⛔ 不复制其逻辑。

三档身份（设计稿 §1/§5）：
- NONE  无身份（旧调用/本地 CLI/内部调用）→ 豁免档（⛔ 裁定 §四 1：不整体删除）；
- PRESENT 持令牌且有 subject → 按 face/归属判定（已识别主体不因 flag 缺失降级）；
- BROKEN 持令牌却缺 subject → 读写皆拒 403，⛔ 不得降级为 NONE。

判定表（读 = 列表可见性/单名读；写 = 标签、启停、删除、待批）：
  BROKEN           → 读写皆 403（残缺身份，文案单源 broken_identity_error）
  NONE             → 放行（全量豁免）
  PRESENT owner 面 → 放行
  PRESENT agent =caller → 放行
  PRESENT agent 归属="" → 读 404 同形 / 写 403 fail-closed
  PRESENT agent 他人    → 读 404 同形 / 写 403 不点名（AF-509）
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Iterable


class TokenState(Enum):
    NONE = "none"        # 无身份：旧调用/本地 CLI/内部调用 → 豁免档（⛔ 不整体删除）
    PRESENT = "present"  # 持令牌且有 subject
    BROKEN = "broken"    # 持令牌却缺 subject → 读写皆拒，⛔ 不得降级为 NONE


class DenyKind(Enum):
    OK = "ok"
    NOT_FOUND = "not_found"              # 读拒：与「归档不存在」逐字同形
    FORBIDDEN = "forbidden"              # 写拒：不点归属者名（AF-509）
    BROKEN_IDENTITY = "broken_identity"  # 残缺身份：403


@dataclass(frozen=True)
class Subject:
    caller: str
    token_state: TokenState
    #: 服务层显式传入的 owner 面旗（MCP/HTTP 调用方已按 `_OWNER_SUBJECTS` 核过才传 True；
    #: 服务直调/CLI 的旧入参形状由此保持兼容）。名单真源仍只有 `af_api._OWNER_SUBJECTS`。
    owner_face: bool = False

    @property
    def is_owner_face(self) -> bool:
        from .af_api import _OWNER_SUBJECTS
        return self.owner_face or self.caller in _OWNER_SUBJECTS

    @classmethod
    def from_current(cls, current: dict[str, Any] | None,
                     token_identity: bool | None = None) -> "Subject":
        """身份档判定单源：`current` 与 `token_identity` 只在此解读一次。"""
        caller = str((current or {}).get("subject") or "")
        if token_identity is True and not caller.strip():
            state = TokenState.BROKEN
        elif caller.strip():
            state = TokenState.PRESENT  # 已识别主体不因 flag 缺失而降级为豁免
        else:
            state = TokenState.NONE
        return cls(caller=caller, token_state=state)

    @classmethod
    def from_caller(cls, caller: str, *, owner_face: bool = False,
                    token_identity: bool | None = None) -> "Subject":
        """服务层标量入参 → Subject：身份映射仍走 `from_current` 单源，⛔ 第二份解读。"""
        return replace(cls.from_current({"subject": str(caller or "")}, token_identity),
                       owner_face=owner_face)


def visible_error(name: str) -> str:
    """读拒文案单源：与不存在逐字同形，⛔ 不回「无权查看」。"""
    return f"没找到归档 {str(name)!r}（或它不属于你）"


def mutator_error(name: str, action: str) -> str:
    """写拒文案单源：⛔ 不点归属者名，⛔ 不回显扫描得来的他人归档名。"""
    return f"归档 {str(name)!r} 不属于当前主体（或不存在），无权{action}"


def broken_identity_error() -> str:
    return "拒绝：令牌缺少 subject（残缺身份）——豁免只对没有令牌身份的本地调用有效"


def decide(owner_of: Callable[[str], str], name: str, sub: Subject, *,
           write: bool) -> DenyKind:
    """唯一判定：身份档 → 归属 → verdict。读写同判，⛔ 各面自造第二份规则。"""
    if sub.token_state is TokenState.BROKEN:
        return DenyKind.BROKEN_IDENTITY
    if sub.token_state is TokenState.NONE or sub.is_owner_face:
        return DenyKind.OK
    if owner_of(name) == sub.caller:
        return DenyKind.OK
    return DenyKind.FORBIDDEN if write else DenyKind.NOT_FOUND


def scope_names(owner_of: Callable[[str], str], names: Iterable[str],
                sub: Subject) -> tuple[list[str], int]:
    """列表面范围判定单源：(可见名, hidden_count)，与 decide 同判。"""
    names = list(names)
    if sub.token_state is TokenState.BROKEN:
        return [], len(names)
    if sub.token_state is TokenState.NONE or sub.is_owner_face:
        return names, 0
    visible = [n for n in names if owner_of(n) == sub.caller]
    return visible, len(names) - len(visible)
