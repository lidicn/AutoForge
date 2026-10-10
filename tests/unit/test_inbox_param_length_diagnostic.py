"""§十八 残余 B.9 的后半格：收件箱载荷**超长**在编译期先拒（`INBOX_PARAM_TOO_LONG`）。

缺口形状（收口前）：长度上限一直只有运行期那一半在把——`af_mqtt_bridge.inbox_publish` 把
`params` 原样交给 `homesdk.presence.<kind>`，库侧 `_len_bounded`（`presence.py:209`）超长抛
`ValueError` ⇒ `ADM_ERR_PAYLOAD_INVALID`。于是这条 IR 能编译、能进库、能进演练台账，**要跑到
执行才红**。而契约 §1.3 已写明 DB 侧对超长载荷 fail-closed 丢弃并审计——这条动作**发出去也必死**，
所以级别取 ERROR（与 `L2_NEEDS_CANARY` 同族），不是 #87 那种"能跑、只是图里读不出来"的 WARN。

判据射程（缺一格就是这条诊断白建，或者更糟——成为第二份会漂移的上限）：
1. 该码注册进 `CHECKS` + `CODE_HINT`（#86/#87 立的纪律：不注册 ⇒ `hint` 静默塌成空串）。
2. **上限不在 AF 写数字**：登记表格子里放的就是库侧那三枚常量本身（`from homesdk.presence import INBOX_MAX_*`），
   扫描器那一侧一个整数字面量都不许出现。
3. **同源**：`INBOX_LEN_LIMITS` 的 `(kind → 字段)` 键集，以及每格值与 `_len_bounded(arg, CONST, "field")`
   里那枚 `CONST` 的现读值，必须与 `homesdk.presence` 源码逐枚相等——库侧改名、换常量、给某个 builder
   新增一枚受限字段，这条腿先红（编译期闸不会悄悄落后于运行期那道闸）。
4. 边界成对：恰好等于上限**不报**、上限 +1 **报**（差一就是误杀合法 IR 或放过必死 IR）。
5. 逐字段发：`notify` 的 `title` 与 `body` 同时超长 ⇒ 两条，不是一条含糊的"载荷有问题"。
6. 不误伤：非 `inbox` 适配器、非字符串取值、未登记的动作名都不报（名单真源在桥，不在这里抄）。
7. ERROR 真的拦发布（`ScanResult.ok` 为假）——与 #87 那条 WARN 的口径相反，两级别别混。
8. **编译期量的就是运行期那份长度**：执行器把 `node.params` 原样交给适配器（`af_executor.py:741`），
   运行期不做插值；同一份超长 `params` 在两端都判红。

反例族（红才说明判据有牙）：删 CHECKS/CODE_HINT 项、把表里的常量换成数字、把 `>` 写成 `>=`、
删掉逐字段循环（只报第一条）、把级别改成 WARNING、删 `spawn` 那类的调用站、执行器改成运行时拼参数。
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest
from homesdk import presence as _presence
from homesdk.adm.errors import ADM_ERR_PAYLOAD_INVALID

from autoforge.af_adapters.inbox import INBOX_LEN_LIMITS, InboxAdapter, inbox_overlong_fields
from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import CHECKS, CODE_HINT, ERROR, StaticScanner

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "autoforge"

CODE = "INBOX_PARAM_TOO_LONG"
#: 三枚主题 kind（= `butler/inbox/<kind>` 的后缀），与库侧 builder 同名
KINDS = ("speak", "notify", "tv")


def _limit(kind: str, field: str) -> int:
    """上限**现读**库侧常量（经 `INBOX_LEN_LIMITS` 那格）——本文件也不写数字，否则判据自己就成了第三份手抄面。"""
    return int(INBOX_LEN_LIMITS[kind][field])


def _long(kind: str, field: str, extra: int = 1) -> str:
    return "字" * (_limit(kind, field) + extra)


def _at_limit(kind: str, field: str) -> str:
    return "字" * _limit(kind, field)


def _inbox_do(action: str, params: dict, **extra: object) -> dict:
    node = {"id": "d1", "kind": "do", "adapter": "inbox", "action": action, "params": params}
    node.update(extra)
    return node


def _scan(do_node: dict, extra_nodes: list[dict] | None = None):
    data = {
        "ir_version": "0.2.1",
        "id": "inbox_len",
        "name": "收件箱长度预拒",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            do_node,
            {"id": "p1", "kind": "pass"},
        ] + (extra_nodes or []),
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }
    return StaticScanner(graph=Graph([load_automation(data)])).scan()


def _hits(result) -> list:
    return [d for d in result.diagnostics if d.code == CODE]


def _scanner_method(fn_name: str = "_check_inbox_payload_len") -> ast.FunctionDef:
    tree = ast.parse((SRC / "af_scanner.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return node
    raise AssertionError(f"读不到 {fn_name}（调用站射程塌了，不是没问题）")


def _library_bounded_fields() -> dict[str, dict[str, str]]:
    """从库侧源里解析 `speak`/`notify`/`tv` 的 `_len_bounded(arg, CONST, "field")` 三元组。

    顶层函数扫（builder 都在顶层）；`ast.walk` 取调用点，不靠正则数引号。
    """
    tree = ast.parse(Path(_presence.__file__).read_text(encoding="utf-8"))
    found: dict[str, dict[str, str]] = {}
    for fn in tree.body:
        if not (isinstance(fn, ast.FunctionDef) and fn.name in KINDS):
            continue
        pairs: dict[str, str] = {}
        for call in ast.walk(fn):
            if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "_len_bounded":
                arg, const, field = call.args[0], call.args[1], call.args[2]
                assert isinstance(arg, ast.Name), f"{fn.name}：`_len_bounded` 第一参不是裸变量，映射表要重核"
                assert isinstance(const, ast.Name), f"{fn.name}：上限不再是库侧常量名（{ast.dump(const)}）"
                assert isinstance(field, ast.Constant) and isinstance(field.value, str)
                pairs[field.value] = const.id
        assert pairs, f"库侧 {fn.name} 里找不到 `_len_bounded` 调用——长度闸换了形状"
        found[fn.name] = pairs
    assert set(found) == set(KINDS), f"库侧三枚 builder 少了：{sorted(found)}"
    return found


class TestRegistration:
    def test_code_is_in_both_catalogs_by_name(self):
        assert CODE in CHECKS, f"{CODE} 未注册进 CHECKS（对外读数少一格硬门）"
        assert CODE in CODE_HINT, f"{CODE} 未注册进 CODE_HINT（hint 会静默塌成空串）"

    def test_hint_tells_the_agent_where_the_limit_comes_from(self):
        hint = CODE_HINT[CODE].strip()
        assert hint, "CODE_HINT 值是空白串——注册了等于没注册"
        assert "INBOX_MAX" in hint and "presence" in hint, \
            f"hint 没把上限真源指给 Agent，它会去 AF 里找数字：{hint!r}"


class TestSameSourceWithLibrary:
    """本批唯一的手抄面（kind → 受限字段名的映射）必须由库侧源码自证，值必须就是那枚常量本身。"""

    def test_limit_table_matches_library_source(self):
        library = _library_bounded_fields()
        assert INBOX_LEN_LIMITS.keys() == library.keys(), (
            f"AF 登记的收件箱主题与库侧带 `_len_bounded` 的 builder 不同源：{sorted(INBOX_LEN_LIMITS)} vs {sorted(library)}"
        )
        for kind, pairs in library.items():
            mine = INBOX_LEN_LIMITS[kind]
            assert mine.keys() == pairs.keys(), (
                f"{kind} 的受限字段名单漂了（库侧 {sorted(pairs)} / AF {sorted(mine)}）："
                "库侧新增一枚受限字段而 AF 没跟上 ⇒ 编译期放行、运行期被拒"
            )
            for field, const_name in pairs.items():
                assert mine[field] == getattr(_presence, const_name), (
                    f"{kind}.{field} 的上限 {mine[field]} 不等于库侧 {const_name} 的现读值 "
                    f"{getattr(_presence, const_name)}：闸慢了一拍"
                )

    def test_bounded_arg_is_a_real_positional_parameter_of_the_builder(self):
        # `_len_bounded(text, …, "text")` 的第一参必须真是 builder 的入参名——
        # 否则 `params` 里那个键根本到不了这道闸，编译期判的就是空气。
        for kind in KINDS:
            params = inspect.signature(getattr(_presence, kind)).parameters
            for field in INBOX_LEN_LIMITS[kind]:
                assert field in params, f"presence.{kind} 没有入参 {field}，映射表漂了"
                assert params[field].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, \
                    f"{kind}.{field} 不是按位置传的入参，桥的 positional 传法接不住它"

    def test_scanner_holds_no_length_number(self):
        """扫描器那一侧不许出现整数字面量：长度只从 `inbox_overlong_fields` 进来。"""
        method = _scanner_method()
        ints = [
            n.value
            for n in ast.walk(method)
            if isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool)
        ]
        assert not ints, f"_check_inbox_payload_len 里出现了整数字面量 {ints}：上限被手抄进扫描器"
        calls = {
            getattr(n.func, "id", "")
            for n in ast.walk(method)
            if isinstance(n, ast.Call)
        }
        assert "inbox_overlong_fields" in calls, "扫描器没走适配器那张表，等于另起一份上限"

    def test_table_values_are_the_library_constant_objects(self):
        """值必须**就是** `homesdk.presence.INBOX_MAX_*` 那几枚对象：有人换成字面量数字，这格先红。"""
        library_constants = [
            value for name, value in vars(_presence).items()
            if name.startswith("INBOX_MAX") and isinstance(value, int)
        ]
        for kind, limits in INBOX_LEN_LIMITS.items():
            for field, limit in limits.items():
                assert isinstance(limit, int) and not isinstance(limit, bool), f"{kind}.{field} 不是整数上限"
                assert any(limit is value for value in library_constants), (
                    f"{kind}.{field} 的值 {limit} 不是库侧常量的现读对象——手抄数字就是第二份会漂移的上限"
                )

    def test_adapter_module_has_no_run_time_attribute_fetch(self):
        """本文件不 import 整个 `presence` 模块、也不按名派发取值（那是 D 判据射程，且会把漂移藏成运行期 `AttributeError`）。"""
        tree = ast.parse((SRC / "af_adapters" / "inbox.py").read_text(encoding="utf-8"))
        calls = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "getattr" not in calls, "inbox.py 里出现了 getattr——上限取值绕回了按名派发"
        imported = {
            alias.name or alias.asname
            for n in ast.walk(tree)
            if isinstance(n, ast.Import)
            for alias in n.names
        }
        assert "homesdk.presence" not in imported, \
            f"整模块 import 回来了（{sorted(imported)}）：D 判据射程里的成员访问随时可能再写出来"
        from_imports = {
            (n.module, alias.name)
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)
            for alias in n.names
        }
        assert {("homesdk.presence", "INBOX_MAX_TEXT"), ("homesdk.presence", "INBOX_MAX_TITLE"),
                ("homesdk.presence", "INBOX_MAX_BODY")} <= from_imports, \
            f"三枚上限常量没有直接 import：{sorted(from_imports)}"


class TestEmission:
    @pytest.mark.parametrize("kind,field", [
        ("speak", "text"),
        ("notify", "title"),
        ("notify", "body"),
        ("tv", "content"),
    ])
    def test_overlong_field_fires_exactly_one_error(self, kind, field):
        result = _scan(_inbox_do(f"inbox.{kind}", {field: _long(kind, field)}))
        hits = _hits(result)
        assert len(hits) == 1, f"{kind}.{field} 超长应恰好一条 {CODE}，实际 {len(hits)}：{result.render()}"
        diag = hits[0]
        assert diag.level == ERROR, f"必死载荷该拦发布（ERROR），实际 {diag.level}"
        assert diag.node_id == "d1"
        assert str(_limit(kind, field)) in diag.message, f"message 没报出上限：{diag.message}"
        assert diag.hint == CODE_HINT[CODE] != ""

    @pytest.mark.parametrize("kind,field", [
        ("speak", "text"),
        ("notify", "title"),
        ("notify", "body"),
        ("tv", "content"),
    ])
    def test_boundary_is_pair_not_slop(self, kind, field):
        at = _hits(_scan(_inbox_do(f"inbox.{kind}", {field: _at_limit(kind, field)})))
        assert at == [], f"{kind}.{field} 恰好等于上限却被拒（{CODE}）＝误杀合法 IR"
        over = _hits(_scan(_inbox_do(f"inbox.{kind}", {field: _long(kind, field, 1)})))
        assert len(over) == 1, f"{kind}.{field} 只超 1 字就该红（上限 +1 没报＝闸慢了）"

    def test_notify_reports_each_overlong_field_separately(self):
        result = _scan(_inbox_do("inbox.notify", {"title": _long("notify", "title"), "body": _long("notify", "body")}))
        hits = _hits(result)
        assert len(hits) == 2, f"title 与 body 各报一条才对（逐字段），实际 {len(hits)}"
        text = " | ".join(d.message for d in hits)
        assert "title" in text and "body" in text, f"没点出具体字段名：{text}"

    def test_bare_action_name_and_dotted_both_fire(self):
        # DSL 把 `do d1 inbox.speak {…}` 切成 adapter=inbox + action=speak（`af_spec.py`），
        # IR JSON 里两种写法都在用——归一由 `kind_of` 单点做，这里钉两头。
        assert len(_hits(_scan(_inbox_do("speak", {"text": _long("speak", "text")})))) == 1
        assert len(_hits(_scan(_inbox_do("INBOX.SPEAK", {"text": _long("speak", "text")})))) == 1

    def test_short_enough_payload_is_clean(self):
        result = _scan(_inbox_do("inbox.speak", {"text": "走廊有人，请播报欢迎词"}))
        assert _hits(result) == []

    def test_other_adapters_are_not_taxed(self):
        node = {"id": "d1", "kind": "do", "adapter": "ha",
                "action": "light.turn_on", "params": {"entity_id": "light.study",
                                                      "text": _long("speak", "text")}}
        assert _hits(_scan(node)) == [], "非 inbox 适配器也被判超长＝把收件箱的契约加到全仓头上"

    @pytest.mark.parametrize("value", [12345, None, {"a": 1}, ["x"], 1.5])
    def test_non_string_values_are_out_of_scope(self, value):
        # 形态错由库侧 `TypeError` 那一路管；混进来会把"类型不对"误报成"超长"
        assert _hits(_scan(_inbox_do("inbox.speak", {"text": value}))) == []

    def test_unregistered_kind_is_not_judged_here(self):
        # 可投递主题的白名单真源是 `presence.INBOX_TOPICS`，判它的是桥；这里不抄第二份名单，
        # 所以未登记的 kind 落到"没有上限可判"⇒ 不发本码（由桥那侧 `ADM_ERR_PAYLOAD_INVALID` 拒）。
        assert _hits(_scan(_inbox_do("inbox.shout", {"text": _long("speak", "text")}))) == []

    def test_empty_or_absent_params_are_clean(self):
        assert _hits(_scan(_inbox_do("inbox.speak", {}))) == []
        assert len(inbox_overlong_fields("inbox.speak", {})) == 0
        assert len(inbox_overlong_fields("inbox.speak", None)) == 0


class TestBlocksPublication:
    """ERROR 的那一半脸：这条诊断必须进 `errors` 并把 `ok` 打成假（与 #87 的 WARN 相反）。"""

    def test_ok_is_false_and_code_is_in_errors(self):
        result = _scan(_inbox_do("inbox.speak", {"text": _long("speak", "text")}))
        assert _hits(result)
        assert any(d.code == CODE for d in result.errors), f"{CODE} 没进 errors：{[d.code for d in result.errors]}"
        assert result.ok is False, "必死的 IR 却过了编译＝这条预拒没生效"


