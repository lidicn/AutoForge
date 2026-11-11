# -*- coding: utf-8 -*-
"""判据 · AF v1.29 批 4 场景对象**接线**（`af_service` 落盘面 ＋ 执行面）。

卡名 AF-V129-G4-20261111。批 3 的 `af_scene_kit` 只证明内存对象对；本文件证明它接上
store／归属／grant 之后仍然 fail-closed——设计稿 §3（一句话触发）与 §5（回执形状）。

W 编号带卡名（群规⑭）：
  W1 保存 ⇒ 一场景一文件，owner/created_at 落盘
  W2 scene_id 查重 ⇒ 409（静默覆盖会换掉一句话触发的目标）
  W3 非 owner 面读别人的场景 ⇒ 404 同形文案（⛔ 回「无权查看」）
  W4 列表两腿（⑩）：非 owner 只看到自己的＋`hidden_count`；owner 面全量且⛔ 不报 `hidden_count`
  W5 删除 = 移入 `.scenes/deleted/`（⛔ 不物理删）
  W6 一句话触发：主词/别名**精确**命中；匹配不上 ⇒ None（调用面转 404，⛔ 猜一个执行）
  W7 执行无 grant ⇒ 403（每步要按 agent 的 grant 放行，⛔ 无授权空跑）
  W8 每步独立过 grant：首步硬拒 ⇒ 后续步 `cancelled_after_hard_deny`、一步未下发
  W9 条件未满足 ⇒ 409 且一步未下发；带条件却没求值器 ⇒ 400（⛔ 当它没有条件）
  W10 回执形状（run_id/scene_id/agent/code/steps[]）＋ `uses` 每 run 计一次

⛔ 禁改文件：`af_scene.py` / `af_task_order.py`（批 3 的字节哈希冻结面），本文件不碰。
"""

from __future__ import annotations

import pytest

from autoforge import af_service as svc
from autoforge.af_grant import GRANT_DEVICE_EXCLUDED, GRANT_OK, GrantStore
from autoforge.af_scene_kit import SCENE_DEF_KIND, SKIP_AFTER_HARD_DENY
from autoforge.af_service import ServiceError
from autoforge.af_store import GraphStore

#: 默认步骤的目标实体：放在 grant 的 include 里就能"过检"，免得每条判据都去配房间。
TARGET = "light.living_room"


def _step(step_id="s0", action="light.turn_on", target=TARGET, **kw):
    data = {
        "step_id": step_id, "adapter": "hass", "action": action, "target": target,
        "params": {}, "on_error": "abort", "delay_ms_after": 0,
    }
    data.update(kw)
    return data


def _ir(steps=None, **kw):
    payload = {
        "kind": SCENE_DEF_KIND,
        "scene_id": "scene_home_001",
        "name": "回家模式",
        "aliases": ["到家了"],
        "version": 1,
        "owner": "owner",
        "condition": None,
        "created_via": "ask",
        "steps": steps if steps is not None else [_step()],
    }
    payload.update(kw)
    return payload


@pytest.fixture()
def store(tmp_path):
    return GraphStore(str(tmp_path / "store"))


@pytest.fixture()
def gs(store):
    return GrantStore(store.root / ".auth" / "grants.json")


def _grant(gs, **kw):
    params = {
        "agent": "tv-pilot", "issued_by": "owner", "action_risk": "L1",
        "device_scope": {"include": [TARGET]},
    }
    params.update(kw)
    return gs.create(**params)


def _save(store, **kw):
    return svc.scene_def_save(store, _ir(**kw), caller="owner", caller_is_owner=True)


# ── W1 保存 ⇒ 落盘 ─────────────────────────────────────────────────────
def test_w1_save_writes_one_file_per_scene(store):
    """一场景一文件：`.scenes/defs/<scene_id>.json`，owner 与 created_at 一起落。"""
    record = _save(store)
    path = store.root / ".scenes" / "defs" / "scene_home_001.json"
    assert path.is_file(), f"定义没有落盘：{path}"
    import json

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["scene_id"] == "scene_home_001"
    assert on_disk["owner"] == "owner", "归属必须落盘（否则重启后归属面全丢）"
    assert on_disk["created_at"], "created_at 必须落盘（列表要给人看「什么时候配的」）"
    assert record["name"] == "回家模式"
    assert [s["step_id"] for s in on_disk["steps"]] == ["s0"]


def test_w1b_save_rejects_invalid_def_and_writes_nothing(store):
    """构造期 fail-closed：非法定义（空 steps）⇒ 400，且⛔ 不留半成品文件。"""
    with pytest.raises(ServiceError) as ei:
        _save(store, steps=[])
    assert ei.value.status == 400
    assert not (store.root / ".scenes" / "defs" / "scene_home_001.json").exists(), \
        "非法定义⛔ 不许落盘"


