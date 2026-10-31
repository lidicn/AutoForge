"""AF-701 界线判据（卡 D，2026-10-31）：豁免只对"无令牌身份"档保留。

依据：DCD 裁定 `20261031-AF-AF701残缺身份契约与批准后真机下发免码-裁定.md` §2.1
（追认 + 豁免限定死在"无令牌身份"档）。裁定给的收口形状：`_assert_archive_mutator` /
`import_store` 的豁免条件从"未携带身份"改为精确判据——`caller=="" 且无令牌上下文` ⇒
放行；`有令牌但 subject 空` ⇒ 拒绝（入口闸已拦，服务层写判据钉住，⛔ 靠注释）。

判据腿（每条都可独立红）：
1. 服务层：`token_identity=True` + `caller=""`（持令牌缺 subject）⇒ 403 拒绝；
2. 服务层：`token_identity=None`（无令牌身份的本地 CLI/内部调用）⇒ 豁免维持放行；
3. MCP 面：`af_set_tags` / `af_import_store` 透传身份 ⇒ 入口闸拒空主体令牌（既有行为），
   服务层判据对"current=None 的原型放行"不误伤；
4. HTTP 面：`/api/graphs/tags` 递 token_identity ⇒ 空主体令牌在 `requires` 已 403
   （既有），本地无鉴权逃生舱（info=None）不被新判据误拦。

⚠️ 环境变量一律 monkeypatch（test_stability_r3 门拦裸 os.environ 直写）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoforge import af_service as svc
from autoforge.af_ir import Graph, load_automation
from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore


def _make_ir(aid: str) -> dict:
    return {"ir_version": "0.3.0", "id": aid, "name": aid, "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on", "name": "t",
                       "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                      {"id": "d", "kind": "do", "name": "d", "adapter": "ha",
                       "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
            "edges": [{"from": "a", "to": "d", "kind": "then"}]}


def _seed(store: GraphStore) -> None:
    store.save(Graph([load_automation(_make_ir("theirs"))]), "theirs", tags=["prod"], owner="agent-b")


def _bundle(store: GraphStore) -> dict:
    """真 bundle：store.export_bundle 的原样导出（format/entries/versions 形状由真源出）。"""
    return store.export_bundle()


# ── 腿 1：服务层残缺身份（持令牌缺 subject）⇒ 拒绝 ─────────────────


def test_mutator_rejects_token_with_empty_subject(tmp_path):
    """`_assert_archive_mutator`：token_identity=True + caller="" ⇒ 403（裁定 §2.1 收口形状）。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    with pytest.raises(svc.ServiceError) as ei:
        svc.set_graph_tags(store, "theirs", ["hijacked"],
                           caller="", caller_is_owner=False, token_identity=True)
    assert ei.value.status == 403
    assert "subject" in str(ei.value), f"拒绝文案要指向病灶（缺 subject）：{ei.value}"


def test_import_store_rejects_token_with_empty_subject(tmp_path):
    """`import_store` 同一道界线：持令牌缺 subject 的 overwrite 档 ⇒ 403。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    with pytest.raises(svc.ServiceError) as ei:
        svc.import_store(store, _bundle(store), "overwrite", allow_bulk=True,
                         owner="", owner_surface=False, token_identity=True)
    assert ei.value.status == 403
    assert "subject" in str(ei.value)


# ── 腿 2：无令牌身份（本地 CLI/内部调用）⇒ 豁免维持 ─────────────────


def test_mutator_exemption_survives_for_no_token_identity(tmp_path):
    """`token_identity=None`＝根本没有令牌身份的本地调用 ⇒ 豁免照旧（裁定 §四 ⛔ 整体删除）。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    res = svc.set_graph_tags(store, "theirs", ["local-ok"],
                             caller="", caller_is_owner=False, token_identity=None)
    assert res["ok"] is True and store.get_tags("theirs") == ["local-ok"]


def test_import_store_exemption_survives_for_no_token_identity(tmp_path):
    """`import_store` 的"未声明身份"档：旧 CLI/MCP 路径不被新判据误拦。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    report = svc.import_store(store, _bundle(store), "skip", allow_bulk=True,
                              owner="", owner_surface=False, token_identity=None)
    # skip 策略对同名归档报 list；imported 计数。两者合计 ≥1 即"没被误拦"。
    n = int(report.get("imported") or 0) + len(report.get("skipped") or [])
    assert n >= 1, f"无令牌身份的本地导入被误拦：{report}"


# ── 腿 3：MCP 面透传（新参数接线不误伤既有行为）─────────────────


ANON_WRITE = {"subject": "", "scopes": ["read", "write"]}
AGENT_B = {"subject": "agent-b", "scopes": ["read", "write"]}


def _call(tool: str, args: dict, store: GraphStore, who: dict | None) -> dict:
    content, _ = dispatch(tool, args, store, who)
    return json.loads(content[0]["text"])


def test_mcp_set_tags_injects_identity_and_rejects_anon_write(tmp_path):
    """`af_set_tags` 已进 _TOOLS_WITH_CONTEXT：空主体 write 令牌在 _guard 被拒（入口闸），
    有名令牌照常改自己的归档（误伤面腿）。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_set_tags", {"name": "theirs", "tags": ["hijacked"]}, store, ANON_WRITE)
    assert body["ok"] is False and "subject" in str(body.get("message") or "")

    body2 = _call("af_set_tags", {"name": "theirs", "tags": ["ok"]}, store, AGENT_B)
    assert body2["ok"] is True and store.get_tags("theirs") == ["ok"]


