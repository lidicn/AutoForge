"""工具名单门禁必须"能变红"（铁律 #8），且必须"不误响"。

判据针对的是本批盘"TOOLS→caps 之外还有没有第二份名单"时抓到的两处真形状：
- `af_orchestrator.observe()` 调 `self._call_safe("af_live", …)`，而注册名是 `af_live_run`；
  `_call_safe` 吞异常 ⇒ 这条路**永远不会响**。
- `af_runtime_ext.mcp_tools()` 另抄一份 5 个 `af_*` 名字的字典，从未接线，其中一个叫
  `af_approve_proposal`（Agent 自批提案，与裁定 20261002 §三 ④A 冲突）。

两处都是"手抄第二份、改名时一边不知道"：不报错、不崩，只让该红的不红。所以本文件既要钉
"这类写法会红"，也要钉"日志文案/HA 服务名不会红"——第一版就因为没查调用面而把
`logger.warning("af_persist: …")` 判成红（4 处误报）。一堵会自己响的墙必须先做到不误响。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_tool_names.py"

#: 测试里当作注册表的三个名字（真实注册表有 31 个，形状一样）
TOOLS = {"af_live_run", "af_build", "af_health"}


def _module():
    spec = importlib.util.spec_from_file_location("check_tool_names", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check(tmp_path: pathlib.Path, code: str) -> list[str]:
    """在 tmp 里种一颗单文件小树，返回发现列表（豁免数不进这里，专注判红形状）。"""
    (tmp_path / "caller.py").write_text(code, encoding="utf-8")
    return _module().check(tmp_path, TOOLS)[0]


# ── 本职：改名/抄第二份名单要能红 ────────────────────────────────────

def test_registered_name_is_clean(tmp_path):
    assert _check(tmp_path, """
def _call_safe(tool, **kw):
    ...

def observe(a):
    return _call_safe("af_live_run", id=a)
""") == []


def test_unregistered_name_on_named_call_is_red(tmp_path):
    """`observe()` 那一处：`af_live` 不在注册表里，`_call_safe` 又把它吞成 `{"ok": False}`。"""
    findings = _check(tmp_path, """
def _call_safe(tool, **kw):
    ...

def observe(a):
    return _call_safe("af_live", id=a)
""")
    assert len(findings) == 1
    assert "af_live" in findings[0]
    assert "caller.py:" in findings[0]  # 报文件+行号，不是抽象描述


def test_method_form_is_red(tmp_path):
    """`self.mcp.call("af_x", …)`：接收者是变量、方法名在名单里 ⇒ 判工具名而不判接收者。"""
    findings = _check(tmp_path, """
class Orchest:
    def go(self):
        return self.mcp.call("af_approve_proposal", id=1)
""")
    assert len(findings) == 1
    assert "af_approve_proposal" in findings[0]


def test_dispatch_bare_function_is_red(tmp_path):
    findings = _check(tmp_path, """
def handler(store):
    return dispatch("af_refresh_catelog", {}, store, None)
""")
    assert len(findings) == 1
    assert "af_refresh_catelog" in findings[0]


def test_tool_keyword_argument_is_red(tmp_path):
    findings = _check(tmp_path, """
def handler(store):
    return submit_pending(store, tool="af_liv_run", payload={})
""")
    assert len(findings) == 1


def test_second_mapping_dict_is_red(tmp_path):
    """`mcp_tools()` 当年的形状：`TOOLS` 之外的第二份字典名单。"""
    findings = _check(tmp_path, """
def mcp_tools(grading):
    return {
        "af_list_proposals": lambda: 1,
        "af_approve_proposal": lambda pid: 2,
    }
""")
    assert len(findings) == 1
    assert "第二份工具名单" in findings[0]
    assert "af_approve_proposal" in findings[0]


def test_set_of_tool_names_is_a_second_mapping(tmp_path):
    """`WRITE_TOOLS = {…}` 这一族：集合里手抄两个注册名也是第二真源（改名时另一边不知道）。"""
    findings = _check(tmp_path, """
WRITE_TOOLS = {"af_live_run", "af_build"}
""")
    assert len(findings) == 1
    assert "第二份工具名单" in findings[0]


def test_tuple_of_tool_names_is_a_second_mapping(tmp_path):
    findings = _check(tmp_path, """
LIVE_ONLY = ("af_live_run", "af_health")
""")
    assert len(findings) == 1


def test_list_with_one_tool_name_is_not_a_mapping(tmp_path):
    assert _check(tmp_path, """
SINGLE = ["af_live_run", "scope"]
""") == []


def test_tool_named_default_is_red(tmp_path):
    findings = _check(tmp_path, """
def build(step, tool="af_build_ir"):
    ...
""")
    assert len(findings) == 1
    assert "af_build_ir" in findings[0]


def test_registered_default_is_clean(tmp_path):
    assert _check(tmp_path, """
def build(step, tool="af_build"):
    ...
""") == []


# ── 不误响：形状不对就不该判 ─────────────────────────────────────────

def test_ha_service_name_is_not_judged(tmp_path):
    """`adapter.call("light.turn_on", …)` 是 HA 服务名，另一套命名，不在射程。"""
    assert _check(tmp_path, """
