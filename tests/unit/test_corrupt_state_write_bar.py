"""第十轮 F10①②③ / F13 那一族在 HEAD 上的收口判据。

失败形状：读侧 `_load` 把「文件存在但解析不出来」静默降级成空快照，写侧 `_persist` / `_save`
是**整档由内存快照重写** ⇒ 一次 `create`、一次成功观测，就把盘上全部历史抹平，连坏字节的
现场一起没了。这四站在 ADM-auditkit 第九轮是 9/9 `data_lost`，而第十、十一轮的修复只存在于
审计方的只读副本 `af-patched`（第十一轮原文：「**原仓库未改动**」）。

判据沿用审计方自己的口径（第十轮 §四、第十一轮 W32）：**只认「写被拒绝」＋「坏字节仍在盘上」**。

反空洞两条：
- 把护栏换成 no-op 后，同一场景必须真的写出「只剩这一条」的空档 ⇒ 证明「没写」是护栏造成的，
  不是别处提前 return；
- 护栏必须装在**真的会落盘的那个方法体内**，且排在写调用之前（第十轮 W30 的错法：装在调用方
  不走的方法上等于没装）。
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from autoforge import af_auth, af_experience
from autoforge.af_auth import AuthCodeStore, PairCodeStore, TokenRegistry
from autoforge.af_experience import ExperienceStore
from autoforge.af_ir import load_graph

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "autoforge"

#: 未闭合 JSON：整档覆盖后「坏字节还在不在」就是决定性读数
CORRUPT = '{"__corrupt": ['
#: 合法 JSON 但形状不对（读侧一样只认自己的形状，认不出就留空）
BAD_LIST = "[1, 2, 3]"
BAD_DICT = '{"a": 1}'

IR = {
    "ir_version": "0.2.1", "id": "e", "name": "e", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [
        {"from": "a", "to": "d", "kind": "then"},
        {"from": "d", "to": "p", "kind": "then"},
    ],
}


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


# ───────────────────────── 站 1：TokenRegistry._persist_issued（F10①）─────────────────────────

def test_issued_registry_control_keeps_both_tokens(tmp_path):
    """CONTROL：健康文件照常读改写，第二次签发不抹第一条。"""
    p = tmp_path / "issued.json"
    t1 = TokenRegistry(issued_path=p).issue_for_agent("alpha")
    t2 = TokenRegistry(issued_path=p).issue_for_agent("beta")
    data = json.loads(_read(p))
    assert set(data) == {t1, t2}


def test_issued_registry_refuses_when_file_corrupt(tmp_path):
    p = tmp_path / "issued.json"
    p.write_text(CORRUPT, encoding="utf-8")
    with pytest.raises(ValueError, match="已损坏，拒绝写入以保护已签发令牌名单"):
        TokenRegistry(issued_path=p).issue_for_agent("alpha")
    # 决定性读数：坏字节还在，既没被盖掉也没被清空
    assert _read(p) == CORRUPT


def test_issued_registry_refuses_when_shape_wrong(tmp_path):
    p = tmp_path / "issued.json"
    p.write_text(BAD_LIST, encoding="utf-8")
    with pytest.raises(ValueError, match="形状不是对象"):
        TokenRegistry(issued_path=p).issue_for_agent("alpha")
    assert _read(p) == BAD_LIST


def test_issued_registry_first_write_is_not_refused(tmp_path):
    """边界：文件不存在是正常首写场景，不该拒。"""
    p = tmp_path / "issued.json"
    assert not p.exists()
    tok = TokenRegistry(issued_path=p).issue_for_agent("alpha")
    assert tok in json.loads(_read(p))


# ───────────────────────── 站 2：PairCodeStore._persist（F10②）─────────────────────────

def test_pair_code_control_accumulates_across_processes(tmp_path):
    p = tmp_path / "pair_codes.json"
    c1 = PairCodeStore(p).create("agentA")
    c2 = PairCodeStore(p).create("agentB")
    assert {rec["code"] for rec in json.loads(_read(p))} == {c1.code, c2.code}


def test_pair_code_refuses_when_file_corrupt(tmp_path):
    p = tmp_path / "pair_codes.json"
    p.write_text(CORRUPT, encoding="utf-8")
    with pytest.raises(ValueError, match="已损坏，拒绝写入以保护已有配对码"):
        PairCodeStore(p).create("agentA")
    assert _read(p) == CORRUPT


def test_pair_code_refuses_when_shape_wrong(tmp_path):
    p = tmp_path / "pair_codes.json"
    p.write_text(BAD_DICT, encoding="utf-8")
    with pytest.raises(ValueError, match="形状不是数组"):
        PairCodeStore(p).create("agentA")
    assert _read(p) == BAD_DICT


def test_pair_code_first_write_is_not_refused(tmp_path):
    p = tmp_path / "pair_codes.json"
    code = PairCodeStore(p).create("agentA")
    assert json.loads(_read(p))[0]["code"] == code.code


def test_pair_code_guard_is_what_blocks_the_wipe(tmp_path, monkeypatch):
    """反空洞：护栏换成 no-op ⇒ 复现原缺陷（新码把盘上已有码整片抹掉）。"""
    monkeypatch.setattr(af_auth, "_refuse_when_list_file_unreadable", lambda path, label: None)
    p = tmp_path / "pair_codes.json"
    old = PairCodeStore(p).create("agentA").code
    p.write_text(CORRUPT, encoding="utf-8")
    new = PairCodeStore(p).create("agentB").code  # 新进程：损坏读不出来 ⇒ 内存里是空的
    recs = json.loads(_read(p))
    assert [r["code"] for r in recs] == [new]
    assert old not in _read(p)


# ───────────────────────── 站 3：AuthCodeStore._persist（F10③）─────────────────────────

def test_auth_code_control_accumulates_across_processes(tmp_path):
    p = tmp_path / "auth_codes.json"
    c1 = AuthCodeStore(p).create("short", 10)
    c2 = AuthCodeStore(p).create("short", 20)
    assert {rec["code"] for rec in json.loads(_read(p))} == {c1.code, c2.code}


def test_auth_code_refuses_when_file_corrupt(tmp_path):
    p = tmp_path / "auth_codes.json"
    p.write_text(CORRUPT, encoding="utf-8")
    with pytest.raises(ValueError, match="已损坏，拒绝写入以保护已有授权码"):
        AuthCodeStore(p).create("short", 10)
    assert _read(p) == CORRUPT


def test_auth_code_refuses_when_shape_wrong(tmp_path):
    p = tmp_path / "auth_codes.json"
    p.write_text(BAD_DICT, encoding="utf-8")
    with pytest.raises(ValueError, match="形状不是数组"):
        AuthCodeStore(p).create("short", 10)
    assert _read(p) == BAD_DICT


def test_auth_code_first_write_is_not_refused(tmp_path):
    p = tmp_path / "auth_codes.json"
    code = AuthCodeStore(p).create("short", 10)
    assert json.loads(_read(p))[0]["code"] == code.code


def test_auth_code_guard_is_what_blocks_the_wipe(tmp_path, monkeypatch):
    """反空洞：授权码站的丢法与配对码同形——一枚新码 = 全部已签发码没了。"""
    monkeypatch.setattr(af_auth, "_refuse_when_list_file_unreadable", lambda path, label: None)
    p = tmp_path / "auth_codes.json"
    old = AuthCodeStore(p).create("long").code
    p.write_text(CORRUPT, encoding="utf-8")
    AuthCodeStore(p).create("short", 10)
    assert old not in _read(p)


# ───────────────────────── 站 4：ExperienceStore._save（F13）─────────────────────────

def test_experience_control_accumulates(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    g = load_graph(IR)
    ExperienceStore(root).observe(g, ok=True)
    ExperienceStore(root).observe(g, ok=True)
    assert json.loads(_read(root / "experience.json"))["observed"] == 2


def test_experience_refuses_when_file_corrupt(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    p = root / "experience.json"
    p.write_text(CORRUPT, encoding="utf-8")
    with pytest.raises(ValueError, match="已损坏，拒绝写入以保护已有经验记录"):
        ExperienceStore(root).observe(load_graph(IR), ok=True)
    assert _read(p) == CORRUPT


def test_experience_refuses_when_shape_wrong(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    p = root / "experience.json"
    p.write_text(BAD_LIST, encoding="utf-8")
    with pytest.raises(ValueError, match="形状不是对象"):
        ExperienceStore(root).observe(load_graph(IR), ok=True)
    assert _read(p) == BAD_LIST


def test_experience_first_write_is_not_refused(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    res = ExperienceStore(root).observe(load_graph(IR), ok=True)
    assert res["updated"] is True
    assert json.loads(_read(root / "experience.json"))["observed"] == 1


def test_experience_clear_still_works_on_a_corrupt_file(tmp_path):
    """`clear()` 是损坏现场的修复出口，不能被同一道护栏挡死。"""
    root = tmp_path / "data"
    root.mkdir()
    (root / "experience.json").write_text(CORRUPT, encoding="utf-8")
    ExperienceStore(root).clear()
    assert json.loads(_read(root / "experience.json"))["observed"] == 0


def test_experience_guard_is_what_blocks_the_wipe(tmp_path, monkeypatch):
    """反空洞：去掉护栏后，一次成功观测把历史计数抹成只剩这次的 1。"""
    monkeypatch.setattr(af_experience, "_refuse_when_dict_file_unreadable", lambda path, label: None)
    root = tmp_path / "data"
    root.mkdir()
    g = load_graph(IR)
    ExperienceStore(root).observe(g, ok=True)
    ExperienceStore(root).observe(g, ok=True)
    p = root / "experience.json"
    p.write_text(CORRUPT, encoding="utf-8")
    ExperienceStore(root).observe(g, ok=True)  # 新进程，读侧只得到空快照
    assert json.loads(_read(p))["observed"] == 1


# ───────────────────────── 结构腿：护栏装在对的方法里、排在写之前 ─────────────────────────

def _fn(module_name: str, cls_name: str | None, fn_name: str) -> ast.FunctionDef:
    tree = ast.parse((SRC / module_name).read_text(encoding="utf-8"))
    if cls_name:
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
        body = cls.body
    else:
        body = tree.body
    return next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == fn_name)


@pytest.mark.parametrize(
    ("module_name", "cls_name", "fn_name", "guard"),
    [
        ("af_auth.py", "PairCodeStore", "_persist", "_refuse_when_list_file_unreadable"),
        ("af_auth.py", "AuthCodeStore", "_persist", "_refuse_when_list_file_unreadable"),
        ("af_experience.py", "ExperienceStore", "_save", "_refuse_when_dict_file_unreadable"),
    ],
)
def test_guard_sits_in_the_writing_method_before_the_write(module_name, cls_name, fn_name, guard):
    """W30 的错法：护栏装在调用方不走的方法上。这里钉「就在这个方法体内、且在写之前」。"""
    fn = _fn(module_name, cls_name, fn_name)
    top_calls = [
        (stmt.value.lineno, ast.unparse(stmt.value.func))
        for stmt in fn.body
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
    ]
    guard_lines = [ln for ln, name in top_calls if name == guard]
    write_lines = [ln for ln, name in top_calls if "write" in name]
    assert len(guard_lines) == 1, f"{cls_name}.{fn_name} 的护栏不在函数体顶层：{top_calls}"
    assert write_lines, f"{cls_name}.{fn_name} 找不到落盘调用"
    assert guard_lines[0] < min(write_lines)


def test_issued_persist_raises_before_the_write(tmp_path):
    """同一条纪律在 `TokenRegistry._persist_issued` 上的形状：拒写在读之后、落盘之前。"""
    fn = _fn("af_auth.py", "TokenRegistry", "_persist_issued")
    refuse_lines = [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Raise) and "拒绝写入" in ast.unparse(n)
    ]
    write_lines = [
        n.args[0].lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_atomic_write_text"
    ]
    assert refuse_lines and write_lines
    assert max(refuse_lines) < min(write_lines)
    # 读侧的静默降级不得复活：`except` 里只能是 raise，不能再退回空字典
    trial = next(n for n in ast.walk(fn) if isinstance(n, ast.Try))
    handler = trial.handlers[0]
    assert all(isinstance(s, ast.Raise) for s in handler.body)