class TestRuntimeFaceAgrees:
    """两端同判：同一份超长 `params` 在编译期报 ERROR、在运行期 fail-closed，且数字来自同一枚常量。"""

    def test_executor_forwards_params_verbatim(self):
        ex = (SRC / "af_executor.py").read_text(encoding="utf-8")
        assert re.search(r"adapter\.call\(node\.action or \"\",\s*dict\(node\.params\)\)", ex), (
            "执行器不再把 `node.params` 原样交给适配器——运行期长度可能和编译期量的不是同一份，"
            "本文件的满射程自证要重核"
        )

    def test_same_overlong_payload_fails_the_adapter_call(self):
        # dry_run 也在库里走一遍 builder（`_RecordingInboxClient`），所以超长照样抛
        result = InboxAdapter(dry_run=True).call("inbox.speak", {"text": _long("speak", "text")})
        assert result.success is False, "超长载荷竟然被适配器报成 done＝假绿"
        assert result.data.get("code") == ADM_ERR_PAYLOAD_INVALID, \
            f"失败码不是载荷侧的：{result.data.get('code')} / {result.error}"

    def test_at_limit_payload_passes_the_adapter_call(self):
        result = InboxAdapter(dry_run=True).call("inbox.speak", {"text": _at_limit("speak", "text")})
        assert result.success is True, f"恰好等于上限的载荷被运行期拒了：{result.error}"
