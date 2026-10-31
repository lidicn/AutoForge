"""AF-706（无效授权码在两个写入口裁决不同形）回归钉，2026-10-31。

出处：QA 拨测——同一枚"形状合法（6/8 位）但不存在"的码，`af_save` 不拒绝、
转 pending + warning；`af_enable_by_tag` 直接拒。同一类输入两种裁决。

统一口径（依据交接单 §2/§5.3 四因合一红线与 AF-402：「无效码一律
『授权码无效（不存在/已消耗/已过期/已锁定之一）』」，⛔ 不外传具体哪一因）：
`_t_save` 无效码路径改为 raise `svc.ServiceError`——dispatch 的服务层异常
映射（403 ⇒ ADM_ERR_AUTH_REQUIRED）与 `af_enable_by_tag` 的 `_auth_code_check`
同一出口、同一文案。若 DCD 对"无效码应拒还是应转 pending"另有裁定，以 DCD
为准、回退本批（提交说明里已写明）。

注：`consume` 阶段失效回落 pending 那一支（R17-01，并发竞争：validate 通过
后码被别的进程抢走）语义不同，不在本卡范围、保持不动。

变异自证（把拒绝改回转 pending ⇒ 判据①红）由实现者手工执行并附命令输出。
"""

from __future__ import annotations

from pathlib import Path

from autoforge.af_auth import AuthCodeStore
from autoforge.af_mcp import _t_save
from autoforge.af_service import ServiceError
from autoforge.af_store import GraphStore

#: `_auth_code_check` 的四因合一文案前段（判据①「逐字同形」的对照物）。
FOUR_IN_ONE = "授权码无效（不存在/已消耗/已过期/已锁定之一）"

_CURRENT = {"subject": "af706-tester", "scopes": ["write"]}


def _ir() -> dict:
    return {
        "ir_version": "0.2.1", "id": "af706", "name": "af706", "version": 1, "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d", "kind": "do", "adapter": "ha", "action": "light.turn_on",
             "params": {"entity_id": "light.x"}},
        ],
        "edges": [{"from": "o", "to": "d", "kind": "then"}],
    }


def _setup(tmp_path: Path) -> tuple[GraphStore, AuthCodeStore]:
    store = GraphStore(str(tmp_path / "store"))
    acs = AuthCodeStore(Path(store.root) / ".auth" / "auth_codes.json")
    return store, acs


def _pending_count(store: GraphStore) -> int:
    from autoforge.af_pending import PendingStore
    return len(PendingStore(store.root).list())


def _args(ir: dict, auth_code: str) -> dict:
    return {"ir": {"automations": [ir]}, "name": "af706_demo", "auth_code": auth_code}


# ── 判据①② 无效码：拒绝 + 不落 pending ─────────────────────────────


def test_invalid_code_rejected_with_four_in_one(tmp_path):
    """无效码 ⇒ ServiceError，文案与 af_enable_by_tag/_auth_code_check 逐字同形。"""
    store, acs = _setup(tmp_path)
    before = _pending_count(store)
    try:
        _t_save(store, _args(_ir(), "123456"), current=dict(_CURRENT))
        raised = False
    except ServiceError as exc:
        raised = True
        assert str(exc).startswith(FOUR_IN_ONE), str(exc)
    assert raised, "无效码没有被拒绝（仍走 pending + warning 的旧裁决）"
    assert _pending_count(store) == before, "无效码落了 pending（判据②）"


# ── 判据③ 正确码：消费即归档（路径 A 不回归） ────────────────────────


def test_valid_code_consumes_and_archives(tmp_path):
    """正确码 ⇒ consume 一次、直归档（deployed_via=auth_code）。"""
    store, acs = _setup(tmp_path)
    code = acs.create("short", ttl_minutes=30).code
    res = _t_save(store, _args(_ir(), code), current=dict(_CURRENT))
    assert res.get("ok") is True, res
    assert res.get("deployed_via") == "auth_code", res
    # 短期码一次性：再用同一枚码必须落到"无效码拒绝"（判据①的同族延伸）
    try:
        _t_save(store, _args(_ir(), code), current=dict(_CURRENT))
        again = False
    except ServiceError as exc:
        again = True
        assert str(exc).startswith(FOUR_IN_ONE), str(exc)
    assert again, "已消费的码没有落到四因合一拒绝"


# ── 判据④ 文案不含具体原因词 ────────────────────────────────────────


def test_four_in_one_copy_hides_specific_reason(tmp_path):
    """四因合一文案 ⛔ 出现具体原因词（防枚举面的硬口径）。"""
    store, acs = _setup(tmp_path)
    try:
        _t_save(store, _args(_ir(), "888888"), current=dict(_CURRENT))
    except ServiceError as exc:
        text = str(exc)
        for word in ("已消耗", "已过期", "已锁定", "不存在，"):
            assert word not in text.replace(FOUR_IN_ONE, ""), f"文案泄因：{text}"
        return
    raise AssertionError("无效码未拒绝")


# ── 无码：原路径 B 不回归（不传 auth_code 照旧入队） ──────────────────


def test_no_code_still_lands_pending(tmp_path):
    """不带 auth_code ⇒ 转待人工审批（本卡只改"带无效码"那一档）。"""
    store, acs = _setup(tmp_path)
    before = _pending_count(store)
    res = _t_save(store, {"ir": {"automations": [_ir()]}, "name": "af706_no_code"},
                  current=dict(_CURRENT))
    assert res.get("ok") is True and res.get("persisted") is False, res
    assert _pending_count(store) == before + 1
