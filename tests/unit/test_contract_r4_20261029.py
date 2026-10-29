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
    assert out["verdict"] == "failed" and out["dispatched"] == 1 and out["failed"] == 1, \
        "部分失败必须显出来，⛔ 不许被成功的那半掩盖"


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
    out = enable_by_tag(store, "qa_r4", True, allow_bulk=False)
    assert out["ok"] is True, out
    assert out.get("watch_note") and "假武装" in out["watch_note"]
    # 禁用时是 None（没有假武装问题）
    out2 = enable_by_tag(store, "qa_r4", False, allow_bulk=False)
    assert out2.get("watch_note") is None


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
    # 三重闸的前两道（LIVE_ENABLED / has_token）也要过：缺了就走不到 transport
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    ir = {"ir_version": "0.3.0", "id": "r4tok", "name": "t", "version": 1, "mode": "single",
          "nodes": [{"id": "a", "kind": "on", "name": "t",
                     "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                    {"id": "d", "kind": "do", "name": "d", "adapter": "ha",
                     "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
          "edges": [{"from": "a", "to": "d", "kind": "then"}]}
    try:
        svc.live_run(ir, ["light.x"], events=[{"entity_id": "binary_sensor.m", "state": "on"}],
                     confirm=True, store=GraphStore(str(store_root)))
    except Exception:
        pass  # 下发本身可能因假 transport 失败——我们只看**到达** transport 的 token
    assert seen.get("token") == "file-side-token",         f"live_run 到达 transport 的 token 是 {seen.get('token')!r}——仍在用 env 那枚（R4-1 未修）"
