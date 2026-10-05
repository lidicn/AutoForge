"""计划表口径门必须**能变红**（铁律 #8），且红的必须是"文档比代码乐观"那一族。

起因（§六 那条登记）：上一批按 `check_ui_api_paths.py --list-uncalled` 的人工读数，把会话族三行的
`✅` 改成 `后端 ✅ ／ UI ✗`，并写下"若下一批要拿这份计划表当已完成依据，先对一遍名单"。那句话是
一次性人工动作：文档与门之间没有任何机械接缝，而这份表的漂移方向恰好是**文档乐观**（面板拆了、
路由改名了，✅ 还在原处），vue-tsc 与 pytest 都不知道那份表说了什么。本门把那次对表变成判据。

四条要害形状，各自单独可红／单独可验：
- 判据 A：`✅` 领头 + 兄弟门读不出认领 ⇒ 红（这是本门存在的理由）
- 判据 B：文档完整写出的 `VERB /api/…` 在服务端路由表里落不到 ⇒ 红（改名/写串口径）
- **上次那次更正不许被判成谎报**：`后端 ✅ ／ UI ✗` 领头不是 ✅ ⇒ 不算声明（否则本门一上线就把
  上一批的产物判红，逼人把它关掉）；而 `✅（expect 编辑入口⚠️）` 领头仍是声明
- 反向那一向（`🔲` 却在服务层读得出调用点）**只登记不判红**：门的调用点包含 `client.ts` 的服务层
  包装，"有这个函数"不等于"面板接了"

反空洞与射程：`text=None`／0 行／0 声明／兄弟门 sites 或 routes 为 0／它有解析不出的调用点
（`unparsed>0` 时判"没人调"会误伤）／行数或声明数掉到下限以下 ⇒ 一律 rc=2。
最后一组验的是"只有注释把它接上、没有真调用行"时 gates.sh 那条链不算接上（与装配覆盖门同族）。
"""
from __future__ import annotations

import importlib.util
import io
import pathlib
import sys
from contextlib import redirect_stdout

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_plan_ui_claims.py"
DOC = ROOT / "docs" / "plan" / "开发计划_WebUI全功能接入.md"
GATES_SH = ROOT / "gates.sh"


