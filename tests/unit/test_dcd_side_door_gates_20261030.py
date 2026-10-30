"""DCD 裁定 2026-10-30 执行判据：真机旁门三路补码 + authorized_by 进审计流水。

出处 `E:/NAS/关键决策部/decisions/20261030-AF-真机旁门授权码与审计留痕-裁定.md` §三 验收矩阵 8 行。
裁定要点（照抄防漂移）：
- Q1 undo 补码：「授权码的语义是'真机动一次就要一次码'，与方向无关」——undo 是
  真机回放不是"关"，⛔ 借 R4-3 禁用免码的豁免；
- Q2 分两半：自动恢复免码（函数层分开为 _restore_watch_after_restart，⛔ 参数区分）；
  手动入口补码；
- Q3 CLI 补码（forge run --live / forge watch）；
- Q4 authorized_by 进审计流水（deploy_audit.jsonl 随 store 根落盘）；
- Q5 追认（已落地 v1.14.19，本批不动代码，判据已有 test_dcd_archive_name_alias 钉着）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoforge.af_store import GraphStore

try:
    from fastapi.testclient import TestClient
    from autoforge.af_api import build_app
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False


def _code(root) -> str:
    """在与 store 同根的 .auth 签一枚真码（码账本与 UndoStore/watch 同根）。"""
    from autoforge.af_auth import AuthCodeStore
    rec = AuthCodeStore(Path(root) / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    return getattr(rec, "code", None) or rec["code"]


@pytest.fixture()
def live_env(monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")  # 本地逃生舱：闸门测试测的是闸门本身
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "t")
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)


# ── 矩阵 1/2：undo 的码闸（HTTP 面）──────────────────────────────

@pytest.mark.skipif(not _HAVE_FASTAPI, reason="fastapi not installed")
def test_matrix_1_undo_without_code_is_403(tmp_path, live_env):
    """矩阵行 1（开关行）：/api/undo/{id} 无码 → 403「需要授权码」。"""
    from autoforge.af_undo import UndoStore
    store = UndoStore(str(tmp_path))
    store.record("dep-1", {"light.study": {"state": "on", "attributes": {}}})
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/undo/dep-1", json={"confirm": True})
    assert r.status_code == 403, r.text
    assert "授权码" in r.text, f"undo 仍是旁门（AF-601）——回执：{r.text[:160]}"


@pytest.mark.skipif(not _HAVE_FASTAPI, reason="fastapi not installed")
def test_matrix_2_undo_with_code_executes_and_leaves_audit_trail(tmp_path, live_env, monkeypatch):
    """矩阵行 2：带有效码 → 撤销执行 + authorized_by 出现在回执**与审计流水**（Q4）。"""
    import autoforge.af_service as svc
    import autoforge.af_undo as af_undo

    class FakeTransport:
        def __init__(self):
            self.states = {"light.study": ("off", {})}
            self.dispatched: list = []
        def __call__(self, action, params):
            return self.call(action, params)
        def call(self, action, params):
            self.dispatched.append((action, dict(params)))
            from autoforge.af_adapters import CallResult
            return CallResult.ok({"action": action, "result": []})
        def all_states(self):
            return dict(self.states)
        def get_state(self, entity_id):
            found = self.states.get(entity_id)
            return found[0] if found else None

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", lambda url, tok: FakeTransport())
    monkeypatch.setattr(af_undo, "DEFAULT_WINDOW_S", 3600.0)
    from autoforge.af_undo import UndoStore as _US
    _US(str(tmp_path)).record("dep-1", {"light.study": {"state": "on", "attributes": {"brightness": 120}}})

    client = TestClient(build_app(store_root=str(tmp_path)))
    code = _code(tmp_path)
    r = client.post("/api/undo/dep-1", json={"confirm": True, "auth_code": code})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    assert body.get("authorized_by"), "回执要带 authorized_by（AF-403）"
    # Q4 的硬判据：审计流水里有 authorized_by
    audit = (Path(tmp_path) / "deploy_audit.jsonl")
    assert audit.exists(), "审计流水文件没出现（deploy_audit.jsonl 随 store 根）"
    blob = audit.read_text(encoding="utf-8")
    assert "authorized_by" in blob, "authorized_by 没进审计流水（AF-403 后半）"
    assert "UNDO_DEPLOYED" in blob, "撤销事件类型没落流水"


# ── 矩阵 3：serve 自动恢复免码（函数层分开）─────────────────────

def test_matrix_3_auto_restore_is_a_separate_free_function():
    """矩阵行 3：自动恢复=独立函数免码，⛔ 用参数区分（裁定 §四 不得条）。"""
    import ast
    import autoforge.af_service as svc
    src = Path(svc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_restore_watch_after_restart" in names, \
        "自动恢复必须是独立内部函数（裁定 §2.2：函数层分开，参数区分=漏传=旁门重开）"
    # 自动恢复函数体内不得出现码闸调用（免码特例，显式声明）
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_restore_watch_after_restart":
            calls = {ast.unparse(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)}
            assert "_auth_code_check" not in calls, "自动恢复特例免码——加了码 serve 重启就卡死（假武装）"
            assert "_start_watch_impl" in calls, "自动恢复要落到实现体"


def test_matrix_3b_serve_auto_restore_calls_the_internal_path():
    """serve 启动序列调用的是 _restore_watch_after_restart（不是公开 start_watch）。"""
    import autoforge.af_cli as cli
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert "_restore_watch_after_restart" in src, \
        "af_cli serve 自动恢复没改走内部免码函数（裁定 §2.2 前半）"


# ── 矩阵 4：手动 start_watch 补码 ────────────────────────────────

@pytest.mark.skipif(not _HAVE_FASTAPI, reason="fastapi not installed")
def test_matrix_4_manual_watch_start_requires_code(tmp_path, live_env):
    """矩阵行 4：/api/watch/start（dry_live=False）无码 → 403。"""
    client = TestClient(build_app(store_root=str(tmp_path)))
    ir = {"id": "g", "automations": [{"id": "a", "name": "a", "version": 1, "mode": "single",
        "nodes": [{"id": "n", "kind": "on", "trigger": {"type": "state",
                   "entity_id": "binary_sensor.m", "to": "on"}}],
        "edges": []}]}
    r = client.post("/api/watch/start", json={"ir": ir, "dry_live": False})
    assert r.status_code == 403, r.text
    assert "授权码" in r.text, f"手动起监听仍是旁门（AF-602 手动半）——{r.text[:160]}"


def test_matrix_4b_dry_live_watch_stays_free():
    """dry-live 不动设备（do 只记意图）——免码口径与 live_preflight 的 confirm 检查同源。"""
    import ast
    import autoforge.af_service as svc
    src = Path(svc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "start_watch":
            text = ast.unparse(node)
            assert "dry_live" in text and "_auth_code_check" in text
            # 免码分支的显式性：dry_live=True 时跳过码闸
            assert "if not dry_live" in text or "not dry_live:" in text, \
                "dry-live 免码必须是显式分支（不写出来就是暗门）"


# ── 矩阵 5/6：CLI 补码 ──────────────────────────────────────────

def test_matrix_5_cli_live_and_watch_declare_auth_code():
    """矩阵行 5 的形状位：forge run --live 与 forge watch 都有 --auth-code 参数。"""
    import autoforge.af_cli as cli
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert src.count('"--auth-code"') >= 3, (
        "forge run / forge watch / forge undo 三条 CLI 路径都要有 --auth-code "
        f"（现读 {src.count('--auth-code')} 处）——AF-603/601 CLI 侧"
    )
    # run --live 的闸在 live 分支内（dry-live 免码）
    assert '"真机下发（forge run --live）"' in src, "forge run --live 的码闸动作文案要在"
    assert '"真机常驻监听（forge watch）"' in src, "forge watch 的码闸动作文案要在"
    assert '"撤销部署（forge undo' in src, "forge undo 的码闸动作文案要在"


def test_matrix_5b_cli_gate_uses_service_layer_check():
    """CLI 三处码闸都走服务层 _auth_code_check（裁定：⛔ 另造一套校验）。"""
    import autoforge.af_cli as cli
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert src.count("_auth_code_check") >= 3, \
        f"CLI 三条路径都要 import _auth_code_check（现读 {src.count('_auth_code_check')} 处）"


# ── 矩阵 7：Q5 追认（既有判据的回归位）──────────────────────────

def test_matrix_7_archive_name_change_stays_ratifled():
    """矩阵行 7：归档名洗空 → InvalidArchiveName 拒绝（v1.14.19 已落地，DCD 追认——
    本批不动代码，钉住形状防悄悄回退）。"""
    from autoforge.af_store import normalize_archive_name, InvalidArchiveName
    with pytest.raises(InvalidArchiveName):
        normalize_archive_name("../../../etc")


# ── 矩阵 8：disable 仍免码（R4-3 先例不动）──────────────────────

@pytest.mark.skipif(not _HAVE_FASTAPI, reason="fastapi not installed")
def test_matrix_8_disable_still_free(tmp_path, live_env):
    """矩阵行 8：禁用免码——R4-3 先例（裁定 §四：⛔ 动 disable 免码）。"""
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/graphs/disable", json={"tag": "whatever"})
    assert r.status_code == 200, r.text
    assert "授权码" not in r.text, "禁用被码闸挡住＝故障时无法止损（R4-3 反例）"


# ── Q4 补充：live_run 的 authorized_by 也进流水 ─────────────────

def test_live_run_writes_authorized_by_to_audit_trail(tmp_path, live_env, monkeypatch):
    import autoforge.af_service as svc

    class FakeTransport:
        def __init__(self):
            self.states = {"binary_sensor.m": ("on", {}), "light.x": ("off", {})}
        def __call__(self, action, params):
            from autoforge.af_adapters import CallResult
            return CallResult.ok({})
        def all_states(self):
            return dict(self.states)
        def get_state(self, eid):
            found = self.states.get(eid)
            return found[0] if found else None

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY",
                        lambda url, tok: FakeTransport())
    code = _code(tmp_path)
    ir = {"ir_version": "0.3.0", "id": "g", "name": "g", "version": 1, "mode": "single",
          "nodes": [{"id": "a", "kind": "on",
                     "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                    {"id": "d", "kind": "do", "name": "d", "adapter": "ha",
                     "action": "light.turn_on", "params": {"entity_id": "light.x"},
                     "on_error": {"default": "pass"}}],
          "edges": [{"from": "a", "to": "d", "kind": "then"}]}
    svc.live_run(ir, ["light.x"], [{"entity_id": "binary_sensor.m", "state": "on"}],
                 confirm=True, store=GraphStore(str(tmp_path)), auth_code=code)
    audit = Path(tmp_path) / "deploy_audit.jsonl"
    assert audit.exists(), "live_run 没落审计流水"
    blob = audit.read_text(encoding="utf-8")
    assert "LIVE_RUN_EXECUTED" in blob and "authorized_by" in blob, \
        "live_run 的 authorized_by 没进审计流水（AF-403 后半）"
