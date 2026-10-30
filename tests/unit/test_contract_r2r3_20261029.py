"""契约 R2 / R3 批次的判据腿（2026-10-29 第二轮）。

R2-5 根因链：`关射灯` 不在别名表 → 原样当服务名进 IR → 分级器认不出域 → 保守判 L2 →
文案谎称"属于门锁/窗帘/空调"。修在三处：起草拒、分级器给诊断、扫描器文案分型。
R2-1/R2-4、R3-1/R3-2/R3-8 各一族：未找到归一码、入队≠落盘、参数静默替换、端点泄露。
"""

from __future__ import annotations

import json
import tempfile

import pytest

from autoforge.af_adapters.base import KNOWN_SERVICE_DOMAINS, unrecognized_service
from autoforge.af_draft import DraftError, draft_intent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_mcp import dispatch
from autoforge.af_scanner import StaticScanner
from autoforge.af_service import ServiceError
from autoforge.af_store import GraphStore
from homesdk.adm.errors import ADM_ERR_PAYLOAD_INVALID

OWNER = {"subject": "owner", "scopes": ["read", "write", "live", "admin"]}


def _call(tool, args):
    with tempfile.TemporaryDirectory() as td:
        content, is_err = dispatch(tool, args, GraphStore(td), OWNER)
    return json.loads(content[0]["text"]), is_err


def _phantom_action_ir(action: str) -> dict:
    return {"ir_version": "0.3.0", "id": "s", "name": "s", "version": 1, "mode": "single",
            "nodes": [
                {"id": "a", "kind": "on", "name": "触发",
                 "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {"id": "d", "kind": "do", "name": "动作", "adapter": "ha",
                 "action": action, "params": {"entity_id": "switch.x"}},
                {"id": "p", "kind": "pass", "name": "结束"}],
            "edges": [{"from": "a", "to": "d", "kind": "then"}, {"from": "d", "to": "p", "kind": "then"}]}


# ── R2-5 根因：未知中文动作名在起草就拒 ───────────────────────────────

def test_unknown_chinese_action_rejected_at_draft():
    with pytest.raises(DraftError) as ei:
        draft_intent({"name": "t", "when": {"entity": "binary_sensor.m", "to": "on"},
                      "do": {"action": "关射灯", "target": "switch.x"}}, catalog=None)
    assert ei.value.code == "E_UNKNOWN_ACTION"
    assert "switch.turn_off" in json.dumps(ei.value.fix, ensure_ascii=False), "fix 要给可抄的正确写法"


@pytest.mark.parametrize("action", ["关灯", "开灯", "开空调", "关空调", "turn_on", "turn_off"])
def test_known_aliases_still_pass(action):
    out = draft_intent({"name": "t", "when": {"entity": "binary_sensor.m", "to": "on"},
                        "do": {"action": action, "target": "switch.x"}}, catalog=None)
    assert out["ok"] is True, f"别名 {action} 不该被新闸误伤"


def test_ascii_service_less_action_not_rejected_at_draft():
    """⛔ 不误伤：ASCII 无点动作名（`set_state` 一类）维持原透传语义，只拦中文短语。"""
    out = draft_intent({"name": "t", "when": {"entity": "binary_sensor.m", "to": "on"},
                        "do": {"action": "set_state", "target": "switch.x", "state": "on"}},
                       catalog=None)
    assert out["ok"] is True


# ── R2-5 文案：认不出 ≠ 真高危 ───────────────────────────────────────

def test_unrecognized_service_helper_distinguishes_forms():
    assert unrecognized_service("关射灯") is not None
    assert unrecognized_service("frobnicate.turn_on") is not None
    assert unrecognized_service("switch.turn_off") is None
    assert unrecognized_service("climate.set_hvac_mode") is None
    assert "switch" in KNOWN_SERVICE_DOMAINS and "lock" in KNOWN_SERVICE_DOMAINS


def test_l2_message_says_unknown_action_not_lock_curtain():
    res = StaticScanner(Graph([load_automation(_phantom_action_ir("关射灯"))])).scan()
    d = next(x for x in res.diagnostics if x.code == "L2_NEEDS_CONFIRM")
    assert "门锁/窗帘/空调" not in d.message, "认不出的动作名不许被说成高危设备"
    assert "改动作名" in d.message, "文案要指对下一步（补 requires_confirm 是错方向）"


def test_l2_message_keeps_original_wording_for_real_l2_domain():
    res = StaticScanner(Graph([load_automation(_phantom_action_ir("climate.turn_on"))])).scan()
    d = next(x for x in res.diagnostics if x.code == "L2_NEEDS_CONFIRM")
    assert "门锁/窗帘/空调" in d.message, "真 L2 域的原文案不能被我改坏"


