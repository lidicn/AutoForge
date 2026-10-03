"""状态源 fail-closed 门禁必须"能变红"（铁律 #8），而且红的是**下一个实现**。

第七轮那批用 `tests/contract/test_state_provider_policy.py` 把已知的四个实现钉在同口径上，
但契约测试有个结构性缺口：它各自只测自己认识的那几个类，**新加一个 `XxxStateProvider` 忘了
raise，现有四条测试一条都不会红**。本文件钉的是门本身。

射程 = 类里名为 `snapshot` 且"是状态源"的方法，判据取两条信号的**并集**（标注 `-> Snapshot`
或方法体造出 `Snapshot`），不由类名决定：`DeviceCatalog.snapshot() -> dict`、
`VersionManager.snapshot() -> Version` 名字一样但契约不一样，标进射程只会把门变成噪音。
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_snapshot_policy.py"

ANCHOR = '''
class StateProvider(Protocol):
    def snapshot(self, entity_ids):
        """状态源契约：未知实体 → UnknownEntity。"""
        ...
'''


def _module():
    spec = importlib.util.spec_from_file_location("check_snapshot_policy", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check(tmp_path: pathlib.Path, code: str) -> list[str]:
    (tmp_path / "provider.py").write_text(code, encoding="utf-8")
    return _module().check(tmp_path)[0]


# ── 本职：新实现忘了 raise 要红 ───────────────────────────────────────

def test_fail_closed_provider_is_clean(tmp_path):
    assert _check(tmp_path, """
class FakeHA:
    def snapshot(self, entity_ids):
        for e in entity_ids:
            if e not in self.states:
                raise UnknownEntity(e)
        return Snapshot(values=self.states)
""") == []


def test_provider_that_silently_drops_unknown_is_red(tmp_path):
    """第七轮的那个形状：缺的实体被静默省略 ⇒ `all()`/`any()` 短路让它永远不被发现。"""
    findings = _check(tmp_path, """
class NewHA:
    def snapshot(self, entity_ids):
        return Snapshot(values={e: self.states[e] for e in entity_ids if e in self.states})
""")
    assert len(findings) == 1
    assert "NewHA.snapshot()" in findings[0]
    assert "provider.py:" in findings[0]      # 报文件+行号，不是抽象描述


def test_provider_that_invents_domain_default_is_red(tmp_path):
    findings = _check(tmp_path, """
class NewHA:
    def snapshot(self, entity_ids):
        return Snapshot(values={e: self.states.get(e, "off") for e in entity_ids})
""")
    assert len(findings) == 1


def test_raising_the_wrong_exception_is_red(tmp_path):
    """契约认的是 `UnknownEntity`：换一种异常类型，上层按域失效的口径就接不上。"""
    findings = _check(tmp_path, """
class NewHA:
    def snapshot(self, entity_ids):
        for e in entity_ids:
            if e not in self.states:
                raise KeyError(e)
        return Snapshot(values=self.states)
""")
    assert len(findings) == 1


# ── 不误响：不在射程的三种形状 ────────────────────────────────────────

def test_protocol_declaration_is_not_an_implementation(tmp_path):
    """纯声明（docstring + `...`）没有实现可判，否则 `StateProvider` 自己就先红。"""
    assert _check(tmp_path, ANCHOR) == []


def test_other_return_type_is_not_a_state_provider(tmp_path):
    """`DeviceCatalog.snapshot() -> dict`：名字一样、契约不一样。"""
    assert _check(tmp_path, """
class DeviceCatalog:
    def snapshot(self, entity_ids) -> dict:
        return {"missing_ok": True}
""") == []


def test_module_level_function_is_out_of_scope(tmp_path):
    """门判的是**类里的**状态源实现；模块级同名函数不是 provider，静态上也接不到契约。"""
    assert _check(tmp_path, """
def snapshot(entity_ids) -> Snapshot:
    return Snapshot(values={})
""") == []


# ── 射程取并集：只用标注会被"不写标注"绕过 ──────────────────────────

def test_annotation_only_provider_is_in_scope(tmp_path):
    """有 `-> Snapshot` 标注、但快照由工厂造出来：仍然在射程内。"""
    findings = _check(tmp_path, """
