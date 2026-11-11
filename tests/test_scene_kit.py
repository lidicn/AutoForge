# -*- coding: utf-8 -*-
"""判据 · AF v1.29 批 3 场景对象（`af_scene_kit.SceneDef` / `SceneStep` / `SceneRunner`）。

设计稿：`docs/architecture/v129_scene_object_design.md`（五节＋测试面）。
钉的是 DCD Q3 甲那条**契约不变量**：场景执行**每步独立过 grant**——
`hard_denied` ⇒ 完成当前步、取消未执行步、⛔ 不回滚已成步；`needs_confirm` ⇒ 单次放行票；
`uses` **每 run 计一次**；档位**不落 IR**（执行期现算）。

⛔ 禁改文件：`af_scene.py` / `af_task_order.py`——本文件只读它们的**字节哈希**做契约冻结
（改一个字节就红），⛔ 不导入、⛔ 不猴子补。
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from autoforge.af_grant import (
    GRANT_BANNED_DOMAINS,
    GRANT_CONFIRM_REQUIRED,
    GRANT_OK,
    GrantCheck,
    GrantStore,
)
from autoforge.af_scene_kit import (
    SCENE_DEF_KIND,
    SKIP_AFTER_HARD_DENY,
    SceneAskSession,
    SceneDef,
    SceneDefError,
    SceneRegistry,
    SceneRunner,
    SceneStep,
)

REPO = pathlib.Path(__file__).resolve().parents[1]

#: 冻结面（设计稿 §6 契约：SceneManager / TaskOrderStore 的 range/sig 哈希与快照一致）。
#: 只算**字节哈希**：本单不许改这两个文件一个字节。
FROZEN = {
    "src/autoforge/af_scene.py": "753a819938e45aad708a53ea",
    "src/autoforge/af_task_order.py": "43dbe2e9c680ec42bd0a3ecb",
}


# ── 夹具 ──────────────────────────────────────────────────────────────
@pytest.fixture()
def gs(tmp_path):
    return GrantStore(tmp_path / ".auth" / "grants.json")


def _grant(gs, **kw):
    params = {"agent": "tv-pilot", "issued_by": "owner", "rooms": ["客厅", "卧室"],
              "action_risk": "L1"}
    params.update(kw)
    return gs.create(**params)


def _ir(steps, **kw):
    payload = {
        "kind": SCENE_DEF_KIND,
        "scene_id": "scene_home_001",
        "name": "回家模式",
        "aliases": ["到家了"],
        "version": 1,
        "owner": "owner",
        "condition": None,
        "created_via": "ask",
        "steps": steps,
    }
    payload.update(kw)
    return payload


def _step(step_id, action="light.turn_on", target="light.living_room", **kw):
    data = {"step_id": step_id, "adapter": "hass", "action": action, "target": target,
            "params": {}, "on_error": "abort", "delay_ms_after": 0}
    data.update(kw)
    return data


# ── 1. 构造期 fail-closed ────────────────────────────────────────────
def test_scenedef_rejects_duplicate_scene_id():
    """scene_id 唯一：同 id 二次构造即拒（对齐 `af_scene.py:138` 的查重纪律）。"""
    registry = SceneRegistry()
    scene = SceneDef.from_ir(_ir([_step("s0")]))
    registry.add(scene)
    with pytest.raises(SceneDefError) as ei:
        registry.add(SceneDef.from_ir(_ir([_step("s0")])))
    assert "scene_id 重复" in str(ei.value)
    assert len(registry) == 1, "撞名不该把旧定义挤掉"


def test_scenedef_rejects_empty_steps():
    with pytest.raises(SceneDefError) as ei:
        SceneDef.from_ir(_ir([]))
    assert "steps" in str(ei.value) and "空" in str(ei.value)


def test_scenedef_rejects_unknown_key():
    """未声明键即拒（静默忽略＝配置写错了也不知道）。"""
    payload = _ir([_step("s0")])
    payload["risk_level"] = "L2"  # ⛔ 档位不落 IR（D5）——写了就是未声明键
    with pytest.raises(SceneDefError) as ei:
        SceneDef.from_ir(payload)
    assert "未声明的键" in str(ei.value) and "risk_level" in str(ei.value)


def test_scenedef_rejects_bad_on_error():
    with pytest.raises(SceneDefError) as ei:
        SceneDef.from_ir(_ir([_step("s0", on_error="xyz")]))
    assert "on_error" in str(ei.value)


def test_scenedef_rejects_l3_and_banned_domain_at_config():
    """L3 与禁入域在**配置期**就拒（⛔ 不写进 IR 再等执行期拒）。"""
    with pytest.raises(SceneDefError) as ei_l3:
        SceneDef.from_ir(_ir([_step("s0", adapter="shell_command", action="run", target="host")]))
    assert "L3" in str(ei_l3.value)

    with pytest.raises(SceneDefError) as ei_ban:
        SceneDef.from_ir(_ir([_step("s0", action="lock.lock", target="lock.front_door")]))
    assert "禁入域" in str(ei_ban.value)
    assert "lock" in GRANT_BANNED_DOMAINS, "四元集与本判据同源"


# ── 2. 顺序执行器 ────────────────────────────────────────────────────
def test_runner_preserves_step_order_and_delay(gs):
    """3 步按 step_no 下发，`delay_ms_after` 生效（注入假 sleep，⛔ 真睡）。"""
    grant = _grant(gs)
    seen: list[str] = []
    slept: list[float] = []
    scene = SceneDef.from_ir(
        _ir([_step("s0", delay_ms_after=500), _step("s1"), _step("s2", delay_ms_after=250)])
    )
    runner = SceneRunner(
        gs,
        executor=lambda step: seen.append(step.step_id) or True,
        room_of=lambda entity: "客厅",
        sleep=slept.append,
        now=lambda: 1762867323.0,
    )
    receipt = runner.run(scene, grant=grant, agent="tv-pilot")

    assert seen == ["s0", "s1", "s2"], f"执行顺序必须按 step_no：{seen}"
    assert slept == [0.5, 0.25], f"delay 必须按步节流（秒）：{slept}"
    assert [e["step_no"] for e in receipt["steps"]] == [0, 1, 2]
    assert receipt["code"] == GRANT_OK
    assert all(e["done"] for e in receipt["steps"])


# ── 3. 每步独立过 grant（DCD Q3 甲）──────────────────────────────────
def test_hard_deny_finishes_current_cancels_rest(gs):
    """第 2 步硬拒（设备被排除）⇒ 第 2 步不下发、第 3 步记取消、已成第 1 步不回滚。"""
    grant = _grant(gs, device_scope={"include": [], "exclude": ["light.kitchen"]})
    dispatched: list[str] = []
    scene = SceneDef.from_ir(
        _ir([
            _step("s0", target="light.living_room"),
            _step("s1", target="light.kitchen"),   # ⛔ 硬拒：GRANT_DEVICE_EXCLUDED
            _step("s2", target="light.desk"),
        ])
    )
    runner = SceneRunner(gs, executor=lambda step: dispatched.append(step.step_id) or True,
                         room_of=lambda entity: "客厅", now=lambda: 1762867323.0)
    receipt = runner.run(scene, grant=grant, agent="tv-pilot")

    assert dispatched == ["s0"], f"硬拒后⛔ 不得再下发：{dispatched}"
    steps = receipt["steps"]
    assert steps[0]["done"] is True and steps[0]["code"] == GRANT_OK, "已成步不回滚（D3）"
    assert steps[1]["done"] is False
    assert steps[1]["code"] == "GRANT_DEVICE_EXCLUDED"
    assert steps[1]["skipped_reason"] == SKIP_AFTER_HARD_DENY
    assert steps[2]["done"] is False
    assert steps[2]["skipped_reason"] == SKIP_AFTER_HARD_DENY, "第 3 步必须记取消，⛔ 不是 ok"
    assert receipt["code"] == "GRANT_DEVICE_EXCLUDED", "整场单码＝硬拒码（⛔ 不得报 ok）"


def test_needs_confirm_issues_ticket_and_consumes_once(gs):
    """档位超授（L2 请求 > L1 授予）⇒ 出票 → 确认放行一次 → 同票二次失效（验票即毁）。"""
    grant = _grant(gs, action_risk="L1")
    scene = SceneDef.from_ir(
        _ir([_step("s0", action="climate.set_temperature", target="climate.bedroom",
                   params={"temperature": 26})])
    )
    seen_codes: list[str] = []

    def confirm(check):
        seen_codes.append(check.code)
        return True

    runner = SceneRunner(gs, executor=lambda step: True, room_of=lambda entity: "客厅",
                         confirm=confirm, now=lambda: 1762867323.0)
    receipt = runner.run(scene, grant=grant, agent="tv-pilot")

    assert seen_codes == [GRANT_CONFIRM_REQUIRED], f"该走确认的码不对：{seen_codes}"
    entry = receipt["steps"][0]
    assert entry["done"] is True, "确认放行后本步应下发"
    assert entry["ticket"], "回执必须带票号（可审计到哪张票放行的）"
    # 验票即毁：同一张票第二次消费必须失败（单次有效）
    assert gs.consume_confirm(entry["ticket"], agent="tv-pilot") is False


def test_confirm_denied_marks_step_and_cancels_rest(gs):
    """人工拒绝 ⇒ 当前步记 confirm_denied、后续步取消（⛔ 不许"跳过去继续跑"）。"""
    grant = _grant(gs, action_risk="L1")
    scene = SceneDef.from_ir(
        _ir([
            _step("s0", action="climate.set_temperature", target="climate.bedroom"),
            _step("s1", target="light.desk"),
        ])
    )
    dispatched: list[str] = []
    runner = SceneRunner(gs, executor=lambda step: dispatched.append(step.step_id) or True,
                         room_of=lambda entity: "客厅", confirm=lambda check: False,
                         now=lambda: 1762867323.0)
    receipt = runner.run(scene, grant=grant, agent="tv-pilot")

    assert dispatched == [], f"拒绝确认后⛔ 不得下发：{dispatched}"
    assert receipt["steps"][0]["skipped_reason"] == "confirm_denied"
    assert receipt["steps"][1]["skipped_reason"] == "confirm_denied"


# ── 4. 回执形状 / uses 计数 ──────────────────────────────────────────
def test_receipt_carries_grant_id_and_code(gs):
    """每步回执含 `grant_id` 与单码 `code`，且键形复用 `GrantCheck.to_dict`（⛔ 不另造判定字段）。"""
    grant = _grant(gs)
    scene = SceneDef.from_ir(_ir([_step("s0")]))
    receipt = SceneRunner(gs, executor=lambda step: True, room_of=lambda entity: "客厅",
                         now=lambda: 1762867323.0).run(scene, grant=grant, agent="tv-pilot")
    entry = receipt["steps"][0]
    for key in ("step_no", "step_id", "target", "grant_id", "code", "done"):
        assert key in entry, f"回执缺 {key}"
    assert entry["grant_id"] == grant["grant_id"]
    # 单码视图与 GrantCheck 同源：同一份判定不得出现两个码
    check = gs.check(grant, agent="tv-pilot", room_of=lambda entity: "客厅",
                     actions=(("hass", "light.turn_on", {}),), targets=("light.living_room",))
    assert isinstance(check, GrantCheck)
    assert entry["code"] == check.code
    assert receipt["run_id"].startswith("r-")


def test_uses_bumped_once_per_run(gs):
    """5 步场景只 `uses +1`（⛔ 逐步计数＝逐步整盘重写账本，D4）。"""
    grant = _grant(gs)
    scene = SceneDef.from_ir(_ir([_step(f"s{i}") for i in range(5)]))
    SceneRunner(gs, executor=lambda step: True, room_of=lambda entity: "客厅",
                now=lambda: 1762867323.0).run(scene, grant=grant, agent="tv-pilot")
    assert gs.get(grant["grant_id"])["uses"] == 1


# ── 5. 配置期 ask：取消不留半成品 ────────────────────────────────────
def test_ask_flow_cancel_leaves_no_partial_def():
    """第 3 轮取消 ⇒ 无 SceneDef 落盘（commit 一律拒）。"""
    session = SceneAskSession()
    session.ask_name("回家模式", ["到家了"])
    session.add_step(step_id="s0", adapter="hass", action="light.turn_on", target="light.living_room")
    session.add_step(step_id="s1", adapter="hass", action="climate.set_temperature",
                     target="climate.bedroom", params={"temperature": 26})
    session.cancel("用户第 3 轮取消")
    assert session.cancelled
    with pytest.raises(SceneDefError) as ei:
        session.commit(scene_id="scene_home_001")
    assert "已取消" in str(ei.value)


def test_ask_flow_commit_produces_complete_def():
    """六轮答完 ⇒ 一次 commit 出完整 IR（created_via=ask，condition 缺省 null）。"""
    session = SceneAskSession()
    session.ask_name("回家模式", ["到家了"])
    session.add_step(step_id="s0", adapter="hass", action="light.turn_on", target="light.living_room")
    session.ask_condition(None)
    scene = session.commit(scene_id="scene_home_001", owner="owner")
    ir = scene.to_ir()
    assert ir["kind"] == "scene_def" and ir["created_via"] == "ask"
    assert ir["condition"] is None, "condition 缺省必须是 null（⛔ 不发明隐式条件，D6）"
    assert [s["step_id"] for s in ir["steps"]] == ["s0"]
    assert "risk" not in ir and all("risk" not in s for s in ir["steps"]), "⛔ 档位不落 IR（D5）"


# ── 容量面（有界缓存门禁要求：增长容器必须有硬上限，⛔ 不靠"应该不会太多"）──
def test_scene_registry_rejects_beyond_cap():
    """场景清单硬上限：超限拒收，⛔ 不淘汰既有定义（淘汰＝人配好的口令消失）。"""
    from autoforge.af_scene_kit import SCENE_MAX_DEFS

    registry = SceneRegistry(max_defs=1)
    registry.add(SceneDef.from_ir(_ir([_step("s0")])))
    with pytest.raises(SceneDefError) as ei:
        registry.add(SceneDef.from_ir(_ir([_step("s0")], scene_id="scene_home_002")))
    assert "硬上限" in str(ei.value)
    assert len(registry) == 1, "超限被拒后旧定义必须还在"
    assert SCENE_MAX_DEFS > 0


def test_ask_session_rejects_beyond_step_cap():
    """单次 ask 会话的步骤硬上限：超限即拒（⛔ 场景不该无限长）。"""
    from autoforge.af_scene_kit import ASK_MAX_ALIASES, ASK_MAX_STEPS

    session = SceneAskSession()
    session.ask_name("回家模式")
    for i in range(ASK_MAX_STEPS):
        session.add_step(step_id=f"s{i}", adapter="hass", action="light.turn_on",
                         target="light.living_room")
    with pytest.raises(SceneDefError) as ei:
        session.add_step(step_id="overflow", adapter="hass", action="light.turn_on",
                         target="light.living_room")
    assert "硬上限" in str(ei.value)

    with pytest.raises(SceneDefError) as ei_alias:
        session.ask_name("回家模式", [f"别名{i}" for i in range(ASK_MAX_ALIASES + 1)])
    assert "aliases" in str(ei_alias.value)


# ── 契约面 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("rel,digest", sorted(FROZEN.items()))
def test_frozen_files_bytes_unchanged(rel, digest):
    """禁改文件**零字节**（设计稿 §6 契约冻结；改一个字节就红）。"""
    path = REPO / rel
    assert path.is_file(), f"{rel} 不存在——契约面被删了"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()[:24]
    assert actual == digest, f"{rel} 被改动了（{actual} ≠ {digest}）：本单⛔ 禁改该文件"


def test_scene_namespace_isolated():
    """`SceneDef.kind == "scene_def"`，且**绝不**注册进 `SceneManager` 的 scenes 字典。"""
    import autoforge.af_scene as af_scene

    assert SceneDef.kind == "scene_def"
    assert af_scene.Scene is not SceneDef
    assert not hasattr(af_scene, "SceneDef"), "⛔ 命名空间不许漏进 af_scene"
    class _Stub:
        def set_automation_enabled(self, automation_id, enabled):
            return True

    manager = af_scene.SceneManager([], executor=_Stub(), persist_dir=str(REPO / "tmp"))
    assert manager.list_scenes() == [], "新场景对象⛔ 不得自动出现在 SceneManager 里"


def test_step_actions_shape_matches_collect_actions():
    """步骤 → `GrantStore.check` 三元组，与 `af_grant.collect_actions` 同形（3 元组 (adapter, action, params)）。"""
    step = SceneStep.from_ir(_step("s0", params={"brightness_pct": 60}), step_no=0)
    actions = step.actions
    assert len(actions) == 1
    adapter, action, params = actions[0]
    assert (adapter, action) == ("hass", "light.turn_on")
    assert params == {"brightness_pct": 60}