# ── R2-1 未找到归一码（只读图工具）─────────────────────────────────────

def test_get_graph_missing_archive_is_payload_invalid_not_internal():
    body, _ = _call("af_get_graph", {"name": "不存在的归档"})
    assert body["code"] == ADM_ERR_PAYLOAD_INVALID, body
    assert (body.get("error") or {}).get("code") == "ARCHIVE_NOT_FOUND"
    assert "af_list_graphs" in json.dumps(body, ensure_ascii=False), "要指下一步"


def test_file_not_found_never_leaks_paths():
    for tool, args in (("af_diff", {"name": "ghost", "old": 1, "new": 2}),
                       ("af_get_graph", {"name": "ghost"})):
        body, _ = _call(tool, args)
        blob = json.dumps(body, ensure_ascii=False)
        assert "v1.json" not in blob and "/data" not in blob, f"{tool} 泄露内部路径"
        assert body.get("code") != "ADM_ERR_INTERNAL", f"{tool} 仍占 5xx"


# ── R2-4 入队 ≠ 落盘 ─────────────────────────────────────────────────

def test_save_receipt_states_persisted_false(tmp_path):
    """`ok:true` + `pending` 曾让 agent 以为已保存，转头 af_get_graph 全未找到。"""
    from autoforge.af_service import submit_pending
    ir = _phantom_action_ir("switch.turn_off")
    store = GraphStore(str(tmp_path / "store"))
    out = submit_pending(store, "af_save", {"ir": ir, "name": "qa_inert"},
                         submitted_by="qa", authenticated_subject="qa")
    assert out["ok"] is True
    assert out["persisted"] is False, "入队不许被读成落盘"
    assert out["landing"] == "pending_approval"
    assert "af_get_graph" in out["next_action"]


# ── R3-1 / R3-2 参数不许静默替换、回执回显生效值 ─────────────────────

@pytest.mark.parametrize("args", [{"days": 0}, {"days": -5}, {"days": "abc"}])
def test_telemetry_rejects_non_positive_days(args):
    body, _ = _call("af_telemetry", args)
    assert body["code"] == ADM_ERR_PAYLOAD_INVALID, body
    assert "整数" in json.dumps(body, ensure_ascii=False)


@pytest.mark.parametrize("args", [{"limit": 0}, {"limit": -1}])
def test_experience_rejects_non_positive_limit(args):
    body, _ = _call("af_experience", args)
    assert body["code"] == ADM_ERR_PAYLOAD_INVALID, body


def test_effective_window_is_echoed_not_the_request_value():
    body, _ = _call("af_telemetry", {"days": 7})
    assert body.get("days") == 7, "回显必须是生效窗口"
    body2, _ = _call("af_experience", {"limit": 3})
    assert body2.get("effective_limit") == 3


def test_default_window_still_applies_when_omitted():
    body, _ = _call("af_telemetry", {})
    assert body.get("ok") is True and body.get("days") == 30


# ── R3-8 端点不在 agent 面泄露 ────────────────────────────────────────

def test_catalog_hides_internal_ha_endpoint(monkeypatch):
    # AF-303 收掉硬编码回落后，ha_url 来自 env——腿自己带一个内网址来量掩码
    # （旧腿绿靠的是硬编码字面量恰好在场；那枚真源已按审计铲除）。
    monkeypatch.setenv("AUTOFORGE_HA_URL", "http://192.168.2.200:8123")
    body, _ = _call("af_catalog", {})
    blob = json.dumps(body, ensure_ascii=False)
    assert "192.168" not in blob and "8123" not in blob, f"内网端点泄露：{blob[:160]}"
    assert "内网端点已隐去" in blob, "要说明被隐了，⛔ 不许静默删字段"


# ── R2-7 文档：不许再教 schema 会拒的写法 ─────────────────────────────

def test_guide_contains_no_invalid_trigger_forms():
    """指引按测试自身所在树读（⛔ cwd 相对路径会读到主检出里的旧 skill——
    第一次跑这条腿就是被它骗红的）。可照抄的 JSON 形态里不许有 schema 会拒的假字段；
    prose 里点名「这是假写法」是允许且必要的。"""
    from pathlib import Path
    guide = Path(__file__).resolve().parents[2] / "skills" / "autoforge.md"
    text = guide.read_text(encoding="utf-8")
    assert '"today_only"' not in text and '"event_name":' not in text
    for truth in ('{"type": "event", "event": "my_event"}', "additionalProperties: false"):
        assert truth in text, f"指引缺真值：{truth}"


def test_service_error_maps_to_payload_invalid_via_dispatch():
    """helper 抛的 ServiceError(400) 必须走 PAYLOAD_INVALID 出口，不许占 INTERNAL。"""
    assert ServiceError("x", status=400).status == 400
