"""v1.29 第三批 · 场景面 REST 契约（五端点 · 正反形状）。

被测：`af_api.build_app` 的 `/api/scenes` 五条路由。它们**只是 HTTP 翻译层**——
归属／范围／授权判定真源全在 `af_service.scene_*`（与 MCP `af_mcp._t_scene` 同闸）。
本文件钉的是「翻译得对不对」，⛔ 不重测服务层判定：

1. **归属两条腿行为相反**（⑩）：owner 面全量且**不出现** `hidden_count` 键；
   非 owner 面只自己名下且**必报**该键（含「归属者·非 owner 面」这条中间腿）；
2. **写面必须显式**：`replace=false` 撞 scene_id ⇒ 409。场景是「一句话触发」的入口，
   静默覆盖等于把用户的「回家模式」换成另一个、触发词还指着它——必须是显式动作；
3. **删＝归档**：源文件消失、`.scenes/deleted/` 多一份（⛔ 不物理删：误删无从恢复）；
4. **单对象读面 404 逐字同形**：他人场景与不存在**同一文案**（存在性 oracle 闭，
   ⛔ 不点归属者名——那是批量采集全库归属者的通道）；
5. **run 不许假绿**：无 grant_id／live 未启用一律 4xx，文案含中文可读原因。

⛔ 本文件不连真机：run 的成功腿在服务层用注入假 executor 测（`scene_run(executor=...)`），
REST 面**不伪造 200**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoforge import af_service
from autoforge.af_api import build_app
from autoforge.af_store import GraphStore

OWNER_SUBJECT = "owner"   # 真 owner 面（_OWNER_SUBJECTS）
DSH = "dsh"               # 腿 A：归属者（名下两条）· 非 owner 面
NON_OWNER = "dsh-pm"      # 腿 B：非 owner 且只自己一条

#: 读拒文案的**字面量模板**（独立期望，⛔ 不引用实现函数）。
#: 真源是 `af_ownership.visible_error` 的 f-string：`没找到归档 {name!r}（或它不属于你）`。
#: 实现若改模板 ⇒ 这里红 ⇒ 有意照面（判据必须独立于被检对象，台账 1.28）。
VISIBLE_TEMPLATE = "没找到归档 '«NAME»'（或它不属于你）"


def _denamed(detail: str, name: str) -> str:
    """只把**请求名**替换成占位符：同形比较用，⛔ 不做正则兜底。"""
    return detail.replace(f"'{name}'", "'«NAME»'")


def _scene_ir(scene_id: str, name: str, *, aliases: list[str] | None = None) -> dict:
    """最小合法 SceneDef IR（键集必须落在 `af_scene_kit._DEF_KEYS` 内，⛔ 多一个即被拒）。"""
    return {
        "kind": "scene_def",
        "scene_id": scene_id,
        "name": name,
        "aliases": list(aliases or []),
        "version": 1,
        "steps": [
            {
                "step_id": "step_1",
                "adapter": "ha",
                "action": "light.turn_on",
                "target": "light.living_room",
                "params": {},
                "on_error": "abort",
                "delay_ms_after": 0,
            }
        ],
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """三条场景：`living_home`／`sleep_mode` 挂 `dsh` 名下；`mine_scene` 挂 `dsh-pm`。

    形状复刻线上：`owner`＝真 owner 面（全量、⛔ 无缺口键）；`dsh`＝归属者·非 owner 面
    （2/3 ⇒ hidden=1）；`dsh-pm`＝非 owner 只自己一条（1/3 ⇒ hidden=2）。
    """
    store_root = tmp_path / "store"
    store = GraphStore(str(store_root))
    for scene_id, name, owner in (
        ("living_home", "回家模式", DSH),
        ("sleep_mode", "睡觉模式", DSH),
        ("mine_scene", "我的场景", NON_OWNER),
    ):
        af_service.scene_def_save(
            store, _scene_ir(scene_id, name),
            caller=owner, caller_is_owner=False, token_identity=True,
        )

    tokens = {
        "tok-owner": {"subject": OWNER_SUBJECT, "scopes": ["read", "write", "live"]},
        "tok-dsh": {"subject": DSH, "scopes": ["read", "write"]},
        "tok-agent": {"subject": NON_OWNER, "scopes": ["read", "write"]},
    }
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps(tokens))
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    monkeypatch.delenv("AUTOFORGE_LIVE_ENABLED", raising=False)

    client = TestClient(build_app(store_root=str(store_root)))
    return {
        "client": client, "store": store, "root": store_root,
        "owner": {"Authorization": "Bearer tok-owner"},
        "dsh": {"Authorization": "Bearer tok-dsh"},
        "agent": {"Authorization": "Bearer tok-agent"},
    }


# ── ① GET /api/scenes：owner 面 ⛔ 不报 hidden_count ──────────────────

def test_list_owner_face_omits_hidden_count(env):
    r = env["client"].get("/api/scenes", headers=env["owner"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["scene_id"] for s in body["scenes"]] == ["living_home", "mine_scene", "sleep_mode"]
    assert "hidden_count" not in body, "owner 面没有藏起来的东西，⛔ 不报缺口键（⑩ 两条腿正相反）"


def test_list_non_owner_face_reports_hidden_count(env):
    r = env["client"].get("/api/scenes", headers=env["agent"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["scene_id"] for s in body["scenes"]] == ["mine_scene"]
    assert body["hidden_count"] == 2, body.get("hidden_count")


def test_list_owner_of_all_reports_own_gap(env):
    """腿 A（归属者·非 owner 面）：名下 2/3 ⇒ hidden_count==1。

    ⛔ 这条和 owner 面正相反——「有归属 ≠ owner 面」，缺口必须照报。
    """
    body = env["client"].get("/api/scenes", headers=env["dsh"]).json()
    assert [s["scene_id"] for s in body["scenes"]] == ["living_home", "sleep_mode"]
    assert body["hidden_count"] == 1


def test_list_summary_excludes_steps_but_keeps_step_count(env):
    """列表要轻：⛔ 不带 steps 全文，但步数必须单独给（否则前端画不出「几步」）。"""
    body = env["client"].get("/api/scenes", headers=env["owner"]).json()
    row = next(s for s in body["scenes"] if s["scene_id"] == "living_home")
    assert "steps" not in row
    assert row["step_count"] == 1


# ── ② POST /api/scenes：写面必须显式 ─────────────────────────────────

def test_save_new_scene_persists_and_stamps_owner(env):
    r = env["client"].post(
        "/api/scenes", headers=env["dsh"], json={"scene": _scene_ir("new_scene", "新场景")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scene_id"] == "new_scene"
    assert body["owner"] == DSH, "owner 取调用主体（⛔ agent 不能自签）"
    assert (env["root"] / ".scenes" / "defs" / "new_scene.json").is_file()


def test_save_duplicate_without_replace_is_409(env):
    r = env["client"].post(
        "/api/scenes", headers=env["dsh"],
        json={"scene": _scene_ir("living_home", "另一个回家")},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "living_home" in detail, detail
    assert "replace" in detail, "拒因必须说清怎么改（显式 replace=true）"

    # 原文件未被静默覆盖：那等于把用户的「回家模式」换掉而触发词还指着它
    on_disk = json.loads(
        (env["root"] / ".scenes" / "defs" / "living_home.json").read_text("utf-8"))
    assert on_disk["name"] == "回家模式"


def test_save_with_replace_overwrites(env):
    r = env["client"].post(
        "/api/scenes", headers=env["dsh"],
        json={"scene": _scene_ir("living_home", "回家模式 v2"), "replace": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "回家模式 v2"
    on_disk = json.loads(
        (env["root"] / ".scenes" / "defs" / "living_home.json").read_text("utf-8"))
    assert on_disk["name"] == "回家模式 v2", "replace=true 必须真落盘"


def test_save_rejects_unknown_key(env):
    """构造期 fail-closed 一路透到 HTTP：多一个未声明键 ⇒ 400（⛔ 静默丢弃）。"""
    bad = _scene_ir("bad_scene", "坏场景")
    bad["trigger"] = "回家"        # 未声明键
    r = env["client"].post("/api/scenes", headers=env["dsh"], json={"scene": bad})
    assert r.status_code == 400, r.text
    assert "trigger" in r.json()["detail"]


# ── ③ GET /api/scenes/{scene_id}：单对象读面 404 逐字同形 ─────────────

def test_get_owner_face_returns_full_steps(env):
    r = env["client"].get("/api/scenes/living_home", headers=env["owner"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scene_id"] == "living_home"
    assert len(body["steps"]) == 1
    assert body["steps"][0]["action"] == "light.turn_on"


def test_get_foreign_and_missing_are_byte_identical_404(env):
    """存在性 oracle 闭：他人场景与不存在**同一文案**（⛔ 不点归属者名）。"""
    foreign = env["client"].get("/api/scenes/living_home", headers=env["agent"])
    missing = env["client"].get("/api/scenes/no_such_scene", headers=env["agent"])

    assert foreign.status_code == 404, foreign.text
    assert missing.status_code == 404, missing.text
    denamed_foreign = _denamed(foreign.json()["detail"], "living_home")
    denamed_missing = _denamed(missing.json()["detail"], "no_such_scene")
    assert denamed_foreign == denamed_missing, "他人场景与不存在必须逐字同形"
    assert denamed_foreign == VISIBLE_TEMPLATE, denamed_foreign
    # 去名后不得残留归属者（存在性 oracle 泄露 ⇒ 红）
    for subject in (OWNER_SUBJECT, DSH, NON_OWNER):
        assert subject not in denamed_foreign, denamed_foreign


# ── ④ DELETE /api/scenes/{scene_id}：删＝归档 ────────────────────────

def test_delete_archives_instead_of_physically_deleting(env):
    src = env["root"] / ".scenes" / "defs" / "mine_scene.json"
    deleted_dir = env["root"] / ".scenes" / "deleted"
    assert src.is_file()
    before = len(list(deleted_dir.glob("*.json"))) if deleted_dir.is_dir() else 0

    r = env["client"].delete("/api/scenes/mine_scene", headers=env["agent"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scene_id"] == "mine_scene"
    assert body["deleted"] is True
    assert Path(body["archived_to"]).is_file(), f"归档文件不存在：{body['archived_to']}"
    assert not src.is_file(), "源文件必须消失（⛔ 留在扫描目录＝每 tick 重现在列表里）"
    assert len(list(deleted_dir.glob("*.json"))) == before + 1


def test_delete_foreign_scene_is_403(env):
    r = env["client"].delete("/api/scenes/living_home", headers=env["agent"])
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert DSH not in detail, f"写拒文案⛔ 不点归属者名（存在性 oracle）：{detail}"
    assert (env["root"] / ".scenes" / "defs" / "living_home.json").is_file(), "被拒不得动文件"


# ── ⑤ POST /api/scenes/{scene_id}/run：⛔ 不许 200 假绿 ───────────────

def test_run_without_grant_id_is_4xx_with_readable_reason(env):
    r = env["client"].post(
        "/api/scenes/living_home/run", headers=env["dsh"], json={"agent": "agent-1"})
    assert r.status_code == 403, r.text
    assert "授权" in r.json()["detail"]


def test_run_with_live_disabled_is_4xx_not_200(env):
    """有 grant 但 live 未启用 ⇒ 403。

    ⛔ 不许静默「假装成功」：场景每一步都是真设备动作，没有 dry 语义
    （dry 走 `executor=` 注入假实现，那是服务层测试的事）。
    """
    grant = af_service.grant_issue(
        env["store"], agent="agent-1", rooms=["客厅"],
        caller=OWNER_SUBJECT, caller_is_owner=True,
    )
    r = env["client"].post(
        "/api/scenes/living_home/run", headers=env["dsh"],
        json={"agent": "agent-1", "grant_id": grant["grant_id"]},
    )
    assert r.status_code == 403, r.text
    assert "未启用真机下发" in r.json()["detail"]
    assert "AUTOFORGE_LIVE_ENABLED" in r.json()["detail"], "文案要能照着修（说清缺哪个开关）"


def test_run_unknown_scene_is_404(env):
    r = env["client"].post("/api/scenes/no_such_scene/run", headers=env["dsh"], json={})
    assert r.status_code == 404, r.text


def test_run_without_agent_is_400_when_anonymous(tmp_path, monkeypatch):
    """无令牌（本地逃生舱）时 caller 为空 ⇒ ⛔ 不能拿 caller 兜 agent：400 且文案可读。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    monkeypatch.delenv("AF_ACCESS_TOKENS", raising=False)
    store_root = tmp_path / "anon"
    af_service.scene_def_save(
        GraphStore(str(store_root)), _scene_ir("s1", "S1"),
        caller=OWNER_SUBJECT, caller_is_owner=True, token_identity=True,
    )
    client = TestClient(build_app(store_root=str(store_root)))
    r = client.post("/api/scenes/s1/run", json={"grant_id": "g-not-a-real-grant"})
    assert r.status_code == 400, r.text
    assert "agent" in r.json()["detail"]