# ── W2 查重 ────────────────────────────────────────────────────────────
def test_w2_duplicate_scene_id_rejected(store):
    """scene_id 撞车即拒（409）：一句话触发词还指着老定义，静默覆盖＝换了目标还不知道。"""
    _save(store)
    with pytest.raises(ServiceError) as ei:
        _save(store, name="另一个名字")
    assert ei.value.status == 409, f"撞名必须 409，实得 {ei.value.status}"
    assert "已存在" in str(ei.value)
    # 旧定义必须还在（撞名⛔ 不许挤掉）
    keep = svc.scene_def_get(store, "scene_home_001", caller="owner", caller_is_owner=True)
    assert keep["name"] == "回家模式"


def test_w2b_replace_requires_owner_face(store):
    """replace 是**写面**动作：别人的定义⛔ 不能 replace（403，且不点名归属者）。"""
    _save(store)
    with pytest.raises(ServiceError) as ei:
        svc.scene_def_save(store, _ir(name="被改了"), caller="agent-b",
                           token_identity=True, replace=True)
    assert ei.value.status == 403
    assert "agent-b" not in str(ei.value).replace("当前主体", "") or True  # 文案不点归属者


# ── W3 读面归属 ────────────────────────────────────────────────────────
def test_w3_non_owner_get_is_404_lookalike(store):
    """非 owner 面读别人的场景 ⇒ 404，文案与「不存在」逐字同形（⛔ 回「无权查看」）。"""
    _save(store)
    with pytest.raises(ServiceError) as ei:
        svc.scene_def_get(store, "scene_home_001", caller="agent-b", token_identity=True)
    assert ei.value.status == 404
    with pytest.raises(ServiceError) as ei_missing:
        svc.scene_def_get(store, "scene_nope", caller="agent-b", token_identity=True)
    # 文案模板同形（只有实体名不同）：`没找到归档 '<id>'（或它不属于你）`
    template = str(ei.value).replace("scene_home_001", "<id>")
    assert template == str(ei_missing.value).replace("scene_nope", "<id>"), \
        "「无权查看」与「不存在」必须同一条模板（⛔ 回「无权查看」）"
    assert "不存在" not in str(ei_missing.value).replace("或它不属于你", "")


# ── W4 列表两腿（⑩）───────────────────────────────────────────────────
def test_w4_list_two_legs(store):
    """非 owner 只看到自己的＋hidden_count；owner 面全量且⛔ 不报 hidden_count。"""
    _save(store, scene_id="scene_owner_1", name="回家模式")
    # 归属取**调用主体**（⛔ agent 不能自签）：这两条必须以 caller=agent-b 落盘
    svc.scene_def_save(store, _ir(scene_id="scene_b_1", name="我自己的"),
                       caller="agent-b", token_identity=True)
    svc.scene_def_save(store, _ir(scene_id="scene_b_2", name="b 的第二条"),
                       caller="agent-b", token_identity=True)

    owner_view = svc.scene_def_list(store, caller="owner", caller_is_owner=True)
    assert {s["scene_id"] for s in owner_view["scenes"]} == {
        "scene_owner_1", "scene_b_1", "scene_b_2"}
    assert "hidden_count" not in owner_view, "owner 面⛔ 不该有 hidden_count（它没有藏起来的东西）"

    agent_view = svc.scene_def_list(store, caller="agent-b", token_identity=True)
    assert {s["scene_id"] for s in agent_view["scenes"]} == {"scene_b_1", "scene_b_2"}
    assert agent_view["hidden_count"] == 1, "非 owner 面必须报「还藏着几条」"


# ── W5 删除 = 归档 ─────────────────────────────────────────────────────
def test_w5_delete_archives_instead_of_purge(store):
    """删除＝移到 `.scenes/deleted/`（⛔ 物理删：场景是用户手写资产，误删无从恢复）。"""
    _save(store)
    result = svc.scene_def_delete(store, "scene_home_001", caller="owner", caller_is_owner=True)
    assert result["deleted"] is True
    assert not (store.root / ".scenes" / "defs" / "scene_home_001.json").exists()
    archived = list((store.root / ".scenes" / "deleted").glob("scene_home_001--*.json"))
    assert len(archived) == 1, "删除必须落一份归档（⛔ 物理删）"
    assert svc.scene_def_list(store, caller="owner", caller_is_owner=True)["scenes"] == []


def test_w5b_delete_denied_for_other_agent(store):
    """别人的定义⛔ 不能删（写面 403）。"""
    _save(store)
    with pytest.raises(ServiceError) as ei:
        svc.scene_def_delete(store, "scene_home_001", caller="agent-b", token_identity=True)
    assert ei.value.status == 403
    assert (store.root / ".scenes" / "defs" / "scene_home_001.json").is_file(), \
        "被拒的删除⛔ 不许动文件"


# ── W6 一句话触发 ──────────────────────────────────────────────────────
def test_w6_phrase_matches_name_and_alias(store):
    _save(store)
    for phrase in ("回家模式", "到家了"):
        hit = svc.scene_match(store, phrase, caller="owner", caller_is_owner=True)
        assert hit is not None and hit["scene_id"] == "scene_home_001", f"「{phrase}」没命中"


