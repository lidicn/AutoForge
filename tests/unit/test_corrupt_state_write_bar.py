"""第十轮 F10①②③ / F11 / F13 那一族在 HEAD 上的收口判据。

失败形状：读侧 `_load` 把「文件存在但解析不出来」静默降级成空快照，写侧 `_persist` / `_save` /
`_rewrite_all` 是**整档由内存快照重写** ⇒ 一次 `create`、一次成功观测、一次正常压缩，就把盘上
全部历史抹平，连坏字节的现场一起没了。这七站在 ADM-auditkit 第九轮是 9/9 `data_lost`，而第十、
十一、十二轮的修复只存在于审计方的只读副本 `af-patched`（第十一轮原文：「**原仓库未改动**」）。

判据沿用审计方自己的口径（第十轮 §四、第十一轮 W32、第十二轮 W34）——按档位分两种正确修法：
- 第 1/2 档（令牌、配对码、授权码、经验、凭据）：**抛异常拒写** ⇒ 坏字节仍在盘上；
- 第 3 档（遥测）：**静默跳过写入**（不能抛穿打断解析），但必须在宽 `except` 外面留痕。

反空洞两条：
- 把护栏换成 no-op / 把标志拆掉后，同一场景必须真的写出「只剩这一条」的空档 ⇒ 证明「没写」是
  护栏造成的，不是别处提前 return；
- 护栏必须装在**真的会落盘的那个方法体内**，且排在写调用之前（第十轮 W30 的错法：装在调用方
  不走的方法上等于没装）；第十二轮 §一 的错法：留痕待在自己的 `except Exception: return` 里
  等于没留。
"""

from __future__ import annotations

import ast
import json
import logging
import pathlib

import pytest

from autoforge import af_auth, af_experience
from autoforge.af_auth import AuthCodeStore, PairCodeStore, TokenRegistry
from autoforge.af_catalog import DeviceCatalog
from autoforge.af_config import Config
from autoforge.af_experience import ExperienceStore
from autoforge.af_ir import load_graph
from autoforge.af_preference import PREFERENCES_FILE, PreferenceModel

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


# ───────────────────────── 站 5：Config.update_credentials（第十轮那处凭证站）─────────────────────────

def _creds_file(root):
    return root / "credentials.json"


def test_config_control_keeps_the_other_credential(tmp_path):
    p = _creds_file(tmp_path)
    p.write_text(json.dumps({"ha_token": "H", "api_token": "A"}), encoding="utf-8")
    Config(tmp_path).update_credentials(ha_token="H2")
    assert json.loads(_read(p)) == {"ha_token": "H2", "api_token": "A"}


def test_config_refuses_when_it_never_read_credentials(tmp_path):
    p = _creds_file(tmp_path)
    p.write_text(CORRUPT, encoding="utf-8")
    with pytest.raises(ValueError, match="拒绝写入以免抹掉盘上其余凭据"):
        Config(tmp_path).update_credentials(ha_token="H")
    assert _read(p) == CORRUPT


def test_config_refuses_when_shape_wrong(tmp_path):
    p = _creds_file(tmp_path)
    p.write_text(BAD_LIST, encoding="utf-8")
    with pytest.raises(ValueError, match="拒绝写入以免抹掉盘上其余凭据"):
        Config(tmp_path).update_credentials(ha_token="H")
    assert _read(p) == BAD_LIST


def test_config_first_write_is_not_refused(tmp_path):
    """边界：文件不存在是正常首建场景，不该拒（否则新部署第一次配令牌就写不进去）。"""
    assert not _creds_file(tmp_path).exists()
    Config(tmp_path).update_credentials(ha_token="H")
    assert json.loads(_read(_creds_file(tmp_path)))["ha_token"] == "H"


def test_config_repairs_the_file_when_memory_holds_a_snapshot(tmp_path):
    """R19-01 的另一半：启动时读成功、之后文件被外部写坏 ⇒ 内存有完整快照，这时**该写**
    （整档重写恰好是把好凭据写回盘上的修复动作）。拒写只针对"本进程一份都没读到过"。"""
    p = _creds_file(tmp_path)
    p.write_text(json.dumps({"ha_token": "H", "api_token": "A"}), encoding="utf-8")
    cfg = Config(tmp_path)
    p.write_text(CORRUPT, encoding="utf-8")
    cfg.refresh()  # 读失败 ⇒ 保留内存凭据（R19-01）
    assert cfg.get_api_token() == "A"
    cfg.update_credentials(ha_token="H2")  # 不拒
    assert json.loads(_read(p))["api_token"] == "A"