def _module():
    spec = importlib.util.spec_from_file_location("check_plan_ui_claims", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _row(verb: str, path: str, status: str) -> str:
    return f"| `{verb} {path}` | 用途 | 前端落点 | {status} |\n"


def _text(*rows: str) -> str:
    return "| 路由 | 用途 | 前端落点 | 状态 |\n|---|---|---|---|\n" + "".join(rows)


def _reading(server, uncalled, **over) -> dict:
    """造一份与 `gate_reading()` 同口径的读数：两侧路径都先过 `norm()`（真读数就是这么来的）。"""
    mod = _module()
    reading = {"server": {(v, mod.norm(p)) for v, p in server},
               "uncalled": {(v, mod.norm(p)) for v, p in uncalled},
               "sites": 9, "routes": len(server), "unparsed": 0, "trees": 3}
    reading.update(over)
    return reading


def _run(mod, text, reading) -> tuple[int, str]:
    rc, out = mod.run(text, reading, min_rows=1, min_claims=1)
    return rc, "\n".join(out)


# ── 判据 A：文档标已接、门读不出认领 ───────────────────────────────

def test_claimed_route_with_no_call_site_goes_red():
    mod = _module()
    text = _text(_row("GET", "/api/alpha", "✅"), _row("POST", "/api/beta", "✅"))
    rc, out = _run(mod, text, _reading([("GET", "/api/alpha"), ("POST", "/api/beta")],
                                       [("POST", "/api/beta")]))
    assert rc == 1 and "判据 A" in out and "/api/beta" in out, out
    # 只报被虚报的那一条，不牵连另一条
    assert "GET /api/alpha" not in out, out


def test_annotation_after_the_checkmark_still_counts_as_a_claim():
    """`✅（expect 编辑入口⚠️）` 领头是 ✅ ⇒ 仍是声明。括号里的附注不许把整条声明降级。"""
    mod = _module()
    text = _text(_row("POST", "/api/sim", "✅（expect 编辑入口⚠️）"))
    rows = mod.parse_doc(text)
    assert rows and rows[0]["claimed"] is True, rows
    rc, out = _run(mod, text, _reading([("POST", "/api/sim")], [("POST", "/api/sim")]))
    assert rc == 1 and "判据 A" in out, out


# ── 判据 B：文档引用了服务端路由表里落不到的契约 ───────────────────

def test_doc_route_absent_from_server_table_goes_red_even_when_not_claimed():
    mod = _module()
    text = _text(_row("GET", "/api/alpha", "✅"), _row("GET", "/api/renamed-away", "🔲"))
    rc, out = _run(mod, text, _reading([("GET", "/api/alpha")], []))
    assert rc == 1 and "判据 B" in out and "/api/renamed-away" in out, out


def test_path_param_names_are_normalized_across_the_two_sides():
    """文档写 `{id}`、服务端写 `{session_id}`：不归一就会把同一件事读成"契约不存在"（假红）。"""
    mod = _module()
    text = _text(_row("GET", "/api/sessions/{id}", "✅"), _row("POST", "/api/spec/compile", "✅"))
    rc, out = _run(mod, text, _reading([("GET", "/api/sessions/{session_id}"),
                                       ("POST", "/api/spec/compile")], []))
    assert rc == 0, out


def test_query_string_in_the_doc_does_not_become_a_phantom_route():
    mod = _module()
    text = _text(_row("GET", "/api/graphs?tag=", "✅"))
    assert mod.parse_doc(text)[0]["path"] == "/api/graphs"
    rc, out = _run(mod, text, _reading([("GET", "/api/graphs")], []))
    assert rc == 0, out


def test_shorthand_tails_are_out_of_range_instead_of_guessed():
    """`·/cancel`、`·/disable` 这类简写两种合并规则互相冲突 ⇒ 只数完整写出的那条，不拼回、不误伤。"""
    mod = _module()
    text = "| `POST /api/sessions/{id}/tick`·`/cancel`·`DELETE` | 会话操作条 | 操作条 | " \
           "后端 ✅ ／ **UI ✗** |\n"
    rows = mod.parse_doc(text)
    assert len(rows) == 1 and rows[0]["path"] == "/api/sessions/{}/tick", rows
    assert rows[0]["claimed"] is False, rows


# ── 上次那次更正的形状不许被判成谎报 ───────────────────────────────

def test_two_part_mark_is_not_a_claim():
    """`后端 ✅ ／ UI ✗` 领头不是 ✅：它已经在说实话，本门不许反过来判它红。"""
    mod = _module()
    text = _text(_row("GET", "/api/alpha", "✅"),
                 _row("GET", "/api/sessions", "后端 ✅ ／ **UI ✗**"))
    reading = _reading([("GET", "/api/alpha"), ("GET", "/api/sessions")],
                       [("GET", "/api/sessions")])
    rc, out = _run(mod, text, reading)
    assert rc == 0 and "判据" not in out, out


def test_reverse_direction_is_registered_but_never_red():
    """`🔲` 却在服务层读得出调用点 ⇒ 只登记。判红会把 client.ts 的服务层包装当成"面板已接"。"""
    mod = _module()
    text = _text(_row("GET", "/api/alpha", "✅"), _row("GET", "/api/metrics", "🔲"))
    rc, out = _run(mod, text, _reading([("GET", "/api/alpha"), ("GET", "/api/metrics")], []))
    assert rc == 0 and "登记（不判红" in out and "/api/metrics" in out, out


# ── 射程与反空洞：读不出就红着，不许报干净 ─────────────────────────

def test_missing_doc_is_range_collapse_not_clean():
    mod = _module()
    rc, out = _run(mod, None, _reading([("GET", "/api/alpha")], []))
    assert rc == 2 and "读不出" in out, out


def test_no_parseable_rows_is_range_collapse():
    mod = _module()
    rc, out = _run(mod, "| 路由 | 用途 |\n|---|---|\n| 会话 | 列表 |\n",
                   _reading([("GET", "/api/alpha")], []))
    assert rc == 2 and "没有射程" in out, out


def test_zero_claims_is_an_empty_set_clean_and_goes_red():
    """整份表一个 ✅ 都没有：本门的『干净』来自空集 ⇒ 判红而不是过关。"""
    mod = _module()
    text = _text(_row("GET", "/api/alpha", "🔲"))
    rc, out = _run(mod, text, _reading([("GET", "/api/alpha")], []))
    assert rc == 2 and "空集" in out, out


def test_sibling_gate_with_zero_range_is_not_a_basis():
    mod = _module()
    text = _text(_row("GET", "/api/alpha", "✅"))
    for over in ({"sites": 0}, {"routes": 0}):
        rc, out = _run(mod, text, _reading([("GET", "/api/alpha")], [], **over))
        assert rc == 2 and "射程" in out, (over, out)


def test_incomplete_claim_set_refuses_to_judge_nobody_calls_it():
    """兄弟门有解析不出的调用点时，"没人认领"不可信 ⇒ rc=2 而不是把真接了的面板判成谎报。"""
    mod = _module()
    text = _text(_row("GET", "/api/alpha", "✅"))
    rc, out = _run(mod, text, _reading([("GET", "/api/alpha")], [("GET", "/api/alpha")],
                                       unparsed=2))
    assert rc == 2 and "解析不出" in out, out


def test_anti_hollow_floors_reject_a_table_that_is_not_this_table():
    mod = _module()
    text = _text(*[_row("GET", f"/api/x{i}", "✅") for i in range(3)])
    reading = _reading([("GET", f"/api/x{i}") for i in range(3)], [])
    rc, out = mod.run(text, reading, min_rows=4, min_claims=1)
    assert rc == 2 and "反空洞" in "\n".join(out), (rc, out)
    rc, out = mod.run(text, reading, min_rows=3, min_claims=4)
    assert rc == 2 and "反空洞" in "\n".join(out), (rc, out)


# ── self-test：检测器失效必须被自己抓到 ────────────────────────────

def test_self_test_passes_against_the_real_repo():
    mod = _module()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.self_test()
    assert rc == 0, buf.getvalue()
    assert "六档全过" in buf.getvalue(), buf.getvalue()


def test_self_test_fails_when_detector_is_blind(monkeypatch):
    """反空洞的反空洞：`self_test` 必须验出一个永远报干净的检测器本体。"""
    mod = _module()
    monkeypatch.setattr(mod, "run", lambda *a, **k: (0, ["✓ 计划表口径门干净"]))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.self_test()
    assert rc == 1 and "检测器自己失效" in buf.getvalue(), buf.getvalue()


# ── 真仓读数：干净且非平凡，且这条门真在 gates.sh 的链上 ──────────

def test_real_repo_reading_is_clean_and_nontrivial():
    mod = _module()
    text = DOC.read_text(encoding="utf-8")
    reading = mod.gate_reading()
    rc, out = mod.run(text, reading)
    joined = "\n".join(out)
    assert rc == 0, joined
    rows = mod.parse_doc(text)
    claims = [r for r in rows if r["claimed"]]
    # 反空洞：真仓的数必须像样，否则"干净"是空集给的干净（下限本身由门把守）
    assert len(rows) >= mod.MIN_ROWS and len(claims) >= mod.MIN_CLAIMS, (len(rows), len(claims))
    assert reading["routes"] > 50 and reading["trees"] == 3, reading
    # 未认领名单里的每一条都不许被这份表标成 ✅（这就是那次人工对表的内容）
    uncalled = reading["uncalled"]
    assert any(claims), claims
    assert not [(r["verb"], r["path"]) for r in claims if (r["verb"], r["path"]) in uncalled]


def test_gate_is_actually_wired_into_gates_sh_as_a_call_not_a_comment():
    """接线形状与装配覆盖门同口径：只有 `"$REPO/scripts/…"` 那一行才算这条链真跑了它。"""
    text = GATES_SH.read_text(encoding="utf-8")
    assert '"$REPO/scripts/check_plan_ui_claims.py"' in text, "本门没接进 gates.sh"
    assert "plan_claims_rc" in text, "接了却没收 rc：那一行等于没判"
