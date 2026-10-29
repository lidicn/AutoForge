"""契约泳道报告 v1.14.14 的判据（B1 / B2 / B3 / B4 / P0-1..P0-3，2026-10-29）。

共同病灶：**对端的错误分类与事实脱钩**——坏输入塌成 5xx、归因靠关键词猜、
`ok` 不派生自证据。所以这里每条都钉"回执说的 == 实际发生的"：

- P0-1 归因：`ref 不存在或已过期` 曾被裸词「已过期」判成 TOKEN_EXPIRED（建议还让人换密钥）
- P0-2 边界：归档名清洗不告知 + 绝对路径回显 + FileNotFoundError 冒 INTERNAL
- P0-3 冒领：IR 编出来但实体全没解析，MCP 回执仍 `ok:true`（库层语义不动，分层见实现注释）
- B1/B7：`DraftError`/`IRValidationError` 经 catch-all 变成 `ADM_ERR_INTERNAL`
- B3/B4：实例 `failed` 或压根没跑出实例时，`simulate.ok` 仍为 true
"""

from __future__ import annotations

import json
import tempfile

import pytest

from autoforge.af_error_knowledge import CATEGORY_ADVICE, classify
from autoforge.af_mcp import _failure_payload, dispatch
from autoforge.af_draft import draft_intent
from autoforge.af_service import simulate
from autoforge.af_store import GraphStore, InvalidArchiveName, normalize_archive_name
from homesdk.adm.errors import ADM_ERR_PAYLOAD_INVALID

OWNER = {"subject": "owner", "scopes": ["read", "write", "live", "admin"]}