def test_config_guard_is_what_blocks_the_wipe(tmp_path):
    """反空洞：把标志拆掉 ⇒ 一枚新令牌把盘上那份 api_token 抹没。"""
    p = _creds_file(tmp_path)
    p.write_text('{"api_token": "A"', encoding="utf-8")  # 未闭合，形状坏
    cfg = Config(tmp_path)
    assert cfg._creds_unreadable is True
    cfg._creds_unreadable = False  # 拆护栏
    cfg.update_credentials(ha_token="H")
    assert "api_token" not in _read(p)


# ───────────────────────── 站 6：DeviceCatalog._record_bucket（F11，第 3 档遥测）─────────────────────────

def _metrics_file(root):
    return root / ".catalog" / "resolve_metrics.json"


def _catalog(tmp_path):
    return DeviceCatalog(tmp_path, fetch_all=lambda: {})


def test_metrics_control_accumulates(tmp_path):
    cat = _catalog(tmp_path)
    cat._record_bucket("exact")
    cat._record_bucket("exact")
    cat._record_bucket("medium")
    data = json.loads(_read(_metrics_file(tmp_path)))
    assert data["total"] == 3
    assert data["buckets"] == {"exact": 2, "medium": 1}


def test_metrics_skips_the_write_and_keeps_the_scene(tmp_path, caplog):
    """第 3 档的正确修法（第十二轮 W34）：静默跳过写入，不抛异常打断解析。
    决定性读数：坏字节还在盘上 ⇒ 写入确实没发生。"""
    p = _metrics_file(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CORRUPT, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert _catalog(tmp_path)._record_bucket("exact") is None
    assert _read(p) == CORRUPT
    assert "RESOLVE_METRICS_UNREADABLE" in caplog.text


def test_metrics_log_survives_the_swallowing_try(tmp_path, caplog):
    """第十二轮 §一 那一族：留痕必须写在宽 `except Exception: return` 的覆盖范围**外面**。
    这里让落盘本身炸掉（`resolve_metrics.json` 的位置是个目录），确认"遥测不影响解析"仍然成立，
    且不抛穿 —— 同时"写失败"和"读不出来"是两档，不能混报。"""
    p = _metrics_file(tmp_path)
    p.mkdir(parents=True)  # 该位置被占成目录 ⇒ 原子写必然失败
    with caplog.at_level(logging.ERROR):
        assert _catalog(tmp_path)._record_bucket("exact") is None  # 不抛穿 = 契约
    assert "RESOLVE_METRICS_UNREADABLE" not in caplog.text  # 失败≠读不出来，两档不混


def test_metrics_first_write_is_not_skipped(tmp_path):
    assert not _metrics_file(tmp_path).exists()
    _catalog(tmp_path)._record_bucket("exact")
    assert json.loads(_read(_metrics_file(tmp_path)))["total"] == 1


def test_metrics_bad_shape_is_not_treated_as_empty(tmp_path):
    """形状不对（合法 JSON 但不是对象）同样跳过：读侧认不出它，写侧就会整档盖掉。"""
    p = _metrics_file(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(BAD_LIST, encoding="utf-8")
    _catalog(tmp_path)._record_bucket("exact")
    assert _read(p) == BAD_LIST


# ───────────────────────── 站 7：PreferenceModel._rewrite_all（F13）─────────────────────────

def _pref_file(root):
    return root / PREFERENCES_FILE


def test_preference_control_compacts_without_loss(tmp_path):
    pm = PreferenceModel(tmp_path)
    for i in range(3):
        pm.record_accept(f"light.turn_on_{i}", automation_id="a1")
    pm._rewrite_all()
    assert len(_read(_pref_file(tmp_path)).strip().splitlines()) == 3
    assert len(PreferenceModel(tmp_path)._records) == 3


def test_preference_refuses_compaction_when_unreadable(tmp_path):
    """第九轮 F13 的确证形状：整档被写成未闭合 JSON ⇒ 一行都读不出来，压缩会把明细抹平。"""
    p = _pref_file(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CORRUPT, encoding="utf-8")
    pm = PreferenceModel(tmp_path)
    assert pm._records_unreadable is True
    pm._rewrite_all()
    assert _read(p) == CORRUPT


def test_preference_append_still_works_on_an_unreadable_file(tmp_path):
    """拒写只针对整档重写；追加式写入只加一行，不动盘上已有的字节 ⇒ 不拦。"""
    p = _pref_file(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CORRUPT, encoding="utf-8")
    pm = PreferenceModel(tmp_path)
    pm.record_accept("light.turn_on", automation_id="a1")
    text = _read(p)
    assert text.startswith(CORRUPT)          # 原字节还在
    assert "light.turn_on" in text           # 新记录追加上了


def test_preference_full_clear_is_the_repair_exit(tmp_path):
    """显式全量清空是修复现场的动作，允许越过拒写护栏。"""
    p = _pref_file(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CORRUPT, encoding="utf-8")
    PreferenceModel(tmp_path).clear()
    assert _read(p) == ""


def test_preference_partial_clear_still_refuses(tmp_path):
    """按 automation 过滤的部分清空同样是整档重写 ⇒ 不越过护栏。"""
    p = _pref_file(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CORRUPT, encoding="utf-8")
    PreferenceModel(tmp_path).clear("a1")
    assert _read(p) == CORRUPT


def test_preference_guard_is_what_blocks_the_wipe(tmp_path):
    """反空洞：把标志拆掉 ⇒ 压缩真的把盘上那份换成"只剩这一条"。"""
    p = _pref_file(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"context": "k", "action": "old.action", "accepted": true}\n', encoding="utf-8")
    pm = PreferenceModel(tmp_path)
    assert pm._records_unreadable is False
    p.write_text(CORRUPT, encoding="utf-8")  # 外部把整档写坏
    pm.record_accept("light.turn_on", automation_id="a1")
    pm._records_unreadable = False  # 新进程视角下标志本该为 True，这里拆掉护栏复现丢法
    pm._rewrite_all()
    assert "old.action" not in _read(p)
    assert CORRUPT not in _read(p)


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


@pytest.mark.parametrize(
    ("module_name", "cls_name", "fn_name", "guard", "writer"),
    [
        # 后三站不是"调用一个 _refuse_* 助手"的形状，而是标志/哨兵返回值驱动的拒写：
        # 判据同一条——拒写的那个语句必须排在真的落盘调用之前，且在同一方法体内。
        ("af_config.py", "Config", "update_credentials", "抹掉盘上其余凭据", "_atomic_write"),
        ("af_preference.py", "PreferenceModel", "_rewrite_all", "_records_unreadable", "atomic_write_text"),
        ("af_catalog.py", "DeviceCatalog", "_bump_metrics_bucket", "unreadable", "atomic_write_text"),
    ],
)
def test_flag_guard_sits_in_the_writing_method_before_the_write(
    module_name, cls_name, fn_name, guard, writer
):
    fn = _fn(module_name, cls_name, fn_name)
    guard_lines = [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, (ast.If, ast.Raise, ast.Return)) and guard in ast.unparse(n)
    ]
    write_lines = [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and writer in ast.unparse(n.func)
    ]
    assert guard_lines, f"{cls_name}.{fn_name} 里找不到拒写分支：{guard}"
    assert write_lines, f"{cls_name}.{fn_name} 里找不到落盘调用：{writer}"
    assert max(guard_lines) < min(write_lines), (
        f"{cls_name}.{fn_name}：护栏（{max(guard_lines)}）排在落盘（{min(write_lines)}）之后 = 没装"
    )


def test_metrics_trace_sits_outside_the_swallowing_try():
    """第十二轮 §一 的结构纪律：留痕不能待在那个会吞掉它自己的 `except Exception` 里面。"""
    fn = _fn("af_catalog.py", "DeviceCatalog", "_record_bucket")
    trials = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert trials, "找不到那个保护遥测的 try"
    swallowed = [n for n in trials if any(
        isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in n.handlers
    )]
    assert swallowed, "try 的处理器不再是宽 except Exception，本判据的形状需要重审"
    for stmt in fn.body:  # 只看函数体顶层：钻进 try 里就不算留痕
        if isinstance(stmt, (ast.Expr, ast.If)) and "RESOLVE_METRICS_UNREADABLE" in ast.unparse(stmt):
            assert all(stmt.lineno > t.end_lineno for t in swallowed)
            break
    else:
        pytest.fail("RESOLVE_METRICS_UNREADABLE 的留痕不在函数体顶层（会被宽 except 吞掉）")