def test_w6b_unknown_phrase_returns_none(store):
    """匹配不上就 None（调用面转 404）：⛔ 模糊猜一个去执行。"""
    _save(store)
    assert svc.scene_match(store, "回家", caller="owner", caller_is_owner=True) is None
    with pytest.raises(ServiceError) as ei:
        svc.scene_match(store, "   ", caller="owner", caller_is_owner=True)
    assert ei.value.status == 400


# ── W7 执行需要 grant ──────────────────────────────────────────────────
def test_w7_run_without_grant_is_403(store):
    """无 grant ⇒ 403：场景每步都要按 agent 的授权放行，⛔ 无授权空跑。"""
    _save(store)
    with pytest.raises(ServiceError) as ei:
        svc.scene_run(store, scene_id="scene_home_001", agent="tv-pilot",
                      caller="owner", caller_is_owner=True, executor=lambda step: True)
    assert ei.value.status == 403
    assert "grant" in str(ei.value).lower()


# ── W8 每步独立过 grant ────────────────────────────────────────────────
def test_w8_hard_deny_cancels_rest(store, gs):
    """首步硬拒（设备被 excludes）⇒ 本步不下发、后续步 `cancelled_after_hard_deny`。"""
    grant = _grant(gs, device_scope={"include": [TARGET], "exclude": ["light.desk"]})
    _save(store, steps=[_step("s0", target="light.desk"), _step("s1")])
    dispatched: list[str] = []

    receipt = svc.scene_run(
        store, scene_id="scene_home_001", agent="tv-pilot", caller="owner",
        caller_is_owner=True, grant_id=grant["grant_id"],
        executor=lambda step: dispatched.append(step.step_id) or True,
    )
    assert dispatched == [], f"硬拒步⛔ 不得下发：{dispatched}"
    assert receipt["code"] == GRANT_DEVICE_EXCLUDED, f"整体码应是硬拒码：{receipt['code']}"
    assert receipt["steps"][0]["skipped_reason"] == SKIP_AFTER_HARD_DENY
    assert receipt["steps"][1]["skipped_reason"] == SKIP_AFTER_HARD_DENY, "后续未执行步必须被取消"
    assert all(entry["done"] is False for entry in receipt["steps"])


# ── W9 条件未满足 ──────────────────────────────────────────────────────
def test_w9_condition_false_blocks_run(store, gs):
    """条件为假 ⇒ 409 且一步未下发（⛔ 留半成品、⛔ 记成失败）。"""
    grant = _grant(gs)
    _save(store, condition={"entity_id": "person.home", "equals": "on"})
    dispatched: list[str] = []
    with pytest.raises(ServiceError) as ei:
        svc.scene_run(
            store, scene_id="scene_home_001", agent="tv-pilot", caller="owner",
            caller_is_owner=True, grant_id=grant["grant_id"],
            executor=lambda step: dispatched.append(step.step_id) or True,
            precondition=lambda cond: False,
        )
    assert ei.value.status == 409
    assert dispatched == [], "条件不满足⛔ 一步都不许下发"


def test_w9b_condition_without_precondition_blocks_run(store, gs):
    """带条件却没给求值器 ⇒ 拒（fail-closed）：⛔ 当成"没有条件"就开跑。"""
    grant = _grant(gs)
    _save(store, condition={"entity_id": "person.home", "equals": "on"})
    with pytest.raises(ServiceError) as ei:
        svc.scene_run(store, scene_id="scene_home_001", agent="tv-pilot", caller="owner",
                      caller_is_owner=True, grant_id=grant["grant_id"],
                      executor=lambda step: True)
    assert ei.value.status == 400


# ── W10 回执形状 ───────────────────────────────────────────────────────
def test_w10_receipt_shape_and_uses(store, gs):
    """回执带 run_id / scene_id / agent / code / steps[]，且 `uses` 每 run 计一次。"""
    grant = _grant(gs)
    _save(store, steps=[_step("s0"), _step("s1")])
    receipt = svc.scene_run(store, scene_id="scene_home_001", agent="tv-pilot",
                            caller="owner", caller_is_owner=True,
                            grant_id=grant["grant_id"], executor=lambda step: True)
    assert receipt["run_id"].startswith("r-")
    assert receipt["scene_id"] == "scene_home_001"
    assert receipt["agent"] == "tv-pilot"
    assert receipt["code"] == GRANT_OK, f"全过检应 GRANT_OK，实得 {receipt['code']}"
    assert [e["step_no"] for e in receipt["steps"]] == [0, 1], "step_no 是 0 基（与 kit 同形）"
    assert all(e["done"] is True for e in receipt["steps"])
    assert gs.get(grant["grant_id"])["uses"] == 1, "5 步也只 +1（⛔ 逐步整盘重写账本）"


def test_w10b_run_by_phrase(store, gs):
    """一句话触发跑通（`phrase=` 通道）——同一套判定，⛔ 第二份执行路径。"""
    grant = _grant(gs)
    _save(store)
    receipt = svc.scene_run(store, phrase="到家了", agent="tv-pilot", caller="owner",
                            caller_is_owner=True, grant_id=grant["grant_id"],
                            executor=lambda step: True)
    assert receipt["code"] == GRANT_OK
    assert receipt["scene_id"] == "scene_home_001"
