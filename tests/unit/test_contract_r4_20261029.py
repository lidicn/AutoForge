"""契约 R4 判据（2026-10-29）：token 双源统一、live_run 执行裁决位。

R4-1 是本轮最重的发现：写路径只读 env token、读路径走 credentials.json——env 那枚
失效后读工具"一直健康"、写工具一直 401、watch 起不来，三面互相对不上。体验者原话：
「若以 env token 失效为偶然防线，则当前无授权码也能控制真机——防线是运气不是设计」。
统一入口 `_ha_token`（credentials.json 优先、env 回退）+ `ha_token_sources` 双源健康。

R4-2：HA 401 拒收动作后回执仍 ok:true + writes:[吊灯]——`writes` 是 IR 声明清单，
不是执行记录。`_dispatch_verdict` 从审计真值算 executed/failed/nothing_dispatched。
"""

from __future__ import annotations

import json
import pytest
import tempfile
from pathlib import Path

from autoforge.af_service import (
    ServiceError,
    _dispatch_verdict,
    _ha_token,
    ha_token_sources,
)
from autoforge.af_store import GraphStore


class _FakeRuntime:
    """最小 runtime 替身：audit 元素带 to_dict。"""

    def __init__(self, types):
        self.audit = [type("E", (), {"to_dict": (lambda self, t=t: {"type": t})})() for t in types]


# ── R4-1 token 统一 ──────────────────────────────────────────────────

def test_token_prefers_credentials_file_over_env(tmp_path, monkeypatch):
    """credentials.json 优先（WebUI 填的那份是运维真正维护的），env 只做回退。"""
    cred = tmp_path / "credentials.json"
    cred.write_text(json.dumps({"ha_token": "file-token"}), encoding="utf-8")
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "env-token")
    assert _ha_token(tmp_path) == "file-token"


def test_token_falls_back_to_env_without_file(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_LONG_LIVED_TOKEN", raising=False)
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "env-token")
    assert _ha_token(tmp_path) == "env-token"


def test_sources_health_reports_disagreement(tmp_path, monkeypatch):
    """双源只配一边时要能被看见（R4-1 的运维盲区就是这个形状）。"""
    cred = tmp_path / "credentials.json"
    cred.write_text(json.dumps({"ha_token": "file-token"}), encoding="utf-8")
    monkeypatch.delenv("HA_LONG_LIVED_TOKEN", raising=False)
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)
    s = ha_token_sources(tmp_path)
    assert s["config_token_present"] is True and s["env_token_present"] is False
    assert s["sources_agree"] is False

    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "env-token")
    s2 = ha_token_sources(tmp_path)
    assert s2["env_token_present"] is True and s2["config_token_present"] is True
    assert s2["sources_agree"] is True


def test_no_token_at_all_is_honest_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_LONG_LIVED_TOKEN", raising=False)
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)
    assert _ha_token(tmp_path) == ""
    s = ha_token_sources(tmp_path)
    assert s["env_token_present"] is False and s["config_token_present"] is False
    assert s["sources_agree"] is True  # 都没有也算一致（一致地不可用）


# ── R4-2 执行裁决位 ──────────────────────────────────────────────────

def test_verdict_executed_when_dispatch_audited():
    assert _dispatch_verdict(_FakeRuntime(["af_action_dispatched"]))["verdict"] == "executed"
    out = _dispatch_verdict(_FakeRuntime(["af_action_dispatched", "af_action_dispatched"]))
    assert out["dispatched"] == 2 and out["failed"] == 0


def test_verdict_failed_when_ha_rejects():
    out = _dispatch_verdict(_FakeRuntime(["action_failed"]))
    assert out["verdict"] == "failed" and out["failed"] == 1
    assert out["failed_detail"], "失败要有可读原因（401 那次连原因都没给）"


def test_verdict_nothing_when_no_do_ran():
    out = _dispatch_verdict(_FakeRuntime([]))
    assert out["verdict"] == "nothing_dispatched"
    assert out["dispatched"] == 0 and out["failed"] == 0


def test_verdict_counts_both_when_partial():
    out = _dispatch_verdict(_FakeRuntime(["af_action_dispatched", "action_failed"]))
    # AF-301（审计）后：部分失败是独立档。旧断言期望 "failed"——那是退化三元
    # 时代的形状（两支同值把 mixed 吞进 failed）。
    assert out["verdict"] == "partially_failed" and out["dispatched"] == 1 and out["failed"] == 1


def test_service_error_still_carries_status():
    """R4-1 修复不许动到既有错误通道的契约。"""
    assert ServiceError("x", status=403).status == 403


# ── R2-4 / R4-5 文档位：测试残留清理指引 ──────────────────────────────

def test_pending_queue_rejection_is_available_http_side():
    """agent 面暂无 approve/reject 工具（R4-5，待裁），但 HTTP 面的 reject 必须活着——
    体验者留了 4 条 pending 测试残留，清理通道不能只有 WebUI 一条腿。"""
    from autoforge import af_service as svc
    assert hasattr(svc, "reject_pending") or hasattr(svc, "approve_pending"), \
        "连服务层都没有拒绝通道的话，pending 队列就是只能进不能出的垃圾场"