def fire(adapter, states):
    return adapter.call("light.turn_on", {"entity_id": "light.x"})
""") == []


def test_logger_text_is_not_judged(tmp_path):
    """第一版把日志文案判成工具名（4 处误报）：调用面不在名单里就一个字都不判。"""
    assert _check(tmp_path, """
import logging
logger = logging.getLogger("af")

def load(path):
    logger.warning("af_persist: 落盘记录校验和不匹配，跳过 %s", path)
    return None
""") == []


def test_variable_tool_name_is_not_judged(tmp_path):
    """`self._call(tool, …)`：静态读不出变量的值 ⇒ 放过（宁少判不误判）。"""
    assert _check(tmp_path, """
class Orch:
    def go(self, tool):
        return self._call(tool, {})
""") == []


def test_dict_with_one_tool_key_is_not_a_mapping(tmp_path):
    """一个 `af_*` 键可能是别的路由表；"第二份名单"的形状至少得两行。"""
    assert _check(tmp_path, """
HANDLERS = {
    "af_live_run": lambda: 1,
    "health": lambda: 2,
}
""") == []


def test_non_tool_parameter_default_is_not_judged(tmp_path):
    """形参名不是工具形状（`topic`）时，默认值再像 `af_*` 也不判——那是主题/前缀，不是工具名。"""
    assert _check(tmp_path, """
def publish(topic="af_automation_fired", payload=None):
    ...
""") == []


def test_dict_key_that_is_not_a_plain_tool_name_is_not_counted(tmp_path):
    """`"af_persist: 落盘"` 这种带冒号的串不是工具名形状，不能凑进"第二份名单"。"""
    assert _check(tmp_path, """
LABELS = {
    "af_persist: 落盘记录": 1,
    "af_watch: 观察结论": 2,
}
""") == []


# ── 注册表读不到时不许假绿（§二之十四 同型教训）──────────────────────

def test_missing_registry_returns_error(tmp_path, monkeypatch):
    g = _module()
    monkeypatch.setattr(g, "REGISTRY", tmp_path / "nope.py")
    names, err = g.registry_names()
    assert names == set()
    assert err


def test_registry_without_tools_literal_returns_error(tmp_path, monkeypatch):
    reg = tmp_path / "af_mcp.py"
    reg.write_text("HANDLERS = {}\n", encoding="utf-8")
    g = _module()
    monkeypatch.setattr(g, "REGISTRY", reg)
    names, err = g.registry_names()
    assert names == set()
    assert "TOOLS" in err


def test_main_exits_2_when_registry_unreadable(tmp_path, monkeypatch, capsys):
    """改名/挪走 TOOLS 之后，本门必须 exit 2 而不是"0 处发现"——后者是假绿。"""
    g = _module()
    reg = tmp_path / "af_mcp.py"
    reg.write_text("TOOLZ = []\n", encoding="utf-8")
    monkeypatch.setattr(g, "REGISTRY", reg)
    assert g.main(["check_tool_names.py", str(tmp_path)]) == 2
    assert "读不到注册表" in capsys.readouterr().out


# ── 现场豁免：必须带理由，标记前缀不对就不作数 ───────────────────────

def test_exemption_with_reason_clears(tmp_path):
    (tmp_path / "caller.py").write_text("""
def go():
    return _call_safe("af_legacy_name", id=1)  # tool-name: exempt(外部网关的别名，注册表里没有)
""", encoding="utf-8")
    findings, _files, exempted = _module().check(tmp_path, TOOLS)
    assert findings == []
    assert exempted == 1


def test_exemption_without_reason_stays_red(tmp_path):
    """空理由的豁免和没有门禁没区别。"""
    (tmp_path / "caller.py").write_text("""
def go():
    return _call_safe("af_legacy_name", id=1)  # tool-name: exempt()
""", encoding="utf-8")
    findings, _files, exempted = _module().check(tmp_path, TOOLS)
    assert len(findings) == 1
    assert exempted == 0


def test_other_gate_marker_does_not_clear(tmp_path):
    """参数注入门的标记不能拿来豁免工具名——两扇门的理由要各自能被 grep 到。"""
    (tmp_path / "caller.py").write_text("""
def go():
    return _call_safe("af_legacy_name", id=1)  # param-injection: exempt(隔壁门的标记)
""", encoding="utf-8")
    assert len(_module().check(tmp_path, TOOLS)[0]) == 1


# ── HEAD 全仓：门必须干净，锚点必须还在 ─────────────────────────────

def test_repo_registry_still_parses():
    g = _module()
    tools, err = g.registry_names()
    assert err == ""
    assert {"af_live_run", "af_health"} <= tools       # 锚点：这两个名字在注册表里
    assert len(tools) >= 25                            # 读到的不是一个碎片


def test_repo_src_is_clean():
    """两处真缺陷已删（`observe()` 的 `af_live`、`mcp_tools()` 的第二份名单），HEAD 上全绿。"""
    g = _module()
    tools, err = g.registry_names()
    assert err == ""
    findings, files, exempted = g.check(ROOT / "src", tools)
    assert findings == []
    assert files >= 90                  # 扫的是整个 src，不是空树假绿
    assert exempted == 0                # 目前没有任何就地豁免