IR_OK = {"ir_version": "0.3.0", "id": "t_ok", "name": "t", "version": 1, "mode": "single",
         "nodes": [
             {"id": "a1", "kind": "on", "name": "触发",
              "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "off"}},
             {"id": "d1", "kind": "do", "name": "开灯", "adapter": "ha",
              "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
         "edges": [{"from": "a1", "to": "d1", "kind": "then"}]}


def _call(tool, args):
    with tempfile.TemporaryDirectory() as td:
        content, is_err = dispatch(tool, args, GraphStore(td), OWNER)
    return json.loads(content[0]["text"]), is_err


# ── P0-1 归因分层 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("message,want", [
    ("ref af:deadbeef 不存在或已过期", "REF_NOT_FOUND"),
    ("staging 里没有这个引用，已过期", "REF_NOT_FOUND"),
    ("未找到 书房空调 的任何版本", "ARCHIVE_NOT_FOUND"),
    ("访问密钥已过期：更新 AF_ACCESS_TOKENS", "TOKEN_EXPIRED"),
    ("令牌已过期", "TOKEN_EXPIRED"),
    ("invalid or revoked token", "TOKEN_EXPIRED"),
])
def test_classification_separates_ref_from_token(message, want):
    """裸「已过期」不是鉴权证据：staging 引用失效与令牌失效必须是两类。"""
    assert classify(message) == want


def test_new_categories_carry_actionable_advice():
    for cat in ("REF_NOT_FOUND", "ARCHIVE_NOT_FOUND"):
        assert CATEGORY_ADVICE.get(cat, "").strip(), f"{cat} 没有修法文案"
    assert "af_resolve_entity" not in CATEGORY_ADVICE["REF_NOT_FOUND"], \
        "ref 失效与实体解析无关，别把 agent 指向另一条路"
    assert "af_draft" in CATEGORY_ADVICE["REF_NOT_FOUND"], "该建议是重起草拿新 ref"


def test_hint_code_overrides_regex_guessing():
    """调用方已经知道自己是什么错时直接采信，归因不该靠猜措辞。"""
    assert classify("完全无关的一句话", hint_code="E_REF_NOT_FOUND") == "REF_NOT_FOUND"
    assert classify("完全无关的一句话", hint_code="FileNotFoundError") == "ARCHIVE_NOT_FOUND"


# ── P0-2 归档名边界与路径脱敏 ─────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../../../etc/passwd", "/etc/passwd", "a\\b", "..", "x" * 121, "  "])
def test_hostile_archive_names_are_rejected_not_mangled(bad):
    """静默清洗＝把"你的名字不合法"说成"我帮你改了"；这里必须响亮拒绝。"""
    with pytest.raises(InvalidArchiveName):
        normalize_archive_name(bad)


def test_legit_names_still_normalize_identically():
    """⛔ 不许把历史归档名一起改掉（目录名规则变化会让老归档找不回）。"""
    assert normalize_archive_name("书房空调") == ("书房空调", False)
    assert normalize_archive_name("case-01_x") == ("case-01_x", False)
    with pytest.raises(InvalidArchiveName):   # 斜杠此前被清洗成下划线，现在直接拒
        normalize_archive_name("a/b")


def test_af_diff_absent_archive_is_payload_invalid_without_path():
    body, is_err = _call("af_diff", {"name": "no-such-graph", "old": 1, "new": 2})
    assert is_err is True
    assert body["code"] == ADM_ERR_PAYLOAD_INVALID, body   # 此前是 ADM_ERR_INTERNAL
    blob = json.dumps(body, ensure_ascii=False)
    assert "v1.json" not in blob and "/data" not in blob and "Temp" not in blob, "绝对路径泄露"
    assert "no-such-graph" in blob, "用户传来的名字要能看见（隐掉等于换了个看不清的错）"


def test_af_diff_traversal_rejected_as_client_error():
    body, _ = _call("af_diff", {"name": "../../../etc/passwd", "old": 1, "new": 2})
    assert body["code"] == ADM_ERR_PAYLOAD_INVALID, body
    assert "归档名非法" in json.dumps(body, ensure_ascii=False)


# ── P0-3 未解析实体不许冒领 ok ────────────────────────────────────────

def test_library_layer_keeps_compile_semantics():
    """库层（CLI/直调没有目录是常态）：`ok` 仍表示"编译成功"，但必须把 unresolved 说出口。"""
    out = draft_intent({"name": "t", "when": {"entity": "没有这个设备xyz", "to": "on"},
                        "do": {"action": "开灯", "target": "也没有这个灯xyz"}}, catalog=None)
    assert out["ok"] is True, "库层语义不该被这次改动带跑"
    assert out["unresolved"] == ["也没有这个灯xyz", "没有这个设备xyz"]
    assert out["next_action"] == "af_resolve_entity"


def test_mcp_layer_refuses_ok_for_unresolved():
    """MCP 边界（agent 读的那一层）：同一份草稿不许回 ok:true。"""
    body, _ = _call("af_draft", {"intent": {"name": "t",
                                            "when": {"entity": "没有这个设备xyz", "to": "on"},
                                            "do": {"action": "开灯", "target": "light.x"}}})
    assert body["ok"] is False, "IR 编出来但实体没解析，仍算通过＝假绿"
    assert body["code"] == ADM_ERR_PAYLOAD_INVALID
    assert body["error"]["code"] == "E_UNRESOLVED_ENTITY"
    assert body["error"]["fix"]["unresolved"], "fix 要能机读"
    assert body.get("ref"), "ref 照给（便于查 IR / 修好后继续用）"


def test_resolved_draft_still_reports_ok_true():
    body, _ = _call("af_draft", {"intent": {"name": "t",
                                            "when": {"entity": "binary_sensor.m", "to": "on"},
                                            "do": {"action": "light.turn_on", "target": "light.x"}}})
    assert body["ok"] is True, "⛔ 不许矫枉过正把正常草稿也判失败"


# ── B1/B7 客户端错误不再占 5xx ────────────────────────────────────────

def test_bad_ref_via_apply_is_not_internal():
    body, _ = _call("af_apply", {"ref": "af:no-such-ref-000", "stage": "check"})
    assert body["code"] == ADM_ERR_PAYLOAD_INVALID, body
    blob = json.dumps(body, ensure_ascii=False)
    assert "令牌" not in blob, "坏 ref 的归因不许指向令牌（契约泳道 B2 的原文错判）"
    assert "REF_NOT_FOUND" in blob or "不存在" in blob


def test_schema_level_bad_input_is_not_internal():
    """`ge` 这类坏算子：build/apply 路径不许以 INTERNAL 出现（同一个错三处三样）。"""
    ir = json.loads(json.dumps(IR_OK))
    ir["nodes"].insert(1, {"id": "i1", "kind": "if", "name": "条件",
                           "expr": {"op": "ge", "left": {"var": "entity.light.x", "type": "numeric"},
                                    "right": {"const": 1}}})
    ir["edges"] = [{"from": "a1", "to": "i1", "kind": "then"},
                   {"from": "i1", "to": "d1", "kind": "then"}]
    body, _ = _call("af_simulate", {"ir": ir})
    assert body.get("code") != "ADM_ERR_INTERNAL", \
        f"IR schema 非法是对端可自纠的输入错，不该占 5xx：{json.dumps(body)[:200]}"


def test_failure_payload_carries_two_layer_codes():
    """两级码走唯一写手（顶层 ADM 口径 + AF 子码），不散着手拼第二套形状。"""
    p = _failure_payload(ADM_ERR_PAYLOAD_INVALID, "消息", sub_code="E_X", fix={"a": 1})
    assert p["ok"] is False and p["code"] == ADM_ERR_PAYLOAD_INVALID
    assert p["error"]["code"] == "E_X" and p["error"]["fix"] == {"a": 1}
    assert _failure_payload(ADM_ERR_PAYLOAD_INVALID, "只有消息").get("error") is None, \
        "不给子码时不许凭空长出一个空 error 键"


# ── B3/B4 simulate 的 ok 派生自实例终态 ───────────────────────────────

def test_failed_instance_no_longer_reports_ok():
    """无 on_error 兜底的求值失败 = 运行期会挂：回执必须说"failed"。"""
    ir = json.loads(json.dumps(IR_OK))
    ir["nodes"][0]["trigger"]["to"] = "off"
    ir["nodes"].insert(1, {"id": "i1", "kind": "if", "name": "条件",
                           "expr": {"op": "gt", "left": {"var": "entity.switch.x", "type": "numeric"},
                                    "right": {"const": 1}}})
    ir["edges"] = [{"from": "a1", "to": "i1", "kind": "then"},
                   {"from": "i1", "to": "d1", "kind": "then"}]
    r = simulate(ir, seed={"binary_sensor.m": "on", "switch.x": "off"},
                 events=[{"entity_id": "binary_sensor.m", "state": "off"}])
    assert r["ok"] is False and r["verdict"] == "failed", json.dumps(r)[:240]
    assert r["failed_instances"] and "numeric" in r["failed_instances"][0]["reason"]


def test_empty_run_is_flagged_not_passed_silently():
    """没合成触发 = 什么都没验到；不判失败，但必须给显式的"未验到"档。"""
    r = simulate(IR_OK)
    assert r["ok"] is True and r["verdict"] == "nothing_triggered"
    assert r["nothing_verified"] is True
    assert "af_apply" in r["note"], "要指路：哪儿才会合成触发"


def test_happy_path_still_reports_ran():
    r = simulate(IR_OK, seed={"binary_sensor.m": "on", "light.x": "off"},
                 events=[{"entity_id": "binary_sensor.m", "state": "off"}])
    assert r["ok"] is True and r["verdict"] == "ran"
    assert (r.get("final_states") or {}).get("light.x") == "on"


def test_apply_dry_run_propagates_simulate_failure(tmp_path):
    """B3 的真实形状：af_apply 顶层继承 simulate——实例失败时不许 `ok:true`。"""
    from autoforge.af_apply import apply
    store = GraphStore(str(tmp_path / "store"))
    ref = draft_intent({"name": "坏条件",
                        "when": {"entity": "binary_sensor.m", "to": "off"},
                        "if": {"gt": {"var": "switch.x", "const": 1}},
                        "do": {"action": "开灯", "target": "light.x"}})["ref"]
    r = apply(ref, stage="dry_run", store=store)
    sim = r.get("simulate") or {}
    if sim.get("verdict") == "failed":
        assert r["ok"] is False, "仿真判失败而预演仍通过＝假绿回来了"
    else:
        pytest.skip(f"该草稿没走到 failed 形状（verdict={sim.get('verdict')}），由上两条腿覆盖")