class NewHA:
    def snapshot(self, entity_ids) -> Snapshot:
        return self._factory(entity_ids)
""")
    assert len(findings) == 1


def test_repo_own_fail_open_sample_is_in_scope():
    """本仓的反面样本 `_FailOpenProvider` **没有返回标注**——只按标注判，门就看不见它。

    这条不是假想题：判据的并集正是因为扫到自己仓里这个类才加的。
    """
    g = _module()
    src = (ROOT / "tests" / "contract" / "test_state_provider_policy.py").read_text(encoding="utf-8")
    shapes = g._findings(ast.parse(src))
    assert [c for _, c, _ in shapes] == ["_FailOpenProvider"]
    assert shapes[0][2] is False          # 且被正确判为"没有 raise"


# ── 现场豁免：必须带理由 ──────────────────────────────────────────────

def test_exemption_with_reason_clears(tmp_path):
    (tmp_path / "provider.py").write_text("""
class DeliberateOpen:
    def snapshot(self, entity_ids) -> Snapshot:  # fail-closed: exempt(已裁定：只读探测面)
        return Snapshot(values={})
""", encoding="utf-8")
    findings, implemented, exempted = _module().check(tmp_path)
    assert findings == []
    assert implemented == 1
    # 豁免必须单独计数：绿行写"全部抛"就是把没验证的算成验证过（铁律 #5）
    assert exempted == 1


def test_exemption_without_reason_stays_red(tmp_path):
    """空理由的豁免和没有门禁没区别。"""
    (tmp_path / "provider.py").write_text("""
class DeliberateOpen:
    def snapshot(self, entity_ids) -> Snapshot:  # fail-closed: exempt()
        return Snapshot(values={})
""", encoding="utf-8")
    assert len(_module().check(tmp_path)[0]) == 1


def test_other_gate_marker_does_not_clear(tmp_path):
    findings = _check(tmp_path, """
class DeliberateOpen:
    def snapshot(self, entity_ids) -> Snapshot:  # tool-name: exempt(隔壁门的标记)
        return Snapshot(values={})
""")
    assert len(findings) == 1


# ── 锚点读不到不许假绿 ────────────────────────────────────────────────

def test_main_exits_2_when_anchor_missing(tmp_path, monkeypatch):
    g = _module()
    missing = tmp_path / "gone.py"
    monkeypatch.setattr(g, "ANCHOR_MODULE", missing)
    (tmp_path / "provider.py").write_text(ANCHOR, encoding="utf-8")
    assert g.main(["check_snapshot_policy.py", str(tmp_path)]) == 2


def test_main_exits_2_when_protocol_signature_changed(tmp_path, monkeypatch):
    """签名从 `-> Snapshot` 改掉 ⇒ 本门射程判据同时失效，此时报"干净"是假绿。"""
    g = _module()
    anchor_file = tmp_path / "af_state.py"
    anchor_file.write_text(ANCHOR.replace("        ...\n", "        ...\n"), encoding="utf-8")
    monkeypatch.setattr(g, "ANCHOR_MODULE", anchor_file)
    (tmp_path / "provider.py").write_text(ANCHOR, encoding="utf-8")
    assert g.main(["check_snapshot_policy.py", str(tmp_path)]) == 2


def test_anchor_ok_passes_on_repo(tmp_path, monkeypatch):
    g = _module()
    monkeypatch.setattr(g, "ANCHOR_MODULE", ROOT / "src" / "autoforge" / "af_state.py")
    assert g.anchor_ok() is None


# ── HEAD 全仓 ─────────────────────────────────────────────────────────

def test_repo_src_is_clean():
    g = _module()
    findings, implemented, exempted = g.check(ROOT / "src")
    assert findings == []
    assert exempted == 0            # HEAD 现状：没有一处靠豁免过关
    # 四个仿真/生产实现 + `HighFidelityHA`；不写死等号：新增一个 fail-closed 实现不该逼改测试
    assert implemented >= 5
