"""MCP 参数↔schema 门禁必须**能变红**（铁律 #8），而且红的是"下一条改错的参数"。

起因：安全审计包 `fp-authcode-bruteforce` 的加重情节 3（"schema 未声明却可用"）。本文件用一份
合成注册表把四类判据各钉一条反例：消费未声明 / 声明未消费 / 缺 `properties` / 读取判不出来。
最后一类必须是 exit 2 而不是"跳过这一条"——§二之二十七 那条正则版把解析不出的调用点静默丢掉、
于是谎报"缺失 0"，同型事故不许在这个门上重演。控制组（未改坏的那份）必须自己数得出数：
2 个工具、3 个声明、3 个消费，否则"干净"无从谈起。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_mcp_arg_schemas.py"
REAL_REGISTRY = ROOT / "src" / "autoforge" / "af_mcp.py"

HEAD = '''
import svc


def _t_alpha(store, args):
    return svc.alpha(store, args["one"], args.get("two"))


def _t_beta(store, args):
    return svc.beta(store, args.get("x"))


TOOLS = [
    ("af_alpha", "desc", {"type": "object", "properties": {"one": {"type": "string"},
                                                           "two": {"type": "string"}},
                          "required": ["one"]}, _t_alpha, None),
    ("af_beta", "desc", {"type": "object", "properties": {"x": {"type": "string"}}}, _t_beta, "write"),
]
'''


def _module():
    spec = importlib.util.spec_from_file_location("check_mcp_arg_schemas", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(tmp_path: pathlib.Path, text: str) -> tuple[int, str]:
    import io
    import contextlib

    mod = _module()
    path = tmp_path / "af_mcp.py"
    path.write_text(text, encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["check_mcp_arg_schemas.py", str(path)])
    return rc, buf.getvalue()


def test_control_is_green_and_actually_counted(tmp_path):
    """控制组不许是"什么都没看到所以干净"。"""
    rc, out = _run(tmp_path, HEAD)
    assert rc == 0, out
    assert "2 个工具" in out and "声明参数 3 个" in out and "handler 消费 3 个" in out, out


def test_consumed_but_undeclared_is_red(tmp_path):
    """审计点名的那一族：handler 读得到、schema 看不见。"""
    rc, out = _run(tmp_path, HEAD.replace(
        'return svc.beta(store, args.get("x"))',
        'return svc.beta(store, args.get("x"), args.get("allow_sneaky"))',
    ))
    assert rc == 1, out
    assert "af_beta" in out and "allow_sneaky" in out, out


def test_declared_but_unconsumed_is_red(tmp_path):
    """镜像方向：`tools/list` 说可以传，实现里没人接＝对调用方撒谎。"""
    text = HEAD.replace('"x": {"type": "string"}}', '"x": {"type": "string"}, "ghost": {"type": "string"}}')
    rc, out = _run(tmp_path, text)
    assert rc == 1, out
    assert "af_beta" in out and "ghost" in out, out


def test_schema_without_properties_is_red(tmp_path):
    """缺 properties 时 `dispatch()` 的未声明键拒绝在该工具上静默关闭——必须判红，不能判"无从核对所以放过"。"""
    rc, out = _run(tmp_path, HEAD.replace('{"type": "object", "properties": {"x": {"type": "string"}}}',
                                         '{"type": "object"}'))
    assert rc == 1, out
    assert "properties" in out and "静默关闭" in out, out


def test_wholesale_args_forwarding_is_exit_two(tmp_path):
    """判据射程读不成＝exit 2，不是跳过这一条。"""
    rc, out = _run(tmp_path, HEAD.replace('return svc.beta(store, args.get("x"))',
                                         'return svc.beta_all(store, args)'))
    assert rc == 2, out
    assert "整包转发" in out, out


def test_dynamic_key_read_is_exit_two(tmp_path):
    rc, out = _run(tmp_path, HEAD.replace('return svc.beta(store, args.get("x"))',
                                         'return svc.beta(store, args.get("x"), args[key_name])'))
    assert rc == 2, out
    assert "不是字面量" in out, out


def test_missing_tools_anchor_is_exit_two(tmp_path):
    """注册表改名/挪走时不许静默全绿（§二之十四 那次"门自己是假洞"同型）。"""
    rc, out = _run(tmp_path, HEAD.replace("TOOLS = [", "TOOLS_V2 = ["))
    assert rc == 2, out
    assert "没找到" in out, out


def test_handler_defined_in_another_module_is_exit_two(tmp_path):
    rc, out = _run(tmp_path, HEAD.replace("_t_beta, \"write\")", "_t_not_here, \"write\")"))
    assert rc == 2, out
    assert "_t_not_here" in out, out


def test_exempt_marker_suppresses_and_is_counted(tmp_path):
    text = HEAD.replace(
        'def _t_beta(store, args):\n    return svc.beta(store, args.get("x"))',
        'def _t_beta(store, args):\n    # mcp-args: exempt(已裁定：x 由下一版接线)\n'
        '    return svc.beta(store, args.get("x"), args.get("y"))',
    )
    rc, out = _run(tmp_path, text)
    assert rc == 0, out
    assert "现场豁免 1 处" in out, out


def test_real_registry_is_green_with_measured_numbers():
    """真注册表的读数钉在绿行里：31 个工具 / 61 声明 / 61 消费 / 双向差额 0。

    这一条同时是"审计那 6 个未声明键已补齐"的回归位：任何一侧再漂移（新增参数忘声明、
    声明了不接），这里先红。
    """
    import io
    import contextlib

    mod = _module()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["check_mcp_arg_schemas.py", str(REAL_REGISTRY)])
    out = buf.getvalue()
    assert rc == 0, out
    assert "31 个工具" in out and "声明参数 61 个" in out and "handler 消费 61 个" in out, out
    assert REAL_REGISTRY.is_file()


def test_the_six_keys_from_the_audit_are_now_declared():
    """审计口径点名要复核的六个键：`allow_bulk`×3 + `session_id` + `spec`/`prompt`。"""
    import sys

    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from autoforge.af_mcp import TOOLS

    by_name = {t[0]: set((t[2].get("properties") or {}).keys()) for t in TOOLS}
    for tool in ("af_save", "af_enable_by_tag", "af_import_store"):
        assert "allow_bulk" in by_name[tool], tool
    assert "session_id" in by_name["af_draft"]
    assert {"text", "spec", "prompt"} <= by_name["af_compile_spec"]
    # 一次性消耗与失败锁定都写在 schema 里，agent 才可能"知道自己在消耗一枚码"
    auth = by_name["af_save"]
    assert "auth_code" in auth
    pattern = TOOLS[[t[0] for t in TOOLS].index("af_save")][2]["properties"]["auth_code"]["pattern"]
    assert pattern == "^[0-9]{8}$"