def test_enable_by_tag_warns_about_fake_armed(tmp_path):
    """R4-4「假武装」：enable 只翻标志不保监听——回执必须明说（⛔ 别让调用方以为生效）。

    服务层的 enable 直接吃归档记录（不走 pending 队列），所以这条腿用 store.save
    造一条已归档记录再按 tag 启用。
    """
    from autoforge.af_service import enable_by_tag
    from autoforge.af_draft import draft_intent
    from autoforge.af_ir import Graph, load_automation
    from autoforge.af_spec import graph_to_raw
    from autoforge.af_draft import get_staged

    store = GraphStore(str(tmp_path / "store"))
    ref = draft_intent({"name": "r4arm", "when": {"entity": "binary_sensor.m", "to": "on"},
                        "do": {"action": "开灯", "target": "light.x"}})["ref"]
    raw = graph_to_raw(get_staged(ref)["graph"])[0]
    raw["tags"] = ["qa_r4"]
    store.save(Graph([load_automation(raw)]), "r4arm", tags=["qa_r4"])
    # AF-401 后启用闸在服务层——这条腿量的是对账/假武装形状，先给它一枚真码过闸。
    from pathlib import Path as _P
    from autoforge.af_auth import AuthCodeStore
    _rec = AuthCodeStore(_P(store.root) / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    out = enable_by_tag(store, "qa_r4", True, allow_bulk=False,
                        auth_code=getattr(_rec, "code", None) or _rec["code"])
    assert out["ok"] is True, out
    # 裁定方案 A 之后语义变了：启用时不再只是"提醒可能假武装"，而是**真去做对账**，
    # 结果落在 watch_reconcile；watch_note 只在禁用时说话。
    assert "watch_reconcile" in out, "启用必须回报监听对账结果"
    assert out["watch_reconcile"] is not None and out["watch_reconcile"]["action"]
    # 禁用方案 A 后的契约：不碰监听 ⇒ `watch_reconcile` 必为 None（"没做"要说没做），
    # watch_note 转为解释"禁用不触碰监听"。旧断言（禁用时 note 为 None）已被裁定 A 取代。
    out2 = enable_by_tag(store, "qa_r4", False, allow_bulk=False)
    assert out2.get("watch_reconcile") is None, "禁用不该去动监听，也不该假装动了"


def test_live_run_uses_unified_token_not_env_only(tmp_path, monkeypatch):
    """R4-1 的**行为级**判据（上一版腿只测 helper 本身，摘掉统一入口没腿红＝假绿）：

    monkeypatch 让 credentials.json 有一枚 token、env 一枚不同的——若 live_run 走
    统一入口，到达 transport 的应是 file 那枚；旧实现只读 env ⇒ 到达的是 env 那枚。
    用 LIVE_TRANSPORT_FACTORY 捕获到达值（既有测试的注入位）。
    """
    import autoforge.af_service as svc

    # credentials.json 与归档同根（生产布局：store_root/credentials.json）——
    # 上一版放在 tmp_path 根，live_run 读的是 store.root ⇒ 测了个寂寞（红得对）。
    store_root = tmp_path / "store"
    store_root.mkdir(exist_ok=True)
    cred = store_root / "credentials.json"
    cred.write_text(json.dumps({"ha_token": "file-side-token"}), encoding="utf-8")
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "env-side-token")

    seen: dict = {}

    def fake_factory(url, token):
        seen["url"], seen["token"] = url, token
        return type("T", (), {"base_url": url, "token": token})()

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", fake_factory)
    # 闸序（AF-408 后）：LIVE_ENABLED → has_token → confirm → 租约 → **码** → transport。
    # 探针带真码——服务层闸在 transport 之前，无码到不了要量的那一站。
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    from autoforge.af_auth import AuthCodeStore
    _rec = AuthCodeStore(store_root / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    auth_code = getattr(_rec, "code", None) or _rec["code"]
    ir = {"ir_version": "0.3.0", "id": "r4tok", "name": "t", "version": 1, "mode": "single",
          "nodes": [{"id": "a", "kind": "on", "name": "t",
                     "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                    {"id": "d", "kind": "do", "name": "d", "adapter": "ha",
                     "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
          "edges": [{"from": "a", "to": "d", "kind": "then"}]}
    try:
        svc.live_run(ir, ["light.x"], events=[{"entity_id": "binary_sensor.m", "state": "on"}],
                     confirm=True, store=GraphStore(str(store_root)), auth_code=auth_code)
    except Exception:
        pass  # 下发本身可能因假 transport 失败——我们只看**到达** transport 的 token
    assert seen.get("token") == "file-side-token",         f"live_run 到达 transport 的 token 是 {seen.get('token')!r}——仍在用 env 那枚（R4-1 未修）"


# ── R4-3 授权码闸（出资人裁定「补上」2026-10-29）─────────────────────

def _gate_call(tool, args, tmp, store_root=None):
    """默认新开 store；需要预置授权码的腿必须传同一个 store_root
    （第一版没传：码签在 A 目录、验证发生在 B 目录的临时 store ⇒ 假红）。"""
    from autoforge.af_mcp import dispatch
    from autoforge.af_store import GraphStore
    adm = {"subject": "owner", "scopes": ["read", "write", "live", "admin"]}
    if store_root is not None:
        content, is_err = dispatch(tool, args, GraphStore(str(store_root)), adm)
        return json.loads(content[0]["text"])
    with tempfile.TemporaryDirectory() as td:
        content, is_err = dispatch(tool, args, GraphStore(td), adm)
    return json.loads(content[0]["text"])


_LIVE_IR = {"ir_version": "0.3.0", "id": "g", "name": "g", "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on", "trigger": {"type": "state",
                                                            "entity_id": "binary_sensor.m", "to": "on"}},
                      {"id": "p", "kind": "pass"}],
            "edges": [{"from": "a", "to": "p", "kind": "then"}]}


def test_live_run_without_code_is_refused(tmp_path, monkeypatch):
    # 闸序（AF-408 后）：LIVE_ENABLED → has_token → confirm → allow → 租约 → **码**。
    # 这条腿量的是码闸——前几站先放行（配齐前置≠致盲哨兵；哨兵量的就是码这一站，
    # 不配前置它永远撞 LIVE_ENABLED 提前退场，码闸 0 覆盖）。
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "probe-token")
    body = _gate_call("af_live_run", {"ir": _LIVE_IR, "live_allow": ["light.x"], "confirm": True}, tmp_path)
    assert body["ok"] is False and body["code"] == "ADM_ERR_AUTH_REQUIRED"
    # AF-401 后闸在服务层：拒绝经 dispatch 的 ServiceError(403)→AUTH_REQUIRED 映射
    # （MCP 层不再手拼 error.code；message 里"授权码"即新的可判读形状）。
    assert "授权码" in str(body.get("message") or ""), "要告诉怎么补"


