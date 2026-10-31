"""AF-705（`af_live_run` 缺 `live_allow` ⇒ ADM_ERR_INTERNAL: KeyError）回归钉，2026-10-31。

出处：QA 拨测——`_t_live` 用 `args["live_allow"]` 取必填参数，不传时 KeyError
穿到 dispatch 的 catch-all，回 `ADM_ERR_INTERNAL`（"工具执行出错：KeyError:
'live_allow'"）。对端读 INTERNAL 的默认动作是重试/报障，正确答案是"补参数"
——同工具对字符串入参早已有清晰桥接提示（dispatch 的 schema 归一层），缺的
只是这一处 `args["x"]` → `args.get("x")`。

修法：`.get` 后 None 落进 `svc.live_run` 的必填闸（ServiceError 400）⇒
`ADM_ERR_PAYLOAD_INVALID`，与 ir/confirm 的缺参同一档。

各腿全程离线：真机闸按序 events-cap → enabled → token → confirm →
**live_allow 必填闸** → 单写者租约 → 授权码闸 → IR 解析。判据①②③在
live_allow 闸或 schema 归一层就停；判据④故意不带 auth_code，停在授权码闸
（403「需要授权码」）——停在**后面**的闸 = 前面的 live_allow 闸已放行。

变异自证（`.get` 改回 `["..."]` ⇒ 判据①红）由实现者手工执行并附命令输出。
"""

from __future__ import annotations

import json

from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore

#: 带 live scope 的令牌身份（`_guard` 三关：有令牌、有 subject、有 scope）。
_CURRENT = {"subject": "af705-tester", "scopes": ["live"]}


def _ir() -> dict:
    return {
        "ir_version": "0.2.1", "id": "af705", "name": "af705", "version": 1, "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d", "kind": "do", "adapter": "ha", "action": "light.turn_on",
             "params": {"entity_id": "light.x"}},
        ],
        "edges": [{"from": "o", "to": "d", "kind": "then"}],
    }


def _call(tmp_path, args: dict, monkeypatch) -> tuple[dict, bool]:
    """过 guard 后真调 dispatch；env 用 monkeypatch（纪律：测试环境变量一律 monkeypatch）。"""
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "af705-test-token")
    store = GraphStore(str(tmp_path / "store"))
    content, is_error = dispatch("af_live_run", args, store, current=dict(_CURRENT))
    body = json.loads(content[0]["text"])
    return body, is_error


def test_missing_live_allow_is_payload_invalid_not_internal(tmp_path, monkeypatch):
    """判据①：不传 live_allow ⇒ PAYLOAD_INVALID 的结构化拒绝（⛔ 不是 INTERNAL/KeyError）。"""
    body, is_error = _call(tmp_path, {"ir": _ir(), "confirm": True}, monkeypatch)
    assert is_error is True
    assert body["code"] == "ADM_ERR_PAYLOAD_INVALID", body
    assert "live_allow" in str(body.get("message") or body.get("error") or ""), body
    assert "KeyError" not in str(body), body


def test_empty_live_allow_list_rejected(tmp_path, monkeypatch):
    """判据②：传 [] ⇒ 同样 PAYLOAD_INVALID（白名单必填，先不开放全量）。"""
    body, is_error = _call(tmp_path, {"ir": _ir(), "live_allow": [], "confirm": True}, monkeypatch)
    assert is_error is True
    assert body["code"] == "ADM_ERR_PAYLOAD_INVALID", body


def test_string_live_allow_gets_bridge_hint(tmp_path, monkeypatch):
    """判据③：传非 JSON 字符串 ⇒ schema 归一层的可读桥接提示（指到"被字符串化"）。"""
    body, is_error = _call(tmp_path, {"ir": _ir(), "live_allow": "light.x", "confirm": True}, monkeypatch)
    assert is_error is True
    assert body["code"] == "ADM_ERR_PAYLOAD_INVALID", body
    msg = str(body.get("message") or body.get("error") or "")
    assert "live_allow" in msg and "字符串" in msg, msg


def test_stringified_json_list_passes_gate(tmp_path, monkeypatch):
    """判据③附：桥接层 json.dumps 过的合法数组能解一层照常放行（既有兜底不回归）。"""
    body, is_error = _call(
        tmp_path,
        {"ir": _ir(), "live_allow": json.dumps(["light.x"]), "confirm": True},
        monkeypatch,
    )
    # 停在授权码闸（403 AUTH_REQUIRED）= live_allow 闸已放行
    assert body["code"] == "ADM_ERR_AUTH_REQUIRED", body
    assert "授权码" in str(body.get("message") or body.get("error") or ""), body


def test_real_entity_ids_pass_live_allow_gate(tmp_path, monkeypatch):
    """判据④：正常传实体 id ⇒ 停在后面的授权码闸（live_allow 闸已放行，全程离线）。"""
    body, is_error = _call(
        tmp_path,
        {"ir": _ir(), "live_allow": ["light.x"], "confirm": True},
        monkeypatch,
    )
    assert is_error is True
    assert body["code"] == "ADM_ERR_AUTH_REQUIRED", body
    assert "授权码" in str(body.get("message") or body.get("error") or ""), body