def test_mcp_set_tags_owner_face_can_mutate(tmp_path):
    """owner 面（subject ∈ _OWNER_SUBJECTS）改他人归档照常——界线判据不拦 owner。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_set_tags", {"name": "theirs", "tags": ["admin-ok"]}, store,
                 {"subject": "owner", "scopes": ["read", "write"]})
    assert body["ok"] is True, body


def test_mcp_import_passes_token_identity(tmp_path):
    """`af_import_store`：空主体令牌在 _guard 被拒（入口闸）；有名令牌导入不误伤。"""
    store = GraphStore(str(tmp_path))
    bundle = _bundle(store)
    body = _call("af_import_store", {"bundle": bundle, "strategy": "skip",
                                     "allow_bulk": True}, store, ANON_WRITE)
    assert body["ok"] is False and "subject" in str(body.get("message") or "")


# ── 腿 4：HTTP 面透传（token_identity 接线 + 逃生舱不误伤）──────────


def test_http_tags_endpoint_passes_token_identity(tmp_path, monkeypatch):
    """`/api/graphs/tags` 递 token_identity：空主体令牌在 requires 403（既有），
    本地无鉴权逃生舱（info=None）不被新判据误拦。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUTOFORGE_TOKENS", json.dumps({
        "tok-empty": {"subject": "", "scopes": ["read", "write"]},
        "tok-ok": {"subject": "owner", "scopes": ["read", "write", "admin"]},
    }))
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "0")
    from autoforge.af_api import build_app
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/graphs/tags", json={"name": "x", "tags": ["t"]},
                    headers={"Authorization": "Bearer tok-empty"})
    assert r.status_code == 403 and "subject" in r.text
    r2 = client.post("/api/graphs/tags", json={"name": "x", "tags": ["t"]},
                     headers={"Authorization": "Bearer tok-ok"})
    assert r2.status_code == 200, r2.text[:200]


def test_http_import_endpoint_passes_token_identity(tmp_path, monkeypatch):
    """/api/store/import 同口径：空主体 403、owner 主体放行。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUTOFORGE_TOKENS", json.dumps({
        "tok-empty": {"subject": "", "scopes": ["read", "write"]},
        "tok-ok": {"subject": "owner", "scopes": ["read", "write", "admin"]},
    }))
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "0")
    from autoforge.af_api import build_app
    client = TestClient(build_app(store_root=str(tmp_path)))
    src_store = GraphStore(str(tmp_path))
    _seed(src_store)
    bundle = _bundle(src_store)
    r = client.post("/api/store/import", json={"bundle": bundle, "strategy": "skip"},
                    headers={"Authorization": "Bearer tok-empty"})
    assert r.status_code == 403 and "subject" in r.text
    r2 = client.post("/api/store/import", json={"bundle": bundle, "strategy": "skip"},
                     headers={"Authorization": "Bearer tok-ok"})
    assert r2.status_code == 200, r2.text[:200]


# ── 卡 B/C 腿：引文与留痕（结构判据，不跑子进程）───────────────────


def test_approve_watch_audit_trail_is_recorded(tmp_path):
    """卡 C：approve 后自动起真机 watch ⇒ deploy_audit.jsonl 落一条
    APPROVE_WATCH_AUTO_STARTED（data.live_watch=True）。起监听本身失败（tmp 无 forge 命令）
    不影响断言——外层 except 把失败写进 watch_error，落盘的那条照记。"""
    store = GraphStore(str(tmp_path))
    ps_root = store.root
    svc.submit_pending(store, "af_save", {
        "ir": _make_ir("watchme"), "name": "watchme", "note": "", "tags": None,
        "expect_version": None, "owner": "", "allow_bulk": False,
    }, submitted_by="alice")
    op_id = _first_pending_id(store)
    result = svc.approve_pending(store, op_id, reviewer="auth_code:xx***")
    assert result["ok"], result
    sink = Path(ps_root) / "deploy_audit.jsonl"
    assert sink.exists(), "deploy_audit.jsonl 没出现（审计留痕面）"
    lines = [json.loads(l) for l in sink.read_text(encoding="utf-8").splitlines() if l.strip()]
    approve_watch = [l for l in lines if l.get("type") == "APPROVE_WATCH_AUTO_STARTED"]
    assert approve_watch, f"缺 APPROVE_WATCH_AUTO_STARTED 事件：{[l.get('type') for l in lines]}"
    data = approve_watch[0].get("data") or {}
    assert data.get("live_watch") is True, data
    assert data.get("approved_by") == "auth_code:xx***"


def _first_pending_id(store: GraphStore) -> str:
    listing = svc.list_pending(store)
    assert listing["items"], "pending 队列为空"
    return str(listing["items"][0]["op_id"])


def test_watch_path_comments_cite_20261031_decision():
    """卡 B 腿：`af_service.py` 不再引 2026-10-30（AF-602）当起监听凭据语义的依据；
    四路径口径指向 20261031 §2.2。注释只陈述事实（引文修正，裁定 §2.2 附带①）。"""
    src = Path(svc.__file__).read_text(encoding="utf-8")
    assert "2026-10-30（AF-602）" not in src, (
        "注释仍在引 2026-10-30（AF-602）当依据——裁定 §四 ⛔ 清单第 3 条"
    )
    assert "20261031" in src, "四路径口径应指向 20261031 §2.2"


def test_watch_path_comments_do_not_legislate():
    """卡 B 腿：注释不得替裁定立法——「起监听不二次要码」这类规则句不进注释，
    注释只陈述凭据事实（reviewer/enable 那一跳已收）。"""
    src = Path(svc.__file__).read_text(encoding="utf-8")
    assert "起监听不二次要码" not in src
    assert "对账拉监听不二次要码" not in src