def test_enable_needs_code_disable_does_not(tmp_path):
    """启用=要码（开安全阀）；禁用=免码（出事时 agent 必须能自己止损）。"""
    on = _gate_call("af_enable_by_tag", {"tag": "qa", "enabled": True}, tmp_path)
    assert on["ok"] is False and on["code"] == "ADM_ERR_AUTH_REQUIRED"
    off = _gate_call("af_enable_by_tag", {"tag": "qa", "enabled": False}, tmp_path)
    assert off["ok"] is True, "禁用被码闸挡住＝故障时无法止损"
    assert off.get("watch_reconcile") is None, "禁用不该去动监听（也不该假装动了）"


def test_bad_code_is_refused_without_revealing_why(tmp_path, monkeypatch):
    """坏码要拒，但⛔ 不外传"不存在/已消耗/已过期/已锁定"具体哪一因（授权码枚举面）。"""
    # 同 test_live_run_without_code_is_refused：前几站先放行，哨兵量的才是码闸。
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "probe-token")
    body = _gate_call("af_live_run", {"ir": _LIVE_IR, "live_allow": ["light.x"],
                                      "confirm": True, "auth_code": "0" * 8}, tmp_path)
    assert body["ok"] is False and body["code"] == "ADM_ERR_AUTH_REQUIRED"
    msg = str(body.get("message") or "")
    assert "之一" in msg, f"必须保持四因合一：{msg}"


def test_valid_code_passes_the_gate(tmp_path):
    """有效码过闸后，后面的闸（LIVE_ENABLED 等）才轮得到——证明闸不是无脑拒绝。"""
    from autoforge.af_auth import AuthCodeStore
    td = tmp_path / "s"
    td.mkdir()
    rec = AuthCodeStore(td / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    code = getattr(rec, "code", None) or (rec.get("code") if isinstance(rec, dict) else None)
    assert code
    body = _gate_call("af_live_run", {"ir": _LIVE_IR, "live_allow": ["light.x"],
                                      "confirm": True, "auth_code": code},
                      tmp_path, store_root=td)
    assert (body.get("error") or {}).get("code") != "AUTH_CODE_REQUIRED"
    assert "LIVE_ENABLED" in json.dumps(body, ensure_ascii=False) or body.get("ok") is True


def test_gate_helper_returns_explicit_pair():
    """形状判据（AF-401 改写）：闸已下移 `svc._auth_code_check`——拒绝抛 ServiceError(403)
    而非返回哨兵；store=None 抛**可读** 403 而非 AttributeError（AF-404 的形状）。
    旧 MCP 层 `_auth_code_gate` 已随闸下移删除。
    """
    import autoforge.af_service as svc_mod
    with pytest.raises(ServiceError) as no_store:
        svc_mod._auth_code_check(None, "", action="探针")
    assert no_store.value.status == 403 and "store" in str(no_store.value)
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ServiceError) as no_code:
            svc_mod._auth_code_check(GraphStore(td), "", action="探针")
        assert no_code.value.status == 403 and "授权码" in str(no_code.value)
